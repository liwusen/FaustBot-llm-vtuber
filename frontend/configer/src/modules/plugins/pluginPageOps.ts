// 插件管理页的纯逻辑：字段解析、健康环 option、详情行与配置 payload。
// 逐条对照旧 libs/configer/modules/plugins.js（parsePluginFieldValue /
// pluginHealthLabel / mountPluginChart / 保存按钮），不依赖 Vue。

import type { InfoRow } from '@/config/types';

export interface PluginConfigSchemaItem {
  key?: string;
  type?: string;
  label?: string;
  description?: string;
  default?: unknown;
  [key: string]: unknown;
}

export interface PluginConfigSnapshot {
  plugin_id?: string;
  schema?: PluginConfigSchemaItem[];
  values?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface PluginHealth {
  status?: string;
  error?: string;
  [key: string]: unknown;
}

export interface PluginTriggerControl {
  enabled?: boolean;
  supports_append_filter?: boolean;
  supports_fire_filter?: boolean;
  [key: string]: unknown;
}

export interface PluginToolInfo {
  name?: string;
  enabled?: boolean;
  description?: string;
  [key: string]: unknown;
}

export interface PluginMiddlewareInfo extends PluginToolInfo {
  priority?: number;
}

/** `/faust/admin/plugins` 的 items 元素（字段见后端 PluginManager.list_plugins）。 */
export interface PluginInfo {
  id?: string;
  name?: string;
  version?: string;
  description?: string;
  author?: string;
  homepage?: string;
  enabled?: boolean;
  permissions?: string[];
  priority?: number;
  tools?: PluginToolInfo[];
  middlewares?: PluginMiddlewareInfo[];
  trigger_control?: PluginTriggerControl | null;
  config?: PluginConfigSnapshot | null;
  health?: PluginHealth | null;
  [key: string]: unknown;
}

export interface PluginUpdateEntry {
  id?: string;
  name?: string;
  installed_version?: string;
  latest_version?: string;
  [key: string]: unknown;
}

export interface PluginUpdateData {
  updates?: PluginUpdateEntry[];
  [key: string]: unknown;
}

export interface PluginConfigPayload {
  values: Record<string, unknown>;
  apply_runtime: true;
  reset_dialog: false;
  no_initial_chat: true;
}

export interface BannerNotice {
  type: 'success';
  text: string;
}

export const PLUGIN_HEALTH_RENDERER = 'svg';

const LLM_CONFIG_TYPES = new Set(['llm', 'model']);

const HEALTH_LABELS: Record<string, string> = {
  healthy: '正常',
  ok: '正常',
  warning: '注意',
  error: '异常',
  unknown: '未知',
};

/** 旧 parsePluginFieldValue：bool 强制布尔，int/float 用 parseInt/parseFloat，json 解析文本。 */
export function parsePluginFieldValue(fieldType: unknown, rawValue: unknown): unknown {
  const t = String(fieldType || 'str').toLowerCase();
  if (t === 'bool') return Boolean(rawValue);
  if (t === 'int') return Number.parseInt(String(rawValue || '0'), 10) || 0;
  if (t === 'float') return Number.parseFloat(String(rawValue || '0')) || 0;
  if (t === 'json') {
    if (!String(rawValue || '').trim()) return null;
    return JSON.parse(String(rawValue));
  }
  return rawValue;
}

/** 旧 field-render.js 的 toText：对象/数组按 2 空格 JSON 展示，其余直接字符串。 */
export function pluginFieldText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export function isLlmConfigType(type: unknown): boolean {
  return LLM_CONFIG_TYPES.has(String(type || '').toLowerCase());
}

export function pluginConfigSchema(plugin: PluginInfo | null | undefined): PluginConfigSchemaItem[] {
  const schema = plugin?.config?.schema;
  if (!Array.isArray(schema)) return [];
  return schema.filter((item): item is PluginConfigSchemaItem => item !== null && typeof item === 'object');
}

/**
 * 进入插件页/切换选中插件时的草稿初值（旧 ensureModuleData 的逐项复制）。
 * JSON 字段预先转成文本：旧实现 textarea 显示 JSON 文本，但草稿保留对象会导致
 * 未编辑直接保存时 JSON.parse("[object Object]") 失败；这里让草稿与控件文本一致。
 */
export function pluginConfigDraftValues(
  plugin: PluginInfo | null | undefined,
): Record<string, unknown> {
  const draft: Record<string, unknown> = {};
  const values = plugin?.config?.values;
  if (values && typeof values === 'object' && !Array.isArray(values)) {
    for (const [key, value] of Object.entries(values)) draft[key] = value;
  }
  for (const item of pluginConfigSchema(plugin)) {
    const key = String(item.key || '');
    if (!key) continue;
    if (String(item.type || 'str').toLowerCase() === 'json' && key in draft) {
      draft[key] = pluginFieldText(draft[key]);
    }
  }
  return draft;
}

/** 保存按钮的 payload：只提交 schema 里登记的键，其余参数与旧实现一致。 */
export function pluginConfigPayload(
  schema: PluginConfigSchemaItem[],
  draft: Record<string, unknown>,
): PluginConfigPayload {
  const values: Record<string, unknown> = {};
  for (const item of schema || []) {
    const key = String(item.key || '');
    if (!key) continue;
    values[key] = parsePluginFieldValue(item.type || 'str', draft[key]);
  }
  return {
    values,
    apply_runtime: true,
    reset_dialog: false,
    no_initial_chat: true,
  };
}

export function pluginHealthStatus(plugin: PluginInfo | null | undefined): string {
  return String((plugin?.health || {}).status || 'unknown').toLowerCase();
}

/** 旧 pluginHealthLabel：健康状态 → 中文标签；未登记的状态原样返回。 */
export function pluginHealthLabel(plugin: PluginInfo | null | undefined): string {
  const status = pluginHealthStatus(plugin);
  return HEALTH_LABELS[status] || status;
}

/** 旧 mountPluginChart 的强调色：异常/警告优先，其次按启用状态取蓝/灰。 */
export function pluginHealthAccent(plugin: PluginInfo | null | undefined): string {
  const status = pluginHealthStatus(plugin);
  if (status === 'error') return '#e54447';
  if (status === 'warning') return '#f59e0b';
  return plugin && plugin.enabled ? '#3f6be8' : '#b8c6d8';
}

/** 旧健康环：启用 72/28，停用 38/62，中心圆 + 插件首字母。 */
export function pluginHealthChartOption(
  plugin: PluginInfo | null | undefined,
): Record<string, unknown> {
  const enabled = Boolean(plugin && plugin.enabled);
  const accent = pluginHealthAccent(plugin);
  const initial = String((plugin && (plugin.name || plugin.id)) || 'P')
    .slice(0, 1)
    .toUpperCase();
  return {
    animation: false,
    tooltip: { show: false },
    series: [
      {
        type: 'pie',
        radius: ['62%', '84%'],
        silent: true,
        label: { show: false },
        data: [
          { value: enabled ? 72 : 38, itemStyle: { color: accent } },
          { value: enabled ? 28 : 62, itemStyle: { color: '#e6edf7' } },
        ],
      },
    ],
    graphic: [
      {
        type: 'circle',
        left: 'center',
        top: 'center',
        shape: { r: 12 },
        style: { fill: '#ffffff', stroke: accent, lineWidth: 1.5 },
      },
      {
        type: 'text',
        left: 'center',
        top: 'center',
        style: {
          text: initial,
          fill: accent,
          fontSize: 12,
          fontWeight: 700,
          textAlign: 'center',
          textVerticalAlign: 'middle',
        },
      },
    ],
  };
}

/** 旧版本列文案：`可更新 → ${latest_version}`。 */
export function pluginUpdateBadge(entry: PluginUpdateEntry | null | undefined): string {
  if (!entry) return '';
  return `可更新 → ${entry.latest_version}`;
}

export function pluginUpdateFor(
  data: PluginUpdateData | null | undefined,
  pluginId: string,
): PluginUpdateEntry | null {
  const updates = Array.isArray(data?.updates) ? data?.updates || [] : [];
  return updates.find((item) => String(item.id) === pluginId) || null;
}

/** 旧「检查更新」banner 文案。 */
export function pluginUpdatesBanner(data: PluginUpdateData | null | undefined): BannerNotice {
  const updates = Array.isArray(data?.updates) ? data?.updates || [] : [];
  if (!updates.length) return { type: 'success', text: '所有插件均为最新版本。' };
  const names = updates
    .map((entry) => `${entry.name || entry.id} (${entry.installed_version || '?'} → ${entry.latest_version})`)
    .join('、');
  return { type: 'success', text: `发现 ${updates.length} 个可更新插件: ${names}` };
}

/** 刷新后选中项回落：仍存在就保留，否则取第一个；列表为空时清空。 */
export function resolvePluginSelection(
  plugins: PluginInfo[],
  currentId: string | null | undefined,
): string {
  const id = String(currentId || '');
  const list = Array.isArray(plugins) ? plugins : [];
  if (id && list.some((plugin) => String(plugin.id || '') === id)) return id;
  return list.length ? String(list[0].id || '') : '';
}

export function pluginBasicRows(plugin: PluginInfo): InfoRow[] {
  return [
    { label: '标识', value: plugin.id },
    { label: '名称', value: plugin.name },
    { label: '版本', value: plugin.version },
    { label: '作者', value: plugin.author },
    { label: '主页', value: plugin.homepage },
    { label: '启用', value: plugin.enabled },
    { label: '优先级', value: plugin.priority },
    { label: '描述', value: plugin.description },
  ];
}

export function pluginHealthRows(plugin: PluginInfo): InfoRow[] {
  const health: PluginHealth =
    plugin.health && typeof plugin.health === 'object' ? plugin.health : {};
  const triggerControl: PluginTriggerControl =
    plugin.trigger_control && typeof plugin.trigger_control === 'object'
      ? plugin.trigger_control
      : {};
  return [
    { label: '状态', value: health.status || 'unknown' },
    { label: '错误信息', value: health.error || '-' },
    { label: '追加过滤器', value: triggerControl.supports_append_filter },
    { label: '触发过滤器', value: triggerControl.supports_fire_filter },
  ];
}

export function pluginPermissionTags(plugin: PluginInfo): string[] {
  return Array.isArray(plugin.permissions) ? plugin.permissions.map((item) => String(item)) : [];
}

export function pluginToolRows(plugin: PluginInfo): unknown[][] {
  const tools = Array.isArray(plugin.tools) ? plugin.tools : [];
  return tools.map((tool) => [tool.name, tool.enabled, tool.description || '-']);
}

export function pluginMiddlewareRows(plugin: PluginInfo): unknown[][] {
  const middlewares = Array.isArray(plugin.middlewares) ? plugin.middlewares : [];
  return middlewares.map((item) => [
    item.name,
    item.priority,
    item.enabled,
    item.description || '-',
  ]);
}

export function pluginVersionText(plugin: PluginInfo): string {
  return String(plugin.version || '-');
}

/** LLM 配置项未选择时的占位文案。 */
export const PLUGIN_LLM_UNSET_TEXT = '(未选择 · 缺省使用主对话模型)';

export function pluginLlmDisplayValue(rawValue: unknown): string {
  if (!pluginLlmHasValue(rawValue)) return PLUGIN_LLM_UNSET_TEXT;
  return String(rawValue);
}

export function pluginLlmHasValue(rawValue: unknown): boolean {
  return !(rawValue === null || rawValue === undefined || String(rawValue) === '');
}

export function pluginLlmDescription(item: PluginConfigSchemaItem): string {
  return String(
    item.description || '当前使用的模型标识符（provider::model）；未选择时使用主对话模型。',
  );
}
