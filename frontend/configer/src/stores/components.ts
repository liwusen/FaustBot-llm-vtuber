import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { getBackendBaseUrl, cfgApi } from '@/api/ipc';
import { formatApiError } from '@/api/errors';
import {
  adminServiceKey,
  buildInstallBody,
  componentTaskEventsUrl,
  installErrorText,
  installProgressText,
} from '@/modules/components/keyMap';
import type {
  ComponentStatus,
  InstallComponent,
  InstallForm,
  InstallProgressEvent,
} from '@/modules/components/keyMap';

const POLL_INTERVAL_MS = 3000;
const POLL_MAX_WAIT_MS = 40000;

/**
 * 组件页状态：GPU/服务/安装状态 + 服务启停轮询 + 安装 SSE。
 * 定时器与 SSE 都收在 store 里，模块卸载时由 dispose() 统一清理。
 */
export const useComponentsStore = defineStore('configer-components', () => {
  const status = ref<ComponentStatus | null>(null);

  const installVisible = ref(false);
  const installStage = ref('');
  const installPercent = ref(0);
  const installMessage = ref('');
  const installLogs = ref<string[]>([]);
  const installTaskId = ref<string | null>(null);
  const installCancelling = ref(false);

  // 非响应式的资源句柄：轮询定时器与安装事件流。
  const pollTimers = new Map<string, ReturnType<typeof setInterval>>();
  let installSource: EventSource | null = null;

  const installLogText = computed(() => installLogs.value.join('\n'));
  const cancelVisible = computed(() => installTaskId.value !== null);

  /** 旧 refreshComponentStatus：失败只告警，不清空已有数据。 */
  async function loadStatus(): Promise<void> {
    try {
      status.value = await cfgApi<ComponentStatus>('GET', API.componentsStatus);
    } catch (err) {
      console.warn('刷新组件状态失败', err);
    }
  }

  /** 首次进入：旧 ensureModuleData('components') 失败时把状态置空。 */
  async function initialLoad(): Promise<void> {
    try {
      status.value = await cfgApi<ComponentStatus>('GET', API.componentsStatus);
    } catch (err) {
      console.warn('components data fetch error', err);
      status.value = null;
    }
  }

  function stopServicePolling(displayKey: string): void {
    const timer = pollTimers.get(displayKey);
    if (timer !== undefined) {
      clearInterval(timer);
      pollTimers.delete(displayKey);
    }
  }

  function stopAllPolling(): void {
    for (const key of [...pollTimers.keys()]) stopServicePolling(key);
  }

  /** 旧 _startServicePolling：立即刷新一次，之后每 3s 一次，最多 40s 或状态落定。 */
  function pollService(displayKey: string): void {
    stopServicePolling(displayKey);
    const startedAt = Date.now();
    let inFlight = false;
    const tick = async (): Promise<void> => {
      if (inFlight) return;
      inFlight = true;
      try {
        await loadStatus();
        const svc = status.value?.services?.[displayKey];
        const elapsed = Date.now() - startedAt;
        if (elapsed >= POLL_MAX_WAIT_MS || !svc || (svc.status !== 'starting' && svc.status !== 'stopping')) {
          stopServicePolling(displayKey);
        }
      } finally {
        inFlight = false;
      }
    };
    const timer = setInterval(() => {
      void tick();
    }, POLL_INTERVAL_MS);
    pollTimers.set(displayKey, timer);
    void tick();
  }

  async function startService(displayKey: string): Promise<void> {
    try {
      await cfgApi('POST', API.serviceStart(adminServiceKey(displayKey)));
      pollService(displayKey);
    } catch (err) {
      console.warn('启动服务失败', err);
    }
  }

  async function stopService(displayKey: string): Promise<void> {
    try {
      await cfgApi('POST', API.serviceStop(adminServiceKey(displayKey)));
      pollService(displayKey);
    } catch (err) {
      console.warn('停止服务失败', err);
    }
  }

  async function fetchServiceLog(displayKey: string): Promise<string> {
    const res = await cfgApi<{ item?: { log_tail?: unknown } }>(
      'GET',
      API.service(adminServiceKey(displayKey), true),
    );
    const log = res?.item?.log_tail;
    if (Array.isArray(log)) return log.map((line) => String(line)).join('\n');
    if (typeof log === 'string') return log;
    return '（无日志内容）';
  }

  /** 旧 showProgress：只在传入数字时推进进度条，pip_output 追加日志。 */
  function showProgress(stage: string, percent: number | null, message: string): void {
    installVisible.value = true;
    installStage.value = stage;
    if (typeof percent === 'number') installPercent.value = percent;
    if (message) installMessage.value = message;
    if (stage === 'pip_output') installLogs.value = [...installLogs.value, message];
  }

  function closeInstallSource(): void {
    if (installSource) {
      installSource.close();
      installSource = null;
    }
  }

  function finishTask(): void {
    closeInstallSource();
    installTaskId.value = null;
    installCancelling.value = false;
  }

  function connectSse(taskId: string): void {
    installTaskId.value = taskId;
    installCancelling.value = false;
    closeInstallSource();
    const es = new EventSource(componentTaskEventsUrl(getBackendBaseUrl(), taskId));
    installSource = es;

    es.addEventListener('progress', (evt) => {
      try {
        const data = JSON.parse(String((evt as MessageEvent).data || '{}')) as InstallProgressEvent;
        const percent = typeof data.progress_percent === 'number' ? data.progress_percent : null;
        showProgress(String(data.stage || ''), percent, installProgressText(data));
      } catch {
        // 旧实现同样忽略无法解析的 progress 帧
      }
    });

    es.addEventListener('complete', () => {
      finishTask();
      showProgress('complete', 100, '安装完成');
      void loadStatus();
    });

    es.addEventListener('cancelled', () => {
      finishTask();
      showProgress('cancelled', null, '任务已终止');
      void loadStatus();
    });

    es.addEventListener('error', (evt) => {
      finishTask();
      let message = '连接错误';
      const raw = (evt as MessageEvent).data;
      if (typeof raw === 'string' && raw) {
        try {
          const data = JSON.parse(raw) as InstallProgressEvent;
          message = installErrorText(data?.error);
        } catch {
          message = '连接错误';
        }
      }
      showProgress('error', null, message);
    });
  }

  async function startInstall(component: InstallComponent, form: InstallForm = {}): Promise<void> {
    try {
      const res = await cfgApi<{ task_id?: string }>(
        'POST',
        API.componentsInstall,
        buildInstallBody(component, form),
      );
      if (res?.task_id) {
        showProgress('preparing', 0, `开始安装 ${component}...`);
        connectSse(res.task_id);
      }
    } catch (err) {
      showProgress('error', null, `启动安装失败: ${formatApiError(err)}`);
    }
  }

  async function cancelInstall(): Promise<void> {
    const taskId = installTaskId.value;
    if (!taskId) return;
    installCancelling.value = true;
    try {
      await cfgApi('POST', API.componentTaskCancel(taskId), {});
      showProgress('cancel_requested', null, '正在终止任务...');
    } catch (err) {
      showProgress('error', null, `终止失败: ${formatApiError(err)}`);
      installCancelling.value = false;
    }
  }

  /** 模块卸载：清掉轮询定时器与安装事件流。 */
  function dispose(): void {
    stopAllPolling();
    closeInstallSource();
  }

  return {
    status,
    installVisible,
    installStage,
    installPercent,
    installMessage,
    installLogs,
    installLogText,
    installTaskId,
    installCancelling,
    cancelVisible,
    initialLoad,
    loadStatus,
    startService,
    stopService,
    pollService,
    stopServicePolling,
    stopAllPolling,
    fetchServiceLog,
    startInstall,
    cancelInstall,
    dispose,
  };
});
