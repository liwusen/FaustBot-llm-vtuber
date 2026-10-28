import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { cfgApi } from '@/api/ipc';
import type { ModelEntry, ProviderBundle, ProviderConfig } from '@/config/types';
import type { AiCatalog, AiCatalogEntry, AiResolvedFormats } from '@/modules/ai/providerOps';

/**
 * 服务商、主/子模型与格式解析结果；AI 页与插件 LLM picker 共用。
 *
 * 所有本地改动只写这里，随窗口顶部「保存」统一提交（见 useConfigStore.saveConfig）。
 * `loadProviders` 只在进入 AI 页时调用；页内重渲染不得再拉后端，否则会冲掉草稿。
 */
export const useProvidersStore = defineStore('configer-providers', () => {
  const providers = ref<ProviderConfig[]>([]);
  const mainModel = ref('');
  const subagentModels = ref<string[]>([]);
  const resolvedFormats = ref<AiResolvedFormats>({});
  const providerCatalog = ref<AiCatalog>({});

  const providersDirty = ref(false);
  const mainModelDirty = ref(false);
  const subagentModelsDirty = ref(false);
  /** 是否已经从后端读过服务商；AI 页非持久，重进时靠它避免冲掉未保存草稿。 */
  const loaded = ref(false);

  const allModelEntries = computed<ModelEntry[]>(() => {
    const entries: ModelEntry[] = [];
    for (const provider of providers.value) {
      for (const model of provider.models || []) {
        entries.push({
          provider: provider.name,
          model,
          spec: `${provider.name}::${model}`,
        });
      }
    }
    return entries;
  });

  function findProvider(name: string): ProviderConfig | undefined {
    return providers.value.find((p) => p.name === name);
  }

  function setBundle(data: Partial<ProviderBundle>): void {
    providers.value = data.providers || [];
    mainModel.value = data.main_model || '';
    subagentModels.value = data.subagent_models || [];
    resolvedFormats.value = (data.resolved_formats || {}) as unknown as AiResolvedFormats;
    providerCatalog.value = (data.catalog || {}) as unknown as AiCatalog;
  }

  /** 进入 AI 页：整包刷新服务商/模型/格式，不把这次读取算成 dirty。 */
  async function loadProviders(): Promise<void> {
    const data = await cfgApi<ProviderBundle>('GET', API.providers);
    setBundle(data);
    loaded.value = true;
  }

  /**
   * 首次进入 AI 页才拉后端；之后切走再回来直接复用 store 里的草稿。
   * 显式刷新（批量操作 / 目录识别 / 保存后的重新加载）仍用 loadProviders。
   */
  async function ensureProviders(): Promise<void> {
    if (loaded.value) return;
    await loadProviders();
  }

  /** 替换服务商列表但不标 dirty（用于「立即写后端」后的本地同步）。 */
  function replaceProviders(next: ProviderConfig[]): void {
    providers.value = next;
  }

  /** 本地修改服务商列表（标 providers dirty）。 */
  function setProviders(next: ProviderConfig[]): void {
    providers.value = next;
    markProvidersDirty();
  }

  /** 替换单个服务商（标 providers dirty）。 */
  function setProvider(name: string, record: ProviderConfig): void {
    providers.value = providers.value.map((p) => (p.name === name ? record : p));
    markProvidersDirty();
  }

  /** 删除服务商并同步清掉它的主/子模型标识（旧 _aiDeleteProvider 的语义）。 */
  function removeProvider(name: string): void {
    providers.value = providers.value.filter((p) => p.name !== name);
    const prefix = `${name}::`;
    if (mainModel.value && mainModel.value.startsWith(prefix)) mainModel.value = '';
    subagentModels.value = subagentModels.value.filter((s) => !s.startsWith(prefix));
    markProvidersDirty();
    markModelsDirty();
  }

  function setMainModel(spec: string): void {
    mainModel.value = spec;
    markModelsDirty();
  }

  function setSubagentModels(list: string[]): void {
    subagentModels.value = [...list];
    markModelsDirty();
  }

  function toggleSubagentModel(spec: string, enabled: boolean): void {
    const next = new Set(subagentModels.value);
    if (enabled) next.add(spec);
    else next.delete(spec);
    subagentModels.value = [...next];
    markModelsDirty();
  }

  /** 批量操作后整表重拉（旧 _reloadProvidersFromBackend：算 dirty）。 */
  async function refreshProviders(): Promise<void> {
    await loadProviders();
    markProvidersDirty();
  }

  /**
   * 只重新拉取并替换「当前服务商」的派生字段，保留其它服务商的未保存改动
   * （旧 _reloadProviderFromBackend）。
   */
  async function refreshProvider(name: string): Promise<ProviderConfig | null> {
    const payload = await cfgApi<ProviderBundle>('GET', API.providers);
    const fresh = (payload.providers || []).find((p) => p.name === name);
    if (!fresh) return null;
    providers.value = providers.value.map((p) => (p.name === name ? fresh : p));
    const formats = (payload.resolved_formats || {}) as unknown as AiResolvedFormats;
    if (formats[name]) {
      resolvedFormats.value = { ...resolvedFormats.value, [name]: formats[name] };
    }
    const catalog = (payload.catalog || {}) as unknown as AiCatalog;
    if (catalog[name]) {
      providerCatalog.value = { ...providerCatalog.value, [name]: catalog[name] };
    }
    loaded.value = true;
    markProvidersDirty();
    return fresh;
  }

  /** 更新模型目录后整表刷新（旧弹窗「更新模型目录」：保留主/子模型标识）。 */
  async function refreshCatalogEntries(): Promise<ProviderBundle> {
    const payload = await cfgApi<ProviderBundle>('GET', API.providers);
    providers.value = payload.providers || [];
    resolvedFormats.value = (payload.resolved_formats || {}) as unknown as AiResolvedFormats;
    providerCatalog.value = (payload.catalog || {}) as unknown as AiCatalog;
    loaded.value = true;
    markProvidersDirty();
    return payload;
  }

  function catalogFor(name: string): AiCatalogEntry | null {
    return providerCatalog.value[name] || null;
  }

  function markProvidersDirty(): void {
    providersDirty.value = true;
  }

  function markModelsDirty(): void {
    mainModelDirty.value = true;
    subagentModelsDirty.value = true;
  }

  function resetDirty(): void {
    providersDirty.value = false;
    mainModelDirty.value = false;
    subagentModelsDirty.value = false;
  }

  function anyDirty(): boolean {
    return providersDirty.value || mainModelDirty.value || subagentModelsDirty.value;
  }

  function dirtyCount(): number {
    return (providersDirty.value ? 1 : 0)
      + (mainModelDirty.value ? 1 : 0)
      + (subagentModelsDirty.value ? 1 : 0);
  }

  return {
    providers,
    mainModel,
    subagentModels,
    resolvedFormats,
    providerCatalog,
    providersDirty,
    mainModelDirty,
    subagentModelsDirty,
    loaded,
    allModelEntries,
    findProvider,
    setBundle,
    loadProviders,
    ensureProviders,
    replaceProviders,
    setProviders,
    setProvider,
    removeProvider,
    setMainModel,
    setSubagentModels,
    toggleSubagentModel,
    refreshProviders,
    refreshProvider,
    refreshCatalogEntries,
    catalogFor,
    markProvidersDirty,
    markModelsDirty,
    resetDirty,
    anyDirty,
    dirtyCount,
  };
});
