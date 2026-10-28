import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { flushPromises, mount } from '@vue/test-utils';
import { MODULES } from '@/config/constants';
import { resolveModule } from '@/modules/registry';
import UnknownModule from '@/modules/UnknownModule.vue';
import OverviewModule from '@/modules/overview/OverviewModule.vue';
import type { FrontendApi } from '@/api/types';
import { useModulesStore } from '@/stores/modules';
import { usePluginsStore } from '@/stores/plugins';
import { installPluginHost } from './pluginHost';
import { loadPluginAssets } from './loadAssets';
import PluginCards from './PluginCards.vue';
import PluginPage from './PluginPage.vue';
import type { PluginPageSpec, PluginUI } from './types';

type WindowWithHost = { pluginUI?: PluginUI; api?: FrontendApi };

type PluginsStore = ReturnType<typeof usePluginsStore>;
type ModulesStore = ReturnType<typeof useModulesStore>;

interface Harness {
  host: PluginUI;
  store: PluginsStore;
  modules: ModulesStore;
}

function clearWindow(): void {
  const w = window as unknown as WindowWithHost;
  delete w.pluginUI;
  delete w.api;
}

function clearInjectedNodes(): void {
  document.head
    .querySelectorAll('script[data-plugin], link[data-plugin], script[data-keep]')
    .forEach((node) => node.remove());
}

/** 每个用例一个全新 Pinia；pluginUI 必须重新安装才能拿到新 store 的数组引用。 */
function harness(): Harness {
  const host = installPluginHost();
  return { host, store: usePluginsStore(), modules: useModulesStore() };
}

function installApi(backendBaseUrl = 'http://127.0.0.1:15555'): {
  api: FrontendApi;
  configRequest: ReturnType<typeof vi.fn>;
} {
  const configRequest = vi.fn(async () => ({ assets: [] }));
  const api = { backendBaseUrl, configRequest } as unknown as FrontendApi;
  window.api = api;
  return { api, configRequest };
}

beforeEach(() => {
  setActivePinia(createPinia());
  clearWindow();
  clearInjectedNodes();
});

afterEach(() => {
  vi.unstubAllGlobals();
  clearWindow();
  clearInjectedNodes();
});

describe('installPluginHost', () => {
  it('幂等安装，并把 _pages/_cards/_persist/_persistDirty 绑到 store 的同一引用', () => {
    const host = installPluginHost();
    expect(installPluginHost()).toBe(host);

    const store = usePluginsStore();
    expect(host._pages).toBe(store.pages);
    expect(host._cards).toBe(store.cards);
    expect(host._persist).toBe(store.persist);
    expect(host._persistDirty).toBe(store.persistDirty);

    // 插件可能直接 push 到暴露的数组，必须立刻驱动导航 computed。
    const modules = useModulesStore();
    host._pages.push({ id: 'direct-page', label: '直插页', render: () => {} });
    expect(modules.allModules.some((m) => m.id === 'direct-page')).toBe(true);
  });
});

