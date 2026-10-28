// 两段式搜索的条件模型：草稿/生效条件比较、高级角标、/advanced-search 载荷。

import type { MemFilter, MemSort } from '@/memory/types';

export const MEM_SEARCH_LIMIT = 200;

export const MEM_SEARCH_DEFAULT: MemFilter = {
  query: '',
  tags: [],
  tagLogic: 'AND',
  dateFrom: '',
  dateTo: '',
  sortBy: 'relevance',
  sortOrder: 'desc',
};

export function defaultFilter(): MemFilter {
  return { ...MEM_SEARCH_DEFAULT, tags: [] };
}

/** 逗号分隔标签 → 去空、去重、保序。 */
export function parseTags(raw: unknown): string[] {
  const out: string[] = [];
  for (const part of String(raw || '').split(',')) {
    const tag = part.trim();
    if (tag && !out.includes(tag)) out.push(tag);
  }
  return out;
}

/** 已生效条件 vs 草稿：标签忽略顺序/大小写。 */
export function tagsKey(tags: unknown): string {
  const list = Array.isArray(tags) ? tags : [];
  return list.map((tag) => String(tag).trim().toLowerCase()).filter(Boolean).sort().join('\u0000');
}

export function isSearchDirty(form: MemFilter, filter: MemFilter): boolean {
  const applied: MemFilter = { ...MEM_SEARCH_DEFAULT, ...(filter || {}) };
  const norm = (value: unknown): string => String(value || '').trim();
  return (
    norm(form.query) !== norm(applied.query) ||
    tagsKey(form.tags) !== tagsKey(applied.tags) ||
    norm(form.dateFrom) !== norm(applied.dateFrom) ||
    norm(form.dateTo) !== norm(applied.dateTo) ||
    norm(form.tagLogic) !== norm(applied.tagLogic) ||
    norm(form.sortBy) !== norm(applied.sortBy) ||
    norm(form.sortOrder) !== norm(applied.sortOrder)
  );
}

/** 高级项里「已启用」的数量（逻辑非默认 / 日期 / 排序非默认）。 */
export function advancedCount(filter: MemFilter | null | undefined): number {
  const applied: MemFilter = { ...MEM_SEARCH_DEFAULT, ...(filter || {}) };
  let count = 0;
  if (String(applied.tagLogic || 'AND') !== 'AND') count += 1;
  if (applied.dateFrom || applied.dateTo) count += 1;
  if (
    String(applied.sortBy || 'relevance') !== 'relevance' ||
    String(applied.sortOrder || 'desc') !== 'desc'
  ) {
    count += 1;
  }
  return count;
}

export function filterActive(filter: MemFilter | null | undefined): boolean {
  const applied = filter || MEM_SEARCH_DEFAULT;
  return Boolean(
    String(applied.query || '').trim() ||
      (applied.tags && applied.tags.length) ||
      applied.dateFrom ||
      applied.dateTo,
  );
}

export interface MemSearchPayload {
  query: string | null;
  tags: string[] | null;
  scope: string | null;
  date_from: string | null;
  date_to: string | null;
  sort_by: string;
  sort_order: string;
  tag_logic: string;
  top_k: number;
}

/** 构造 /advanced-search 载荷：无关键词时明确按更新时间取数。 */
export function buildSearchPayload(filter: MemFilter, scope: string | null): MemSearchPayload {
  const query = String(filter.query || '').trim();
  return {
    query: query || null,
    tags: filter.tags && filter.tags.length ? filter.tags : null,
    scope,
    date_from: filter.dateFrom || null,
    date_to: filter.dateTo || null,
    sort_by: query ? filter.sortBy || 'relevance' : 'updated_at',
    sort_order: query ? filter.sortOrder || 'desc' : 'desc',
    tag_logic: filter.tagLogic || 'AND',
    top_k: MEM_SEARCH_LIMIT,
  };
}

/** 取数顺序决定表头初始排序（相关度 → score，否则 updated_at）。 */
export function sortFromPayload(payload: MemSearchPayload): MemSort {
  return {
    by: payload.sort_by === 'relevance' ? 'score' : 'updated_at',
    order: payload.sort_order === 'asc' ? 'asc' : 'desc',
  };
}
