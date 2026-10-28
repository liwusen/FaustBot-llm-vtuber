import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { apiLocal, cfgApi } from '@/api/ipc';
import { formatApiError } from '@/api/errors';
import { LIVE2D_KEYS, SECRET_KEYS } from '@/config/constants';
import type { ConfigBundle, ConfigDict, ConfigScope } from '@/config/types';
import { useModulesStore } from '@/stores/modules';
import { usePluginsStore } from '@/stores/plugins';
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';

export const useConfigStore = defineStore('configer-config', () => {
  const publicConfig = ref<ConfigDict>({});
  const privateConfig = ref<ConfigDict>({});
  const originalPublic = ref<ConfigDict>({});
  const originalPrivate = ref<ConfigDict>({});

  const dirtyPublic = ref<Map<string, unknown>>(new Map());
  const dirtyPrivate = ref<Map<string, unknown>>(new Map());

  const runtime = ref<Record<string, unknown>>({});

  const config = computed<ConfigBundle>(() => ({
    public: publicConfig.value,
    private: privateConfig.value,
  }));

  const globalDirtyCount = computed(() => {
    const providers = useProvidersStore();
    return dirtyPublic.value.size
      + dirtyPrivate.value.size
      + (providers.providersDirty ? 1 : 0)
      + (providers.mainModelDirty ? 1 : 0)
      + (providers.subagentModelsDirty ? 1 : 0);
  });

  const dirtyCount = computed(() => globalDirtyCount.value + usePluginsStore().dirtyCount);

  function resetDirty(): void {
    dirtyPublic.value.clear();
    dirtyPrivate.value.clear();
    useProvidersStore().resetDirty();
  }

  function loadConfig(): Promise<void> {
    return cfgApi<ConfigBundle>('GET', API.config).then((data) => {
      publicConfig.value = { ...(data.public || {}) };
      privateConfig.value = { ...(data.private || {}) };
      originalPublic.value = { ...(data.public || {}) };
      originalPrivate.value = { ...(data.private || {}) };
      resetDirty();
    });
  }

  function loadRuntimeSummary(): Promise<void> {
    return cfgApi<{ runtime?: Record<string, unknown> }>('GET', API.runtime).then((data) => {
      runtime.value = data.runtime || {};
    });
  }

  function originalFor(scope: ConfigScope, key: string): unknown {
    return (scope === 'private' ? originalPrivate.value : originalPublic.value)[key];
  }

  /** 与旧 field-render.js 完全一致的脏标记语义。 */
  function updateValue(scope: ConfigScope, key: string, nextValue: unknown): void {
    const values = scope === 'private' ? privateConfig.value : publicConfig.value;
    const dirtyMap = scope === 'private' ? dirtyPrivate.value : dirtyPublic.value;
    const original = originalFor(scope, key);
    values[key] = nextValue;
    if (SECRET_KEYS.has(key)) {
      if (typeof nextValue === 'string' && nextValue.length > 0) dirtyMap.set(key, nextValue);
      else dirtyMap.delete(key);
    } else if (nextValue === original) {
      dirtyMap.delete(key);
    } else {
      dirtyMap.set(key, nextValue);
    }
  }

  function revertValue(scope: ConfigScope, key: string): void {
    const values = scope === 'private' ? privateConfig.value : publicConfig.value;
    const dirtyMap = scope === 'private' ? dirtyPrivate.value : dirtyPublic.value;
    if (!dirtyMap.has(key)) return;
    values[key] = originalFor(scope, key);
    dirtyMap.delete(key);
  }

  function isDirty(scope: ConfigScope, key: string): boolean {
    return (scope === 'private' ? dirtyPrivate.value : dirtyPublic.value).has(key);
  }

  function dirtyValue(scope: ConfigScope, key: string): unknown {
    return (scope === 'private' ? dirtyPrivate.value : dirtyPublic.value).get(key);
  }

  async function saveConfig(): Promise<void> {
    const ui = useUiStore();
    const modules = useModulesStore();
    const plugins = usePluginsStore();
    const providers = useProvidersStore();

    const globalDirty = globalDirtyCount.value;
    const pluginDirty = plugins.dirtyCount;
    if (globalDirty + pluginDirty <= 0) {
      ui.showBanner('info', '当前没有未保存更改。');
      return;
    }

    ui.setBusy(true);
    try {
      // 插件草稿先各自由插件落盘（互不影响；失败的插件保持 dirty 以便重试）。
      const failures = await plugins.runPersistence('save');
      const providersWereDirty = providers.anyDirty();
      if (globalDirty > 0) {
        const mdBlockChanged = dirtyPublic.value.has('MD_BLOCK_ENABLED');
        const payload: Record<string, unknown> = {
          public: Object.fromEntries(dirtyPublic.value.entries()),
          private: Object.fromEntries(dirtyPrivate.value.entries()),
        };
        // [统一保存] provider 配置随公共配置一起提交。
        if (providersWereDirty) {
          payload.providers = providers.providers;
          payload.main_model = providers.mainModel;
          payload.subagent_models = providers.subagentModels;
        }
        await cfgApi('POST', API.config, payload);
        const hasLive2D = LIVE2D_KEYS.some((k) => dirtyPublic.value.has(k));
        if (hasLive2D) {
          await cfgApi('POST', API.live2dApply, {
            public: Object.fromEntries(
              [...dirtyPublic.value.entries()].filter(([k]) => LIVE2D_KEYS.includes(k)),
            ),
          });
        }
        if (mdBlockChanged) {
          await cfgApi('POST', API.configReload, {
            reset_dialog: false,
            no_initial_chat: true,
          });
        }
        await loadConfig();
        // 旧实现保存后会 renderModule(true)，AI 页重入会重新 GET providers。
        // 重新读取后端归一化后的 base_url / resolved_formats / catalog，避免格式徽章停在旧值。
        if (providersWereDirty) await providers.loadProviders();
      }
      plugins.clearDirty(failures.map((item) => item.plugin));
      // 只有插件保存失败、又没有全局改动时保留现场：不重渲染，草稿留着让用户改完重试。
      if (globalDirty > 0) {
        await loadRuntimeSummary();
        modules.reloadAllModules();
      } else if (!failures.length) {
        modules.reloadAllModules();
      }
      if (failures.length) {
        ui.showBanner(
          'error',
          `配置已保存，但以下插件保存失败：${plugins.pluginFailuresText(failures)}`,
        );
      } else {
        ui.showBanner('success', '配置已保存。');
      }
    } catch (err) {
      ui.showBanner('error', `保存失败: ${formatApiError(err)}`);
    } finally {
      ui.setBusy(false);
    }
  }

  async function applyRuntime(): Promise<void> {
    const ui = useUiStore();
    const plugins = usePluginsStore();
    ui.setBusy(true);
    try {
      const failures = await plugins.runPersistence('apply');
      await cfgApi('POST', API.configReload, {
        reset_dialog: false,
        no_initial_chat: true,
      });
      await loadRuntimeSummary();
      if (failures.length) {
        ui.showBanner(
          'error',
          `运行时已刷新，但以下插件应用失败：${plugins.pluginFailuresText(failures)}`,
        );
      } else {
        ui.showBanner('success', '运行时已刷新，配置已应用。');
      }
    } catch (err) {
      ui.showBanner('error', `应用失败: ${formatApiError(err)}`);
    } finally {
      ui.setBusy(false);
    }
  }

  async function reloadAll(): Promise<boolean> {
    const ui = useUiStore();
    const modules = useModulesStore();
    const providers = useProvidersStore();
    ui.setBusy(true);
    try {
      await loadConfig();
      await loadRuntimeSummary();
      // 「重新加载」应丢弃草稿并从磁盘重读；未进入过 AI 页时不额外请求。
      if (providers.loaded) await providers.loadProviders();
      ui.hideBanner();
      modules.reloadAllModules();
      return true;
    } catch (err) {
      ui.showBanner('error', `刷新失败: ${formatApiError(err)}`);
      return false;
    } finally {
      ui.setBusy(false);
    }
  }

  async function reloadFromDisk(): Promise<void> {
    const ui = useUiStore();
    const plugins = usePluginsStore();
    const dirty = globalDirtyCount.value + plugins.dirtyCount;
    if (dirty > 0) {
      const ok = window.confirm('当前有未保存修改，继续重新加载会丢失这些修改。是否继续？');
      if (!ok) return;
    }
    const failures = await plugins.runPersistence('reload');
    const reloaded = await reloadAll();
    if (!reloaded) return;
    if (failures.length) {
      ui.showBanner(
        'error',
        `已重新读取配置，但以下插件重新加载失败：${plugins.pluginFailuresText(failures)}`,
      );
    } else {
      ui.showBanner('info', '已重新读取配置。');
    }
  }

  async function applyTtsReferToService(): Promise<void> {
    const ui = useUiStore();
    try {
      const mode = String(publicConfig.value.TTS_MODE || 'gpt-sovits').toLowerCase();
      if (mode !== 'gpt-sovits') {
        ui.showBanner('info', '当前 TTS 模式不是 gpt-sovits，无法应用参考音频到本地 TTS 服务。');
        return;
      }
      await apiLocal('POST', 'http://127.0.0.1:5000/change_refer', {
        refer_wav_path: publicConfig.value.TTS_REFER_WAV_PATH,
        prompt_text: publicConfig.value.TTS_PROMPT_TEXT,
        prompt_language: publicConfig.value.TTS_PROMPT_LANGUAGE,
      });
      ui.showBanner('success', 'TTS 参考音频已同步到本地服务。');
    } catch (err) {
      ui.showBanner('error', `同步 TTS 服务失败: ${formatApiError(err)}`);
    }
  }

  return {
    publicConfig,
    privateConfig,
    originalPublic,
    originalPrivate,
    dirtyPublic,
    dirtyPrivate,
    runtime,
    config,
    globalDirtyCount,
    dirtyCount,
    resetDirty,
    loadConfig,
    loadRuntimeSummary,
    updateValue,
    revertValue,
    isDirty,
    dirtyValue,
    saveConfig,
    applyRuntime,
    reloadAll,
    reloadFromDisk,
    applyTtsReferToService,
  };
});