describe('addPage / addCard / modifyPage', () => {
  it('addPage 校验 id/label，按 plugin:id 去重，并响应式注入导航', () => {
    const { host, store, modules } = harness();
    const render = (): void => {};

    host.addPage(null as unknown as PluginPageSpec);
    host.addPage({ id: 'no-label', render } as unknown as PluginPageSpec);
    host.addPage({ label: 'no-id', render } as unknown as PluginPageSpec);
    expect(store.pages).toHaveLength(0);

    host.addPage({ plugin: 'plg', id: 'p1', label: 'P1', desc: '插件页', render });
    host.addPage({ plugin: 'plg', id: 'p1', label: '重复', render });
    expect(store.pages).toHaveLength(1);
    expect(store.pages[0]._key).toBe('plg:p1');
    expect(store.pages[0].label).toBe('P1');

    // 不同插件允许同 id
    host.addPage({ plugin: 'other', id: 'p1', label: 'Other', render });
    expect(store.pages).toHaveLength(2);

    const injected = modules.allModules.filter((m) => m.id === 'p1');
    expect(injected).toHaveLength(1);
    expect(injected[0]).toMatchObject({ title: 'P1', desc: '插件页' });
    expect(modules.switchModule('p1')).toBe(true);
    expect(modules.currentModule).toMatchObject({ id: 'p1', title: 'P1', desc: '插件页' });
  });

  it('addCard 校验 moduleId/title，按 plugin:moduleId:title 去重', () => {
    const { host, store } = harness();

    host.addCard('', { title: '无模块' });
    host.addCard('m', { title: '' });
    host.addCard('m', null as never);
    expect(store.cards).toHaveLength(0);

    host.addCard('m', { plugin: 'p', title: 'T', content: 'x' });
    host.addCard('m', { plugin: 'p', title: 'T', content: 'y' });
    expect(store.cards).toHaveLength(1);
    expect(store.cards[0]).toMatchObject({ moduleId: 'm', plugin: 'p', title: 'T', content: 'x' });
    expect(store.cards[0]._key).toBe('p:m:T');

    host.addCard('m', { plugin: 'q', title: 'T' });
    host.addCard('m2', { plugin: 'p', title: 'T' });
    expect(store.cards).toHaveLength(3);
  });

  it('保留旧渲染回退：内置模块优先；无页面的未知模块走卡片；页面优先于卡片', () => {
    const { host } = harness();

    host.addCard('overview', { plugin: 'p', title: '内置卡片', content: 'x' });
    expect(resolveModule('overview')).toMatchObject({ component: OverviewModule, isPlugin: false });

    host.addCard('ghost', { plugin: 'p', title: '未知卡片', content: 'x' });
    expect(resolveModule('ghost')).toMatchObject({ component: PluginCards, isPlugin: true });

    host.addPage({ plugin: 'p', id: 'plug', label: 'Plug', render: () => {} });
    host.addCard('plug', { plugin: 'p', title: '同页卡片', content: 'x' });
    expect(resolveModule('plug')).toMatchObject({ component: PluginPage, isPlugin: true });

    expect(resolveModule('nothing').component).toBe(UnknownModule);
  });

  it('modifyPage 查内置模块与插件页，找不到时回调 undefined', () => {
    const { host, store } = harness();
    const seen: unknown[] = [];

    host.modifyPage('overview', (module) => seen.push(module));
    expect(seen[0]).toBe(MODULES.find((m) => m.id === 'overview'));

    store.addPage({ plugin: 'p', id: 'plug', label: '插件页', desc: '说明', render: () => {} });
    host.modifyPage('plug', (module) => seen.push(module));
    expect(seen[1]).toEqual({ id: 'plug', title: '插件页', desc: '说明' });

    host.modifyPage('missing', (module) => seen.push(module));
    expect(seen[2]).toBeUndefined();

    host.modifyPage('overview', null as never);
    expect(seen).toHaveLength(3);
  });
});

describe('registerPersistence / unregisterPersistence', () => {
  it('缺少 plugin 抛错；同 plugin::module 覆盖；注册清该插件 dirty', () => {
    const { host, store } = harness();

    expect(() => host.registerPersistence({} as never)).toThrow('registerPersistence 缺少 plugin');
    expect(() => host.registerPersistence({ plugin: '   ' } as never)).toThrow('缺少 plugin');

    const firstSave = vi.fn();
    const first = host.registerPersistence({ plugin: 'p', save: firstSave });
    expect(first.plugin).toBe('p');
    expect(first.module).toBe('p');
    expect(typeof first.setDirty).toBe('function');
    expect(typeof first.dispose).toBe('function');

    first.setDirty(true);
    expect(host.dirtyCount()).toBe(1);

    const secondSave = vi.fn();
    const second = host.registerPersistence({ plugin: 'p', module: 'p', save: secondSave });
    expect(store.persist).toHaveLength(1);
    expect(store.persist[0]?.save).toBe(secondSave);
    expect(store.persist[0]?._key).toBe('p::p');
    expect(host.isPluginDirty('p')).toBe(false);
    expect(second.module).toBe('p');

    // 同插件不同 module 追加，而不是覆盖
    host.registerPersistence({ plugin: 'p', module: 'other', apply: vi.fn() });
    expect(store.persist.map((entry) => entry._key)).toEqual(['p::p', 'p::other']);

    // dispose 只注销自己注册的 module
    second.dispose();
    expect(store.persist.map((entry) => entry._key)).toEqual(['p::other']);
  });

  it('setDirty 走 handle，绑定自己的 plugin', () => {
    const { host, store } = harness();
    const handle = host.registerPersistence({ plugin: 'bound', module: 'm' });
    handle.setDirty(true);
    expect(host.isPluginDirty('bound')).toBe(true);
    expect(store.dirtyCount).toBe(1);
    handle.setDirty(false);
    expect(host.isPluginDirty('bound')).toBe(false);
    expect(store.dirtyCount).toBe(0);
  });

  it('unregister：同一插件仍有其它钩子时 dirty 保留，否则清除', () => {
    const { host, store } = harness();

    host.registerPersistence({ plugin: 'multi', module: 'm1' });
    host.registerPersistence({ plugin: 'multi', module: 'm2' });
    host.setPluginDirty('multi', true);

    host.unregisterPersistence('multi', 'm1');
    expect(store.persist.map((entry) => entry._key)).toEqual(['multi::m2']);
    expect(host.isPluginDirty('multi')).toBe(true);

    host.unregisterPersistence('multi', 'm2');
    expect(store.persist).toHaveLength(0);
    expect(host.isPluginDirty('multi')).toBe(false);
  });

  it('unregister 默认 module=plugin；未命中时不动 dirty', () => {
    const { host } = harness();

    host.registerPersistence({ plugin: 'solo' });
    host.setPluginDirty('solo', true);
    host.unregisterPersistence('solo');
    expect(host.isPluginDirty('solo')).toBe(false);

    host.registerPersistence({ plugin: 'keep', module: 'm1' });
    host.setPluginDirty('keep', true);
    host.unregisterPersistence('keep', 'missing');
    expect(host.isPluginDirty('keep')).toBe(true);
  });
});

