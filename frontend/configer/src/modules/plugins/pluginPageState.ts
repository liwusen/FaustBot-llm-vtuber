import { ref } from 'vue';
import type { PluginUpdateData } from './pluginPageOps';

/**
 * 页级状态：旧 config-window.js 把 selectedPluginId / pluginUpdates 放在模块级
 * `state` 里，切换模块再回来仍保留。这里用模块作用域 ref 复现同一语义；
 * 插件列表与配置草稿则由每次进入/刷新重新拉取，和旧 ensureModuleData 一致。
 */
export const selectedPluginId = ref('');

export const pluginUpdates = ref<PluginUpdateData | null>(null);

/** 测试或需要彻底重置页级状态时使用。 */
export function resetPluginPageState(): void {
  selectedPluginId.value = '';
  pluginUpdates.value = null;
}
