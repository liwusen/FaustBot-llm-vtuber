import { describe, expect, it } from 'vitest';
import {
  MEM_LIST_PAGE_SIZE,
  clampPage,
  pageSlice,
  totalPagesOf,
} from '@/memory/rows';

describe('分页', () => {
  it('页大小 200', () => {
    expect(MEM_LIST_PAGE_SIZE).toBe(200);
    expect(totalPagesOf(0)).toBe(1);
    expect(totalPagesOf(1)).toBe(1);
    expect(totalPagesOf(200)).toBe(1);
    expect(totalPagesOf(201)).toBe(2);
    expect(totalPagesOf(401)).toBe(3);
  });

  it('页码钳制到 [0, totalPages-1]，非法值归零', () => {
    expect(clampPage(-3, 2)).toBe(0);
    expect(clampPage(0, 2)).toBe(0);
    expect(clampPage(1, 2)).toBe(1);
    expect(clampPage(9, 2)).toBe(1);
    expect(clampPage(Number.NaN, 2)).toBe(0);
    expect(clampPage(1.7, 3)).toBe(1);
  });

  it('切片：第 2 页只给余下 1 条', () => {
    const rows = Array.from({ length: 201 }, (_, index) => index);
    const first = pageSlice(rows, 0);
    expect(first.page).toBe(0);
    expect(first.start).toBe(0);
    expect(first.rows).toHaveLength(200);

    const second = pageSlice(rows, 1);
    expect(second.page).toBe(1);
    expect(second.start).toBe(200);
    expect(second.rows).toEqual([200]);
  });

  it('空列表只有第 1 页且为空', () => {
    const slice = pageSlice([], 5);
    expect(slice.page).toBe(0);
    expect(slice.rows).toEqual([]);
  });
});