describe('dirty 记账', () => {
  it('setPluginDirty/dirtyCount/isPluginDirty 裁剪空白并忽略空 id', () => {
    const { host, store } = harness();

    host.setPluginDirty(' a ', true);
    expect(host.isPluginDirty('a')).toBe(true);
    expect(host.isPluginDirty(' a ')).toBe(true);
    expect(host.dirtyCount()).toBe(1);
    expect(store.dirtyCount).toBe(1);

    host.setPluginDirty('', true);
    expect(host.dirtyCount()).toBe(1);

    host.setPluginDirty('a', false);
    expect(host.dirtyCount()).toBe(0);

    // 旧插件会直接操作暴露的 Set，必须仍然是同一引用。
    host._persistDirty.add('direct');
    expect(host.dirtyCount()).toBe(1);
    host._persistDirty.delete('direct');
    expect(host.dirtyCount()).toBe(0);
  });

  it('clearDirty(except) 只清保存成功的插件，失败的保持 dirty', () => {
    const { host } = harness();
    host.setPluginDirty('ok-plugin', true);
    host.setPluginDirty('bad-plugin', true);
    host.setPluginDirty('other', true);

    host.clearDirty(['bad-plugin']);
    expect(host.isPluginDirty('bad-plugin')).toBe(true);
    expect(host.isPluginDirty('ok-plugin')).toBe(false);
    expect(host.isPluginDirty('other')).toBe(false);
    expect(host.dirtyCount()).toBe(1);

    host.clearDirty();
    expect(host.dirtyCount()).toBe(0);
  });
});

