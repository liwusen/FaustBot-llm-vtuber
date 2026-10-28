export interface FileDialogFilter {
  name: string;
  extensions: string[];
}

export interface FileDialogOptions {
  title?: string;
  filters?: FileDialogFilter[];
}

export interface ModelProfileResult {
  ok: boolean;
  error?: string;
  profile?: Record<string, unknown>;
  cached?: boolean;
  coverage?: { mappedKeyCount: number; facsKeyCount: number };
}

export interface FrontendApi {
  backendBaseUrl: string;
  setIgnoreMouseEvents(v: boolean): Promise<unknown>;
  focusMainWindow(): Promise<unknown>;
  showNotification(options: unknown): Promise<unknown>;
  setPttMode(enabled: boolean): Promise<unknown>;
  hideToTray(): Promise<unknown>;
  showFromTray(): Promise<unknown>;
  openConfigWindow(): Promise<unknown>;
  openLiveWindow(): Promise<{ ok: boolean; mode?: string }>;
  resolveFrontendAssetPath(relativePath: string): Promise<string>;
  getFaustbotRoot(): Promise<string>;
  restartFaust(): Promise<unknown>;
  configRequest<T = unknown>(
    method: string,
    path: string,
    payload?: unknown,
    query?: Record<string, unknown> | null,
    options?: { timeoutMs?: number },
  ): Promise<T>;
  configOpenFile(options?: FileDialogOptions): Promise<string | null>;
  configOpenDirectory(options?: { title?: string }): Promise<string | null>;
  configOpenPath(targetPath: string): Promise<{ ok: boolean; error: string | null }>;
  configHttpRequest<T = unknown>(method: string, url: string, payload?: unknown): Promise<T>;
  toggleLogPanel(): Promise<boolean>;
  toggleWidgetEditMode(): Promise<unknown>;
  recreateFrontendWindow(): Promise<{ ok: boolean }>;
  ensureModelProfile(modelDir: string, force?: boolean): Promise<ModelProfileResult>;
  saveModelProfile(modelDir: string, json: unknown): Promise<{ ok: boolean; error?: string }>;
  isNestedLive2DModelDir(modelDir: string): Promise<unknown>;
  flattenNestedLive2DModelDir(modelDir: string): Promise<{ ok: boolean; error?: string }>;
  ensureModel3Declarations(modelDir: string): Promise<unknown>;
  readClipboardImage(): Promise<string | null>;
  readClipboardFilePaths(): Promise<string[]>;
  pickAttachments(): Promise<string[]>;
}
