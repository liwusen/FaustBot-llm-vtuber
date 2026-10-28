import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import {
  MEM_COLUMN_DEFS,
  MEM_VIZ_TABS,
  MEM_VIEWS,
  useMemoryStore,
} from '@/stores/memory';
import {
  MEM_COLUMNS_KEY,
  MEM_TREE_KEY,
  MEM_VIEW_KEY,
  MEM_VIZ_TAB_KEY,
} from '@/memory/persist';

describe('memory store：默认值与 localStorage 键名', () => {
  beforeEach(() => {
    window.localStorage.clear();
    setActivePinia(createPinia());
    // 检索类动作会发请求；store 单测只关心同步状态与持久化
    (window as unknown as { api: unknown }).api = {
      configRequest: vi.fn().mockResolvedValue({ items: [] }),
    };
  });

  afterEach(() => {
    delete (window as unknown as { api?: unknown }).api;
  });

  it('键名逐字沿用旧实现', () => {
    expect(MEM_TREE_KEY).toBe('faustbot.memory.tree');
    expect(MEM_VIEW_KEY).toBe('faustbot.memory.view');
    expect(MEM_COLUMNS_KEY).toBe('faustbot.memory.columns');
    expect(MEM_VIZ_TAB_KEY).toBe('faustbot.memory.viztab');
  });

  it('视图/可视化 tab/列定义与旧文案一致', () => {
    expect(MEM_VIEWS.map((item) => `${item.id}:${item.label}`)).toEqual([
      'list:列表',
      'graph:图谱',
    ]);
    expect(MEM_VIZ_TABS.map((item) => `${item.id}:${item.label}`)).toEqual([
      'cloud:词云',
      'timeline:时间线',
      'treemap:Treemap',
    ]);
    expect(MEM_COLUMN_DEFS.map((item) => `${item.id}:${item.label}`)).toEqual([
      'tags:标签',
      'updated:更新',
      'size:规模',
      'declared_by:创建者',
      'indexed:索引',
      'score_patch:权重',
      'content_type:类型',
    ]);
  });

  it('无持久化时使用旧默认值', () => {
    const store = useMemoryStore();
    expect(store.expanded).toEqual(['/']);
    expect(store.columns).toEqual(['tags', 'updated', 'size']);
    expect(store.view).toBe('list');
    expect(store.vizTab).toBe('cloud');
    expect(store.sort).toEqual({ by: 'updated_at', order: 'desc' });
    expect(store.page).toBe(0);
    expect(store.selection).toEqual({ kind: 'dir', path: '/' });
    expect(store.clipboard).toBeNull();
    expect(store.editor).toBeNull();
    expect(store.searchResults).toBeNull();
    expect(store.filter).toEqual({
      query: '',
      tags: [],
      tagLogic: 'AND',
      dateFrom: '',
      dateTo: '',
      sortBy: 'relevance',
      sortOrder: 'desc',
    });
  });

  it('读取已保存的展开/列/视图/tab', () => {
    window.localStorage.setItem(MEM_TREE_KEY, JSON.stringify(['/', '/a']));
    window.localStorage.setItem(MEM_COLUMNS_KEY, JSON.stringify(['tags', 'indexed']));
    window.localStorage.setItem(MEM_VIEW_KEY, JSON.stringify('graph'));
    window.localStorage.setItem(MEM_VIZ_TAB_KEY, JSON.stringify('treemap'));
    const store = useMemoryStore();
    expect(store.expanded).toEqual(['/', '/a']);
    expect(store.columns).toEqual(['tags', 'indexed']);
    expect(store.view).toBe('graph');
    expect(store.vizTab).toBe('treemap');
  });

  it('非法/空持久化值回退默认，未知列被过滤', () => {
    window.localStorage.setItem(MEM_TREE_KEY, JSON.stringify([]));
    window.localStorage.setItem(MEM_COLUMNS_KEY, JSON.stringify(['bogus']));
    window.localStorage.setItem(MEM_VIEW_KEY, JSON.stringify('nope'));
    window.localStorage.setItem(MEM_VIZ_TAB_KEY, JSON.stringify('nope'));
    const store = useMemoryStore();
    expect(store.expanded).toEqual(['/']);
    expect(store.columns).toEqual(['tags', 'updated', 'size']);
    expect(store.view).toBe('list');
    expect(store.vizTab).toBe('cloud');
  });

  it('toggleExpanded / toggleColumn 立即写回 localStorage', () => {
    const store = useMemoryStore();
    store.toggleExpanded('/a');
    expect(JSON.parse(window.localStorage.getItem(MEM_TREE_KEY) as string)).toEqual(['/', '/a']);
    store.toggleExpanded('/a');
    expect(JSON.parse(window.localStorage.getItem(MEM_TREE_KEY) as string)).toEqual(['/']);

    store.toggleColumn('tags', false);
    expect(JSON.parse(window.localStorage.getItem(MEM_COLUMNS_KEY) as string)).toEqual([
      'updated',
      'size',
    ]);
    store.toggleColumn('indexed', true);
    expect(JSON.parse(window.localStorage.getItem(MEM_COLUMNS_KEY) as string)).toEqual([
      'updated',
      'size',
      'indexed',
    ]);
  });

  it('applySort 只改排序，不触发检索条件之外的副作用', () => {
    const store = useMemoryStore();
    store.applySort('relevance', 'asc');
    expect(store.filter.sortBy).toBe('relevance');
    expect(store.sort).toEqual({ by: 'score', order: 'asc' });
    expect(store.searchDraft.sortBy).toBe('relevance');
    expect(store.searchDraft.sortOrder).toBe('asc');
  });

  it('toggleTag 追加/移除（同步草稿）', () => {
    const store = useMemoryStore();
    store.toggleTag('a');
    expect(store.filter.tags).toEqual(['a']);
    expect(store.searchDraft.tags).toEqual(['a']);
    store.toggleTag('b');
    expect(store.filter.tags).toEqual(['a', 'b']);
    store.toggleTag('a');
    expect(store.filter.tags).toEqual(['b']);
  });
});