describe('dropPersistenceExcept / resetPersistence / resetAll 引用语义', () => {
  it('dropPersistenceExcept 只保留当前模块钩子，并清掉已无钩子的插件 dirty', () => {
    const { host, store } = harness();
    host.registerPersistence({ plugin: 'a', module: 'm1' });
    host.registerPersistence({ plugin: 'multi', module: 'm1' });
    host.registerPersistence({ plugin: 'multi', module: 'm2' });
    host.setPluginDirty('a', true);
    host.setPluginDirty('multi', true);

    host.dropPersistenceExcept('m2');
    expect(store.persist.map((entry) => entry._key)).toEqual(['multi::m2']);
    expect(host.isPluginDirty('a')).toBe(false);
    expect(host.isPluginDirty('multi')).toBe(true);

    // 没有可丢弃项时是 no-op，也不换数组引用
    const persistRef = host._persist;
    host.dropPersistenceExcept('m2');
    expect(host._persist).toBe(persistRef);
    expect(store.persist).toHaveLength(1);
  });

  it('resetPersistence 清空 persist/dirty 但不换引用', () => {
    const { host } = harness();
    const persistRef = host._persist;
    const dirtyRef = host._persistDirty;
    host.registerPersistence({ plugin: 'p' }).setDirty(true);

    host.resetPersistence();
    expect(host._persist).toBe(persistRef);
    expect(host._persistDirty).toBe(dirtyRef);
    expect(persistRef).toHaveLength(0);
    expect(dirtyRef.size).toBe(0);
  });

  it('resetAll / unregister / drop 之后 _pages 等引用保持不变', () => {
    const { host, store } = harness();
    const pagesRef = host._pages;
    const cardsRef = host._cards;
    const persistRef = host._persist;
    const dirtyRef = host._persistDirty;

    host.addPage({ id: 'p1', label: 'P1', render: () => {} });
    host.addCard('p1', { title: 'c1', content: 'x' });
    host.registerPersistence({ plugin: 'p', module: 'm1' }).setDirty(true);
    host.registerPersistence({ plugin: 'q', module: 'm2' });

    store.resetAll();
    expect(host._pages).toBe(pagesRef);
    expect(host._cards).toBe(cardsRef);
    expect(host._persist).toBe(persistRef);
    expect(host._persistDirty).toBe(dirtyRef);
    expect(pagesRef).toHaveLength(0);
    expect(cardsRef).toHaveLength(0);
    expect(persistRef).toHaveLength(0);
    expect(dirtyRef.size).toBe(0);

    // 重新注册 + 注销 + 丢弃后仍然复用同一批数组
    host.addPage({ id: 'p2', label: 'P2', render: () => {} });
    host.registerPersistence({ plugin: 'p', module: 'm1' });
    host.registerPersistence({ plugin: 'q', module: 'm2' });
    host.unregisterPersistence('p', 'm1');
    host.dropPersistenceExcept('m2');
    expect(host._pages).toBe(pagesRef);
    expect(host._persist).toBe(persistRef);
    expect(host._persistDirty).toBe(dirtyRef);
    expect(host._pages).toBe(store.pages);
  });
});

describe('runPersistence', () => {
  it('并行执行所有钩子（不是逐个 await）', async () => {
    const { host, store } = harness();
    const started: string[] = [];
    let releaseSlow!: () => void;
    const slowGate = new Promise<void>((resolve) => {
      releaseSlow = resolve;
    });

    host.registerPersistence({
      plugin: 'slow',
      save: async () => {
        started.push('slow');
        await slowGate;
      },
    });
    host.registerPersistence({
      plugin: 'fast',
      save: async () => {
        started.push('fast');
      },
    });

    const running = store.runPersistence('save');
    // 慢插件还没结束时快插件已经进入钩子 → Promise.all 并行
    expect(started).toEqual(['slow', 'fast']);
    releaseSlow();
    await expect(running).resolves.toEqual([]);
  });

  it('只触发注册了该阶段的插件，ctx 精确为 {phase,plugin,module,dirty}', async () => {
    const { host, store } = harness();
    const contexts: unknown[] = [];
    const handle = host.registerPersistence({
      plugin: 'ctx',
      module: 'ctx-module',
      save: (ctx) => {
        contexts.push(ctx);
      },
      apply: (ctx) => {
        contexts.push(ctx);
      },
    });

    handle.setDirty(true);
    await store.runPersistence('save');
    expect(contexts).toEqual([
      { phase: 'save', plugin: 'ctx', module: 'ctx-module', dirty: true },
    ]);

    contexts.length = 0;
    handle.setDirty(false);
    await store.runPersistence('apply');
    expect(contexts).toEqual([
      { phase: 'apply', plugin: 'ctx', module: 'ctx-module', dirty: false },
    ]);

    contexts.length = 0;
    await expect(store.runPersistence('reload')).resolves.toEqual([]);
    expect(contexts).toHaveLength(0);
  });

  it('单插件失败不抛，按 {plugin,error} 聚合，pluginFailuresText 用「、」连接', async () => {
    const { host, store } = harness();
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const seen: string[] = [];

    host.registerPersistence({ plugin: 'good', save: async () => { seen.push('good'); } });
    host.registerPersistence({
      plugin: 'broken',
      save: async () => {
        seen.push('broken');
        throw new Error('boom');
      },
    });
    host.registerPersistence({
      plugin: 'thrown-string',
      save: () => {
        throw 'bad';
      },
    });

    const failures = await store.runPersistence('save');
    expect(seen).toEqual(['good', 'broken']);
    expect(failures).toEqual([
      { plugin: 'broken', error: 'boom' },
      { plugin: 'thrown-string', error: 'bad' },
    ]);
    expect(store.pluginFailuresText(failures)).toBe('broken（boom）、thrown-string（bad）');
    expect(errorSpy).toHaveBeenCalled();
  });
});

