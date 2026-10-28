import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import type { FrontendApi } from '@/api/types';
import { useConfigStore } from './config';
import { usePluginsStore } from './plugins';
import { useProvidersStore } from './providers';
import { useUiStore } from './ui';

interface Call {
  method: string;
  path: string;
  payload?: unknown;
}

function installApi(initial: Record<string, unknown> = {}): { calls: Call[]; api: FrontendApi } {
  const calls: Call[] = [];
  const configData = {
    public: { FOO: 'original', LIVE2D_MODEL_SCALE: 1, MD_BLOCK_ENABLED: false, ...initial },
    private: { SEARCH_API_KEY: '********' },
  };
  const api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string, payload?: unknown) => {
      calls.push({ method, path, payload });
      if (method === 'GET' && path === '/faust/admin/config') {
        return JSON.parse(JSON.stringify(configData));
      }
      if (method === 'GET' && path === '/faust/admin/runtime') {
        return { runtime: { status: 'ok' } };
      }
      if (method === 'GET' && path === '/faust/admin/providers') {
        return { providers: [], main_model: '', subagent_models: [], resolved_formats: {}, catalog: {} };
      }
      return { ok: true };
    }),
  } as unknown as FrontendApi;
  window.api = api;
  return { calls, api };
}

describe('config store dirty semantics', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    installApi();
  });

  it('marks non-secret changes only when different from original', async () => {
    const store = useConfigStore();
    await store.loadConfig();
    store.updateValue('public', 'FOO', 'changed');
    expect(store.dirtyPublic.get('FOO')).toBe('changed');
    store.updateValue('public', 'FOO', 'original');
    expect(store.dirtyPublic.has('FOO')).toBe(false);
  });

  it('never marks an empty secret, but marks a non-empty replacement', async () => {
    const store = useConfigStore();
    await store.loadConfig();
    store.updateValue('private', 'SEARCH_API_KEY', '');
    expect(store.dirtyPrivate.has('SEARCH_API_KEY')).toBe(false);
    store.updateValue('private', 'SEARCH_API_KEY', 'sk-new');
    expect(store.dirtyPrivate.get('SEARCH_API_KEY')).toBe('sk-new');
  });

  it('reverts a dirty value back to the original', async () => {
    const store = useConfigStore();
    await store.loadConfig();
    store.updateValue('public', 'FOO', 'changed');
    store.revertValue('public', 'FOO');
    expect(store.publicConfig.FOO).toBe('original');
    expect(store.dirtyPublic.has('FOO')).toBe(false);
  });
});

describe('config store save flow', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('posts only dirty values, then live2d apply and reload when required', async () => {
    const { calls } = installApi();
    const store = useConfigStore();
    await store.loadConfig();
    store.updateValue('public', 'LIVE2D_MODEL_SCALE', 1.2);
    store.updateValue('public', 'MD_BLOCK_ENABLED', true);

    await store.saveConfig();

    const posts = calls.filter((c) => c.method === 'POST');
    expect(posts.map((c) => c.path)).toEqual([
      '/faust/admin/config',
      '/faust/admin/live2d/apply',
      '/faust/admin/config/reload',
    ]);
    expect(posts[0].payload).toMatchObject({
      public: { LIVE2D_MODEL_SCALE: 1.2, MD_BLOCK_ENABLED: true },
      private: {},
    });
    expect(posts[1].payload).toEqual({ public: { LIVE2D_MODEL_SCALE: 1.2 } });
    expect(posts[2].payload).toEqual({ reset_dialog: false, no_initial_chat: true });
  });

  it('attaches provider bundle only when provider state is dirty', async () => {
    const { calls } = installApi();
    const store = useConfigStore();
    const providers = useProvidersStore();
    await store.loadConfig();
    providers.setBundle({
      providers: [{ name: 'p', models: ['m'] }],
      main_model: 'p::m',
      subagent_models: ['p::m'],
      resolved_formats: {},
      catalog: {},
    });
    providers.markProvidersDirty();
    expect(store.globalDirtyCount).toBe(1);

    await store.saveConfig();

    const configPost = calls.find((c) => c.method === 'POST' && c.path === '/faust/admin/config');
    expect(configPost?.payload).toMatchObject({
      public: {},
      private: {},
      providers: [{ name: 'p', models: ['m'] }],
      main_model: 'p::m',
      subagent_models: ['p::m'],
    });
    // IPC 的结构化克隆不能接受 Vue reactive Proxy，cfgApi 必须先转普通对象。
    expect(() => structuredClone(configPost?.payload)).not.toThrow();
    // 保存后需要重读后端归一化后的 providers / resolved_formats / catalog。
    expect(
      calls.filter((c) => c.method === 'GET' && c.path === '/faust/admin/providers'),
    ).toHaveLength(1);
  });

  it('keeps a failed plugin dirty and reports it without writing global config', async () => {
    const { calls } = installApi();
    const store = useConfigStore();
    const plugins = usePluginsStore();
    const ui = useUiStore();
    await store.loadConfig();
    plugins.registerPersistence({
      plugin: 'broken-plugin',
      save: () => {
        throw new Error('boom');
      },
    });
    plugins.setPluginDirty('broken-plugin', true);

    await store.saveConfig();

    expect(calls.some((c) => c.method === 'POST' && c.path === '/faust/admin/config')).toBe(false);
    expect(plugins.isPluginDirty('broken-plugin')).toBe(true);
    expect(ui.bannerType).toBe('error');
    expect(ui.bannerText).toContain('broken-plugin（boom）');
  });

  it('shows info and does not call the API when nothing is dirty', async () => {
    const { calls } = installApi();
    const store = useConfigStore();
    const ui = useUiStore();
    await store.loadConfig();
    const before = calls.length;

    await store.saveConfig();

    expect(calls.length).toBe(before);
    expect(ui.bannerText).toBe('当前没有未保存更改。');
  });
});
