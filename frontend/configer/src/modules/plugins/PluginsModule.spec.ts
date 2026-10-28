import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import type { FrontendApi } from '@/api/types';
import PluginsModule from './PluginsModule.vue';
import { resetPluginPageState } from './pluginPageState';
import type { PluginInfo } from './pluginPageOps';

const plugins: PluginInfo[] = [
  {
    id: 'pet',
    name: 'Pet Plugin',
    version: '1.0.0',
    author: 'me',
    homepage: 'https://example.com/pet',
    enabled: true,
    priority: 5,
    description: '宠物插件',
    permissions: ['read'],
    health: { status: 'healthy' },
    trigger_control: { supports_append_filter: true, supports_fire_filter: false },
    tools: [{ name: 'ping', enabled: true, description: 'ping tool' }],
    middlewares: [{ name: 'guard', priority: 1, enabled: true, description: 'guard mw' }],
    config: {
      schema: [
        { key: 'ENABLED', type: 'bool', label: '启用项', description: 'bool desc' },
        { key: 'MODEL', type: 'llm', label: '模型', description: '' },
        { key: 'OPTIONS', type: 'json', label: '选项' },
        { key: 'COUNT', type: 'int', label: '数量' },
        { key: 'RATIO', type: 'float', label: '比例' },
        { key: 'NAME', type: 'str', label: '名称' },
        { key: 'SEARCH_API_KEY', type: 'str', label: '密钥' },
      ],
      values: {
        ENABLED: true,
        MODEL: 'openai::gpt-4o',
        OPTIONS: { a: 1 },
        COUNT: 3,
        RATIO: 0.5,
        NAME: 'faust',
        SEARCH_API_KEY: '***',
      },
    },
  },
  {
    id: 'clock',
    name: 'Clock Plugin',
    version: '2.0.0',
    enabled: false,
    description: '时钟插件',
    health: { status: 'warning' },
    tools: [],
    middlewares: [],
    config: { schema: [], values: {} },
  },
];

let wrapper: VueWrapper | null = null;
let configRequest: ReturnType<typeof vi.fn>;
let currentPlugins: PluginInfo[];

function mountModule(): VueWrapper {
  const pinia = createPinia();
  setActivePinia(pinia);
  return mount(PluginsModule, { global: { plugins: [pinia] } });
}

beforeEach(() => {
  resetPluginPageState();
  currentPlugins = plugins.map((plugin) => ({ ...plugin }));
  configRequest = vi.fn(async (method: string, path: string) => {
    if (method === 'GET' && path === '/faust/admin/plugins') return { items: currentPlugins };
    if (method === 'GET' && path === '/faust/admin/plugins/assets') return { assets: [] };
    if (method === 'DELETE' && path === '/faust/admin/plugins/pet') {
      currentPlugins = currentPlugins.filter((plugin) => plugin.id !== 'pet');
      return {};
    }
    return {};
  });
  window.api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest,
  } as unknown as FrontendApi;
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  resetPluginPageState();
  delete (window as { api?: FrontendApi }).api;
});

describe('PluginsModule', () => {
  it('renders toolbar, list and the selected plugin detail', async () => {
    wrapper = mountModule();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('插件操作');
    expect(text).toContain('重载插件');
    expect(text).toContain('从 ZIP 安装');
    expect(text).toContain('检查更新');
    expect(text).toContain('打包为 ZIP');
    expect(text).toContain('gh-proxy 镜像加速');
    expect(text).toContain('Pet Plugin');
    expect(text).not.toContain('可更新');

    expect(text).toContain('插件基本信息');
    expect(text).toContain('健康状态');
    expect(text).toContain('权限');
    expect(text).toContain('工具注册');
    expect(text).toContain('中间件注册');
    expect(text).toContain('插件配置');
    expect(text).toContain('openai::gpt-4o');
    expect(wrapper.findAll('.plugin-field')).toHaveLength(7);
    expect(wrapper.find('.range-input').exists()).toBe(true);
    expect(wrapper.find('input.number').exists()).toBe(true);
    expect(wrapper.find('input[type="password"]').exists()).toBe(true);
    expect((wrapper.find('textarea.textarea').element as HTMLTextAreaElement).value).toBe(
      '{\n  "a": 1\n}',
    );
    expect(configRequest).toHaveBeenCalledWith('GET', '/faust/admin/plugins', undefined, undefined, undefined);
  });

  it('selects another row and reloads its config draft', async () => {
    wrapper = mountModule();
    await flushPromises();

    const rows = wrapper.findAll('.plugin-list-table tbody tr');
    expect(rows.length).toBe(2);
    await rows[1].trigger('click');
    await flushPromises();

    expect(wrapper.text()).toContain('Clock Plugin');
    expect(wrapper.text()).toContain('该插件未注册配置项。');
    expect(wrapper.findAll('.plugin-field')).toHaveLength(0);
  });

  it('falls back to the remaining plugin after deleting the selected one', async () => {
    wrapper = mountModule();
    await flushPromises();

    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const firstRow = wrapper.findAll('.plugin-list-table tbody tr')[0];
    const deleteButton = firstRow.findAll('button').find((button) => button.text() === '删除');
    expect(deleteButton).toBeTruthy();
    await deleteButton!.trigger('click');
    await flushPromises();

    expect(confirmSpy).toHaveBeenCalledWith('确定删除插件 pet ?');
    const deleteCall = configRequest.mock.calls.find((call) => call[0] === 'DELETE');
    expect(deleteCall?.[1]).toBe('/faust/admin/plugins/pet');
    expect(deleteCall?.[2]).toBeNull();
    expect(deleteCall?.[3]).toEqual({
      apply_runtime: 'true',
      reset_dialog: 'false',
      no_initial_chat: 'true',
    });
    expect(wrapper.findAll('.plugin-list-table tbody tr')).toHaveLength(1);
    expect(wrapper.find('.plugin-list-table tbody tr.selected').text()).toContain('Clock Plugin');
    expect(wrapper.text()).toContain('该插件未注册配置项。');
  });

  it('posts the exact plugin config payload on save', async () => {
    wrapper = mountModule();
    await flushPromises();

    const saveButton = wrapper
      .findAll('button')
      .find((button) => button.text() === '保存插件配置并重载');
    expect(saveButton).toBeTruthy();
    await saveButton!.trigger('click');
    await flushPromises();

    const configCall = configRequest.mock.calls.find(
      (call) => call[1] === '/faust/admin/plugins/pet/config',
    );
    expect(configCall?.[0]).toBe('POST');
    expect(configCall?.[2]).toEqual({
      values: {
        ENABLED: true,
        MODEL: 'openai::gpt-4o',
        OPTIONS: { a: 1 },
        COUNT: 3,
        RATIO: 0.5,
        NAME: 'faust',
        SEARCH_API_KEY: '***',
      },
      apply_runtime: true,
      reset_dialog: false,
      no_initial_chat: true,
    });
  });
});
