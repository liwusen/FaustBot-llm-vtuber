import { beforeEach, describe, expect, it } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { useModulesStore } from './modules';
import { usePluginsStore } from './plugins';

describe('modules store', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
  });

  it('switches to a built-in module and updates title', () => {
    const modules = useModulesStore();
    expect(modules.switchModule('ai')).toBe(true);
    expect(modules.activeModule).toBe('ai');
    expect(modules.currentModule.title).toBe('AI 服务商');
  });

  it('cancels switching when a leave guard returns false', () => {
    const modules = useModulesStore();
    modules.switchModule('memory');
    modules.registerLeaveGuard(() => false);
    expect(modules.switchModule('ai')).toBe(false);
    expect(modules.activeModule).toBe('memory');
  });

  it('rebuilds a persistent module by replacing the KeepAlive epoch', () => {
    const modules = useModulesStore();
    modules.switchModule('memory');
    const before = modules.reloadEpoch;
    modules.refreshCurrent();
    expect(modules.reloadEpoch).toBe(before + 1);
  });

  it('bumps nonce when re-clicking a non-persistent module but not a persistent one', () => {
    const modules = useModulesStore();
    modules.switchModule('ai');
    const before = modules.nonce;
    modules.navigate('ai');
    expect(modules.nonce).toBe(before + 1);

    modules.switchModule('memory');
    const persistentBefore = modules.nonce;
    modules.navigate('memory');
    expect(modules.nonce).toBe(persistentBefore);
  });

  it('adds plugin pages to navigation and drops their persistence when leaving', () => {
    const modules = useModulesStore();
    const plugins = usePluginsStore();
    plugins.addPage({ id: 'my-plugin', label: '我的插件', render: () => {} });
    expect(modules.allModules.some((m) => m.id === 'my-plugin')).toBe(true);

    modules.switchModule('my-plugin');
    plugins.registerPersistence({ plugin: 'my-plugin', save: async () => {} });
    expect(plugins.persist).toHaveLength(1);

    modules.switchModule('overview');
    expect(plugins.persist).toHaveLength(0);
  });

  it('falls back to overview when the active plugin page disappears', () => {
    const modules = useModulesStore();
    const plugins = usePluginsStore();
    plugins.addPage({ id: 'gone', label: '即将消失', render: () => {} });
    modules.switchModule('gone');
    plugins.resetAll();
    modules.ensureActiveValid();
    expect(modules.activeModule).toBe('overview');
  });
});
