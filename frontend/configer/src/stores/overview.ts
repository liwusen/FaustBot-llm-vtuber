import { ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { cfgApi, getBackendBaseUrl } from '@/api/ipc';
import { useUiStore } from '@/stores/ui';
import type {
  DownloadProgress,
  DownloadStatusResponse,
  OverviewPlugin,
  OverviewService,
  RecentError,
  RuntimeSummary,
  UpdateCheckResponse,
  UpdateManualResponse,
  UpdateResult,
} from '@/modules/overview/types';

/** 离开概览页（KeepAlive 失活）时中断等待中的下载监听，避免悬挂的 Promise。 */
class DownloadCancelled extends Error {
  constructor() {
    super('下载监听已取消');
    this.name = 'DownloadCancelled';
  }
}

export const useOverviewStore = defineStore('configer-overview', () => {
  const plugins = ref<OverviewPlugin[]>([]);
  const services = ref<OverviewService[]>([]);
  const recentErrors = ref<RecentError[]>([]);
  const mainModel = ref('');
  const runtime = ref<RuntimeSummary>({});

  // 更新状态只在内存里：刷新应用后回到初始值（旧 state.runtimeUpdate 同样不落盘）。
  const runtimeUpdate = ref<UpdateCheckResponse | null>(null);
  const updateResult = ref<UpdateResult>({ kind: 'none' });
  const proxyEnabled = ref(true);

  const checking = ref(false);
  const updating = ref(false);
  const manualBusy = ref<'none' | 'verifying' | 'applying'>('none');
  const progress = ref<DownloadProgress>({ visible: false, percent: 0, text: '' });

  let eventSource: EventSource | null = null;
  let downloadReject: ((reason?: unknown) => void) | null = null;
  // 每次下载/取消都递增；用于丢弃「取消后仍然到达」的启动响应。
  let downloadToken = 0;

  /**
   * 旧 ensureModuleData('overview') 的 4 个请求保持整组 Promise.all 语义；
   * runtime 是后来单独加的，不能因为它失败就把插件/服务/日志/主模型一起丢掉。
   */
  async function loadOverview(): Promise<void> {
    try {
      const [pluginData, serviceData, errorData, providerData] = await Promise.all([
        cfgApi<{ items?: OverviewPlugin[] }>('GET', API.plugins),
        cfgApi<{ items?: OverviewService[] }>('GET', API.services),
        cfgApi<{ errors?: RecentError[] }>('GET', API.recentErrors),
        cfgApi<{ main_model?: string }>('GET', API.providers),
      ]);
      plugins.value = pluginData.items || [];
      services.value = serviceData.items || [];
      recentErrors.value = errorData.errors || [];
      mainModel.value = providerData.main_model || '';
    } catch (err) {
      console.warn('overview data fetch error', err);
    }
    try {
      const runtimeData = await cfgApi<{ runtime?: RuntimeSummary }>('GET', API.runtime);
      runtime.value = runtimeData.runtime || {};
    } catch (err) {
      console.warn('overview runtime fetch error', err);
    }
  }

  function closeEventSource(): void {
    if (!eventSource) return;
    eventSource.close();
    eventSource = null;
  }

  /** 仅在概览模块真正卸载时调用：断开 SSE，并让等待中的 applyUpdate 走取消分支。 */
  function cancelDownload(): void {
    downloadToken += 1;
    closeEventSource();
    const reject = downloadReject;
    downloadReject = null;
    progress.value = { visible: false, percent: 0, text: '' };
    if (reject) reject(new DownloadCancelled());
  }

  /** 旧 overview.js 的 sseDownload：启动下载后监听 /faust/update/download/{id}/events。 */
  function sseDownload(tag: string, assetName: string, useProxy: boolean): Promise<Record<string, unknown>> {
    const token = ++downloadToken;
    return new Promise((resolve, reject) => {
      progress.value = { visible: true, percent: 0, text: '准备下载...' };
      downloadReject = reject;

      cfgApi<DownloadStatusResponse>('POST', API.updateDownload, {
        tag,
        asset_name: assetName,
        use_proxy: useProxy,
      })
        .then((res) => {
          // 启动响应回来前已被取消（如模块卸载）：不要建立新的 SSE 连接。
          if (token !== downloadToken) return;
          if (res.status !== 'started') {
            downloadReject = null;
            reject(new Error(res.error || '启动下载失败'));
            return;
          }
          const downloadId = String(res.download_id || '').trim();
          if (!downloadId) {
            downloadReject = null;
            reject(new Error(res.error || '启动下载失败'));
            return;
          }
          const base = getBackendBaseUrl();
          const source = new EventSource(`${base}${API.updateDownloadEvents(downloadId)}`);
          eventSource = source;
          let closed = false;

          const done = (data: Record<string, unknown>): void => {
            if (closed) return;
            closed = true;
            if (eventSource === source) closeEventSource();
            downloadReject = null;
            resolve(data);
          };
          const fail = (message: string): void => {
            if (closed) return;
            closed = true;
            if (eventSource === source) closeEventSource();
            downloadReject = null;
            reject(new Error(message));
          };

          source.addEventListener('progress', (event) => {
            try {
              const data = JSON.parse((event as MessageEvent).data) as Record<string, unknown>;
              if (data.done) {
                done(data);
                return;
              }
              progress.value = {
                visible: true,
                percent: Number(data.progress) || 0,
                text: `${data.downloaded_mb}MB / ${data.total_mb}MB  (${data.speed_mbps}MB/s)`,
              };
            } catch {
              // 与旧实现一致：无法解析的进度事件直接忽略。
            }
          });
          source.addEventListener('complete', (event) => {
            try {
              done(JSON.parse((event as MessageEvent).data) as Record<string, unknown>);
            } catch {
              fail('解析完成事件失败');
            }
          });
          source.addEventListener('error', (event) => {
            try {
              const data = JSON.parse((event as MessageEvent).data) as { error?: string };
              if (data && data.error) {
                fail(data.error);
                return;
              }
            } catch {
              // 原生连接错误没有 data，落到下面的统一文案。
            }
            fail('下载连接中断');
          });
        })
        .catch((err: unknown) => {
          if (token !== downloadToken) return;
          downloadReject = null;
          reject(err);
        });
    });
  }

  /** 旧 checkBtn 的完整流程：错误状态直接进结果区，成功才写 runtimeUpdate。 */
  async function checkUpdate(): Promise<void> {
    checking.value = true;
    updateResult.value = { kind: 'none' };
    progress.value = { ...progress.value, visible: false };
    try {
      const data = await cfgApi<UpdateCheckResponse>('POST', API.updateCheck, {});
      const payload = data || {};
      if (payload.status === 'error') {
        updateResult.value = { kind: 'error', text: String(payload.error || '检查失败') };
        return;
      }
      runtimeUpdate.value = payload;
      if (payload.has_update) {
        updateResult.value = {
          kind: 'available',
          latestTag: String(payload.latest_tag || ''),
          latestVersion: String(payload.latest_version || ''),
          releaseBody: String(payload.release_body || '暂无发布说明').substring(0, 500),
          assetName: String(payload.asset_name || ''),
        };
      } else {
        updateResult.value = { kind: 'latest' };
      }
    } catch (err) {
      updateResult.value = { kind: 'error', text: `检查更新失败: ${String(err)}` };
    } finally {
      checking.value = false;
    }
  }

  /** 「开始更新」：SSE 下载 → apply；完成后结果区只留准备就绪文案。 */
  async function applyUpdate(tag: string, assetName: string): Promise<void> {
    const ui = useUiStore();
    updating.value = true;
    try {
      await sseDownload(tag, assetName, proxyEnabled.value);
      const ar = await cfgApi<{ status?: string; error?: string }>('POST', API.updateApply, {
        tag,
        asset_name: assetName,
      });
      if (ar.status === 'update_prepared') {
        updateResult.value = { kind: 'prepared' };
        progress.value = { ...progress.value, visible: false };
      } else {
        ui.showBanner('error', `更新失败: ${ar.error || '未知错误'}`);
      }
    } catch (err) {
      // 页面失活导致的中断不弹错误横幅（下载本身在后端继续）。
      if (!(err instanceof DownloadCancelled)) {
        ui.showBanner('error', `更新异常: ${String(err)}`);
      }
    } finally {
      updating.value = false;
    }
  }

  /** 「手动选择更新包」：选 zip → 校验 → 结果区给出可直接应用的入口。 */
  async function chooseManualPackage(filePath: string): Promise<void> {
    const ui = useUiStore();
    manualBusy.value = 'verifying';
    updateResult.value = { kind: 'none' };
    progress.value = { ...progress.value, visible: false };
    try {
      const data = await cfgApi<UpdateManualResponse>('POST', API.updateManual, { file_path: filePath });
      const payload = data || {};
      if (payload.status !== 'ok') {
        ui.showBanner('error', String(payload.error || '手动更新包校验失败'));
        return;
      }
      updateResult.value = {
        kind: 'manual',
        tag: String(payload.tag || ''),
        version: String(payload.version || ''),
        assetName: String(payload.asset_name || ''),
      };
    } catch (err) {
      ui.showBanner('error', `手动更新包校验异常: ${String(err)}`);
    } finally {
      manualBusy.value = 'none';
    }
  }

  /** 手动包已就绪后点「开始更新」：跳过下载直接 apply。 */
  async function applyManualUpdate(): Promise<void> {
    const ui = useUiStore();
    const current = updateResult.value;
    if (current.kind !== 'manual') return;
    manualBusy.value = 'applying';
    try {
      const ar = await cfgApi<{ status?: string; error?: string }>('POST', API.updateApply, {
        tag: current.tag,
        asset_name: current.assetName,
      });
      if (ar.status === 'update_prepared') {
        updateResult.value = { kind: 'prepared' };
      } else {
        ui.showBanner('error', `更新失败: ${ar.error || '未知错误'}`);
      }
    } catch (err) {
      ui.showBanner('error', `更新异常: ${String(err)}`);
    } finally {
      manualBusy.value = 'none';
    }
  }

  return {
    plugins,
    services,
    recentErrors,
    mainModel,
    runtime,
    runtimeUpdate,
    updateResult,
    proxyEnabled,
    checking,
    updating,
    manualBusy,
    progress,
    loadOverview,
    cancelDownload,
    checkUpdate,
    applyUpdate,
    chooseManualPackage,
    applyManualUpdate,
  };
});
