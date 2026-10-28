// 模块冒烟：确认三栏结构与关键文案能真实挂载（不做全量 DOM 快照）。
// 图谱/编辑器/ECharts 需要 canvas 与真实实例，此处不覆盖（单测见 logic spec）。

import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import MemoryModule from './MemoryModule.vue';
import { useMemoryStore } from '@/stores/memory';
import type { KbNode } from '@/memory/types';

const tree: KbNode = {
  path: '/',
  name: '/',
  type: 'dir',
  children: [
    {
      path: '/notes',
      name: 'notes',
      type: 'dir',
      description: '',
      children: [{ path: '/notes/a.md', name: 'a.md', type: 'file', tags: ['日报'] }],
    },
    { path: '/readme.md', name: 'readme.md', type: 'file', tags: [] },
  ],
};

function stubApi(): void {
  (window as unknown as { api: unknown }).api = {
    configRequest: vi.fn((method: string, path: string) => {
      if (path === '/faust/memory/tree') return Promise.resolve({ tree });
      if (path === '/faust/memory/tasks') return Promise.resolve({ items: [] });
      return Promise.resolve({});
    }),
  };
}

describe('MemoryModule 冒烟', () => {
  beforeEach(() => {
    window.localStorage.clear();
    setActivePinia(createPinia());
    stubApi();
  });

  it('渲染三栏 + 面包屑 + 可视化卡片', async () => {
    const wrapper = mount(MemoryModule);
    await flushPromises();

    expect(wrapper.find('.mem-shell-wrap').exists()).toBe(true);
    expect(wrapper.find('.mem-tree').exists()).toBe(true);
    expect(wrapper.find('.mem-main').exists()).toBe(true);
    expect(wrapper.find('.mem-detail').exists()).toBe(true);
    expect(wrapper.find('.mem-viz-card').exists()).toBe(true);

    // 面包屑根按钮与视图切换
    expect(wrapper.find('.mem-crumbs').text()).toContain('记忆');
    expect(wrapper.findAll('.mem-views .seg-item').map((item) => item.text())).toEqual([
      '列表',
      '图谱',
    ]);

    // 树：根行 + 目录/文件行（根默认展开）
    const rows = wrapper.findAll('.mem-tree-row');
    expect(rows).toHaveLength(3);
    expect(rows[0].text()).toContain('▪ /');
    expect(rows[1].text()).toContain('▪ notes');
    expect(rows[1].find('.mem-tree-count').text()).toBe('1');
    expect(rows[2].find('.mem-tree-label').text()).toBe('▪ readme.md');

    // 可视化：默认词云，有标签数据
    expect(wrapper.find('.mem-viz-title').text()).toBe('可视化');
    expect(wrapper.findAll('.mem-viz-tabs .seg-item').map((item) => item.text())).toEqual([
      '词云',
      '时间线',
      'Treemap',
    ]);

    // 详情栏：目录概览
    expect(wrapper.find('.mem-detail').text()).toContain('概览');
    expect(wrapper.find('.mem-detail').text()).toContain('子目录');

    wrapper.unmount();
  });

  it('结果集表格：相关度列、标签 chip、刻度尺与分页状态', async () => {
    const wrapper = mount(MemoryModule);
    await flushPromises();

    const store = useMemoryStore();
    store.filter = { ...store.filter, query: '日报' };
    store.searchResults = [
      {
        type: 'file',
        path: '/notes/a.md',
        name: 'a.md',
        dir: '/notes',
        description: '日报',
        tags: ['日报', '周报'],
        updated_at: new Date().toISOString(),
        declared_by: 'config-center',
        score_patch: 0.5,
        chunk_count: 6,
        indexed: true,
        content_type: 'text/markdown',
        score: 0.75,
      },
    ];
    await flushPromises();

    const headers = wrapper.findAll('.mem-list-table th').map((th) => th.text());
    expect(headers[0]).toContain('名称');
    expect(headers[1]).toContain('相关度');
    expect(headers.join('|')).toContain('标签');
    expect(headers.join('|')).toContain('更新');
    expect(headers.join('|')).toContain('规模');

    const cells = wrapper.findAll('.mem-list-table tbody td');
    expect(cells[1].text()).toBe('0.75');
    expect(cells[1].classes()).toContain('mem-col-score');
    expect(wrapper.findAll('.mem-list-table tbody .mem-chip')).toHaveLength(2);
    expect(wrapper.find('.mem-list-table .mem-scale').exists()).toBe(true);
    expect(wrapper.find('.mem-list-status').text()).toContain('命中 1 条');
    expect(wrapper.findAll('.mem-list-table tbody tr.selected')).toHaveLength(0);

    wrapper.unmount();
  });
});
