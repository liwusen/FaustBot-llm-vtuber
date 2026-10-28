import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { MODULES } from '@/config/constants';
import type { ModuleDef } from '@/config/types';
import { usePluginsStore } from '@/stores/plugins';

export const PERSISTENT_MODULES = ['overview', 'memory', 'araya'] as const;
export const PERSISTENT_COMPONENT_NAMES = [
  'module-overview',
  'module-memory',
  'module-araya',
] as const;

export const useModulesStore = defineStore('configer-modules', () => {
  const activeModule = ref('overview');
  const reloadEpoch = ref(0);
  const nonce = ref(0);

  const leaveGuards: Array<(nextModuleId: string) => boolean | void> = [];
  const visibilityListeners: Array<(moduleId: string) => void> = [];

  const allModules = computed<ModuleDef[]>(() => {
    const plugins = usePluginsStore();
    const list: ModuleDef[] = MODULES.map((m) => ({ ...m }));
    for (const page of plugins.pages) {
      if (!list.some((m) => m.id === page.id)) {
        list.push({
          id: page.id,
          title: page.label,
          desc: page.desc || '',
        });
      }
    }
    return list;
  });

  const currentModule = computed<ModuleDef>(() => (
    allModules.value.find((m) => m.id === activeModule.value) || allModules.value[0]
  ));

  function isPersistent(moduleId: string): boolean {
    return (PERSISTENT_MODULES as readonly string[]).includes(moduleId);
  }

  function registerLeaveGuard(fn: (nextModuleId: string) => boolean | void): () => void {
    if (typeof fn === 'function' && !leaveGuards.includes(fn)) leaveGuards.push(fn);
    return () => {
      const index = leaveGuards.indexOf(fn);
      if (index >= 0) leaveGuards.splice(index, 1);
    };
  }

  function onVisibilityChange(fn: (moduleId: string) => void): () => void {
    if (typeof fn === 'function' && !visibilityListeners.includes(fn)) visibilityListeners.push(fn);
    return () => {
      const index = visibilityListeners.indexOf(fn);
      if (index >= 0) visibilityListeners.splice(index, 1);
    };
  }

  /**
   * 切换模块：依次执行同步离开守卫，任一返回 false 即取消。
   * 成功后通知可见性监听并注销不可见插件页的持久化钩子。
   */
  function switchModule(moduleId: string): boolean {
    const target = allModules.value.find((m) => m.id === moduleId);
    if (!target) return false;
    for (const fn of leaveGuards) {
      try {
        if (fn(moduleId) === false) return false;
      } catch (err) {
        console.error('[module] 离开守卫失败', err);
      }
    }
    activeModule.value = moduleId;
    for (const fn of visibilityListeners) {
      try {
        fn(moduleId);
      } catch (err) {
        console.error('[module] 可见性回调失败', err);
      }
    }
    usePluginsStore().dropPersistenceExcept(moduleId);
    return true;
  }

  /** 侧边栏点击：非持久模块重复点击会强制重挂；持久模块只保持原实例。 */
  function navigate(moduleId: string): boolean {
    const same = moduleId === activeModule.value;
    if (!switchModule(moduleId)) return false;
    if (same && !isPersistent(moduleId)) nonce.value += 1;
    return true;
  }

  /** 重新渲染当前模块（持久模块通过替换 KeepAlive 真正重建，与旧 refreshModule 一致）。 */
  function refreshCurrent(): void {
    if (isPersistent(activeModule.value)) reloadEpoch.value += 1;
    else nonce.value += 1;
  }

  /** 保存/应用/重载后让全部模块以新 key 重建。 */
  function reloadAllModules(): void {
    reloadEpoch.value += 1;
    nonce.value += 1;
  }

  /** 插件重载后当前页可能已不存在，回落到 overview。 */
  function ensureActiveValid(): void {
    if (!allModules.value.some((m) => m.id === activeModule.value)) {
      activeModule.value = 'overview';
      usePluginsStore().dropPersistenceExcept('overview');
    }
  }

  return {
    activeModule,
    reloadEpoch,
    nonce,
    allModules,
    currentModule,
    leaveGuards,
    visibilityListeners,
    isPersistent,
    registerLeaveGuard,
    onVisibilityChange,
    switchModule,
    navigate,
    refreshCurrent,
    reloadAllModules,
    ensureActiveValid,
  };
});
