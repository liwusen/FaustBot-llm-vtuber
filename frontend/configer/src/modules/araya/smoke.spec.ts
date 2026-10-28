// Araya 页挂载冒烟：概览、设置、触发滑块、最近运行与轨迹。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { flushPromises, mount } from '@vue/test-utils';
import type { FrontendApi } from '@/api/types';
import ArayaModule from './ArayaModule.vue';

const STARTED = '2026-01-01T10:00:00Z';

const STATUS = {
  enabled: true,
  enabled_by_config: false,
  idle_minutes: 30,
  idle_seconds: 600,
  target_agent: 'faust',
  run_in_progress: false,
  last_error: '',
  last_main_activity_at: '2026-01-01 10:00:00',
  updated_at: '2026-01-01 10:05:00',
  last_log: {
    reason: 'manual_from_configer',
    status: 'ok',
    duration_seconds: 12.5,
    started_at_iso: STARTED,
    finished_at_iso: '2026-01-01T10:00:12Z',
    response: '本次整理完成',
  },
};

const TRACE = {
  conversation_id: 'c1',
  reason: 'manual',
  status: 'ok',
  started_at_iso: STARTED,
  messages: [
    { role: 'user', content: '内部指令' },
    { role: 'assistant', content: '开始整理' },
    { role: 'tool', tool_name: 'memory_write', call_id: 'call_1', args: { path: '/a' } },
    {
      role: 'tool_result',
      tool_name: 'memory_write',
      call_id: 'call_1',
      result: 'ok',
      duration_seconds: 1.234,
    },
  ],
};

function installApi(): void {
  window.api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string) => {
      if (path === '/faust/araya/status') return { araya: STATUS };
      if (path === '/faust/araya/trace') return { trace: TRACE };
      return {};
    }),
  } as unknown as FrontendApi;
}

describe('Araya module smoke', () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    pinia = createPinia();
    setActivePinia(pinia);
    installApi();
  });

  it('renders overview, settings, trigger, last run and trace', async () => {
    const wrapper = mount(ArayaModule, { global: { plugins: [pinia] } });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('运行概览');
    expect(text).toContain('等待触发');
    expect(text).toContain('目标 Agent');
    expect(text).toContain('主 Agent 空闲（阈值 30 分钟）');
    expect(text).toContain('距下次自动触发');
    expect(text).toContain('自动维护设置');
    expect(text).toContain('启用自动维护');
    expect(text).toContain('空闲分钟');
    expect(text).toContain('保存设置');
    expect(text).toContain('配置文件里 ARAYA_ENABLED 为 false，这里的开关不会生效。');
    expect(text).toContain('手动触发');
    expect(text).toContain('向右拖动触发 Araya');
    expect(text).toContain('最近一次运行');
    expect(text).toContain('配置中心手动触发');
    expect(text).toContain('成功');
    expect(text).toContain('13 秒');
    expect(text).toContain('本次整理完成');
    expect(text).toContain('上次运行轨迹');
    expect(text).toContain('1 次工具调用');
    expect(text).toContain('Araya 输出');
    expect(text).toContain('开始整理');
    expect(text).toContain('memory_write');
    expect(text).toContain('1.23s');
  });

  it('shows the empty run/trace copy when nothing has run yet', async () => {
    (window.api as unknown as { configRequest: ReturnType<typeof vi.fn> }).configRequest
      .mockImplementation(async (method: string, path: string) => {
        if (path === '/faust/araya/status') {
          return { araya: { enabled: true, idle_minutes: 30, idle_seconds: 0, last_log: null } };
        }
        if (path === '/faust/araya/trace') return { trace: null };
        return {};
      });

    const wrapper = mount(ArayaModule, { global: { plugins: [pinia] } });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('还没有运行记录。等空闲自动触发，或拖动「手动触发」里的滑块跑一次。');
    expect(text).toContain('还没有运行记录。触发一次后，这里会按顺序显示 Araya 的输出与每次工具调用。');
    expect(text).toContain('无记录');
  });
});