describe('communicate / communicateSSE', () => {
  it('无 window.api 时 communicate reject 固定文案', async () => {
    const { host } = harness();
    clearWindow();
    await expect(host.communicate('p')).rejects.toThrow('window.api.configRequest 未实现');
  });

  it('window.api 存在但 configRequest 不可用时同样 reject', async () => {
    const { host } = harness();
    window.api = { backendBaseUrl: 'http://127.0.0.1:15555' } as unknown as FrontendApi;
    await expect(host.communicate('p')).rejects.toThrow('window.api.configRequest 未实现');
  });

  it('communicate 用 POST + 编码插件 id，payload 缺省为 {}', async () => {
    const { host } = harness();
    const { configRequest } = installApi();

    await expect(host.communicate('a/b?', { key: 1 })).resolves.toEqual({ assets: [] });
    expect(configRequest).toHaveBeenCalledWith(
      'POST',
      '/faust/plugins/a%2Fb%3F/communicate',
      { key: 1 },
      undefined,
      undefined,
    );

    await host.communicate('plain');
    expect(configRequest).toHaveBeenLastCalledWith(
      'POST',
      '/faust/plugins/plain/communicate',
      {},
      undefined,
      undefined,
    );
  });

  it('communicateSSE 返回原生 EventSource，URL 带 backendBaseUrl 与查询串', () => {
    class FakeEventSource {
      static urls: string[] = [];
      url: string;
      constructor(url: string) {
        this.url = url;
        FakeEventSource.urls.push(url);
      }
    }
    vi.stubGlobal('EventSource', FakeEventSource);
    const { api } = installApi('http://127.0.0.1:17777');
    expect(api.backendBaseUrl).toBe('http://127.0.0.1:17777');
    const { host } = harness();

    const source = host.communicateSSE('a/b', { topic: 'x y', n: '1' });
    expect(source).toBeInstanceOf(FakeEventSource);
    expect(FakeEventSource.urls).toEqual([
      'http://127.0.0.1:17777/faust/plugins/a%2Fb/sse-communicate?topic=x+y&n=1',
    ]);

    host.communicateSSE('plain');
    expect(FakeEventSource.urls[1]).toBe(
      'http://127.0.0.1:17777/faust/plugins/plain/sse-communicate',
    );
  });

  it('无 window.api 时 communicateSSE 回落到默认后端地址', () => {
    class FakeEventSource {
      static urls: string[] = [];
      url: string;
      constructor(url: string) {
        this.url = url;
        FakeEventSource.urls.push(url);
      }
    }
    vi.stubGlobal('EventSource', FakeEventSource);
    const { host } = harness();
    clearWindow();

    host.communicateSSE('a/b');
    expect(FakeEventSource.urls).toEqual([
      'http://127.0.0.1:13900/faust/plugins/a%2Fb/sse-communicate',
    ]);
  });
});

