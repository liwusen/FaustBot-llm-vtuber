// AI 服务商页的纯数据操作与展示格式化。
// 逐条对应旧实现 frontend/libs/configer/providerWizard.js：
//   · 上下文长度 / 接口格式 / 密钥状态 / 目录识别摘要的文案
//   · 服务商表单写入、模型重命名迁移、模型级字段写入
//   · 主/子模型标识在重命名与删除时的同步
// 这些函数不做后端调用，也不 import store，方便单测。

import { API_FORMAT_LABELS, FORMAT_SOURCE_LABELS } from '@/config/constants';
import type { FormatDecision, ModelEntry, ProviderConfig } from '@/config/types';

/** 后端 describe_provider_formats / describe_all_formats 的真实返回项。 */
export interface AiResolvedFormat {
  api_format?: string | null;
  source?: string;
  reason?: string | null;
  base_url?: string | null;
  thinking?: AiThinking | null;
}

export interface AiThinking {
  mode?: string;
  source?: string;
}

/** `resolved_formats`：{ provider: { model: decision } }。 */
export type AiResolvedFormats = Record<string, Record<string, AiResolvedFormat>>;

/** 后端 /faust/admin/providers 的 catalog 概览项。 */
export interface AiCatalogEntry {
  provider_id?: string | null;
  host_matched?: boolean;
  synced_at?: string | null;
  routed?: number;
  missing?: string[];
  formats?: unknown;
}

export type AiCatalog = Record<string, AiCatalogEntry>;

/** ProviderConfig 的已知扩展字段（旧 providerWizard.js 会读写）。 */
export interface AiProvider extends ProviderConfig {
  opencode_go?: boolean;
  stream_usage?: boolean | null;
  format_base_urls?: Record<string, string>;
  model_catalog_routes?: Record<string, { format?: string; host_matched?: boolean } | undefined>;
  catalog_synced_at?: string | null;
  model_api_capabilities?: Record<string, unknown>;
  model_thinking_caps?: Record<string, unknown>;
  model_thinking_modes?: Record<string, string>;
}

/** 服务商弹窗读取到的表单值（新增与编辑共用）。 */
export interface ProviderForm {
  base_url: string;
  key: string | null;
  thinking_type: string;
  opencode_go: boolean;
  api_format: string;
  stream_usage: boolean | null;
  format_base_urls: Record<string, string>;
  catalog_provider_id: string | null;
}

/** 模型弹窗保存时要落地的模型级字段。 */
export interface ModelSettingsForm {
  modelName: string;
  contextLength: number | null;
  maxOutputTokens: number | null;
  /** "auto" 表示清掉手动覆盖。 */
  apiFormat: string;
  /** "auto" 表示清掉手动思考格式覆盖。 */
  thinkingMode: string;
  serverTools: string[];
}

/** 主/子模型标识（`provider::model`）。 */
export interface ModelSelection {
  mainModel: string;
  subagentModels: string[];
}

/** POST /providers/{name}/load-models 的返回（只取前端会用到的字段）。 */
export interface LoadModelsResult {
  models?: string[];
  model_context_lengths?: Record<string, number>;
  model_max_output_tokens?: Record<string, number>;
  model_api_capabilities?: Record<string, unknown>;
  model_thinking_caps?: Record<string, unknown>;
  catalog?: AiCatalogEntry | null;
}

/** 当前 AI 页模型表每页条数（旧 AI_TABLE_PAGE_SIZE）。 */
export const AI_TABLE_PAGE_SIZE = 10;

/** 所有按模型名索引、重命名时要一起迁移的字段（顺序与旧实现一致）。 */
export const MODEL_INDEX_FIELDS = [
  'model_context_lengths',
  'model_max_output_tokens',
  'model_api_formats',
  'model_server_tools',
  'model_api_capabilities',
  'model_thinking_caps',
  'model_thinking_modes',
] as const;

export const THINKING_MODE_LABELS: Record<string, string> = {
  effort: '思维等级',
  budget_tokens: '思维预算',
  toggle: '思考开关',
  none: '不发送思考',
};

export const THINKING_SOURCE_LABELS: Record<string, string> = {
  manual: '手动',
  models_api: '服务商',
  models_dev: '模型目录',
  heuristic: '模型名推断',
};

export function asAiProvider(provider: ProviderConfig | null | undefined): AiProvider | null {
  return (provider as AiProvider | null | undefined) ?? null;
}

/** 深拷贝服务商列表：既避免 Pinia 代理，也保证 IPC 可结构化克隆。 */
export function cloneProviders(providers: ProviderConfig[] | null | undefined): ProviderConfig[] {
  return JSON.parse(JSON.stringify(providers || [])) as ProviderConfig[];
}

