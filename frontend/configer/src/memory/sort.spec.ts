import { describe, expect, it } from 'vitest';
import {
  formatRelative,
  formatScore,
  nextSort,
  parseMemoryDate,
  scaleLevel,
  scaleX,
  sortRows,
} from '@/memory/sort';
import type { MemRow } from '@/memory/types';

function row(partial: Partial<MemRow> & { name: string }): MemRow {
  return {
    type: 'file',
    path: `/x/${partial.name}`,
    dir: '/x',
    description: '',
    tags: [],
    updated_at: '',
    declared_by: '',
    score_patch: 0,
    chunk_count: 0,
    indexed: false,
    content_type: '',
    ...partial,
  };
}

describe('sortRows', () => {
  it('名称升/降序（忽略大小写，按 locale 比较）', () => {
    const rows = [row({ name: 'b' }), row({ name: 'A' }), row({ name: 'c' })];
    expect(sortRows(rows, { by: 'name', order: 'asc' }).map((item) => item.name)).toEqual([
      'A',
      'b',
      'c',
    ]);
    expect(sortRows(rows, { by: 'name', order: 'desc' }).map((item) => item.name)).toEqual([
      'c',
      'b',
      'A',
    ]);
  });

  it('规模按 chunk_count 数值排序', () => {
    const rows = [
      row({ name: 'small', chunk_count: 2 }),
      row({ name: 'big', chunk_count: 20 }),
      row({ name: 'mid', chunk_count: 9 }),
    ];
    expect(sortRows(rows, { by: 'size', order: 'desc' }).map((item) => item.name)).toEqual([
      'big',
      'mid',
      'small',
    ]);
  });

  it('权重 / 相关度数值排序', () => {
    const rows = [
      row({ name: 'a', score_patch: 0.2 }),
      row({ name: 'b', score_patch: 1.5 }),
    ];
    expect(sortRows(rows, { by: 'score_patch', order: 'desc' })[0].name).toBe('b');

    const scored = [row({ name: 'low', score: 0.1 }), row({ name: 'high', score: 0.9 })];
    expect(sortRows(scored, { by: 'score', order: 'desc' })[0].name).toBe('high');
  });

  it('更新时间按字符串排序（ISO 可直接比较）', () => {
    const rows = [
      row({ name: 'old', updated_at: '2025-01-01T00:00:00Z' }),
      row({ name: 'new', updated_at: '2026-05-01T00:00:00Z' }),
    ];
    expect(sortRows(rows, { by: 'updated_at', order: 'desc' })[0].name).toBe('new');
    expect(sortRows(rows, { by: 'updated_at', order: 'asc' })[0].name).toBe('old');
  });

  it('相等时按名称升序（与顺序方向无关）', () => {
    const rows = [row({ name: 'b', chunk_count: 3 }), row({ name: 'a', chunk_count: 3 })];
    expect(sortRows(rows, { by: 'size', order: 'desc' }).map((item) => item.name)).toEqual([
      'a',
      'b',
    ]);
  });

  it('目录行缺 updated_at 时排在文件之后（旧实现保留行为）', () => {
    const dir = row({ name: 'dir', type: 'dir', path: '/dir' });
    const file = row({ name: 'file', updated_at: '2026-05-01T00:00:00Z' });
    expect(sortRows([dir, file], { by: 'updated_at', order: 'desc' }).map((item) => item.name)).toEqual([
      'file',
      'dir',
    ]);
  });
});

describe('nextSort', () => {
  it('未激活列：name 升序，其余降序', () => {
    expect(nextSort({ by: 'updated_at', order: 'desc' }, 'name')).toEqual({
      by: 'name',
      order: 'asc',
    });
    expect(nextSort({ by: 'name', order: 'asc' }, 'size')).toEqual({
      by: 'size',
      order: 'desc',
    });
  });

  it('已激活列翻转方向', () => {
    expect(nextSort({ by: 'name', order: 'asc' }, 'name')).toEqual({ by: 'name', order: 'desc' });
    expect(nextSort({ by: 'score', order: 'desc' }, 'score')).toEqual({
      by: 'score',
      order: 'asc',
    });
  });
});

describe('parseMemoryDate', () => {
  it('纯日期补 UTC 零点', () => {
    expect(parseMemoryDate('2026-09-10')?.toISOString()).toBe('2026-09-10T00:00:00.000Z');
  });

  it('无时区补 Z', () => {
    expect(parseMemoryDate('2026-09-10T03:00:00')?.toISOString()).toBe(
      '2026-09-10T03:00:00.000Z',
    );
  });

  it('保留已有偏移', () => {
    expect(parseMemoryDate('2026-09-10T03:00:00+08:00')?.toISOString()).toBe(
      '2026-09-09T19:00:00.000Z',
    );
  });

  it('空值与非法值返回 null', () => {
    expect(parseMemoryDate('')).toBeNull();
    expect(parseMemoryDate(null)).toBeNull();
    expect(parseMemoryDate('not-a-date')).toBeNull();
  });
});

describe('formatRelative', () => {
  const now = Date.UTC(2026, 8, 10, 12, 0, 0);

  it('分档文案', () => {
    expect(formatRelative(new Date(now - 30 * 1000), now)).toBe('刚刚');
    expect(formatRelative(new Date(now - 5 * 60 * 1000), now)).toBe('5 分钟前');
    expect(formatRelative(new Date(now - 3 * 3600 * 1000), now)).toBe('3 小时前');
    expect(formatRelative(new Date(now - 2 * 86400 * 1000), now)).toBe('2 天前');
    expect(formatRelative(new Date(now - 60 * 86400 * 1000), now)).toBe('2 个月前');
    expect(formatRelative(new Date(now - 800 * 86400 * 1000), now)).toBe('2 年前');
  });
});

describe('刻度尺', () => {
  it('chunk 档位', () => {
    expect(scaleLevel(0)).toBe(0);
    expect(scaleLevel(1)).toBe(1);
    expect(scaleLevel(4)).toBe(1);
    expect(scaleLevel(5)).toBe(2);
    expect(scaleLevel(19)).toBe(2);
    expect(scaleLevel(20)).toBe(3);
  });

  it('横轴位置：越新越靠左，365 天封顶', () => {
    const now = Date.UTC(2026, 8, 10);
    expect(scaleX(new Date(now), now)).toBe(2);
    expect(scaleX(new Date(now - 365 * 86400 * 1000), now)).toBeCloseTo(54, 5);
    expect(scaleX(new Date(now - 400 * 86400 * 1000), now)).toBeCloseTo(54, 5);
  });

  it('相关度两位小数', () => {
    expect(formatScore(0.8333)).toBe('0.83');
    expect(formatScore(undefined)).toBe('0.00');
  });
});