describe('loadPluginAssets', () => {
  it('forceReload 清理旧节点与插件状态、当前插件页回落、设置 backendBaseUrl 并等待 JS', async () => {
    const { host, store, modules } = harness();
    host.addPage({ plugin: 'pp', id: 'plugin-page', label: '插件页', render: () => {} });
    host.addCard('plugin-page', { plugin: 'pp', title: '卡片', content: 'x' });
    host.registerPersistence({ plugin: 'pp', module: 'plugin-page' }).setDirty(true);
    expect(modules.switchModule('plugin-page')).toBe(true);
    expect(modules.activeModule).toBe('plugin-page');

    const staleScript = document.createElement('script');
    staleScript.setAttribute('data-plugin', 'old');
    staleScript.src = 'http://127.0.0.1:1/old.js';
    document.head.appendChild(staleScript);
    const staleLink = document.createElement('link');
    staleLink.rel = 'stylesheet';
    staleLink.setAttribute('data-plugin', 'old');
    staleLink.href = 'http://127.0.0.1:1/old.css';
    document.head.appendChild(staleLink);
    const keepScript = document.createElement('script');
    keepScript.setAttribute('data-keep', '1');
    document.head.appendChild(keepScript);

    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {});
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const configRequest = vi.fn(async (method: string, path: string) => {
      if (method === 'GET' && path === '/faust/admin/plugins/assets') {
        return {
          assets: [
            { type: 'js', path: '/plugins/a.js', plugin_id: 'pp' },
            { type: 'js', path: '/plugins/b.js?x=1' },
            { type: 'css', path: '/plugins/a.css', plugin_id: 'pp' },
            { type: 'js' },
            { type: 'css', path: '' },
          ],
        };
      }
      return null;
    });
    window.api = {
      backendBaseUrl: 'http://127.0.0.1:15555',
      configRequest,
    } as unknown as FrontendApi;

    const loading = loadPluginAssets(true);
    await vi.waitFor(() => {
      expect(document.head.querySelectorAll('script[data-plugin]')).toHaveLength(2);
    });

    expect(configRequest).toHaveBeenCalledWith(
      'GET',
      '/faust/admin/plugins/assets',
      undefined,
      undefined,
      undefined,
    );
    const scripts = Array.from(
      document.head.querySelectorAll<HTMLScriptElement>('script[data-plugin]'),
    );
    expect(scripts[0].src).toMatch(
      /^http:\/\/127\.0\.0\.1:15555\/plugins\/a\.js\?v=\d+$/,
    );
    expect(scripts[1].src).toMatch(
      /^http:\/\/127\.0\.0\.1:15555\/plugins\/b\.js\?x=1&v=\d+$/,
    );
    expect(scripts[0].getAttribute('data-plugin')).toBe('pp');
    expect(scripts[1].getAttribute('data-plugin')).toBe('');

    const links = Array.from(
      document.head.querySelectorAll<HTMLLinkElement>('link[data-plugin]'),
    );
    expect(links).toHaveLength(1);
    expect(links[0].getAttribute('rel')).toBe('stylesheet');
    expect(links[0].getAttribute('data-plugin')).toBe('pp');
    expect(links[0].href).toMatch(
      /^http:\/\/127\.0\.0\.1:15555\/plugins\/a\.css\?v=\d+$/,
    );

    expect(staleScript.isConnected).toBe(false);
    expect(staleLink.isConnected).toBe(false);
    expect(keepScript.isConnected).toBe(true);

    expect(host.backendBaseUrl).toBe('http://127.0.0.1:15555');
    expect(store.pages).toHaveLength(0);
    expect(store.cards).toHaveLength(0);
    expect(store.persist).toHaveLength(0);
    expect(host.dirtyCount()).toBe(0);
    expect(modules.activeModule).toBe('overview');

    // 一个成功一个失败：失败只 warn，不阻断，全部 load 结束后才 resolve
    scripts[0].dispatchEvent(new Event('load'));
    scripts[1].dispatchEvent(new Event('error'));
    await expect(loading).resolves.toBeUndefined();
    expect(logSpy).toHaveBeenCalledWith('[plugin] loaded JS:', '/plugins/a.js');
    expect(warnSpy).toHaveBeenCalledWith('[plugin] failed to load JS:', '/plugins/b.js?x=1');
    expect(logSpy).toHaveBeenCalledWith('[plugin] all JS assets loaded');
  });

  it('非 force 不清空插件状态，仅刷新 backendBaseUrl', async () => {
    const { host, store, modules } = harness();
    host.addPage({ plugin: 'pp', id: 'kept', label: '保留页', render: () => {} });
    expect(modules.switchModule('kept')).toBe(true);

    installApi('http://127.0.0.1:15556');
    await loadPluginAssets(false);

    expect(store.pages).toHaveLength(1);
    expect(host._pages).toHaveLength(1);
    expect(modules.activeModule).toBe('kept');
    expect(host.backendBaseUrl).toBe('http://127.0.0.1:15556');
  });

  it('无 window.api 时 force 仍先清空并 warn，不抛错', async () => {
    const { host, store } = harness();
    host.addPage({ plugin: 'pp', id: 'gone', label: '待清理', render: () => {} });
    host.registerPersistence({ plugin: 'pp', module: 'gone' }).setDirty(true);
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    clearWindow();

    await expect(loadPluginAssets(true)).resolves.toBeUndefined();
    expect(store.pages).toHaveLength(0);
    expect(store.persist).toHaveLength(0);
    expect(store.dirtyCount).toBe(0);
    expect(warnSpy).toHaveBeenCalledWith(
      '[loadPluginAssets] window.api.configRequest not available',
    );
  });
});

