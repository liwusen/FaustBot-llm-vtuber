// 列表排序 / 时间格式化 / 「更新刻度尺」档位：逐条移植 list.js 的 memSortRows 与相关工具。

import type { MemRow, MemSort } from '@/memory/types';

/** 「更新刻度尺」高度/颜色档位，索引 = chunk_count 档位（0 / 1-4 / 5-19 / ≥20）。 */
export const MEM_SCALE_LEVELS = [
  { height: 3, color: '#c3d0e3' },
  { height: 5, color: '#9ab4e8' },
  { height: 8, color: '#5b87d6' },
  { height: 10, color: '#2f5fb3' },
] as const;

/** 列 id → memSort.by 的排序键（不在表内 = 该列不可排序）。 */
export const MEM_LIST_SORT_KEYS: Record<string, string> = {
  name: 'name',
  updated: 'updated_at',
  size: 'size',
  score_patch: 'score_patch',
  score: 'score',
};

/** 旧实现的就地排序：返回值即入参数组。 */
export function sortRows(rows: MemRow[], sort: MemSort): MemRow[] {
  const order = sort.order === 'asc' ? 1 : -1;
  rows.sort((a, b) => {
    let av: string | number;
    let bv: string | number;
    if (sort.by === 'name') {
      av = String(a.name || '').toLowerCase();
      bv = String(b.name || '').toLowerCase();
      return av.localeCompare(bv) * order;
    }
    if (sort.by === 'size') {
      av = Number(a.chunk_count || 0);
      bv = Number(b.chunk_count || 0);
    } else if (sort.by === 'score_patch') {
      av = Number(a.score_patch || 0);
      bv = Number(b.score_patch || 0);
    } else if (sort.by === 'score') {
      av = Number(a.score || 0);
      bv = Number(b.score || 0);
    } else {
      const key = sort.by as keyof MemRow;
      av = String(a[key] || '');
      bv = String(b[key] || '');
    }
    if (av === bv) return String(a.name || '').localeCompare(String(b.name || ''));
    return av > bv ? order : -order;
  });
  return rows;
}

/** 表头点击：激活列方向翻转；未激活列 name 升序、其余降序。 */
export function nextSort(sort: MemSort, sortKey: string): MemSort {
  const active = sort.by === sortKey;
  if (!active) {
    return { by: sortKey, order: sortKey === 'name' ? 'asc' : 'desc' };
  }
  return { by: sortKey, order: sort.order === 'asc' ? 'desc' : 'asc' };
}

// ── 时间 ──

/** 更新时间解析：纯日期补 `T00:00:00Z`，无时区补 `Z`。 */
export function parseMemoryDate(stamp: unknown): Date | null {
  const raw = String(stamp || '').trim();
  if (!raw) return null;
  let iso = raw;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) iso = `${raw}T00:00:00Z`;
  else if (!/[zZ]$|[+-]\d{2}:?\d{2}$/.test(raw)) iso = `${raw}Z`;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 相对时间文案（与旧实现同一分档）。 */
export function formatRelative(date: Date, now: number = Date.now()): string {
  const minutes = Math.floor((now - date.getTime()) / 60000);
  if (minutes < 1) return '刚刚';
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months} 个月前`;
  return `${Math.floor(days / 365)} 年前`;
}

// ── 刻度尺 ──

/** chunk_count → 档位 0..3（0 块 / 1-4 / 5-19 / ≥20）。 */
export function scaleLevel(chunkCount: unknown): number {
  const count = Number(chunkCount || 0);
  if (count <= 0) return 0;
  if (count <= 4) return 1;
  if (count <= 19) return 2;
  return 3;
}

/** 横轴位置：越靠左越新，365 天铺满 52px。 */
export function scaleX(date: Date, now: number = Date.now()): number {
  const ageDays = Math.max(0, (now - date.getTime()) / 86400000);
  const frac = Math.min(1, ageDays / 365);
  return 2 + frac * 52;
}

/** 相关度固定两位小数。 */
export function formatScore(score: unknown): string {
  return Number(score || 0).toFixed(2);
}
