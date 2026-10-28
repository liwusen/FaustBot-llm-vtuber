import { beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import type { FrontendApi } from '@/api/types';
import App from '@/App.vue';

function installApi(): void {
  const api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string) => {
      if (method === 'GET' && path === '/faust/admin/plugins/assets') return { assets: [] };
      if (method === 'GET' && path === '/faust/admin/config') {
        return { public: {}, private: {} };
      }
      if (method === 'GET' && path === '/faust/admin/runtime') return { runtime: {} };
      if (method === 'GET' && path === '/faust/ui-setting') {
        return { settings: { onboarding: { done: true } } };
      }
      return {};
    }),
  } as unknown as FrontendApi;
  window.api = api;
}

describe('Configer shell', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    installApi();
    document.body.innerHTML = '';
  });

  it('renders the sidebar, topbar and overview module after bootstrap', async () => {
    const wrapper = mount(App, { attachTo: document.body });
    await flushPromises();
    await flushPromises();

    expect(wrapper.find('.app-shell').exists()).toBe(true);
    expect(wrapper.findAll('#moduleNav .nav-btn').length).toBe(13);
    expect(wrapper.find('#moduleTitle').text()).toBe('概览');
    expect(wrapper.find('#saveBtn').text()).toBe('保存');
    expect(wrapper.find('#applyBtn').text()).toBe('应用');
    expect(wrapper.find('#reloadBtn').text()).toBe('重新加载');
    expect(wrapper.find('#validBadge').text()).toBe('配置有效');
    expect(wrapper.find('#dirtyBadge').classes()).toContain('hidden');
    expect(wrapper.find('#banner').classes()).toContain('hidden');
    expect(wrapper.find('#cardsRoot .module-container').exists()).toBe(true);

    wrapper.unmount();
  });
});
