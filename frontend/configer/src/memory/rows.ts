// 列表行构造 / 子树统计 / 分页：逐条移植 explorer.js 与 list.js 的数据组装逻辑。

import { findKbNodeByPath, kbParentPath, normalizeKbPath } from '@/memory/kbUtils';
import { inScope } from '@/memory/paths';
import type { KbNode, MemFilter, MemRow } from '@/memory/types';

export const MEM_LIST_PAGE_SIZE = 200;

function emptyRowBase(type: 'dir' | 'file', path: string, name: string): MemRow {
  return {
    type,
    path,
    name,
    dir: kbParentPath(path),
    description: '',
    tags: [],
    updated_at: '',
    declared_by: '',
    score_patch: 0,
    chunk_count: 0,
    indexed: false,
    content_type: '',
  };
}

/** 目录行（列表浏览用）；childCount 为全部子项数量（含实体）。 */
export function dirRow(node: KbNode): MemRow {
  const path = normalizeKbPath(node.path);
  const row = emptyRowBase('dir', path, String(node.name || ''));
  row.description = String(node.description || '');
  row.childCount = (node.children || []).length;
  return row;
}

/** 目录内的文件行（浏览态）。 */
export function browseFileRow(node: KbNode): MemRow {
  const path = normalizeKbPath(node.path);
  const row = emptyRowBase('file', path, String(node.name || ''));
  row.description = String(node.description || '');
  row.tags = Array.isArray(node.tags) ? node.tags : [];
  row.updated_at = String(node.updated_at || '');
  row.chunk_count = Number(node.chunk_count || 0);
  row.indexed = Boolean(node.indexed);
  row.declared_by = String(node.declared_by || '');
  row.score_patch = Number(node.score_patch || 0);
  row.content_type = String(node.content_type || '');
  return row;
}

/** 任意文件节点 → 行（名称缺失时退回路径尾段）。 */
export function fileRow(node: KbNode): MemRow {
  const path = normalizeKbPath(node.path || '/');
  const row = emptyRowBase('file', path, String(node.name || path.split('/').pop() || path));
  row.description = String(node.description || '');
  row.tags = Array.isArray(node.tags) ? node.tags : [];
  row.updated_at = String(node.updated_at || '');
  row.chunk_count = Number(node.chunk_count || 0);
  row.indexed = Boolean(node.indexed);
  row.declared_by = String(node.declared_by || '');
  row.score_patch = Number(node.score_patch || 0);
  row.content_type = String(node.content_type || '');
  return row;
}

/** 检索结果 → 行：服务端条目 + 树元数据（chunk_count/indexed/content_type 只有树里有）。 */
export function searchResultRow(item: KbNode, node?: KbNode | null): MemRow {
  const path = normalizeKbPath(item.path);
  const meta = node || {};
  const tags = Array.isArray(item.tags) && item.tags.length
    ? item.tags
    : (Array.isArray(meta.tags) ? meta.tags : []);
  const row = emptyRowBase(
    'file',
    path,
    String(meta.name || String(item.path || '').split('/').pop() || path),
  );
  row.description = String(item.description || meta.description || '');
  row.tags = tags;
  row.updated_at = String(item.updated_at || meta.updated_at || '');
  row.declared_by = String(item.declared_by || meta.declared_by || '');
  row.score_patch = Number(item.score_patch || meta.score_patch || 0);
  row.chunk_count = Number(meta.chunk_count || 0);
  row.indexed = Boolean(meta.indexed);
  row.content_type = String(meta.content_type || '');
  row.score = Number(item.score || 0);
  return row;
}

/** 整棵树里的全部文件行。 */
export function flattenFiles(tree: KbNode | null | undefined): MemRow[] {
  const out: MemRow[] = [];
  const walk = (node: KbNode | null | undefined): void => {
    if (!node) return;
    if (node.type === 'file') {
      out.push(fileRow(node));
      return;
    }
    for (const child of node.children || []) walk(child);
  };
  walk(tree);
  return out;
}

