import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { mount, type VueWrapper } from '@vue/test-utils';
import ConfigField from '@/components/config/ConfigField.vue';
import { useConfigStore } from '@/stores/config';
import type { ConfigDict, ConfigScope } from '@/config/types';

function setupStore(publicValues: ConfigDict = {}, privateValues: ConfigDict = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const config = useConfigStore();
  config.publicConfig = { ...publicValues };
  config.privateConfig = { ...privateValues };
  config.originalPublic = { ...publicValues };
  config.originalPrivate = { ...privateValues };
  return { pinia, config };
}

function mountField(
  pinia: ReturnType<typeof createPinia>,
  scope: ConfigScope,
  fieldKey: string,
  extra: Record<string, unknown> = {},
): VueWrapper {
  return mount(ConfigField, {
    props: { scope, fieldKey, ...extra },
    global: { plugins: [pinia] },
  });
}

function inputValue(wrapper: VueWrapper, selector: string): string {
  return (wrapper.get(selector).element as HTMLInputElement).value;
}

beforeEach(() => {
  (window as unknown as Record<string, unknown>).TtsSplitter = undefined;
});

describe('ConfigField · secret', () => {
  it('已设置密钥：初始为空、清空不 dirty、输入非空即 dirty', async () => {
    const { pinia, config } = setupStore({}, { SEARCH_API_KEY: '****' });
    const wrapper = mountField(pinia, 'private', 'SEARCH_API_KEY');
    const input = wrapper.get('input[type="password"]');

    expect((input.element as HTMLInputElement).placeholder).toBe('已设置，保持不变');
    expect(inputValue(wrapper, 'input[type="password"]')).toBe('');
    expect(config.isDirty('private', 'SEARCH_API_KEY')).toBe(false);

    await input.setValue('');
    expect(config.isDirty('private', 'SEARCH_API_KEY')).toBe(false);

    await input.setValue('sk-live');
    expect(config.isDirty('private', 'SEARCH_API_KEY')).toBe(true);
    expect(config.dirtyValue('private', 'SEARCH_API_KEY')).toBe('sk-live');

    await input.setValue('');
    expect(config.isDirty('private', 'SEARCH_API_KEY')).toBe(false);
  });

  it('未设置密钥时占位文案为“请输入密钥”', () => {
    const { pinia } = setupStore({}, { SEARCH_API_KEY: '' });
    const wrapper = mountField(pinia, 'private', 'SEARCH_API_KEY');
    const input = wrapper.get('input[type="password"]');
    expect((input.element as HTMLInputElement).placeholder).toBe('请输入密钥');
  });
});

describe('ConfigField · 普通文本', () => {
  it('改回原值后不再 dirty', async () => {
    const { pinia, config } = setupStore({ NORMAL_KEY: 'orig' });
    const wrapper = mountField(pinia, 'public', 'NORMAL_KEY');
    const input = wrapper.get('input.input');

    await input.setValue('changed');
    expect(config.isDirty('public', 'NORMAL_KEY')).toBe(true);

    await input.setValue('orig');
    expect(config.isDirty('public', 'NORMAL_KEY')).toBe(false);
    expect(config.publicConfig.NORMAL_KEY).toBe('orig');
  });
});

