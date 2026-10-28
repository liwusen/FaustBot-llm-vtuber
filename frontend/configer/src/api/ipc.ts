import type { FileDialogOptions, FrontendApi, ModelProfileResult } from '@/api/types';

export function requireApi(): FrontendApi {
  const api = window.api;
  if (!api || typeof api.configRequest !== 'function') {
    throw new Error('配置中心必须运行在 Electron 中（window.api 不可用）');
  }
  return api;
}

export function getBackendBaseUrl(): string {
  return window.api?.backendBaseUrl || 'http://127.0.0.1:13900';
}

/**
 * Electron IPC 使用结构化克隆，Vue reactive Proxy 无法被克隆。
 * 所有请求体都按 JSON 语义转成普通对象后再过 IPC。
 */
function toIpcPayload<T>(value: T): T {
  if (value === undefined || value === null) return value;
  try {
    return JSON.parse(JSON.stringify(value)) as T;
  } catch {
    return value;
  }
}

/** 唯一允许的后端请求入口：Electron IPC。 */
export function cfgApi<T = unknown>(
  method: string,
  path: string,
  payload?: unknown,
  query?: Record<string, unknown> | null,
  options?: { timeoutMs?: number },
): Promise<T> {
  return requireApi().configRequest<T>(
    method,
    path,
    toIpcPayload(payload),
    toIpcPayload(query),
    options,
  );
}

export function apiLocal<T = unknown>(method: string, url: string, payload?: unknown): Promise<T> {
  const api = window.api;
  if (!api || typeof api.configHttpRequest !== 'function') {
    return Promise.reject(new Error('window.api.configHttpRequest 未实现'));
  }
  return api.configHttpRequest<T>(method, url, toIpcPayload(payload));
}

export function openFileDialog(options?: FileDialogOptions): Promise<string | null> {
  return requireApi().configOpenFile(options);
}

export function openDirectoryDialog(options?: { title?: string }): Promise<string | null> {
  return requireApi().configOpenDirectory(options);
}

export function openPath(targetPath: string): Promise<{ ok: boolean; error: string | null }> {
  return requireApi().configOpenPath(targetPath);
}

export function getFaustbotRoot(): Promise<string> {
  return requireApi().getFaustbotRoot();
}

export function openLiveWindow(): Promise<{ ok: boolean; mode?: string }> {
  return requireApi().openLiveWindow();
}

export function toggleLogPanel(): Promise<boolean> {
  return requireApi().toggleLogPanel();
}

export function toggleWidgetEditMode(): Promise<unknown> {
  return requireApi().toggleWidgetEditMode();
}

export function ensureModelProfile(modelDir: string, force = false): Promise<ModelProfileResult> {
  return requireApi().ensureModelProfile(modelDir, force);
}

export function saveModelProfile(
  modelDir: string,
  json: unknown,
): Promise<{ ok: boolean; error?: string }> {
  return requireApi().saveModelProfile(modelDir, json);
}

export function isNestedLive2DModelDir(modelDir: string): Promise<unknown> {
  return requireApi().isNestedLive2DModelDir(modelDir);
}

export function flattenNestedLive2DModelDir(
  modelDir: string,
): Promise<{ ok: boolean; error?: string }> {
  return requireApi().flattenNestedLive2DModelDir(modelDir);
}

export function ensureModel3Declarations(modelDir: string): Promise<unknown> {
  return requireApi().ensureModel3Declarations(modelDir);
}
