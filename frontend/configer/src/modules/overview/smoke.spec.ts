// 六个简单模块的挂载冒烟测试：确认模板在真实 store / API 形状下能渲染。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { flushPromises, mount } from '@vue/test-utils';
import type { FrontendApi } from '@/api/types';
import { useConfigStore } from '@/stores/config';
import { useOverviewStore } from '@/stores/overview';
import OverviewModule from './OverviewModule.vue';
import AdvancedModule from '@/modules/advanced/AdvancedModule.vue';
import RuntimeModule from '@/modules/runtime/RuntimeModule.vue';
import TriggersModule from '@/modules/triggers/TriggersModule.vue';
import SkillsModule from '@/modules/skills/SkillsModule.vue';
import McpModule from '@/modules/mcp/McpModule.vue';
import TriggerEditorModal from '@/modules/triggers/TriggerEditorModal.vue';
import McpEditorModal from '@/modules/mcp/McpEditorModal.vue';

const CONFIG = {
  public: {
    AGENT_NAME: 'faust',
    TTS_MODE: 'openai',
    ASR_MODE: 'whisper',
    MODEL_TYPE: 'live2d',
    LIVE2D_MODEL_PATH: 'C:/models/faust/faust.model3.json',
    CUSTOM_KNOB: 'on',
    ANOTHER_KNOB: 5,
  },
  private: { CUSTOM_SECRET: '***' },
};

function installApi(): void {
  const api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string) => {
      if (path === '/faust/admin/config') return JSON.parse(JSON.stringify(CONFIG));
      if (path === '/faust/admin/runtime') {
        return { runtime: { current_agent: 'faust' } };
      }
      if (path === '/faust/admin/plugins') return { items: [{ id: 'p1', enabled: true }] };
      if (path === '/faust/admin/services') {
        return {
          items: [
            { key: 'tts', name: 'TTS', is_running: true, port: 5000 },
            { key: 'asr', name: 'ASR', is_running: false },
          ],
        };
      }
      if (path === '/faust/admin/services/tts') {
        return { item: { key: 'tts', log_tail: 'boot ok' } };
      }
      if (path === '/faust/admin/log/recent-errors') {
        return { errors: [{ timestamp: 'ts', name: 'E', message: 'boom' }] };
      }
      if (path === '/faust/admin/providers') return { main_model: 'openai::gpt-x' };
      if (path === '/faust/admin/triggers') {
        return {
          items: [
            {
              id: 'daily',
              type: 'interval',
              run_background: false,
              lifespan: null,
              recall_description: '日报',
            },
          ],
        };
      }
      if (path === '/faust/admin/skills') {
        return { items: [{ slug: 'web-search', version: '1.0.0', enabled: true }] };
      }
      if (path === '/faust/admin/skills/web-search') {
        return {
          detail: {
            slug: 'web-search',
            enabled: true,
            installed_at: '2026-01-01T00:00:00Z',
            source: 'zip',
            path: '/skills/web-search',
            skill_md: '# hi',
            meta: { name: '搜索', tags: ['web'] },
            files: ['SKILL.md', 'run.py'],
          },
        };
      }
      if (path === '/faust/admin/mcp/servers') {
        return {
          items: [
            {
              server_id: 'filesystem',
              enabled: true,
              transport: 'stdio',
              status: 'running',
              tool_count: 2,
              tools: [{ name: 'read', title: '读', description: '读文件' }],
              headers: {},
            },
          ],
        };
      }
      return { ok: true };
    }),
  } as unknown as FrontendApi;
  window.api = api;
  vi.spyOn(window, 'confirm').mockReturnValue(true);
}

let pinia: ReturnType<typeof createPinia>;

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
  installApi();
});

async function mountModule(component: Parameters<typeof mount>[0]): Promise<ReturnType<typeof mount>> {
  const wrapper = mount(component, { global: { plugins: [pinia] } });
  await flushPromises();
  return wrapper;
}

/** 让下一次 configRequest 返回指定结果（用于更新流程的状态机测试）。 */
function mockRequestOnce(result: unknown): void {
  const api = window.api as unknown as {
    configRequest: { mockResolvedValueOnce: (value: unknown) => void };
  };
  api.configRequest.mockResolvedValueOnce(result);
}