/** 返回 [{provider, model, spec}]，按服务商顺序展平。 */
export function allModelEntries(providers: ProviderConfig[] | null | undefined): ModelEntry[] {
  const out: ModelEntry[] = [];
  for (const provider of providers || []) {
    for (const model of provider.models || []) {
      out.push({ provider: provider.name, model, spec: `${provider.name}::${model}` });
    }
  }
  return out;
}

/** `provider::model` → 两段；没有 `::` 时 provider 为空（旧实现的分支）。 */
export function splitModelSpec(spec: string): { provider: string; model: string } {
  const text = String(spec || '');
  const idx = text.indexOf('::');
  if (idx < 0) return { provider: '', model: text };
  return { provider: text.slice(0, idx), model: text.slice(idx + 2) };
}

// 上下文长度：1048576 → "1M"，131072 → "128K"，400000 → "400K"；未配置时说明后端默认值
export function formatContext(tokens: unknown): string {
  const n = Number(tokens);
  if (!Number.isFinite(n) || n <= 0) return '默认 128K';
  if (n % 1048576 === 0) return `${n / 1048576}M`;
  if (n % 1000000 === 0) return `${n / 1000000}M`;
  if (n >= 1048576) return `${Number((n / 1048576).toFixed(1))}M`;
  if (n % 1024 === 0) return `${n / 1024}K`;
  if (n >= 1000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

/** 密钥状态：只说有没有配，不显示值（旧 `_keyStatus`：已设置 / 未设置）。 */
export function keyStatus(provider: ProviderConfig | null | undefined): string {
  return provider && provider.key ? '已设置' : '未设置';
}

/**
 * 模型用哪种接口格式：本地手动覆盖 → 后端解析结果 → 服务商默认 → 目录 → 自动。
 * format 为 "auto" 表示交给后端按端点 / 模型类型推断。
 */
export function formatDecision(
  provider: ProviderConfig | null | undefined,
  model: string,
  resolvedFormats?: AiResolvedFormats | null,
): FormatDecision {
  if (!provider) return { format: 'auto', source: '' };
  const manual = (provider.model_api_formats || {})[model];
  if (manual) return { format: manual, source: 'manual' };
  const resolved = ((resolvedFormats || {})[provider.name] || {})[model];
  if (resolved) {
    if (resolved.source === 'error') return { format: 'auto', source: 'error' };
    if (resolved.api_format) return { format: resolved.api_format, source: resolved.source || '' };
  }
  if (provider.api_format && provider.api_format !== 'auto') {
    return { format: provider.api_format, source: 'provider_default' };
  }
  const route = (provider as AiProvider).model_catalog_routes?.[model];
  if (route && route.format) return { format: route.format, source: 'catalog' };
  return { format: 'auto', source: '' };
}

/** 格式徽标文案：`OpenAI Chat · 手动指定`；auto 显示「自动」。 */
export function formatText(format: string, source?: string): string {
  const labels = API_FORMAT_LABELS as unknown as Record<string, string>;
  const sourceLabels = FORMAT_SOURCE_LABELS as unknown as Record<string, string>;
  const label = labels[format] || (format === 'auto' ? '自动' : format);
  const sourceLabel = sourceLabels[source || ''] || '';
  return sourceLabel ? `${label} · ${sourceLabel}` : label;
}

/** 思考形状文案：`思维等级 · 模型目录`；没有结果时「未提供」。 */
export function thinkingText(thinking: AiThinking | null | undefined): string {
  if (!thinking || !thinking.mode) return '未提供';
  const mode = THINKING_MODE_LABELS[thinking.mode] || thinking.mode;
  const source = THINKING_SOURCE_LABELS[thinking.source || ''] || thinking.source || '';
  return source ? `${mode} · ${source}` : mode;
}

/** 合法（正整数）返回 int；留空或非法返回 null（语义：未配置 / 清空该配置）。 */
export function parseContextLength(raw: unknown): number | null {
  const text = String(raw === undefined || raw === null ? '' : raw).trim();
  if (!text) return null;
  const n = Number(text);
  if (!Number.isFinite(n) || !Number.isInteger(n) || n <= 0) return null;
  return n;
}

/** 就地写入 provider 的 model_context_lengths[modelName]。 */
export function setModelContextLength(
  providers: ProviderConfig[],
  providerName: string,
  modelName: string,
  length: number | null,
): void {
  const target = providers.find((p) => p.name === providerName);
  if (!target) return;
  if (!target.model_context_lengths) target.model_context_lengths = {};
  if (length === null) delete target.model_context_lengths[modelName];
  else target.model_context_lengths[modelName] = length;
}

/** 模型级字段（格式覆盖 / 最大输出 / 服务端工具…）统一写入。 */
export function setModelField(
  providers: ProviderConfig[],
  providerName: string,
  modelName: string,
  field: string,
  value: unknown,
): void {
  const target = providers.find((p) => p.name === providerName);
  if (!target) return;
  const record = target as Record<string, unknown>;
  const existing = record[field];
  if (!existing || typeof existing !== 'object') record[field] = {};
  const map = record[field] as Record<string, unknown>;
  if (value === null || value === undefined || value === '') delete map[modelName];
  else map[modelName] = value;
}

/** 模型重命名时把所有「按模型名索引」的配置一起迁移；newName 为 null 表示删除。 */
export function renameModelConfig(
  providers: ProviderConfig[],
  providerName: string,
  oldName: string,
  newName: string | null,
): void {
  const target = providers.find((p) => p.name === providerName);
  if (!target) return;
  const record = target as Record<string, unknown>;
  for (const field of MODEL_INDEX_FIELDS) {
    const map = record[field];
    if (!map || typeof map !== 'object') continue;
    const entries = map as Record<string, unknown>;
    if (!(oldName in entries)) continue;
    const value = entries[oldName];
    delete entries[oldName];
    if (newName) entries[newName] = value;
  }
}

/** 把服务商编辑表单写进 provider 对象（新增与编辑共用，避免字段漏掉）。 */
export function applyProviderForm(target: ProviderConfig, form: ProviderForm): ProviderConfig {
  target.base_url = form.base_url;
  // 空 key 表示「不修改原密钥」：占位符 ******** 读出来就是空串。
  if (form.key) target.key = form.key;
  target.thinking_type = form.thinking_type;
  target.opencode_go = form.opencode_go;
  target.api_format = form.api_format || 'auto';
  target.stream_usage = form.stream_usage;
  target.format_base_urls = { ...(form.format_base_urls || {}) };
  if (form.catalog_provider_id !== undefined) {
    // 后端接受 null（表示自动识别），但 shared 类型声明是非空字符串。
    (target as unknown as Record<string, unknown>).catalog_provider_id = form.catalog_provider_id;
  }
  return target;
}

/** 新建服务商的完整默认结构（后端 ModelProvider 也有同样的默认值）。 */
export function newProviderRecord(name: string, form: ProviderForm): ProviderConfig {
  return applyProviderForm(
    {
      name,
      base_url: '',
      key: null,
      models: [],
      model_context_lengths: {},
      thinking_type: 'qwen',
      opencode_go: false,
      catalog_provider_id: null,
      model_catalog_routes: {},
      catalog_synced_at: null,
      api_format: 'auto',
      model_api_formats: {},
      format_base_urls: {},
      model_max_output_tokens: {},
      model_api_capabilities: {},
      model_thinking_caps: {},
      model_thinking_modes: {},
      model_server_tools: {},
      stream_usage: null,
    } as unknown as ProviderConfig,
    form,
  );
}

/** 服务商在模型目录里的识别结果，一句话说清（弹窗内展示）。 */
export function catalogSummary(
  provider: ProviderConfig | null | undefined,
  catalog?: AiCatalog | null,
): string {
  if (!provider) {
    return '添加服务商并拉取模型后，这里会显示模型目录的识别结果。';
  }
  const routes = (provider as AiProvider).model_catalog_routes || {};
  const count = Object.keys(routes).length;
  const entry = (catalog || {})[provider.name] || null;
  const providerId = provider.catalog_provider_id || (entry && entry.provider_id) || null;
  if (providerId) {
    const hostNote = entry && entry.host_matched === false
      ? '接口地址与目录不同（只采用格式，地址仍用你填的）'
      : '接口地址与目录一致';
    return `已识别为目录里的「${providerId}」：${hostNote}，${count} 个模型有格式结论。`;
  }
  if (count) {
    return `已有 ${count} 个模型按模型名对上了目录，但没记录目录条目；可以点「自动识别服务商」再试一次。`;
  }
  return '还没有识别出对应的目录条目。可以点「自动识别服务商」，或直接在下面填目录条目名。';
}

/** 目录识别接口返回后的摘要。 */
export function catalogResultText(result?: AiCatalogEntry | null): string {
  if (!result) return '没有返回结果。';
  if (!result.provider_id) {
    const missing = (result.missing || []).length;
    return `没找到对应的目录条目${missing ? `（${missing} 个模型在目录里没有记录）` : ''}。`
      + '可以直接填目录条目名，或在模型设置里手动指定接口格式。';
  }
  const parts = [`识别为目录条目「${result.provider_id}」`, `${result.routed || 0} 个模型有格式结论`];
  if (!result.host_matched) parts.push('接口地址与目录不同，只采用了格式');
  const missingModels = result.missing || [];
  if (missingModels.length) parts.push(`${missingModels.length} 个模型目录里没有`);
  return `${parts.join('；')}。`;
}

/**
 * 把 load-models 的结果写回服务商（旧「从服务商拉取模型」按钮的行为）：
 * 整体替换 models 与四份派生字典；目录条目只在识别到时写入。
 */
export function applyLoadModelsResult(
  providers: ProviderConfig[],
  providerName: string,
  result: LoadModelsResult | null | undefined,
): void {
  const target = providers.find((p) => p.name === providerName);
  if (!target) return;
  const data = result || {};
  target.models = (data.models || []).slice();
  if (data.model_context_lengths) target.model_context_lengths = { ...data.model_context_lengths };
  if (data.model_max_output_tokens) target.model_max_output_tokens = { ...data.model_max_output_tokens };
  if (data.model_api_capabilities) {
    (target as AiProvider).model_api_capabilities = { ...data.model_api_capabilities };
  }
  if (data.model_thinking_caps) {
    (target as AiProvider).model_thinking_caps = { ...data.model_thinking_caps };
  }
  if (data.catalog && data.catalog.provider_id) {
    target.catalog_provider_id = data.catalog.provider_id;
  }
}

/**
 * 模型弹窗「保存到配置」的完整落地：重命名模型数组 + 迁移字典 + 写入表单字段。
 * 返回 false 表示服务商不存在（旧实现直接关弹窗）。
 */
export function applyModelSettings(
  providers: ProviderConfig[],
  providerName: string,
  oldName: string,
  form: ModelSettingsForm,
): boolean {
  const target = providers.find((p) => p.name === providerName);
  if (!target) return false;
  const newName = form.modelName;
  target.models = (target.models || []).map((m) => (m === oldName ? newName : m));
  renameModelConfig(providers, providerName, oldName, newName);
  setModelContextLength(providers, providerName, newName, form.contextLength);
  setModelField(providers, providerName, newName, 'model_max_output_tokens', form.maxOutputTokens);
  setModelField(
    providers,
    providerName,
    newName,
    'model_api_formats',
    form.apiFormat === 'auto' ? null : form.apiFormat,
  );
  setModelField(
    providers,
    providerName,
    newName,
    'model_thinking_modes',
    form.thinkingMode === 'auto' ? null : form.thinkingMode,
  );
  setModelField(
    providers,
    providerName,
    newName,
    'model_server_tools',
    form.serverTools.length ? form.serverTools : null,
  );
  return true;
}

/** 删除模型：从 models 数组移除，并清掉所有按模型名索引的配置。 */
export function deleteModelFromProvider(
  providers: ProviderConfig[],
  providerName: string,
  modelName: string,
): void {
  const target = providers.find((p) => p.name === providerName);
  if (!target) return;
  target.models = (target.models || []).filter((m) => m !== modelName);
  renameModelConfig(providers, providerName, modelName, null);
}

/** 删除服务商：只从列表移除（主/子模型标识由 removeProviderSelection 同步）。 */
export function deleteProviderFromList(providers: ProviderConfig[], name: string): void {
  const index = providers.findIndex((p) => p.name === name);
  if (index >= 0) providers.splice(index, 1);
}

/** 模型重命名后同步主/子模型标识。 */
export function renameModelSelection(
  selection: ModelSelection,
  providerName: string,
  oldName: string,
  newName: string,
): ModelSelection {
  const oldSpec = `${providerName}::${oldName}`;
  const newSpec = `${providerName}::${newName}`;
  return {
    mainModel: selection.mainModel === oldSpec ? newSpec : selection.mainModel,
    subagentModels: (selection.subagentModels || []).map((s) => (s === oldSpec ? newSpec : s)),
  };
}

/** 删除模型后同步主/子模型标识。 */
export function removeModelSelection(
  selection: ModelSelection,
  providerName: string,
  modelName: string,
): ModelSelection {
  const spec = `${providerName}::${modelName}`;
  return {
    mainModel: selection.mainModel === spec ? '' : selection.mainModel,
    subagentModels: (selection.subagentModels || []).filter((s) => s !== spec),
  };
}

/** 删除服务商后同步主/子模型标识（所有该服务商下的模型）。 */
export function removeProviderSelection(
  selection: ModelSelection,
  providerName: string,
): ModelSelection {
  const prefix = `${providerName}::`;
  return {
    mainModel: selection.mainModel && selection.mainModel.startsWith(prefix) ? '' : selection.mainModel,
    subagentModels: (selection.subagentModels || []).filter((s) => !s.startsWith(prefix)),
  };
}