/** 当前目录子树内的全部文件（可视化用）。 */
export function subtreeFiles(tree: KbNode | null | undefined, dir: string): MemRow[] {
  return flattenFiles(tree).filter((file) => inScope(file.path, dir));
}

/** 浏览态：当前目录的直接子项（目录在前，各自按名称排序）。 */
export function browseRows(node: KbNode | null | undefined): MemRow[] {
  const children = (node && node.children) || [];
  const dirs: MemRow[] = [];
  const files: MemRow[] = [];
  for (const child of children) {
    if (child.type === 'entity') continue;
    if (child.type === 'file') files.push(browseFileRow(child));
    else dirs.push(dirRow(child));
  }
  dirs.sort((a, b) => a.name.localeCompare(b.name));
  files.sort((a, b) => a.name.localeCompare(b.name));
  return dirs.concat(files);
}

/** 列表行：有结果集 → 共享结果集；否则 → 当前目录浏览。 */
export function buildRows(
  searchResults: MemRow[] | null,
  tree: KbNode | null,
  dir: string,
): MemRow[] {
  if (searchResults) {
    return searchResults.map((item) => ({
      ...item,
      type: 'file',
      dir: item.dir || kbParentPath(item.path),
    }) as MemRow);
  }
  return browseRows(findKbNodeByPath(tree, dir));
}

/** 详情栏「查看关联文件」：路径集合 → 行（非条件型结果集）。 */
export function linkedFileRows(paths: unknown[], tree: KbNode | null): MemRow[] {
  return (Array.isArray(paths) ? paths : []).map((raw) => {
    const path = normalizeKbPath(raw);
    const node = findKbNodeByPath(tree, path) || {};
    const row = emptyRowBase(
      'file',
      path,
      String(node.name || path.split('/').pop() || path),
    );
    row.description = String(node.description || '');
    row.tags = Array.isArray(node.tags) ? node.tags : [];
    row.updated_at = String(node.updated_at || '');
    row.chunk_count = Number(node.chunk_count || 0);
    row.indexed = Boolean(node.indexed);
    row.declared_by = String(node.declared_by || '');
    row.score_patch = Number(node.score_patch || 0);
    row.content_type = String(node.content_type || '');
    return row;
  });
}

/** 相关度列只在「填了关键词的检索」下出现。 */
export function hasScoreColumn(
  searchResults: MemRow[] | null,
  filter: MemFilter | null | undefined,
): boolean {
  return Boolean(searchResults && String((filter || {}).query || '').trim());
}

// ── 分页 ──

export function totalPagesOf(count: number, pageSize: number = MEM_LIST_PAGE_SIZE): number {
  return Math.max(1, Math.ceil(count / pageSize));
}

export function clampPage(page: unknown, totalPages: number): number {
  const raw = Number(page);
  const base = Number.isFinite(raw) ? Math.floor(raw) : 0;
  return Math.min(Math.max(0, base), totalPages - 1);
}

export interface PageSlice<T> {
  page: number;
  start: number;
  rows: T[];
}

export function pageSlice<T>(
  rows: readonly T[],
  page: unknown,
  pageSize: number = MEM_LIST_PAGE_SIZE,
): PageSlice<T> {
  const totalPages = totalPagesOf(rows.length, pageSize);
  const current = clampPage(page, totalPages);
  const start = current * pageSize;
  return { page: current, start, rows: rows.slice(start, start + pageSize) };
}

/** Treemap 只画当前目录这一层起（当前目录 = 它的根）。 */
export function scopedTree(tree: KbNode | null, dir: string): KbNode | null {
  const node = findKbNodeByPath(tree, dir);
  if (node) {
    return {
      path: node.path,
      name: node.name || dir,
      type: 'dir',
      description: node.description || '',
      children: node.children || [],
    };
  }
  return tree;
}
