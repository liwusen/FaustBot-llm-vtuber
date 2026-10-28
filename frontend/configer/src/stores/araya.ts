import { computed, ref, shallowRef } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { cfgApi, getBackendBaseUrl } from '@/api/ipc';
import { formatApiError } from '@/api/errors';
import { useUiStore } from '@/stores/ui';
import {
  arayaDuration,
  arayaTraceBlocks,
} from '@/modules/araya/format';
import type {
  ArayaRunLog,
  ArayaStatus,
  ArayaTrace,
  ArayaTraceMessage,
} from '@/modules/araya/format';

/**
 * Araya 页状态。
 *
 * SSE 连接放在 store（而不是组件）里：Araya 是 KeepAlive 页面，切到别的模块时
 * 组件被 deactivated，但运行中的流必须继续写入；运行结束时再刷新状态并标记页面失效。
 * 连接只在组件卸载 / 应用退出时关闭。
 */
export const useArayaStore = defineStore('configer-araya', () => {
  const status = ref<ArayaStatus | null>(null);
  const lastTrace = ref<ArayaTrace | null>(null);
  const liveTrace = ref<ArayaTrace | null>(null);
  const traceError = ref('');
  const stale = ref(false);
  const eventSource = shallowRef<EventSource | null>(null);

  const running = computed(() => Boolean(eventSource.value) || status.value?.run_in_progress === true);
  const streaming = computed(() => Boolean(eventSource.value));
  const activeTrace = computed<ArayaTrace | null>(() => liveTrace.value || lastTrace.value);
  const lastLog = computed<ArayaRunLog | null>(() => {
    const raw = status.value?.last_log;
    return raw && typeof raw === 'object' ? (raw as ArayaRunLog) : null;
  });
  const traceBlocks = computed(() => arayaTraceBlocks(activeTrace.value?.messages));
  const toolCount = computed(
    () => traceBlocks.value.filter((block) => block.kind === 'tool').length,
  );

  async function loadStatus(): Promise<void> {
    try {
      const data = await cfgApi<{ araya?: ArayaStatus }>('GET', API.arayaStatus);
      status.value = data?.araya || null;
    } catch (err) {
      console.warn('araya data fetch error', err);
      status.value = null;
    }
  }

  async function loadTrace(): Promise<void> {
    traceError.value = '';
    try {
      const data = await cfgApi<{ trace?: ArayaTrace }>('GET', API.arayaTrace);
      lastTrace.value = data?.trace || null;
    } catch (err) {
      lastTrace.value = null;
      traceError.value = formatApiError(err);
    }
  }

  async function saveSettings(enabled: boolean, idleMinutes: unknown): Promise<void> {
    const data = await cfgApi<{ araya?: ArayaStatus }>('POST', API.arayaSettings, {
      enabled,
      idle_minutes: Number(idleMinutes || 30),
    });
    if (data?.araya) status.value = data.araya;
  }

  /** 运行结束/被外部触发后让页面失效：下次激活时重新拉状态与轨迹。 */
  function invalidate(): void {
    stale.value = true;
  }

  function consumeStale(): boolean {
    const value = stale.value;
    stale.value = false;
    return value;
  }

  function closeStream(): void {
    const source = eventSource.value;
    eventSource.value = null;
    if (source) source.close();
    liveTrace.value = null;
    window.removeEventListener('beforeunload', closeStream);
  }

  function pushTraceMessage(message: ArayaTraceMessage): void {
    const trace = liveTrace.value;
    if (!trace) return;
    if (!Array.isArray(trace.messages)) trace.messages = [];
    trace.messages.push(message);
  }

  function handleStep(evt: Event): void {
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(String((evt as MessageEvent).data || '{}')) as Record<string, unknown>;
    } catch (err) {
      console.warn('SSE step parse error', err);
      return;
    }
    switch (data.type) {
      case 'start':
        liveTrace.value = {
          conversation_id: `araya-live-${Date.now()}`,
          reason: String(data.reason || '-'),
          status: 'running',
          started_at_iso: new Date().toISOString(),
          messages: [],
          tool_calls: [],
        };
        // 先把页面切到「运行中 + 本次运行轨迹」，流式事件才有正确的落点
        invalidate();
        break;
      case 'llm_chunk':
        pushTraceMessage({ role: 'assistant', content: String(data.content || '') });
        break;
      case 'tool_start':
        pushTraceMessage({
          role: 'tool',
          tool_name: String(data.tool || 'tool'),
          call_id: String(data.call_id || ''),
          args: data.args || {},
        });
        break;
      case 'tool_end':
        pushTraceMessage({
          role: 'tool_result',
          tool_name: String(data.tool || 'tool'),
          call_id: String(data.call_id || ''),
          result: data.result ?? '',
          duration_seconds: data.duration ?? null,
        });
        break;
      default:
        // llm_start 等事件旧实现同样忽略
        break;
    }
  }

  async function finishRun(duration: unknown): Promise<void> {
    await loadStatus();
    await loadTrace();
    const ui = useUiStore();
    ui.showBanner(
      'success',
      `Araya 执行完成${duration ? `（${arayaDuration(duration)}）` : ''}。`,
    );
  }

  async function failRun(message: string): Promise<void> {
    await loadStatus();
    await loadTrace();
    const ui = useUiStore();
    ui.showBanner('error', `Araya 错误: ${message}`);
  }

  /** 手动触发：SSE 全程只写 liveTrace；resolve/reject 供拖拽滑块显示结果。 */
  function startRun(): Promise<string | void> {
    if (eventSource.value) return Promise.resolve('Araya 正在运行');
    const url = `${getBackendBaseUrl()}${API.arayaTriggerSSE}?reason=manual_from_configer`;

    return new Promise<string | void>((resolve, reject) => {
      const es = new EventSource(url);
      eventSource.value = es;
      window.addEventListener('beforeunload', closeStream);

      const finish = (): void => {
        if (eventSource.value === es) eventSource.value = null;
        es.close();
        window.removeEventListener('beforeunload', closeStream);
        liveTrace.value = null;
      };

      es.addEventListener('step', handleStep);

      es.addEventListener('done', (evt) => {
        let duration: unknown = null;
        try {
          const data = JSON.parse(String((evt as MessageEvent).data || '{}')) as Record<string, unknown>;
          duration = data.duration ?? null;
        } catch (err) {
          console.warn('SSE done parse error', err);
        }
        finish();
        invalidate();
        void (async () => {
          try {
            await finishRun(duration);
          } finally {
            resolve();
          }
        })();
      });

      es.addEventListener('error', (evt) => {
        let message = '连接中断';
        const raw = (evt as MessageEvent).data;
        if (typeof raw === 'string' && raw) {
          try {
            const data = JSON.parse(raw) as Record<string, unknown>;
            message = String(data.message || data.error || message);
          } catch (err) {
            console.warn('SSE error parse error', err);
          }
        }
        finish();
        invalidate();
        void (async () => {
          try {
            await failRun(message);
          } finally {
            reject(new Error(message));
          }
        })();
      });
    });
  }

  return {
    status,
    lastTrace,
    liveTrace,
    traceError,
    stale,
    eventSource,
    running,
    streaming,
    activeTrace,
    lastLog,
    traceBlocks,
    toolCount,
    loadStatus,
    loadTrace,
    saveSettings,
    invalidate,
    consumeStale,
    closeStream,
    startRun,
  };
});