describe('ConfigField · range / number 选择', () => {
  it('0~1 的普通数值默认使用滑块，显示两位小数', () => {
    const { pinia } = setupStore({ PLAIN_FRACTION: 0.5 });
    const wrapper = mountField(pinia, 'public', 'PLAIN_FRACTION');

    const range = wrapper.get('input.range-input');
    expect((range.element as HTMLInputElement).min).toBe('0');
    expect((range.element as HTMLInputElement).max).toBe('1');
    expect((range.element as HTMLInputElement).step).toBe('0.01');
    expect(wrapper.get('.slider-value').text()).toBe('0.50');
    expect(wrapper.find('input.number').exists()).toBe(false);
  });

  it('非 0~1 且无预设的整数使用 number 输入，步长 1', () => {
    const { pinia } = setupStore({ PLAIN_INT: 5 });
    const wrapper = mountField(pinia, 'public', 'PLAIN_INT');

    const number = wrapper.get('input.number');
    expect((number.element as HTMLInputElement).step).toBe('1');
    expect(inputValue(wrapper, 'input.number')).toBe('5');
    expect(wrapper.find('input.range-input').exists()).toBe(false);
  });

  it('非整数 number 输入步长为 0.01', () => {
    const { pinia } = setupStore({ PLAIN_FLOAT: 2.5 });
    const wrapper = mountField(pinia, 'public', 'PLAIN_FLOAT');
    expect((wrapper.get('input.number').element as HTMLInputElement).step).toBe('0.01');
  });

  it('SCALE_PRESETS 优先于 0~1 规则', () => {
    const { pinia } = setupStore({ LIVE2D_MODEL_SCALE: 2 });
    const wrapper = mountField(pinia, 'public', 'LIVE2D_MODEL_SCALE');

    const range = wrapper.get('input.range-input');
    expect((range.element as HTMLInputElement).min).toBe('0.01');
    expect((range.element as HTMLInputElement).max).toBe('1');
    expect((range.element as HTMLInputElement).step).toBe('0.01');
    expect(wrapper.get('.slider-value').text()).toBe('2.00x');
    expect(wrapper.get('.slider-hint').text()).toBe('0.01 - 1x');
  });

  it('拖动滑块写入数值并标记 dirty', async () => {
    const { pinia, config } = setupStore({ PLAIN_FRACTION: 0.5 });
    const wrapper = mountField(pinia, 'public', 'PLAIN_FRACTION');

    await wrapper.get('input.range-input').setValue('0.8');
    expect(config.publicConfig.PLAIN_FRACTION).toBe(0.8);
    expect(config.isDirty('public', 'PLAIN_FRACTION')).toBe(true);
    expect(wrapper.get('.slider-value').text()).toBe('0.80');
  });
});

describe('ConfigField · TTS 分块预览', () => {
  it('TTS_CHUNK_IDEAL_TOKENS 使用 window.TtsSplitter.demoSplit 渲染分段', () => {
    const demoSplit = vi.fn().mockReturnValue(['你', '好']);
    (window as unknown as Record<string, unknown>).TtsSplitter = { demoSplit };
    const { pinia } = setupStore({ TTS_CHUNK_IDEAL_TOKENS: 30 });
    const wrapper = mountField(pinia, 'public', 'TTS_CHUNK_IDEAL_TOKENS');

    expect(demoSplit).toHaveBeenCalledTimes(1);
    expect(demoSplit.mock.calls[0][1]).toBe(30);
    const segments = wrapper.findAll('.tts-split-preview .tts-split-seg');
    expect(segments.map((segment) => segment.text())).toEqual(['你', '好']);
    expect(segments[1].classes()).toContain('alt');
  });
});

describe('ConfigField · boolean / select', () => {
  it('布尔值使用 .switch-row 并即时写回', async () => {
    const { pinia, config } = setupStore({ BOOL_KEY: true });
    const wrapper = mountField(pinia, 'public', 'BOOL_KEY');

    expect(wrapper.find('.switch-row').exists()).toBe(true);
    expect(wrapper.get('.switch-text').text()).toBe('已启用');

    await wrapper.get('input[type="checkbox"]').setValue(false);
    expect(config.publicConfig.BOOL_KEY).toBe(false);
    expect(wrapper.get('.switch-text').text()).toBe('已禁用');
  });

  it('下拉框使用 FIELD_OPTIONS 与 FIELD_OPTION_LABELS', async () => {
    const { pinia, config } = setupStore({ REASONING_CONFIG: 'low' });
    const wrapper = mountField(pinia, 'public', 'REASONING_CONFIG');
    const select = wrapper.get('select.select');

    expect(select.findAll('option')).toHaveLength(4);
    expect(select.findAll('option')[1].text()).toBe('低');

    await select.setValue('high');
    expect(config.publicConfig.REASONING_CONFIG).toBe('high');
  });
});
