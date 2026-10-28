import { flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FrontendApi } from '@/api/types';
import { AI_GROUPS_TYPED, AI_PUBLIC_KEYS } from '@/config/constants';
import type { ProviderBundle } from '@/config/types';
import { useConfigStore } from '@/stores/config';
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import AiModule from './AiModule.vue';

function defaultPublicConfig(): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const key of AI_PUBLIC_KEYS) values[key] = true;
  return {
    ...values,
    REASONING_CONFIG: 'medium',
    AGENT_NAME: 'Faust',
    EMBED_MODEL: 'text-embedding',
    EMBED_API_BASE: 'https://embed.example.com/v1',
    RERANK_TOP_K: 5,
    BM25_ONLY: false,
    ARAYA_IDLE_MINUTES: 30,
    COMPACT_THRESHOLD_RATIO: 0.8,
    COMPACT_KEEP_ROUNDS: 3,
    COMPACT_COST_CONTROL_ENABLED: false,
    COMPACT_MAX_OUTPUT_TOKENS: 0,
    COMPACT_MAX_INPUT_TOKENS: 0,
    MM_BRIDGE_MAX_SCAN: 8,
    MM_BRIDGE_MAX_PIXELS: 2000000,
  };
}

interface ApiCall {
  method: string;
  path: string;
}

function installApi(
  bundle: Partial<ProviderBundle> = {},
  publicOverrides: Record<string, unknown> = {},
): ApiCall[] {
  const data: ProviderBundle = {
    providers: [],
    main_model: '',
    subagent_models: [],
    resolved_formats: {},
    catalog: {},
    ...bundle,
  };
  const calls: ApiCall[] = [];
  const api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string) => {
      calls.push({ method, path });
      if (method === 'GET' && path === '/faust/admin/config') {
        return {
          public: { ...defaultPublicConfig(), ...publicOverrides },
          private: { SEARCH_API_KEY: '********', EMBED_API_KEY: '' },
        };
      }
      if (method === 'GET' && path === '/faust/admin/providers') {
        return JSON.parse(JSON.stringify(data));
      }
      return { status: 'ok' };
    }),
  } as unknown as FrontendApi;
  window.api = api;
  return calls;
}

interface Page {
  wrapper: ReturnType<typeof mount>;
  config: ReturnType<typeof useConfigStore>;
  ui: ReturnType<typeof useUiStore>;
  pinia: ReturnType<typeof createPinia>;
  calls: ApiCall[];
}

async function setupPage(
  bundle: Partial<ProviderBundle> = {},
  publicOverrides: Record<string, unknown> = {},
): Promise<Page> {
  const pinia = createPinia();
  setActivePinia(pinia);
  const calls = installApi(bundle, publicOverrides);
  const config = useConfigStore();
  await config.loadConfig();
  const wrapper = mount(AiModule, { global: { plugins: [pinia] }, attachTo: document.body });
  await flushPromises();
  return { wrapper, config, ui: useUiStore(), pinia, calls };
}

