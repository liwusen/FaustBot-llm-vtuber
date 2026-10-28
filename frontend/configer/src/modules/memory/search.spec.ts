import { describe, expect, it } from 'vitest';
import {
  MEM_SEARCH_LIMIT,
  advancedCount,
  buildSearchPayload,
  defaultFilter,
  filterActive,
  isSearchDirty,
  parseTags,
  sortFromPayload,
  tagsKey,
} from '@/memory/search';

describe('buildSearchPayload', () => {
  it('默认条件：关键词空/tags 空 → null，取数按更新时间', () => {
    const payload = buildSearchPayload(defaultFilter(), null);
    expect(payload).toEqual({
      query: null,
      tags: null,
      scope: null,
      date_from: null,
      date_to: null,
      sort_by: 'updated_at',
      sort_order: 'desc',
      tag_logic: 'AND',
      top_k: MEM_SEARCH_LIMIT,
    });
  });

  it('关键词 trim 后保留，排序沿用条件', () => {
    const filter = {
      ...defaultFilter(),
      query: '  周报  ',
      sortBy: 'updated_at',
      sortOrder: 'asc',
      tags: ['a', 'b'],
      dateFrom: '2026-01-01',
      dateTo: '2026-02-01',
    };
    const payload = buildSearchPayload(filter, '/notes');
    expect(payload.query).toBe('周报');
    expect(payload.tags).toEqual(['a', 'b']);
    expect(payload.scope).toBe('/notes');
    expect(payload.date_from).toBe('2026-01-01');
    expect(payload.date_to).toBe('2026-02-01');
    expect(payload.sort_by).toBe('updated_at');
    expect(payload.sort_order).toBe('asc');
    expect(payload.top_k).toBe(200);
  });

  it('无关键词时忽略「相关度」，强制 updated_at + desc', () => {
    const filter = { ...defaultFilter(), sortBy: 'relevance', sortOrder: 'asc', tags: ['x'] };
    const payload = buildSearchPayload(filter, null);
    expect(payload.sort_by).toBe('updated_at');
    expect(payload.sort_order).toBe('desc');
  });

  it('tag_logic 透传', () => {
    const filter = { ...defaultFilter(), tags: ['x'], tagLogic: 'OR' };
    expect(buildSearchPayload(filter, null).tag_logic).toBe('OR');
  });
});

describe('sortFromPayload', () => {
  it('相关度 → score，其余 → updated_at', () => {
    expect(
      sortFromPayload({ ...buildSearchPayload(defaultFilter(), null), sort_by: 'relevance' }),
    ).toEqual({ by: 'score', order: 'desc' });
    expect(sortFromPayload(buildSearchPayload(defaultFilter(), null))).toEqual({
      by: 'updated_at',
      order: 'desc',
    });
    expect(
      sortFromPayload({
        ...buildSearchPayload(defaultFilter(), null),
        sort_by: 'updated_at',
        sort_order: 'asc',
      }),
    ).toEqual({ by: 'updated_at', order: 'asc' });
  });
});

describe('isSearchDirty', () => {
  const applied = defaultFilter();

  it('完全相同 / 空白等价不算脏', () => {
    expect(isSearchDirty({ ...applied }, applied)).toBe(false);
    expect(isSearchDirty({ ...applied, query: '   ' }, applied)).toBe(false);
  });

  it('任一条件变化算脏（标签忽略顺序与大小写）', () => {
    expect(isSearchDirty({ ...applied, query: 'x' }, applied)).toBe(true);
    expect(
      isSearchDirty({ ...applied, tags: ['B', 'a'] }, { ...applied, tags: ['a', 'b'] }),
    ).toBe(false);
    expect(isSearchDirty({ ...applied, tagLogic: 'OR' }, applied)).toBe(true);
    expect(isSearchDirty({ ...applied, dateFrom: '2026-01-01' }, applied)).toBe(true);
    expect(isSearchDirty({ ...applied, sortBy: 'updated_at' }, applied)).toBe(true);
    expect(isSearchDirty({ ...applied, sortOrder: 'asc' }, applied)).toBe(true);
  });
});

describe('advancedCount', () => {
  it('默认 0；逻辑/日期/排序各计 1', () => {
    expect(advancedCount(defaultFilter())).toBe(0);
    expect(advancedCount({ ...defaultFilter(), tagLogic: 'OR' })).toBe(1);
    expect(advancedCount({ ...defaultFilter(), dateTo: '2026-01-01' })).toBe(1);
    expect(advancedCount({ ...defaultFilter(), sortBy: 'updated_at' })).toBe(1);
    expect(
      advancedCount({
        ...defaultFilter(),
        tagLogic: 'OR',
        dateFrom: '2026-01-01',
        sortBy: 'updated_at',
        sortOrder: 'asc',
      }),
    ).toBe(3);
  });
});

describe('parseTags / tagsKey', () => {
  it('逗号分隔：去空、去重、保序', () => {
    expect(parseTags('a, b ,, A ,a')).toEqual(['a', 'b', 'A']);
    expect(parseTags('')).toEqual([]);
  });

  it('比较键忽略顺序与大小写', () => {
    expect(tagsKey(['B', 'a'])).toBe(tagsKey(['A', 'b']));
    expect(tagsKey([])).toBe('');
  });
});

describe('filterActive', () => {
  it('关键词/标签/日期任一存在即生效', () => {
    expect(filterActive(defaultFilter())).toBe(false);
    expect(filterActive({ ...defaultFilter(), query: '  ' })).toBe(false);
    expect(filterActive({ ...defaultFilter(), query: 'x' })).toBe(true);
    expect(filterActive({ ...defaultFilter(), tags: ['x'] })).toBe(true);
    expect(filterActive({ ...defaultFilter(), dateFrom: '2026-01-01' })).toBe(true);
  });
});
