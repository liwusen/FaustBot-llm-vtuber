// 组件页的纯逻辑：服务 key 映射、状态文案/配色、安装请求体与进度事件文案。
// 逐行对照旧 modules/components.js。

import { API } from '@/api/endpoints';

export interface ServiceState {
  key?: string;
  name?: string;
  description?: string;
  is_running?: boolean;
  port?: number | string;
  status?: string;
  log_tail?: unknown;
  [key: string]: unknown;
}

export interface GpuState {
  name?: string;
  cuda_version?: string;
  driver_version?: string;
  [key: string]: unknown;
}

export interface GpuInfo {
  has_nvidia?: boolean;
  gpus?: GpuState[];
}

export interface FunasrState {
  installed?: boolean;
  version?: string | null;
  torch_version?: string | null;
  torch_variant?: string | null;
}

export interface TtsState {
  installed?: boolean;
  path?: string | null;
  variant?: string | null;
}

export interface MinecraftBridgeState {
  is_running?: boolean;
}

export interface ComponentStatus {
  gpu?: GpuInfo;
  components?: {
    funasr?: FunasrState;
    tts?: TtsState;
    minecraft_bridge?: MinecraftBridgeState;
  };
  services?: Record<string, ServiceState>;
}

export type InstallComponent = 'funasr' | 'tts';

export interface InstallForm {
  torchVariant?: string;
  useAliyunMirror?: boolean;
  ttsVariant?: string;
}

export interface InstallBody {
  component: string;
  torch_variant?: string;
  use_aliyun_mirror?: boolean;
  tts_variant?: string;
}

export interface InstallProgressEvent {
  stage?: string;
  progress_percent?: number;
  log_lines?: string[];
  error?: string;
  [key: string]: unknown;
}

// admin API 使用实际 service key，与组件状态显示 key 可能不同
export const SERVICE_KEY_MAP: Record<string, string> = { minecraft: 'mc_operator' };

export function adminServiceKey(displayKey: string): string {
  return SERVICE_KEY_MAP[displayKey] || displayKey;
}

/** 旧 _serviceStatusText。 */
export function serviceStatusText(svc: ServiceState | null | undefined): string {
  if (svc?.status === 'starting') return '启动中';
  if (svc?.status === 'stopping') return '关闭中';
  if (svc?.is_running) return '运行中';
  return '已停止';
}

/** 旧 _serviceTone（cfg-dot 的配色 class）。 */
export function serviceTone(svc: ServiceState | null | undefined): string {
  if (svc?.status === 'starting') return 'is-warn';
  if (svc?.status === 'stopping') return 'is-busy';
  if (svc?.is_running) return 'is-ok';
  return 'is-stop';
}

export function servicePortText(svc: ServiceState | null | undefined): string {
  return svc?.port ? `端口 ${svc.port}` : '';
}

/** 旧 `_row("服务", \`${svcStatus} ${svcPort}\`)`（端口为空时保留旧实现的尾随空格）。 */
export function serviceSummary(svc: ServiceState | null | undefined): string {
  return `${serviceStatusText(svc)} ${servicePortText(svc)}`;
}

export function funasrInstallText(data: FunasrState | null | undefined): string {
  return data?.installed ? `已安装 (funasr ${data.version || ''})` : '未安装';
}

export function funasrTorchText(data: FunasrState | null | undefined): string {
  return data?.torch_version
    ? `${data.torch_version} (${data.torch_variant || '?'})`
    : '未检测';
}

export function ttsInstallText(data: TtsState | null | undefined): string {
  return data?.installed ? `已安装 (${data.path || ''})` : '未安装';
}

export function ttsVariantText(data: TtsState | null | undefined): string {
  return data?.variant || 'standard';
}

export const FUNASR_VARIANTS = [
  { value: 'cu128', label: 'cu128 (CUDA 12.8)' },
  { value: 'cu121', label: 'cu121 (CUDA 12.1)' },
  { value: 'cu130', label: 'cu130 (CUDA 13.0)' },
  { value: 'cpu', label: 'cpu (无 GPU)' },
] as const;

export const TTS_VARIANTS = [
  { value: 'standard', label: 'Standard' },
  { value: 'nvidia50', label: 'NVIDIA 50 系列' },
] as const;

/** 旧 startComponentInstall 的请求体：funasr 带 torch 变体与镜像开关，tts 带变体。 */
export function buildInstallBody(component: InstallComponent, form: InstallForm = {}): InstallBody {
  if (component === 'funasr') {
    return {
      component: 'funasr',
      torch_variant: form.torchVariant,
      use_aliyun_mirror: Boolean(form.useAliyunMirror),
    };
  }
  return { component: 'tts', tts_variant: form.ttsVariant };
}

/** SSE 任务的相对路径。 */
export function componentTaskEventsPath(taskId: string): string {
  return API.componentTaskEvents(String(taskId || ''));
}

export function componentTaskEventsUrl(baseUrl: string, taskId: string): string {
  return `${String(baseUrl || '').replace(/\/+$/, '')}${componentTaskEventsPath(taskId)}`;
}

/** 旧 progress 处理器：最后一行日志优先，其次 stage。 */
export function installProgressText(event: InstallProgressEvent | null | undefined): string {
  const lines = Array.isArray(event?.log_lines) ? event?.log_lines || [] : [];
  const last = lines.length ? lines[lines.length - 1] : '';
  return String(last || event?.stage || '');
}

/** 旧 error 处理器：服务端错误 → 错误: xxx，解析失败 → 连接错误。 */
export function installErrorText(error: unknown): string {
  return `错误: ${error || '未知'}`;
}

/** 旧 GPU 卡：没有 NVIDIA 时只有一行状态。 */
export function gpuRows(gpu: GpuInfo | null | undefined): Array<{ label: string; value: string }> {
  const data = gpu || {};
  const gpus = Array.isArray(data.gpus) ? data.gpus : [];
  if (!data.has_nvidia || gpus.length === 0) {
    return [{ label: '状态', value: '未检测到 NVIDIA GPU' }];
  }
  const rows: Array<{ label: string; value: string }> = [];
  gpus.forEach((item, index) => {
    rows.push({ label: `GPU ${index + 1}`, value: String(item.name || '未知') });
    if (item.cuda_version) rows.push({ label: 'CUDA 版本', value: String(item.cuda_version) });
    if (item.driver_version) rows.push({ label: 'Driver 版本', value: String(item.driver_version) });
  });
  return rows;
}
