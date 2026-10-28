// Agent 文件编辑的纯逻辑：文件内容规范化、保存 payload、目录路径、列表状态文案。
// 逐行对照旧 kb-editor.js / modules/agent.js。

import { AGENT_FILES } from '@/config/constants';

export interface AgentListItem {
  name?: string;
  path?: string;
  is_current?: boolean;
  can_delete?: boolean;
  core_files?: Record<string, boolean>;
  [key: string]: unknown;
}

export interface AgentFileEntry {
  content?: string;
  readonly?: boolean;
  [key: string]: unknown;
}

export interface AgentDetail {
  agent?: AgentListItem | null;
  files?: Record<string, AgentFileEntry>;
}

export interface AgentFileValue {
  content: string;
  readonly: boolean;
}

export function agentName(item: AgentListItem | null | undefined): string {
  return String(item?.name || '');
}

/** 旧 renderAgentModule：is_current 显示「当前使用」，其余「备用」。 */
export function agentStatusText(item: AgentListItem | null | undefined): string {
  return item?.is_current ? '当前使用' : '备用';
}

/**
 * 旧 kb-editor.js 的文件取值：
 * 对象取 content/readonly；字符串原样当作内容；缺失当作空内容且可编辑。
 */
export function agentFileValue(
  files: Record<string, AgentFileEntry | string> | null | undefined,
  filename: string,
): AgentFileValue {
  const raw = files ? files[filename] : undefined;
  if (raw && typeof raw === 'object') {
    return { content: String(raw.content || ''), readonly: Boolean(raw.readonly) };
  }
  if (raw === undefined || raw === null) return { content: '', readonly: false };
  return { content: String(raw), readonly: false };
}

/** 四个 Tab 的初值（顺序与 AGENT_FILES 一致）。 */
export function agentFileValues(
  files: Record<string, AgentFileEntry | string> | null | undefined,
): Record<string, AgentFileValue> {
  const values: Record<string, AgentFileValue> = {};
  for (const filename of AGENT_FILES) values[filename] = agentFileValue(files, filename);
  return values;
}

/** 旧「保存全部文件」的 payload：四个固定文件全部提交，缺省为空串。 */
export function buildAgentFilesPayload(values: Record<string, string> | null | undefined): {
  files: Record<string, string>;
} {
  const files: Record<string, string> = {};
  for (const filename of AGENT_FILES) {
    files[filename] = values && Object.prototype.hasOwnProperty.call(values, filename)
      ? String(values[filename] ?? '')
      : '';
  }
  return { files };
}

/** 与旧 guessEditorLanguage 相同：.json → json，其余 markdown。 */
export function agentFileLanguage(filename: string): string {
  return String(filename || '').toLowerCase().endsWith('.json') ? 'json' : 'markdown';
}

/** 旧打开目录的路径拼接：`${root}/agents/${name}`（不额外归一化分隔符）。 */
export function agentDirPath(root: string, name: string): string {
  return `${String(root || '')}/agents/${String(name || '')}`;
}
