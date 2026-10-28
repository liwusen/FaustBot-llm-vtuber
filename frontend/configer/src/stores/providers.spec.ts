import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import type { FrontendApi } from '@/api/types';
import type { ProviderBundle } from '@/config/types';
import { useProvidersStore } from './providers';

interface Call {
  method: string;
  path: string;
  payload?: unknown;
}

function makeBundle(overrides: Partial<ProviderBundle> = {}): ProviderBundle {
  return {
    providers: [
      { name: 'p', base_url: 'https://api.example.com/v1', key: 'sk-1', models: ['m1', 'm2'] },
      { name: 'q', base_url: 'https://q.example.com/v1', key: '', models: ['m3'] },
    ],
    main_model: 'p::m1',
    subagent_models: ['p::m2'],
    resolved_formats: {},
    catalog: {},
    ...overrides,
  };
}

function installApi(factory: () => ProviderBundle = () => makeBundle()): { calls: Call[]; api: FrontendApi } {
  const calls: Call[] = [];
  const api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string, payload?: unknown) => {
      calls.push({ method, path, payload });
      if (method === 'GET' && path === '/faust/admin/providers') {
        return JSON.parse(JSON.stringify(factory()));
      }
      return { status: 'ok' };
    }),
  } as unknown as FrontendApi;
  window.api = api;
  return { calls, api };
}

describe('providers store loading', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('loads the whole provider bundle without marking anything dirty', async () => {
    const { calls } = installApi();
    const store = useProvidersStore();

    await store.loadProviders();

    expect(calls).toEqual([{ method: 'GET', path: '/faust/admin/providers', payload: undefined }]);
    expect(store.providers.map((p) => p.name)).toEqual(['p', 'q']);
    expect(store.mainModel).toBe('p::m1');
    expect(store.subagentModels).toEqual(['p::m2']);
    expect(store.anyDirty()).toBe(false);
    expect(store.dirtyCount()).toBe(0);
  });

  it('normalizes missing bundle fields to empty defaults', () => {
    const store = useProvidersStore();
    store.setBundle({});
    expect(store.providers).toEqual([]);
    expect(store.mainModel).toBe('');
    expect(store.subagentModels).toEqual([]);
    expect(store.resolvedFormats).toEqual({});
    expect(store.providerCatalog).toEqual({});
  });

  it('exposes flattened model entries and provider lookup', () => {
    const store = useProvidersStore();
    store.setBundle(makeBundle());
    expect(store.allModelEntries.map((entry) => entry.spec)).toEqual(['p::m1', 'p::m2', 'q::m3']);
    expect(store.findProvider('q')?.models).toEqual(['m3']);
    expect(store.findProvider('missing')).toBeUndefined();
  });
});

describe('providers store dirty flags', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('marks provider edits dirty without touching the model selection flags', () => {
    installApi();
    const store = useProvidersStore();
    store.setBundle(makeBundle());

    store.setProviders([{ name: 'p', models: ['m1'] }]);

    expect(store.providersDirty).toBe(true);
    expect(store.mainModelDirty).toBe(false);
    expect(store.subagentModelsDirty).toBe(false);
    expect(store.dirtyCount()).toBe(1);
  });

  it('marks both model flags for main/subagent edits', () => {
    installApi();
    const store = useProvidersStore();
    store.setBundle(makeBundle());

    store.setMainModel('q::m3');
    expect(store.mainModel).toBe('q::m3');
    expect(store.mainModelDirty).toBe(true);
    expect(store.subagentModelsDirty).toBe(true);

    store.resetDirty();
    expect(store.anyDirty()).toBe(false);

    store.toggleSubagentModel('p::m1', true);
    expect(store.subagentModels).toContain('p::m1');
    store.toggleSubagentModel('p::m1', false);
    expect(store.subagentModels).not.toContain('p::m1');
    expect(store.mainModelDirty).toBe(true);
  });

  it('removes a provider together with its main/subagent specs and marks both flags', () => {
    installApi();
    const store = useProvidersStore();
    store.setBundle(makeBundle());

    store.removeProvider('p');

    expect(store.providers.map((p) => p.name)).toEqual(['q']);
    expect(store.mainModel).toBe('');
    expect(store.subagentModels).toEqual([]);
    expect(store.providersDirty).toBe(true);
    expect(store.mainModelDirty).toBe(true);
    expect(store.subagentModelsDirty).toBe(true);
  });

  it('keeps unrelated main/subagent specs when deleting another provider', () => {
    installApi();
    const store = useProvidersStore();
    store.setBundle(makeBundle({ main_model: 'q::m3', subagent_models: ['q::m3', 'p::m2'] }));

    store.removeProvider('p');

    expect(store.mainModel).toBe('q::m3');
    expect(store.subagentModels).toEqual(['q::m3']);
  });

  it('replaceProviders is silent and setProvider marks providers dirty', () => {
    installApi();
    const store = useProvidersStore();
    store.setBundle(makeBundle());

    store.replaceProviders([{ name: 'x', models: [] }]);
    expect(store.providers.map((p) => p.name)).toEqual(['x']);
    expect(store.providersDirty).toBe(false);

    store.setProvider('x', { name: 'x', models: ['m'] });
    expect(store.providers[0].models).toEqual(['m']);
    expect(store.providersDirty).toBe(true);
  });
});