describe('AiModule page', () => {
  it('renders the group directory and one card per AI_GROUPS entry', async () => {
    const { wrapper } = await setupPage();

    expect(wrapper.findAll('.ai-dir-chip')).toHaveLength(AI_GROUPS_TYPED.length);
    for (const spec of AI_GROUPS_TYPED) {
      expect(wrapper.find(`#group-${spec.id}`).exists()).toBe(true);
    }
    expect(wrapper.find('.ai-dir').attributes('aria-label')).toBe('AI 服务商页分组');
    expect(wrapper.text()).toContain('还没有选择主模型');
    expect(wrapper.text()).toContain('前往「角色」模块');
  });

  it('scrolls to the clicked group and marks that chip active', async () => {
    const { wrapper } = await setupPage();
    const scrollIntoView = vi.fn();
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollIntoView;

    const chips = wrapper.findAll('.ai-dir-chip');
    const target = chips[1];
    await target.trigger('click');

    expect(scrollIntoView).toHaveBeenCalledTimes(1);
    expect(target.classes()).toContain('active');
    expect(chips[0].classes()).not.toContain('active');

    Element.prototype.scrollIntoView = original;
  });

  it('renders the provider table, model table and folded group labels', async () => {
    const { wrapper } = await setupPage({
      providers: [{
        name: 'p',
        base_url: 'https://api.example.com/v1',
        key: 'sk-1',
        models: ['m1'],
        catalog_provider_id: 'deepseek',
      }],
      main_model: 'p::m1',
      catalog: { p: { provider_id: 'deepseek', host_matched: true, routed: 1 } } as unknown as ProviderBundle['catalog'],
    });

    expect(wrapper.text()).toContain('目录 deepseek');
    expect(wrapper.text()).toContain('已设置');
    expect(wrapper.text()).toContain('1 个');
    expect(wrapper.text()).toContain('主模型');
    expect(wrapper.text()).toContain('p · m1');
    expect(wrapper.text()).toContain('自动');
    expect(wrapper.text()).toContain('默认 128K');
    expect(wrapper.find('.table-pager').isVisible()).toBe(false);
    expect(wrapper.text()).toContain('展开（2 项）');
  });

  it('shows the first-run empty state when no provider exists', async () => {
    const { wrapper } = await setupPage();

    expect(wrapper.text()).toContain('还没有服务商。填三项就能开始用');
    expect(wrapper.text()).not.toContain('还没有模型：在服务商里点');
    expect(wrapper.find('.ai-table').exists()).toBe(false);
  });

  it('hides gated rows until their switch is on', async () => {
    const { wrapper, config } = await setupPage();

    expect(wrapper.find('[aria-label="复制配置键名 COMPACT_MAX_OUTPUT_TOKENS"]').exists()).toBe(false);
    expect(wrapper.find('[aria-label="复制配置键名 REASONING_CONFIG"]').exists()).toBe(true);

    config.updateValue('public', 'COMPACT_COST_CONTROL_ENABLED', true);
    await flushPromises();
    expect(wrapper.find('[aria-label="复制配置键名 COMPACT_MAX_OUTPUT_TOKENS"]').exists()).toBe(true);
  });

  it('keeps search text in the shared table UI state across remounts', async () => {
    const models = Array.from({ length: 12 }, (_, index) => `m${index + 1}`);
    const { wrapper, ui, pinia } = await setupPage({
      providers: [{ name: 'p', base_url: 'https://x/v1', key: '', models }],
    });

    await wrapper.find('input.ai-search').setValue('m12');
    await flushPromises();
    expect(ui.tableState('ai-models').query).toBe('m12');
    expect(ui.tableState('ai-models').page).toBe(1);
    expect(wrapper.text()).toContain('第 1 / 1 页 · 共 1 个模型');

    wrapper.unmount();
    const second = mount(AiModule, { global: { plugins: [pinia] } });
    await flushPromises();
    expect((second.find('input.ai-search').element as HTMLInputElement).value).toBe('m12');
  });

  it('only fetches providers once so unsaved drafts survive module remounts', async () => {
    const { wrapper, pinia, calls } = await setupPage({
      providers: [{ name: 'p', base_url: 'https://x/v1', key: '', models: ['m1'] }],
      main_model: 'p::m1',
    });
    const providerCalls = (): number => calls.filter(
      (call) => call.method === 'GET' && call.path === '/faust/admin/providers',
    ).length;
    expect(providerCalls()).toBe(1);

    // 页内改了一堆未保存的服务商草稿，然后切走再回来。
    const store = useProvidersStore();
    store.setProviders([{ name: 'draft', base_url: 'https://draft.example.com/v1', models: ['d1'] }]);
    wrapper.unmount();

    const second = mount(AiModule, { global: { plugins: [pinia] } });
    await flushPromises();

    expect(providerCalls()).toBe(1);
    expect(second.text()).toContain('draft');
    expect(second.text()).toContain('d1');
  });
});
