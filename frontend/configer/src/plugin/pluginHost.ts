import { cfgApi, getBackendBaseUrl } from '@/api/ipc';
import { API } from '@/api/endpoints';
import type { ModuleDef } from '@/config/types';
import { usePluginsStore } from '@/stores/plugins';
import type {
  PluginCardSpec,
  PluginPageSpec,
  PluginPersistenceHandle,
  PluginPersistenceSpec,
  PluginUI,
} from '@/plugin/types';

/**
 * 安装 window.pluginUI。必须在任何插件脚本加载前调用。
 * 数组字段直接引用 Pinia store 的响应式数组，插件后续 push 会即时驱动导航。
 */
export function installPluginHost(): PluginUI {
  if (window.pluginUI) return window.pluginUI;

  const store = usePluginsStore();

  const host: PluginUI = {
    _pages: store.pages,
    _cards: store.cards,
    _persist: store.persist,
    _persistDirty: store.persistDirty,
    backendBaseUrl: getBackendBaseUrl(),

    addPage(spec: PluginPageSpec): void {
      store.addPage(spec);
    },

    addCard(moduleId: string, spec: PluginCardSpec): void {
      store.addCard(moduleId, spec);
    },

    modifyPage(moduleId: string, fn: (module: ModuleDef | undefined) => void): void {
      store.modifyPage(moduleId, fn);
    },

    registerPersistence(spec: PluginPersistenceSpec): PluginPersistenceHandle {
      return store.registerPersistence(spec);
    },

    unregisterPersistence(plugin: string, moduleId?: string): void {
      store.unregisterPersistence(plugin, moduleId);
    },

    setPluginDirty(plugin: string, dirty: boolean): void {
      store.setPluginDirty(plugin, dirty);
    },

    dirtyCount(): number {
      return store.dirtyCount;
    },

    isPluginDirty(plugin: string): boolean {
      return store.isPluginDirty(plugin);
    },

    clearDirty(exceptPlugins?: string[]): void {
      store.clearDirty(exceptPlugins);
    },

    resetPersistence(): void {
      store.resetPersistence();
    },

    dropPersistenceExcept(activeModuleId: string): void {
      store.dropPersistenceExcept(activeModuleId);
    },

    communicate(pluginId: string, payload?: unknown): Promise<unknown> {
      if (!window.api || typeof window.api.configRequest !== 'function') {
        return Promise.reject(new Error('window.api.configRequest 未实现'));
      }
      return cfgApi(
        'POST',
        API.pluginCommunicate(String(pluginId || '')),
        payload ?? {},
      );
    },

    communicateSSE(pluginId: string, params?: Record<string, string>): EventSource {
      const base = getBackendBaseUrl();
      const query = new URLSearchParams(params || {}).toString();
      const url = `${base}${API.pluginCommunicateSSE(String(pluginId || ''))}${query ? `?${query}` : ''}`;
      return new EventSource(url);
    },
  };

  window.pluginUI = host;
  return host;
}
