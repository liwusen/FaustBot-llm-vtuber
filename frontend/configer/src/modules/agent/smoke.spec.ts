// Agent 页挂载冒烟：确认 store 数据形状与模板（列表/状态/文件区）能渲染。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { flushPromises, mount } from '@vue/test-utils';
import type { FrontendApi } from '@/api/types';
import AgentModule from './AgentModule.vue';

const AGENTS = [
  {
    name: 'faust',
    is_current: true,
    core_files: { 'AGENT.md': true },
  },
  { name: 'other', is_current: false },
];

let files: Record<string, unknown> | null = null;

function installApi(): void {
  window.api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string) => {
      if (path === '/faust/admin/agents') return { items: AGENTS };
      if (path === '/faust/admin/agents/faust') {
        return { detail: { agent: AGENTS[0], files } };
      }
      if (path === '/faust/admin/agents/other') {
        return { detail: { agent: AGENTS[1], files: { 'AGENT.md': { content: '', readonly: false } } } };
      }
      return {};
    }),
  } as unknown as FrontendApi;
  vi.spyOn(window, 'confirm').mockReturnValue(true);
}

describe('Agent module smoke', () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    pinia = createPinia();
    setActivePinia(pinia);
    files = {
      'AGENT.md': { content: 'agent', readonly: true },
      'ROLE.md': { content: 'role', readonly: false },
      'COREMEMORY.md': { content: 'core', readonly: false },
      'TASK.md': { content: 'task', readonly: false },
    };
    installApi();
  });

  it('renders toolbar, agent list and file actions', async () => {
    const wrapper = mount(AgentModule, { global: { plugins: [pinia] } });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('Agent 操作');
    expect(text).toContain('从角色卡导入');
    expect(text).toContain('切换为当前');
    expect(text).toContain('删除 Checkpoint');
    expect(text).toContain('Agent 列表');
    expect(text).toContain('faust');
    expect(text).toContain('当前使用');
    expect(text).toContain('备用');
    expect(text).toContain('编辑文件');
    expect(text).toContain('Agent 文件操作');
    expect(text).toContain('编辑 Agent 文件');
    expect(text).toContain('打开 Agent 目录');

    await wrapper.findAll('tbody tr')[1].trigger('click');
    await flushPromises();
    expect(wrapper.text()).toContain('Agent 文件操作');
  });

  it('shows the empty state before an agent has files', async () => {
    files = null;
    const wrapper = mount(AgentModule, { global: { plugins: [pinia] } });
    await flushPromises();
    expect(wrapper.text()).toContain('请选择 Agent 后编辑文件。');
  });
});
