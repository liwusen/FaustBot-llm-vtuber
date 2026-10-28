import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { MODULES } from '@/config/constants';
import type { ModuleDef } from '@/config/types';
import type {
  PluginCardSpec,
  PluginFailure,
  PluginPageSpec,
  PluginPersistenceEntry,
  PluginPersistenceHandle,
  PluginPersistenceSpec,
} from '@/plugin/types';

/**
 * 插件页面/卡片/持久化钩子的唯一状态源。
 * window.pluginUI 由 pluginHost.ts 包装本 store，数组必须是同一引用，
 * 因此 reset 时只清空、不替换数组。
 */
export const usePluginsStore = defineStore('configer-plugins', () => {
  const pages = ref<PluginPageSpec[]>([]);
  const cards = ref<PluginCardSpec[]>([]);
  const persist = ref<PluginPersistenceEntry[]>([]);
  const persistDirty = ref<Set<string>>(new Set());

  const dirtyCount = computed(() => persistDirty.value.size);

  function addPage(spec: PluginPageSpec): void {
    if (!spec || !spec.id || !spec.label) return;
    const key = `${spec.plugin || ''}:${spec.id}`;
    if (pages.value.find((p) => p._key === key)) return;
    pages.value.push({ ...spec, _key: key });
  }

  function addCard(moduleId: string, spec: PluginCardSpec): void {
    if (!moduleId || !spec || !spec.title) return;
    const key = `${spec.plugin || ''}:${moduleId}:${spec.title}`;
    if (cards.value.find((c) => c._key === key)) return;
    cards.value.push({ ...spec, moduleId, _key: key });
  }

  /**
   * 旧实现里 addPage 会把插件页压进 MODULES 数组，因此 modifyPage 也能查到插件页。
   * Vue 版本的 MODULES 是静态常量、插件页只存在于 pages，这里先查内置模块再回退到插件页，
   * 保证 `modifyPage('插件页', fn)` 拿到的形状与旧实现一致（{ id, title, desc }）。
   */
  function findModule(moduleId: string): ModuleDef | undefined {
    const builtin = MODULES.find((m) => m.id === moduleId);
    if (builtin) return builtin;
    const page = pages.value.find((p) => p.id === moduleId);
    if (!page) return undefined;
    return { id: page.id, title: page.label, desc: page.desc || '' };
  }

  function modifyPage(moduleId: string, fn: (module: ModuleDef | undefined) => void): void {
    if (typeof fn !== 'function') return;
    fn(findModule(moduleId));
  }

  function registerPersistence(spec: PluginPersistenceSpec): PluginPersistenceHandle {
    const plugin = String((spec && spec.plugin) || '').trim();
    if (!plugin) throw new Error('registerPersistence 缺少 plugin');
    const moduleId = String((spec && spec.module) || plugin).trim() || plugin;
    const entry: PluginPersistenceEntry = {
      plugin,
      module: moduleId,
      save: typeof spec.save === 'function' ? spec.save : null,
      apply: typeof spec.apply === 'function' ? spec.apply : null,
      reload: typeof spec.reload === 'function' ? spec.reload : null,
      _key: `${plugin}::${moduleId}`,
    };
    // 同一插件同一页面重复注册视为覆盖：页面重渲染后旧处理器必须失效。
    const index = persist.value.findIndex((item) => item._key === entry._key);
    if (index >= 0) persist.value.splice(index, 1, entry);
    else persist.value.push(entry);
    // 重渲染/首次注册 = 面板按服务端状态重建，旧的未保存标记作废。
    persistDirty.value.delete(plugin);
    return {
      plugin,
      module: moduleId,
      setDirty(value: boolean) {
        setPluginDirty(plugin, !!value);
      },
      dispose() {
        unregisterPersistence(plugin, moduleId);
      },
    };
  }

  function unregisterPersistence(plugin: string, moduleId?: string): void {
    const key = `${plugin}::${moduleId || plugin}`;
    const before = persist.value.length;
    const kept = persist.value.filter((item) => item._key !== key);
    persist.value.splice(0, persist.value.length, ...kept);
    if (persist.value.length !== before && !persist.value.some((item) => item.plugin === plugin)) {
      persistDirty.value.delete(plugin);
    }
  }

  function setPluginDirty(plugin: string, dirty: boolean): void {
    const id = String(plugin || '').trim();
    if (!id) return;
    if (dirty) persistDirty.value.add(id);
    else persistDirty.value.delete(id);
  }

  function isPluginDirty(plugin: string): boolean {
    return persistDirty.value.has(String(plugin || '').trim());
  }

  /** 只清除保存成功的插件标记：失败的插件保持 dirty，用户可以重试。 */
  function clearDirty(exceptPlugins?: string[]): void {
    const failed = new Set(exceptPlugins || []);
    for (const id of Array.from(persistDirty.value)) {
      if (!failed.has(id)) persistDirty.value.delete(id);
    }
  }

  function resetPersistence(): void {
    persist.value.splice(0, persist.value.length);
    persistDirty.value.clear();
  }

  /** 切换页面后注销不可见页面的钩子（插件页 DOM 已被销毁）。 */
  function dropPersistenceExcept(activeModuleId: string): void {
    const keep = String(activeModuleId || '');
    const dropped = persist.value.filter((item) => item.module !== keep);
    if (!dropped.length) return;
    const kept = persist.value.filter((item) => item.module === keep);
    persist.value.splice(0, persist.value.length, ...kept);
    for (const item of dropped) {
      if (!persist.value.some((entry) => entry.plugin === item.plugin)) {
        persistDirty.value.delete(item.plugin);
      }
    }
  }

  function resetAll(): void {
    pages.value.splice(0, pages.value.length);
    cards.value.splice(0, cards.value.length);
    resetPersistence();
  }

  async function runPersistence(phase: 'save' | 'apply' | 'reload'): Promise<PluginFailure[]> {
    const entries = persist.value.filter((item) => typeof item[phase] === 'function');
    if (!entries.length) return [];
    const results = await Promise.all(
      entries.map(async (entry): Promise<PluginFailure | null> => {
        try {
          await entry[phase]!({
            phase,
            plugin: entry.plugin,
            module: entry.module,
            dirty: isPluginDirty(entry.plugin),
          });
          return null;
        } catch (err) {
          console.error(`[plugin] ${entry.plugin} 的 ${phase} 钩子失败`, err);
          return {
            plugin: entry.plugin,
            error: String((err as { message?: unknown })?.message || err),
          };
        }
      }),
    );
    return results.filter((item): item is PluginFailure => item !== null);
  }

  function pluginFailuresText(failed: PluginFailure[]): string {
    return failed.map((item) => `${item.plugin}（${item.error}）`).join('、');
  }

  return {
    pages,
    cards,
    persist,
    persistDirty,
    dirtyCount,
    addPage,
    addCard,
    modifyPage,
    registerPersistence,
    unregisterPersistence,
    setPluginDirty,
    isPluginDirty,
    clearDirty,
    resetPersistence,
    dropPersistenceExcept,
    resetAll,
    runPersistence,
    pluginFailuresText,
  };
});
