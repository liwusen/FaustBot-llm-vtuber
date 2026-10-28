// MCP 编辑器的纯逻辑：状态文案、参数/请求头文本互转、表单默认值与 payload。
// 逐行对照旧 modules/mcp.js。

import type { InfoRow } from '@/config/types';

export type McpTransport = 'stdio' | 'sse' | 'streamable-http';

export interface McpTool {
  name?: string;
  title?: string;
  description?: string;
  [key: string]: unknown;
}

export interface McpServerRecord {
  server_id?: string;
  id?: string;
  enabled?: boolean;
  description?: string;
  transport?: string;
  custom?: boolean;
  command?: string;
  args?: unknown;
  url?: string;
  headers?: unknown;
  status?: string;
  tool_count?: number;
  tools?: McpTool[];
  log_tail?: unknown;
  error?: string;
  [key: string]: unknown;
}

export interface McpEditorBody {
  server_id: string;
  enabled: boolean;
  description: string;
  transport: McpTransport;
  custom: boolean;
  command: string;
  args: string[];
  url: string;
  headers: Record<string, string>;
}

export interface McpEditorForm {
  server_id: string;
  enabled: boolean;
  description: string;
  transport: string;
  custom: boolean;
  command: string;
  argsText: string;
  url: string;
  headersText: string;
}

export interface McpPayload {
  server_id: string;
  enabled: boolean;
  description: string;
  transport: McpTransport;
  custom: boolean;
  command: string;
  args: string[];
  url: string;
  headers: Record<string, string>;
}

export function mcpServerId(item: McpServerRecord | null | undefined): string {
  return String(item?.server_id || item?.id || '');
}

/** 旧 _mcpStatusText。 */
export function mcpStatusText(item: McpServerRecord | null | undefined): string {
  if (item && item.status === 'running') return '运行中';
  if (item && item.status === 'error') return '错误';
  return '已停止';
}

/** 旧 _mcpStatusDot 的配色。 */
export function mcpStatusColor(item: McpServerRecord | null | undefined): string {
  if (item && item.status === 'running') return '#4caf50';
  if (item && item.status === 'error') return '#f44336';
  return '#8b95a7';
}

/** 表单三选一：非 sse / streamable-http 一律视为 stdio（旧 radio 默认勾选逻辑）。 */
export function normalizeTransport(value: unknown): McpTransport {
  if (value === 'sse') return 'sse';
  if (value === 'streamable-http') return 'streamable-http';
  return 'stdio';
}

export function mcpArgsToText(args: unknown): string {
  return Array.isArray(args) ? args.join('\n') : '';
}

/** 旧 _mcpTextToArgs：按行拆分、trim、丢弃空行。 */
export function mcpTextToArgs(text: unknown): string[] {
  return String(text ?? '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** 旧 _mcpHeadersToText：`key: value`，每行一条。 */
export function mcpHeadersToText(headers: unknown): string {
  if (!headers || typeof headers !== 'object') return '';
  return Object.entries(headers as Record<string, unknown>)
    .map(([key, value]) => `${key}: ${value}`)
    .join('\n');
}

/** 旧 _mcpTextToHeaders：第一个冒号切分；空行、无键、无冒号的行忽略。 */
export function mcpTextToHeaders(text: unknown): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const line of String(text ?? '').split(/\r?\n/)) {
    const raw = line.trim();
    if (!raw) continue;
    const index = raw.indexOf(':');
    if (index <= 0) continue;
    const key = raw.slice(0, index).trim();
    const value = raw.slice(index + 1).trim();
    if (!key) continue;
    headers[key] = value;
  }
  return headers;
}

/** 旧 openMcpEditorModal 的初始 body（新建与编辑两套默认值）。 */
export function mcpEditorBody(existing?: McpServerRecord | null): McpEditorBody {
  if (!existing) {
    return {
      server_id: '',
      enabled: true,
      description: '',
      transport: 'stdio',
      custom: false,
      command: 'node',
      args: [],
      url: '',
      headers: {},
    };
  }
  const rawHeaders = existing.headers && typeof existing.headers === 'object'
    ? (existing.headers as Record<string, unknown>)
    : {};
  const headers: Record<string, string> = {};
  for (const [key, value] of Object.entries(rawHeaders)) headers[key] = String(value);

  return {
    server_id: mcpServerId(existing),
    enabled: existing.enabled !== false,
    description: String(existing.description || ''),
    transport: normalizeTransport(existing.transport),
    custom: Boolean(existing.custom),
    command: existing.command ? String(existing.command) : 'node',
    args: Array.isArray(existing.args) ? existing.args.map((arg) => String(arg)) : [],
    url: String(existing.url || ''),
    headers,
  };
}

/** 旧保存按钮构造的 payload（先 trim，再解析参数/请求头）。 */
export function buildMcpPayload(form: McpEditorForm): McpPayload {
  return {
    server_id: String(form.server_id || '').trim(),
    enabled: Boolean(form.enabled),
    description: String(form.description || '').trim(),
    transport: normalizeTransport(form.transport),
    custom: Boolean(form.custom),
    command: String(form.command || '').trim(),
    args: mcpTextToArgs(form.argsText),
    url: String(form.url || '').trim(),
    headers: mcpTextToHeaders(form.headersText),
  };
}

/** 旧列表行文本：`id | transport | 状态 | tools=N`。 */
export function mcpListLine(item: McpServerRecord): string {
  return `${mcpServerId(item)} | ${item.transport || 'stdio'} | ${mcpStatusText(item)} | tools=${item.tool_count || 0}`;
}

/** 旧详情「基本信息」10 行。 */
export function mcpBasicRows(item: McpServerRecord): InfoRow[] {
  const headers = item.headers && typeof item.headers === 'object' ? item.headers : {};
  return [
    { label: '服务器标识', value: item.server_id || item.id },
    { label: '状态', value: mcpStatusText(item) },
    { label: '启用', value: item.enabled },
    { label: '传输', value: item.transport || 'stdio' },
    { label: '自定义', value: item.custom },
    { label: '命令', value: item.command || 'builtin' },
    { label: 'URL', value: item.url || '-' },
    {
      label: '请求头',
      value: Object.keys(headers).length ? JSON.stringify(headers) : '-',
    },
    { label: '描述', value: item.description || '-' },
    { label: '错误', value: item.error || '-' },
  ];
}

/** 旧详情「工具列表」：名称 / 标题 / 描述。 */
export function mcpToolRows(item: McpServerRecord): unknown[][] {
  const tools = Array.isArray(item.tools) ? item.tools : [];
  return tools.map((tool) => [
    tool?.name || '-',
    tool?.title || '-',
    tool?.description || '-',
  ]);
}

/** 旧日志弹窗内容：数组按行拼接，否则「（无日志）」。 */
export function mcpLogText(logTail: unknown): string {
  return Array.isArray(logTail) ? logTail.join('\n') : '（无日志）';
}