describe('providers store backend refreshes', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('ensureProviders only fetches once, so page remounts keep local drafts', async () => {
    const { calls } = installApi();
    const store = useProvidersStore();

    await store.ensureProviders();
    expect(calls).toHaveLength(1);
    expect(store.loaded).toBe(true);

    store.setProviders([{ name: 'draft', models: [] }]);
    await store.ensureProviders();
    expect(calls).toHaveLength(1);
    expect(store.providers.map((p) => p.name)).toEqual(['draft']);

    await store.refreshProviders();
    expect(calls).toHaveLength(2);
    expect(store.providers.map((p) => p.name)).toEqual(['p', 'q']);
  });

  it('refreshProviders re-reads the bundle and marks providers dirty (legacy bulk refresh)', async () => {
    const { calls } = installApi(() => makeBundle({ providers: [{ name: 'p', models: ['fresh'] }] }));
    const store = useProvidersStore();
    store.setBundle(makeBundle());

    await store.refreshProviders();

    expect(calls.map((call) => call.path)).toEqual(['/faust/admin/providers']);
    expect(store.providers).toEqual([{ name: 'p', models: ['fresh'] }]);
    expect(store.providersDirty).toBe(true);
    expect(store.mainModel).toBe('p::m1');
  });

  it('refreshProvider only replaces that provider and keeps other unsaved edits', async () => {
    const { calls } = installApi(() => makeBundle({
      providers: [
        { name: 'p', models: ['fresh'], model_context_lengths: { fresh: 128000 } },
        { name: 'q', models: ['backend'] },
      ],
      resolved_formats: {
        p: { fresh: { api_format: 'openai_chat', source: 'catalog' } },
      } as unknown as ProviderBundle['resolved_formats'],
      catalog: { p: { provider_id: 'deepseek', routed: 1 } } as unknown as ProviderBundle['catalog'],
    }));
    const store = useProvidersStore();
    store.setBundle(makeBundle({
      providers: [
        { name: 'p', models: ['draft'] },
        { name: 'q', models: ['draft-q'] },
      ],
    }));

    const fresh = await store.refreshProvider('p');

    expect(calls).toHaveLength(1);
    expect(fresh?.models).toEqual(['fresh']);
    expect(store.providers.find((p) => p.name === 'p')?.models).toEqual(['fresh']);
    expect(store.providers.find((p) => p.name === 'q')?.models).toEqual(['draft-q']);
    expect(store.resolvedFormats.p).toEqual({
      fresh: { api_format: 'openai_chat', source: 'catalog' },
    });
    expect(store.catalogFor('p')?.provider_id).toBe('deepseek');
    expect(store.providersDirty).toBe(true);
  });

  it('refreshProvider returns null and keeps the draft when the provider vanished', async () => {
    installApi(() => makeBundle({ providers: [] }));
    const store = useProvidersStore();
    store.setBundle(makeBundle());

    const fresh = await store.refreshProvider('p');

    expect(fresh).toBeNull();
    expect(store.providers.map((p) => p.name)).toEqual(['p', 'q']);
    expect(store.providersDirty).toBe(false);
  });

  it('refreshCatalogEntries replaces providers/formats/catalog but keeps the model selection', async () => {
    installApi(() => makeBundle({
      providers: [{ name: 'p', models: ['new'] }],
      main_model: 'p::new',
      subagent_models: [],
    }));
    const store = useProvidersStore();
    store.setBundle(makeBundle());

    await store.refreshCatalogEntries();

    expect(store.providers).toEqual([{ name: 'p', models: ['new'] }]);
    expect(store.mainModel).toBe('p::m1');
    expect(store.subagentModels).toEqual(['p::m2']);
    expect(store.providersDirty).toBe(true);
    expect(store.mainModelDirty).toBe(false);
  });
});
