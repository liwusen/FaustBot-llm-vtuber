// 编辑器视图冒烟：状态文案、索引开关、退出/保存按钮（CodeMirror 用轻量 stub 替换）。

import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import { beforeEach, describe, expect, it } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import MemEditor from './MemEditor.vue';
import { useMemoryStore } from '@/stores/memory';

const cmStub = { template: '<div class="cm-stub"></div>' };

describe('MemEditor', () => {
  beforeEach(() => {
    window.localStorage.clear();
    setActivePinia(createPinia());
    (window as unknown as { api: unknown }).api = {
      configRequest: () => Promise.resolve({}),
    };
  });

  it('已保存/未保存状态、元数据行、索引开关与动作按钮', async () => {
    const store = useMemoryStore();
    store.editor = {
      path: '/notes/a.md',
      original: 'hello',
      content: 'hello',
      dirty: false,
      isNew: false,
      index: true,
      meta: { updated_at: '2026-09-10', chunk_count: 2, indexed: true },
    };

    const wrapper = mount(MemEditor, { global: { stubs: { CodeMirrorHost: cmStub } } });

    expect(wrapper.find('.mem-editor-name').text()).toBe('/notes/a.md');
    expect(wrapper.find('.mem-editor-status').text()).toBe('已保存');
    expect(wrapper.find('.mem-editor-status').classes()).not.toContain('dirty');
    expect(wrapper.find('.mem-editor-meta').text()).toContain('更新 2026-09-10');
    expect(wrapper.find('.mem-editor-meta').text()).toContain('分块 2');
    expect(wrapper.find('.cm-stub').exists()).toBe(true);

    const buttons = wrapper.findAll('.mem-editor-bar button').map((btn) => btn.text());
    expect(buttons).toEqual(['退出编辑', '保存']);

    store.updateEditorContent('hello world');
    await nextTick();
    expect(wrapper.find('.mem-editor-status').text()).toBe('未保存');
    expect(wrapper.find('.mem-editor-status').classes()).toContain('dirty');

    await wrapper.find('.mem-editor-meta input[type="checkbox"]').setValue(false);
    expect(store.editor?.index).toBe(false);

    wrapper.unmount();
  });

  it('新建态显示未创建', async () => {
    const store = useMemoryStore();
    store.editor = {
      path: '/new.md',
      original: '',
      content: '',
      dirty: false,
      isNew: true,
      index: true,
      meta: {},
    };
    const wrapper = mount(MemEditor, { global: { stubs: { CodeMirrorHost: cmStub } } });
    expect(wrapper.find('.mem-editor-status').text()).toBe('未创建');
    expect(wrapper.find('.mem-editor-meta').text()).toContain('未创建');
    wrapper.unmount();
  });
});
