import type { ModuleDef } from '@/config/types';

export interface PluginPageSpec {
  id: string;
  label: string;
  desc?: string;
  plugin?: string;
  render: (container: HTMLElement) => void | Promise<void>;
  _key?: string;
  [key: string]: unknown;
}

export interface PluginCardSpec {
  title: string;
  content?: string;
  html?: string;
  render?: (container: HTMLElement) => void;
  plugin?: string;
  moduleId?: string;
  _key?: string;
  [key: string]: unknown;
}

export interface PluginPersistenceContext {
  phase: 'save' | 'apply' | 'reload';
  plugin: string;
  module: string;
  dirty: boolean;
}

export interface PluginPersistenceSpec {
  plugin: string;
  module?: string;
  save?: (ctx: PluginPersistenceContext) => void | Promise<void>;
  apply?: (ctx: PluginPersistenceContext) => void | Promise<void>;
  reload?: (ctx: PluginPersistenceContext) => void | Promise<void>;
}

export interface PluginPersistenceEntry {
  plugin: string;
  module: string;
  save: ((ctx: PluginPersistenceContext) => void | Promise<void>) | null;
  apply: ((ctx: PluginPersistenceContext) => void | Promise<void>) | null;
  reload: ((ctx: PluginPersistenceContext) => void | Promise<void>) | null;
  _key: string;
}

export interface PluginPersistenceHandle {
  plugin: string;
  module: string;
  setDirty(value: boolean): void;
  dispose(): void;
}

export interface PluginFailure {
  plugin: string;
  error: string;
}

/** window.pluginUI 的公共契约；插件脚本只应使用这里的成员。 */
export interface PluginUI {
  _pages: PluginPageSpec[];
  _cards: PluginCardSpec[];
  _persist: PluginPersistenceEntry[];
  _persistDirty: Set<string>;
  backendBaseUrl: string;
  addPage(spec: PluginPageSpec): void;
  addCard(moduleId: string, spec: PluginCardSpec): void;
  modifyPage(moduleId: string, fn: (module: ModuleDef | undefined) => void): void;
  registerPersistence(spec: PluginPersistenceSpec): PluginPersistenceHandle;
  unregisterPersistence(plugin: string, moduleId?: string): void;
  setPluginDirty(plugin: string, dirty: boolean): void;
  dirtyCount(): number;
  isPluginDirty(plugin: string): boolean;
  clearDirty(exceptPlugins?: string[]): void;
  resetPersistence(): void;
  dropPersistenceExcept(activeModuleId: string): void;
  communicate(pluginId: string, payload?: unknown): Promise<unknown>;
  communicateSSE(pluginId: string, params?: Record<string, string>): EventSource;
}
