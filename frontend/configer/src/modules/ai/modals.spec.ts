import { flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FrontendApi } from '@/api/types';
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import ModelModal from './ModelModal.vue';
import ProviderModal from './ProviderModal.vue';

function installApi(): void {
  const api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async () => ({ status: 'ok' })),
  } as unknown as FrontendApi;
  window.api = api;
}

function buttonByText(wrapper: ReturnType<typeof mount>, text: string) {
  const button = wrapper.findAll('button').find((item) => item.text().trim() === text);
  if (!button) throw new Error(`button not found: ${text}`);
  return button;
}

async function setup() {
  const pinia = createPinia();
  setActivePinia(pinia);
  installApi();
  const store = useProvidersStore();
  const ui = useUiStore();
  store.setBundle({
    providers: [
      {
        name: 'p',
        base_url: 'https://api.example.com/v1',
        key: 'sk-1',
        models: ['old', 'keep'],
        model_context_lengths: { old: 1000 },
        model_api_formats: { old: 'openai_chat' },
      },
    ],
    main_model: 'p::old',
    subagent_models: ['p::old'],
    resolved_formats: {},
    catalog: {},
  });
  store.resetDirty();
  return { pinia, store, ui };
}

describe('ProviderModal', () => {
  beforeEach(() => {
    installApi();
  });

  it('saves an edited provider into pending state and keeps the blank key', async () => {
    const { store, ui, pinia } = await setup();
    const provider = store.providers[0];
    const wrapper = mount(ProviderModal, {
      props: { provider },
      global: { plugins: [pinia] },
    });

    const nameInput = wrapper.find('input[placeholder="例如 deepseek"]');
    expect((nameInput.element as HTMLInputElement).disabled).toBe(true);
    expect((wrapper.find('input[type="password"]').element as HTMLInputElement).value).toBe('********');

    await wrapper.find('input[placeholder="例如 https://api.deepseek.com/v1"]').setValue('https://new.example.com/v1');
    await buttonByText(wrapper, '保存到配置').trigger('click');

    expect(store.providers[0].base_url).toBe('https://new.example.com/v1');
    expect(store.providers[0].key).toBe('sk-1');
    expect(store.providersDirty).toBe(true);
    expect(ui.bannerText).toBe('服务商已更新（点窗口顶部「保存」写入配置）');
    expect(ui.modalOpen).toBe(false);
  });

  it('adds a new provider with backend-compatible defaults', async () => {
    const { store, ui, pinia } = await setup();
    const wrapper = mount(ProviderModal, {
      props: { provider: null },
      global: { plugins: [pinia] },
    });

    await wrapper.find('input[placeholder="例如 deepseek"]').setValue('fresh');
    await wrapper.find('input[placeholder="例如 https://api.deepseek.com/v1"]').setValue('https://fresh.example.com/v1');
    await buttonByText(wrapper, '保存到配置').trigger('click');

    const record = store.providers.find((item) => item.name === 'fresh');
    expect(record).toBeTruthy();
    expect(record?.models).toEqual([]);
    expect(record?.api_format).toBe('auto');
    expect(record?.catalog_provider_id).toBeNull();
    expect(ui.bannerText).toBe('服务商已添加（点窗口顶部「保存」写入配置）');
  });

  it('rejects a duplicate provider name and keeps the modal open', async () => {
    const { store, ui, pinia } = await setup();
    const wrapper = mount(ProviderModal, {
      props: { provider: null },
      global: { plugins: [pinia] },
    });

    await wrapper.find('input[placeholder="例如 deepseek"]').setValue('p');
    await wrapper.find('input[placeholder="例如 https://api.deepseek.com/v1"]').setValue('https://dup.example.com/v1');
    await buttonByText(wrapper, '保存到配置').trigger('click');

    expect(store.providers).toHaveLength(1);
    expect(ui.bannerText).toBe('这个服务商已经存在');
  });

  it('requires a name and base url before saving', async () => {
    const { ui, pinia } = await setup();
    const wrapper = mount(ProviderModal, {
      props: { provider: null },
      global: { plugins: [pinia] },
    });

    await buttonByText(wrapper, '保存到配置').trigger('click');
    expect(ui.bannerText).toBe('名称与接口地址必填');
  });
});

describe('ModelModal', () => {
  beforeEach(() => {
    installApi();
  });

  it('renames the model, migrates indexed config and syncs main/subagent specs', async () => {
    const { store, ui, pinia } = await setup();
    const entry = { provider: 'p', model: 'old', spec: 'p::old' };
    const wrapper = mount(ModelModal, {
      props: { entry },
      global: { plugins: [pinia] },
    });

    expect(wrapper.text()).toContain('当前格式：OpenAI Chat · 手动指定');

    await wrapper.find('input.input').setValue('renamed');
    await buttonByText(wrapper, '保存到配置').trigger('click');
    await flushPromises();

    expect(store.providers[0].models).toEqual(['renamed', 'keep']);
    expect(store.providers[0].model_context_lengths).toEqual({ renamed: 1000 });
    expect(store.providers[0].model_api_formats).toEqual({ renamed: 'openai_chat' });
    expect(store.mainModel).toBe('p::renamed');
    expect(store.subagentModels).toEqual(['p::renamed']);
    expect(ui.bannerText).toBe('模型已保存（点窗口顶部「保存」写入配置）');
  });

  it('deletes a model and clears its indexed config plus main/subagent specs', async () => {
    const { store, ui, pinia } = await setup();
    const entry = { provider: 'p', model: 'old', spec: 'p::old' };
    const wrapper = mount(ModelModal, {
      props: { entry },
      global: { plugins: [pinia] },
    });

    await buttonByText(wrapper, '删除模型').trigger('click');

    expect(store.providers[0].models).toEqual(['keep']);
    expect(store.providers[0].model_context_lengths).toEqual({});
    expect(store.mainModel).toBe('');
    expect(store.subagentModels).toEqual([]);
    expect(ui.bannerText).toBe('模型已删除（点窗口顶部「保存」写入配置）');
  });

  it('disables server tools for formats that do not support them', async () => {
    const { pinia } = await setup();
    const entry = { provider: 'p', model: 'old', spec: 'p::old' };
    const wrapper = mount(ModelModal, {
      props: { entry },
      global: { plugins: [pinia] },
    });
    const checkbox = wrapper.find('.tool-row input[type="checkbox"]');
    // 手动覆盖为 OpenAI Chat：不支持服务端联网搜索。
    expect((checkbox.element as HTMLInputElement).disabled).toBe(true);
    expect(wrapper.text()).toContain('不支持服务端工具');
  });
});