describe('PluginPage.vue', () => {
  it('render 抛错时显示「模块加载失败: …」且卸载清空容器', async () => {
    const { host } = harness();
    host.addPage({
      plugin: 'p',
      id: 'boom',
      label: 'Boom',
      render: () => {
        throw new Error('kaboom');
      },
    });

    const wrapper = mount(PluginPage, { props: { moduleId: 'boom' } });
    await flushPromises();
    expect(wrapper.text()).toContain('模块加载失败: kaboom');
    const hostEl = wrapper.find('.plugin-page-host').element as HTMLElement;
    expect(hostEl.innerHTML).toBe('');

    const errorEl = wrapper.find('.empty-state').element as HTMLElement;
    expect(errorEl.textContent).toBe('模块加载失败: kaboom');

    wrapper.unmount();
    expect(hostEl.innerHTML).toBe('');
  });

  it('异步 render reject 同样被拦截，成功 render 的容器卸载时清空', async () => {
    const { host } = harness();
    host.addPage({
      plugin: 'p',
      id: 'async-boom',
      label: 'AsyncBoom',
      render: async () => {
        throw new Error('async-kaboom');
      },
    });
    const failing = mount(PluginPage, { props: { moduleId: 'async-boom' } });
    await flushPromises();
    expect(() => failing.text()).not.toThrow();
    expect(failing.text()).toContain('模块加载失败: async-kaboom');
    failing.unmount();

    host.addPage({
      plugin: 'p',
      id: 'ok',
      label: 'OK',
      render: (container) => {
        container.innerHTML = '<p class="from-plugin">hello</p>';
      },
    });
    const wrapper = mount(PluginPage, { props: { moduleId: 'ok' } });
    await flushPromises();
    const hostEl = wrapper.find('.plugin-page-host').element as HTMLElement;
    expect(hostEl.innerHTML).toContain('from-plugin');
    wrapper.unmount();
    expect(hostEl.innerHTML).toBe('');
  });
});

describe('PluginCards.vue', () => {
  it('保留 card.render 与 content/html 回退，render 抛错显示模块加载失败', async () => {
    const { host } = harness();
    const rendered: HTMLElement[] = [];
    host.addCard('m', {
      plugin: 'p',
      title: '渲染卡',
      render: (container) => {
        rendered.push(container);
        container.innerHTML = '<span class="rendered-content">rendered</span>';
      },
    });
    host.addCard('m', { plugin: 'p', title: '内容卡', content: '<b class="content-fallback">content</b>' });
    host.addCard('m', { plugin: 'p', title: 'HTML 卡', html: '<i class="html-fallback">html</i>' });
    host.addCard('m', {
      plugin: 'p',
      title: '坏卡',
      render: () => {
        throw new Error('card-boom');
      },
    });
    host.addCard('other', { plugin: 'p', title: '别的模块', content: 'x' });

    const wrapper = mount(PluginCards, { props: { moduleId: 'm' } });
    await flushPromises();
    expect(rendered).toHaveLength(1);
    expect(rendered[0].innerHTML).toContain('rendered-content');
    expect(wrapper.findAll('section')).toHaveLength(4);
    expect(wrapper.find('.rendered-content').exists()).toBe(true);
    // 旧实现 el('div', 'card-content', content) 是 textContent，HTML 按字面文本显示。
    const textCards = wrapper.findAll('.card-content');
    expect(textCards).toHaveLength(2);
    expect(textCards[0].text()).toBe('<b class="content-fallback">content</b>');
    expect(textCards[1].text()).toBe('<i class="html-fallback">html</i>');
    expect(wrapper.text()).toContain('模块加载失败: card-boom');
    expect(wrapper.text()).not.toContain('别的模块');

    const cardHost = rendered[0];
    wrapper.unmount();
    expect(cardHost.innerHTML).toBe('');
  });

  it('挂载后新增的卡片也会被渲染', async () => {
    const { host } = harness();
    host.addCard('m', { plugin: 'p', title: 'one', content: 'one' });
    const wrapper = mount(PluginCards, { props: { moduleId: 'm' } });
    await flushPromises();
    expect(wrapper.findAll('section')).toHaveLength(1);

    host.addCard('m', {
      plugin: 'p',
      title: 'two',
      render: (container) => {
        container.textContent = 'two-rendered';
      },
    });
    await flushPromises();
    expect(wrapper.findAll('section')).toHaveLength(2);
    expect(wrapper.text()).toContain('two-rendered');
    wrapper.unmount();
  });
});
