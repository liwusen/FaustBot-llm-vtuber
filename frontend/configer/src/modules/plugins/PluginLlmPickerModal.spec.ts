import { describe, expect, it, vi } from 'vitest';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import type { ProviderBundle } from '@/config/types';
import { useProvidersStore } from '@/stores/providers';
import PluginLlmPickerModal from './PluginLlmPickerModal.vue';

function mountPicker(
  props: {
    fieldKey?: string;
    fieldLabel?: string;
    currentValue?: string;
    onPick?: (spec: string, entry: unknown) => void;
  },
  bundle: Partial<ProviderBundle> = {},
) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const providers = useProvidersStore();
  providers.setBundle({
    providers: [],
    main_model: '',
    subagent_models: [],
    resolved_formats: {},
    catalog: {},
    ...bundle,
  });
  providers.loaded = true;
  return {
    providers,
    wrapper: mount(PluginLlmPickerModal, {
      props: {
        fieldKey: 'MODEL',
        fieldLabel: '模型',
        currentValue: '',
        ...props,
      },
      global: { plugins: [pinia] },
    }),
  };
}

describe('PluginLlmPickerModal', () => {
  it('lists provider models with context lengths and reports the pick', async () => {
    const onPick = vi.fn();
    const { wrapper } = mountPicker(
      { onPick },
      {
        providers: [
          {
            name: 'openai',
            models: ['gpt-4o', 'o1'],
            model_context_lengths: { 'gpt-4o': 128000 },
          },
        ],
      },
    );
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain('模型列表 - 模型');
    expect(wrapper.text()).toContain('openai');
    expect(wrapper.text()).toContain('128000');
    expect(wrapper.text()).toContain('默认');
    expect(wrapper.findAll('.simple-table tbody tr')).toHaveLength(2);

    const radios = wrapper.findAll('input[type="radio"]');
    expect(radios).toHaveLength(2);
    await radios[0].setValue(true);
    expect(onPick).toHaveBeenCalledWith(
      'openai::gpt-4o',
      expect.objectContaining({ provider: 'openai', model: 'gpt-4o' }),
    );
  });

  it('shows the empty state when no providers/models exist', async () => {
    const { wrapper } = mountPicker({});
    await wrapper.vm.$nextTick();

    expect(wrapper.text()).toContain('暂无可用模型');
    expect(wrapper.find('.simple-table').exists()).toBe(false);
  });
});
