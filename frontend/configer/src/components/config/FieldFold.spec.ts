import { describe, expect, it } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { mount } from '@vue/test-utils';
import FieldFold from '@/components/config/FieldFold.vue';
import { useUiStore } from '@/stores/ui';

function setupPinia() {
  const pinia = createPinia();
  setActivePinia(pinia);
  return pinia;
}

function mountFold(
  pinia: ReturnType<typeof createPinia>,
  props: Record<string, unknown>,
  content = '内容',
) {
  return mount(FieldFold, {
    props: { title: '高级配置', stateKey: 'advanced:test', ...props },
    slots: { default: `<span class="fold-content">${content}</span>` },
    global: { plugins: [pinia] },
  });
}

describe('FieldFold', () => {
  it('默认收起，点击后展开并写入 ui.folds', async () => {
    const pinia = setupPinia();
    const ui = useUiStore();
    const wrapper = mountFold(pinia, {});

    expect(wrapper.get('.field-fold-btn').attributes('aria-expanded')).toBe('false');
    expect(wrapper.get('.field-fold-body').classes()).not.toContain('open');
    expect(ui.folds['advanced:test']).toBeUndefined();

    await wrapper.get('.field-fold-btn').trigger('click');

    expect(wrapper.get('.field-fold-btn').attributes('aria-expanded')).toBe('true');
    expect(wrapper.get('.field-fold-body').classes()).toContain('open');
    expect(ui.folds['advanced:test']).toBe(true);
    expect(wrapper.get('.fold-content').text()).toBe('内容');
  });

  it('卸载后重新挂载保留展开状态', async () => {
    const pinia = setupPinia();
    const first = mountFold(pinia, {});
    await first.get('.field-fold-btn').trigger('click');
    expect(first.get('.field-fold-body').classes()).toContain('open');
    first.unmount();

    const second = mountFold(pinia, {});
    expect(second.get('.field-fold-btn').attributes('aria-expanded')).toBe('true');
    expect(second.get('.field-fold-body').classes()).toContain('open');
  });

  it('forceOpen 初始展开，用户仍可手动收起', async () => {
    const pinia = setupPinia();
    const ui = useUiStore();
    const wrapper = mountFold(pinia, { stateKey: 'advanced:force', forceOpen: true });

    expect(wrapper.get('.field-fold-body').classes()).toContain('open');
    await wrapper.get('.field-fold-btn').trigger('click');
    expect(wrapper.get('.field-fold-body').classes()).not.toContain('open');
    expect(ui.folds['advanced:force']).toBe(false);
  });

  it('defaultOpen 只在没有历史状态时生效', async () => {
    const pinia = setupPinia();
    const ui = useUiStore();
    const first = mountFold(pinia, { stateKey: 'advanced:default', defaultOpen: true });
    expect(first.get('.field-fold-body').classes()).toContain('open');

    await first.get('.field-fold-btn').trigger('click');
    expect(ui.folds['advanced:default']).toBe(false);
    first.unmount();

    const second = mountFold(pinia, { stateKey: 'advanced:default', defaultOpen: true });
    expect(second.get('.field-fold-body').classes()).not.toContain('open');
  });
});
