import { API } from '@/api/endpoints';
import { cfgApi, getBackendBaseUrl } from '@/api/ipc';
import { useModulesStore } from '@/stores/modules';
import { usePluginsStore } from '@/stores/plugins';

interface PluginAsset {
  type: 'js' | 'css' | string;
  path?: string;
  plugin_id?: string;
}

interface PluginAssetsResponse {
  assets?: PluginAsset[];
}

/**
 * 动态加载插件前端资源。脚本失败只 warn，不阻断配置中心；
 * forceReload 时先清空插件页面/卡片/persistence 与已注入节点。
 */
export async function loadPluginAssets(forceReload = false): Promise<void> {
  try {
    const store = usePluginsStore();
    const modules = useModulesStore();
    if (forceReload) {
      document.querySelectorAll('script[data-plugin], link[data-plugin]').forEach((node) => {
        node.remove();
      });
      store.resetAll();
      modules.ensureActiveValid();
    }
    if (!window.api || typeof window.api.configRequest !== 'function') {
      console.warn('[loadPluginAssets] window.api.configRequest not available');
      return;
    }
    const data = await cfgApi<PluginAssetsResponse>('GET', API.pluginAssets);
    if (!data) return;
    const assets = data.assets || [];
    const baseUrl = getBackendBaseUrl();
    if (window.pluginUI) window.pluginUI.backendBaseUrl = baseUrl;

    const loadPromises: Promise<void>[] = [];
    const cacheBust = `v=${Date.now()}`;
    for (const asset of assets) {
      if (asset.type === 'js' && asset.path) {
        const script = document.createElement('script');
        script.src = baseUrl + asset.path + (asset.path.includes('?') ? '&' : '?') + cacheBust;
        script.setAttribute('data-plugin', asset.plugin_id || '');
        const loaded = new Promise<void>((resolve) => {
          script.onload = () => {
            console.log('[plugin] loaded JS:', asset.path);
            resolve();
          };
          script.onerror = () => {
            console.warn('[plugin] failed to load JS:', asset.path);
            resolve();
          };
        });
        loadPromises.push(loaded);
        document.head.appendChild(script);
      } else if (asset.type === 'css' && asset.path) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = baseUrl + asset.path + (asset.path.includes('?') ? '&' : '?') + cacheBust;
        link.setAttribute('data-plugin', asset.plugin_id || '');
        document.head.appendChild(link);
      }
    }
    if (loadPromises.length > 0) {
      await Promise.all(loadPromises);
      console.log('[plugin] all JS assets loaded');
    }
  } catch (err) {
    console.warn('[loadPluginAssets] Error:', err);
  }
}
