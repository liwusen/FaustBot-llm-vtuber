// SillyTavern 角色卡导入的纯逻辑：目录名校验/冲突提示、预览摘要、请求体、结果文案。
// 逐行对照旧 card-import.js。

import type { InfoRow } from '@/config/types';

export const CARD_IMPORT_NAME_RE = /^[A-Za-z0-9_.-]+$/;
// 世界书逐条嵌入可能耗时数十秒到分钟级，放宽 IPC 默认 30s 超时
export const CARD_IMPORT_TIMEOUT_MS = 10 * 60 * 1000;
export const CARD_IMPORT_NAME_INVALID = '目录名非法：只能包含字母、数字、下划线、横线和点。';
export const CARD_IMPORT_NAME_HELP =
  '只能包含字母、数字、下划线、横线和点；创建为 ~/.faustbot/agents/<目录名>，AGENT.md 恒为 faust 模板。';

export interface CardLorebookSummary {
  book?: string;
  total?: number;
  enabled?: number;
  imported?: number;
  skipped_disabled?: number;
  error_count?: number;
  errors?: string[];
}

export interface CardPreview {
  suggested_name?: string;
  name?: string;
  spec?: string;
  creator?: string;
  character_version?: string;
  tags?: string[];
  description_preview?: string;
  first_mes_preview?: string;
  has_system_prompt?: boolean;
  has_post_history_instructions?: boolean;
  has_avatar?: boolean;
  lorebook?: CardLorebookSummary;
  source_format?: string;
  source_path?: string;
  [key: string]: unknown;
}

export interface CardImportResult {
  agent_name?: string;
  avatar?: { path?: string } | null;
  lorebook?: CardLorebookSummary;
  [key: string]: unknown;
}

export interface CardImportNameState {
  /** 是否可以提交导入。 */
  ok: boolean;
  /** 展示在弹窗状态行的文案（合法且不冲突时为空串）。 */
  message: string;
  /** 目标目录名已存在（旧后端会返回 409，前端提前拦下）。 */
  conflict: boolean;
}

function cardImportValue(value: unknown): string {
  return String(value || '-');
}

/**
 * 旧弹窗的 validate() + 覆盖保护：
 * 正则与旧 CARD_IMPORT_NAME_RE 一致；同名目录无法覆盖（后端 import-card 只创建新目录），
 * 因此提前判冲突并禁用导入。
 */
export function cardImportNameState(
  raw: unknown,
  existingNames: readonly string[] = [],
): CardImportNameState {
  const name = String(raw ?? '').trim();
  if (!CARD_IMPORT_NAME_RE.test(name)) {
    return { ok: false, message: CARD_IMPORT_NAME_INVALID, conflict: false };
  }
  const lowered = name.toLowerCase();
  const conflict = existingNames.some((item) => String(item || '').trim().toLowerCase() === lowered);
  if (conflict) {
    return { ok: false, message: `Agent 已存在：${name}，请换一个目录名。`, conflict: true };
  }
  return { ok: true, message: '', conflict: false };
}

export function buildCardImportBody(cardPath: string, agentName: string): {
  card_path: string;
  agent_name: string;
} {
  return { card_path: String(cardPath || ''), agent_name: String(agentName || '').trim() };
}

/** 旧 cardImportSummary 的 9 行，顺序与文案完全一致。 */
export function cardSummaryRows(preview: CardPreview | null | undefined): InfoRow[] {
  const data = preview || {};
  const book = data.lorebook || {};
  const tags = Array.isArray(data.tags) ? data.tags : [];
  const extras = [
    data.has_system_prompt ? 'system_prompt' : '',
    data.has_post_history_instructions ? 'post_history_instructions' : '',
  ].filter(Boolean);
  return [
    { label: '卡片名称', value: cardImportValue(data.name) },
    { label: '规格', value: cardImportValue(data.spec) },
    { label: '作者', value: cardImportValue(data.creator) },
    { label: '版本', value: cardImportValue(data.character_version) },
    { label: '标签', value: cardImportValue(tags.join('、')) },
    {
      label: '世界书',
      value: book.total
        ? `${String(book.book || '')}（启用 ${book.enabled} / 共 ${book.total}）`
        : '无',
    },
    { label: '立绘', value: data.has_avatar ? '有 → 写入记忆附件' : '无' },
    { label: '卡片附加约束', value: cardImportValue(extras.join('、')) },
    { label: '来源', value: `${String(data.source_format || '')} · ${String(data.source_path || '')}` },
  ];
}

export interface CardImportBanner {
  type: 'success' | 'error';
  text: string;
}

/** 旧导入成功/世界书写入失败的 banner 文案。 */
export function cardImportBanner(result: CardImportResult | null | undefined): CardImportBanner {
  const data = result || {};
  const book = data.lorebook || {};
  const parts: string[] = [];
  if (book.enabled) parts.push(`世界书 ${book.imported}/${book.enabled}`);
  if (data.avatar) parts.push('立绘已存入记忆');
  if (book.error_count) {
    const detail = (Array.isArray(book.errors) ? book.errors : []).slice(0, 3).join('；');
    return {
      type: 'error',
      text: `已创建 Agent ${String(data.agent_name || '')}，但世界书有 ${book.error_count} 条写入失败：${detail}`,
    };
  }
  return {
    type: 'success',
    text: `已从角色卡创建 Agent: ${String(data.agent_name || '')}${parts.length ? `（${parts.join('，')}）` : ''}`,
  };
}
