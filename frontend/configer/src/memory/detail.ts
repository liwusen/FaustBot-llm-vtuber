// 详情栏纯逻辑：格式化、目录子树统计、概览行、实体描述截断、图片 data URL。

import type { KbNode } from '@/memory/types';

export const MEM_DETAIL_ENTITY_DESC_MAX = 80;

export function detailMsg(err: unknown): string {
  const value = err as { message?: unknown } | null | undefined;
  return String((value && value.message) || err || '未知错误');
}

/** 空值 → `—`；布尔 → 是/否；空串 → `—`。 */
export function detailDash(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'boolean') return value ? '是' : '否';
  const text = String(value);
  return text || '—';
}

/** ISO 字符串补 Z 后本地化；解析失败原样返回。 */
export function detailFmtTime(raw: unknown): string {
  const text = String(raw || '').trim();
  if (!text) return '—';
  const hasZone = text.endsWith('Z') || /[+-]\d{2}:?\d{2}$/.test(text);
  const parsed = new Date(hasZone ? text : `${text}Z`);
  if (Number.isNaN(parsed.getTime())) return text;
  return parsed.toLocaleString();
}

/** 时间戳（用于比较最近更新）；空/非法 → 0。 */
export function detailTimeValue(raw: unknown): number {
  const text = String(raw || '').trim();
  if (!text) return 0;
  const hasZone = text.endsWith('Z') || /[+-]\d{2}:?\d{2}$/.test(text);
  const parsed = new Date(hasZone ? text : `${text}Z`);
  return Number.isNaN(parsed.getTime()) ? 0 : parsed.getTime();
}

export interface SubtreeStats {
  dirs: number;
  files: number;
  chunks: number;
  updatedAt: string;
}

/** 目录子树统计：跳过实体，最晚更新时间取所有文件。 */
export function subtreeStats(root: KbNode | null | undefined): SubtreeStats {
  const stats: SubtreeStats = { dirs: 0, files: 0, chunks: 0, updatedAt: '' };
  if (!root) return stats;
  const walk = (node: KbNode): void => {
    for (const child of node.children || []) {
      const type = String(child.type || 'dir');
      if (type === 'entity') continue;
      if (type === 'file') {
        stats.files += 1;
        stats.chunks += Number(child.chunk_count || 0);
        const stamp = String(child.updated_at || '');
        if (stamp && detailTimeValue(stamp) >= detailTimeValue(stats.updatedAt)) {
          stats.updatedAt = stamp;
        }
      } else {
        stats.dirs += 1;
        walk(child);
      }
    }
  };
  walk(root);
  return stats;
}

/** 文件概览已展示的键；其余标量键收进「展开全部」。 */
export const MEM_FILE_OVERVIEW_SHOWN = [
  'path',
  'updated_at',
  'declared_by',
  'chunk_count',
  'score_patch',
  'indexed',
  'content_type',
  'description',
  'tags',
];

export interface KvRow {
  label: string;
  value: unknown;
}

/** 文件概览额外的标量键（跳过已展示、null/undefined、对象）。 */
export function fileExtraKvRows(meta: Record<string, unknown> | null | undefined): KvRow[] {
  const source = meta || {};
  const rows: KvRow[] = [];
  for (const key of Object.keys(source)) {
    if (MEM_FILE_OVERVIEW_SHOWN.indexOf(key) >= 0) continue;
    const value = source[key];
    if (value === null || value === undefined) continue;
    if (typeof value === 'object') continue;
    rows.push({ label: key, value });
  }
  return rows;
}

/** 实体描述：超过 80 字截断加省略号。 */
export function truncateEntityDesc(
  description: unknown,
  max: number = MEM_DETAIL_ENTITY_DESC_MAX,
): string {
  const text = String(description || '');
  if (text.length <= max) return text;
  return `${text.slice(0, max)}\u2026`;
}

export function isImageMeta(meta: Record<string, unknown> | null | undefined, path: string): boolean {
  const contentType = String((meta || {}).content_type || '');
  return contentType.indexOf('image/') === 0 || /\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(String(path || ''));
}

export function imageDataUrl(resp: unknown): string {
  const data = (resp || {}) as { content_base64?: unknown; content_type?: unknown };
  const base64 = String(data.content_base64 || '');
  if (!base64) return '';
  return `data:${String(data.content_type || 'image/png')};base64,${base64}`;
}