describe('module smoke', () => {
  it('overview renders summary, stats, update card, data dir and error logs', async () => {
    const store = useOverviewStore();
    await store.loadOverview();
    const wrapper = await mountModule(OverviewModule);

    const text = wrapper.text();
    expect(text).toContain('运行概览');
    expect(text).toContain('faust');
    expect(text).toContain('openai::gpt-x');
    expect(text).toContain('系统状态');
    expect(text).toContain('1/1');
    expect(text).toContain('1/2');
    expect(text).toContain('打开直播控制台');
    expect(text).toContain('当前版本: -');
    expect(text).toContain('检查更新');
    expect(text).toContain('手动选择更新包');
    expect(text).toContain('gh-proxy.com');
    expect(text).toContain('打开数据目录');
    expect(text).toContain('ts [E] boom');
  });

  it('advanced lists unclassified fields and the log panel entry', async () => {
    const config = useConfigStore();
    await config.loadConfig();
    const wrapper = await mountModule(AdvancedModule);

    const text = wrapper.text();
    expect(text).toContain('CUSTOM_KNOB');
    expect(text).toContain('ANOTHER_KNOB');
    expect(text).not.toContain('LIVE2D_MODEL_PATH');
    expect(text).toContain('日志面板');
    expect(text).toContain('打开日志面板');
  });

  it('runtime renders service table and log area', async () => {
    const wrapper = await mountModule(RuntimeModule);
    const text = wrapper.text();
    expect(text).toContain('运行时控制');
    expect(text).toContain('服务列表');
    expect(text).toContain('运行中');
    expect(text).toContain('5000');
    expect(text).toContain('查看');
    expect(text).toContain('服务日志');
  });

  it('triggers renders rows and action buttons', async () => {
    const wrapper = await mountModule(TriggersModule);
    const text = wrapper.text();
    expect(text).toContain('触发器操作');
    expect(text).toContain('daily');
    expect(text).toContain('前台');
    expect(text).toContain('未设置');
    expect(text).toContain('触发器列表');
  });

  it('skills renders list, then detail after selecting a skill', async () => {
    const wrapper = await mountModule(SkillsModule);
    expect(wrapper.text()).toContain('web-search');
    expect(wrapper.text()).toContain('请选择技能查看详情。');

    await wrapper.findAll('tbody tr')[0].trigger('click');
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('技能基本信息');
    expect(text).toContain('SKILL.md');
    expect(text).toContain('文档');
    expect(text).toContain('web');
  });

  it('mcp renders server list, status line and detail tables', async () => {
    const wrapper = await mountModule(McpModule);
    const text = wrapper.text();
    expect(text).toContain('服务器列表');
    expect(text).toContain('filesystem | stdio | 运行中 | tools=2');
    expect(text).toContain('查看日志');
    expect(text).toContain('工具列表');
    expect(text).toContain('读文件');
  });

  it('overview check update only records runtimeUpdate after a successful check', async () => {
    const store = useOverviewStore();
    expect(store.runtimeUpdate).toBeNull();
    expect(store.updateResult).toEqual({ kind: 'none' });

    mockRequestOnce({
      status: 'ok',
      has_update: true,
      current_tag: 'v1.0.0',
      latest_tag: 'v1.1.0',
      latest_version: '1.1.0',
      release_body: 'r'.repeat(600),
      asset_name: 'faust.zip',
    });
    await store.checkUpdate();

    expect(store.runtimeUpdate?.current_tag).toBe('v1.0.0');
    const result = store.updateResult;
    expect(result.kind).toBe('available');
    if (result.kind === 'available') {
      expect(result.latestTag).toBe('v1.1.0');
      expect(result.releaseBody).toHaveLength(500);
      expect(result.assetName).toBe('faust.zip');
    }
  });

  it('overview check update keeps runtimeUpdate empty on backend error', async () => {
    const store = useOverviewStore();
    mockRequestOnce({ status: 'error', error: '网络异常' });
    await store.checkUpdate();

    expect(store.runtimeUpdate).toBeNull();
    expect(store.updateResult).toEqual({ kind: 'error', text: '网络异常' });
    expect(store.checking).toBe(false);
  });

  it('trigger editor submits a payload and switches dynamic fields', async () => {
    const onSubmit = vi.fn(async (_payload: Record<string, unknown>) => {});
    const wrapper = mount(TriggerEditorModal, {
      props: { initial: null, onSubmit },
      global: { plugins: [pinia] },
    });

    expect(wrapper.text()).toContain('间隔秒数');
    await wrapper.find('select.select').setValue('datetime');
    expect(wrapper.text()).toContain('触发时间');
    await wrapper.find('select.select').setValue('py-eval');
    expect(wrapper.text()).toContain('eval 表达式');

    const inputs = wrapper.findAll('input.input');
    await inputs[0].setValue('daily-report');
    await wrapper.findAll('button').find((button) => button.text() === '保存')?.trigger('click');
    await flushPromises();

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toMatchObject({
      id: 'daily-report',
      type: 'py-eval',
      recall_description: '',
      run_background: false,
      priority: 'normal',
    });
  });

  it('mcp editor starts with stdio defaults and switches to HTTP fields', async () => {
    const wrapper = mount(McpEditorModal, {
      props: { initial: null, onSubmit: vi.fn(async () => {}) },
      global: { plugins: [pinia] },
    });

    // 旧 syncUi 在 stdio 下也会把隐藏的 URL 标签写成 Streamable HTTP URL。
    expect(wrapper.text()).toContain('Streamable HTTP URL');
    expect(wrapper.text()).toContain('自定义 stdio server');
    const command = wrapper.find('input[placeholder="例如 node、python、npx"]');
    expect((command.element as HTMLInputElement).value).toBe('node');
    expect((command.element as HTMLInputElement).disabled).toBe(true);

    const radios = wrapper.findAll('input[type="radio"]');
    await radios[1].setValue();
    expect(wrapper.text()).toContain('SSE URL');
    expect(wrapper.find('textarea[placeholder="Authorization: Bearer xxxxx"]').exists()).toBe(true);

    await radios[2].setValue();
    expect(wrapper.text()).toContain('Streamable HTTP URL');
  });
});
