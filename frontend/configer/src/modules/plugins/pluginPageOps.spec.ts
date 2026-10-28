import { describe, expect, it } from 'vitest';
import {
  PLUGIN_LLM_UNSET_TEXT,
  isLlmConfigType,
  parsePluginFieldValue,
  pluginConfigDraftValues,
  pluginConfigPayload,
  pluginHealthChartOption,
  pluginHealthLabel,
  pluginLlmDisplayValue,
  pluginLlmHasValue,
  pluginMiddlewareRows,
  pluginToolRows,
  pluginUpdateBadge,
  pluginUpdateFor,
  pluginUpdatesBanner,
  resolvePluginSelection,
  type PluginConfigSchemaItem,
  type PluginInfo,
} from './pluginPageOps';

describe('parsePluginFieldValue', () => {
  it('coerces bool like Boolean(rawValue)', () => {
    expect(parsePluginFieldValue('bool', true)).toBe(true);
    expect(parsePluginFieldValue('bool', false)).toBe(false);
    expect(parsePluginFieldValue('bool', 'false')).toBe(true);
    expect(parsePluginFieldValue('bool', 0)).toBe(false);
    expect(parsePluginFieldValue('BOOL', 1)).toBe(true);
  });

  it('parses int with parseInt fallback 0', () => {
    expect(parsePluginFieldValue('int', '42')).toBe(42);
    expect(parsePluginFieldValue('int', ' 7abc ')).toBe(7);
    expect(parsePluginFieldValue('int', '')).toBe(0);
    expect(parsePluginFieldValue('int', 'abc')).toBe(0);
    expect(parsePluginFieldValue('int', null)).toBe(0);
    expect(parsePluginFieldValue('int', 0)).toBe(0);
  });

  it('parses float with parseFloat fallback 0', () => {
    expect(parsePluginFieldValue('float', '3.5')).toBe(3.5);
    expect(parsePluginFieldValue('float', '2.5suffix')).toBe(2.5);
    expect(parsePluginFieldValue('float', '')).toBe(0);
    expect(parsePluginFieldValue('float', 'nope')).toBe(0);
    expect(parsePluginFieldValue('float', null)).toBe(0);
  });

  it('parses json text and keeps empty as null', () => {
    expect(parsePluginFieldValue('json', '{"a":1}')).toEqual({ a: 1 });
    expect(parsePluginFieldValue('json', '[1,2]')).toEqual([1, 2]);
    expect(parsePluginFieldValue('json', '   ')).toBeNull();
    expect(parsePluginFieldValue('json', null)).toBeNull();
    expect(() => parsePluginFieldValue('json', '{oops')).toThrow();
  });

  it('passes str and unknown types through unchanged', () => {
    expect(parsePluginFieldValue('str', 'hello')).toBe('hello');
    expect(parsePluginFieldValue('', 'hello')).toBe('hello');
    expect(parsePluginFieldValue(undefined, 12)).toBe(12);
    expect(parsePluginFieldValue('weird', { a: 1 })).toEqual({ a: 1 });
  });
});

describe('isLlmConfigType', () => {
  it('accepts llm/model case-insensitively', () => {
    expect(isLlmConfigType('llm')).toBe(true);
    expect(isLlmConfigType('model')).toBe(true);
    expect(isLlmConfigType('LLM')).toBe(true);
    expect(isLlmConfigType(' Model ')).toBe(false);
    expect(isLlmConfigType('str')).toBe(false);
    expect(isLlmConfigType(undefined)).toBe(false);
  });
});

describe('pluginHealthLabel', () => {
  it('maps known statuses to Chinese labels', () => {
    expect(pluginHealthLabel({ health: { status: 'healthy' } })).toBe('正常');
    expect(pluginHealthLabel({ health: { status: 'ok' } })).toBe('正常');
    expect(pluginHealthLabel({ health: { status: 'warning' } })).toBe('注意');
    expect(pluginHealthLabel({ health: { status: 'error' } })).toBe('异常');
    expect(pluginHealthLabel({ health: { status: 'unknown' } })).toBe('未知');
  });

  it('falls back to unknown or the raw status', () => {
    expect(pluginHealthLabel(null)).toBe('未知');
    expect(pluginHealthLabel({})).toBe('未知');
    expect(pluginHealthLabel({ health: { status: 'degraded' } })).toBe('degraded');
    expect(pluginHealthLabel({ health: { status: 'ERROR' } })).toBe('异常');
  });
});

describe('pluginHealthChartOption', () => {
  it('uses 72/28 and blue for an enabled healthy plugin', () => {
    const option = pluginHealthChartOption({
      id: 'pet',
      name: 'pet',
      enabled: true,
      health: { status: 'healthy' },
    }) as {
      series: Array<{ data: Array<{ value: number; itemStyle: { color: string } }> }>;
      graphic: Array<{ style: { text?: string; stroke?: string } }>;
    };
    expect(option.series[0].data).toEqual([
      { value: 72, itemStyle: { color: '#3f6be8' } },
      { value: 28, itemStyle: { color: '#e6edf7' } },
    ]);
    expect(option.graphic[1].style.text).toBe('P');
    expect(option.graphic[0].style.stroke).toBe('#3f6be8');
  });

  it('uses 38/62 and error/warning accents', () => {
    const disabled = pluginHealthChartOption({
      id: 'pet',
      name: 'pet',
      enabled: false,
      health: { status: 'warning' },
    }) as {
      series: Array<{ data: Array<{ value: number; itemStyle: { color: string } }> }>;
      graphic: Array<{ style: { text?: string; stroke?: string } }>;
    };
    expect(disabled.series[0].data).toEqual([
      { value: 38, itemStyle: { color: '#f59e0b' } },
      { value: 62, itemStyle: { color: '#e6edf7' } },
    ]);
    expect(disabled.graphic[0].style.stroke).toBe('#f59e0b');

    const errored = pluginHealthChartOption({ id: 'x', enabled: true, health: { status: 'error' } }) as {
      graphic: Array<{ style: { text?: string; stroke?: string } }>;
    };
    expect(errored.graphic[0].style.stroke).toBe('#e54447');
    expect(errored.graphic[1].style.text).toBe('X');
  });

  it('falls back to id initial, then P', () => {
    const byId = pluginHealthChartOption({ id: 'alpha' }) as {
      graphic: Array<{ style: { text?: string } }>;
    };
    expect(byId.graphic[1].style.text).toBe('A');
    const fallback = pluginHealthChartOption(null) as {
      graphic: Array<{ style: { text?: string; stroke?: string } }>;
    };
    expect(fallback.graphic[1].style.text).toBe('P');
    expect(fallback.graphic[0].style.stroke).toBe('#b8c6d8');
  });
});

describe('pluginConfigPayload', () => {
  const schema: PluginConfigSchemaItem[] = [
    { key: 'enabled', type: 'bool' },
    { key: 'count', type: 'int' },
    { key: 'ratio', type: 'float' },
    { key: 'options', type: 'json' },
    { key: 'name', type: 'str' },
    { type: 'str' },
    { key: 'model', type: 'llm' },
  ];

  it('builds the exact save payload from schema keys only', () => {
    const payload = pluginConfigPayload(schema, {
      enabled: 1,
      count: '7',
      ratio: '2.5',
      options: '{"a":1}',
      name: 'abc',
      model: 'openai::gpt-4o',
      ignored: 'not-in-schema',
    });
    expect(payload).toEqual({
      values: {
        enabled: true,
        count: 7,
        ratio: 2.5,
        options: { a: 1 },
        name: 'abc',
        model: 'openai::gpt-4o',
      },
      apply_runtime: true,
      reset_dialog: false,
      no_initial_chat: true,
    });
  });

  it('handles missing draft values exactly like the old parser', () => {
    expect(pluginConfigPayload(schema, {}).values).toEqual({
      enabled: false,
      count: 0,
      ratio: 0,
      options: null,
      name: undefined,
      model: undefined,
    });
  });
});

describe('pluginConfigDraftValues', () => {
  it('copies every stored value into the draft', () => {
    const plugin: PluginInfo = {
      config: {
        schema: [{ key: 'name', type: 'str' }],
        values: { name: 'faust', extra: 1 },
      },
    };
    expect(pluginConfigDraftValues(plugin)).toEqual({ name: 'faust', extra: 1 });
  });

  it('converts json values to textarea text', () => {
    const plugin: PluginInfo = {
      config: {
        schema: [{ key: 'options', type: 'json' }],
        values: { options: { a: 1 }, name: 'x' },
      },
    };
    const draft = pluginConfigDraftValues(plugin);
    expect(draft.options).toBe('{\n  "a": 1\n}');
    expect(draft.name).toBe('x');
    expect(pluginConfigPayload(plugin.config!.schema!, draft).values.options).toEqual({ a: 1 });
  });

  it('returns an empty draft for a missing plugin', () => {
    expect(pluginConfigDraftValues(null)).toEqual({});
    expect(pluginConfigDraftValues(undefined)).toEqual({});
  });
});

describe('plugin update helpers', () => {
  const data = {
    updates: [
      {
        id: 'pet',
        name: 'Pet Plugin',
        installed_version: '1.0.0',
        latest_version: '1.2.0',
      },
    ],
  };

  it('formats the version badge', () => {
    expect(pluginUpdateBadge(null)).toBe('');
    expect(pluginUpdateBadge(data.updates[0])).toBe('可更新 → 1.2.0');
  });

  it('finds and removes updates by id', () => {
    expect(pluginUpdateFor(data, 'pet')).toEqual(data.updates[0]);
    expect(pluginUpdateFor(data, 'other')).toBeNull();
    expect(pluginUpdateFor(null, 'pet')).toBeNull();
  });

  it('builds the check-updates banner', () => {
    expect(pluginUpdatesBanner(data)).toEqual({
      type: 'success',
      text: '发现 1 个可更新插件: Pet Plugin (1.0.0 → 1.2.0)',
    });
    expect(pluginUpdatesBanner({ updates: [] })).toEqual({
      type: 'success',
      text: '所有插件均为最新版本。',
    });
    expect(pluginUpdatesBanner(null)).toEqual({
      type: 'success',
      text: '所有插件均为最新版本。',
    });
  });
});

describe('resolvePluginSelection', () => {
  it('keeps a still-present selection', () => {
    expect(resolvePluginSelection([{ id: 'a' }, { id: 'b' }], 'b')).toBe('b');
  });

  it('falls back to the first plugin or empty state', () => {
    expect(resolvePluginSelection([{ id: 'a' }, { id: 'b' }], 'gone')).toBe('a');
    expect(resolvePluginSelection([], 'gone')).toBe('');
    expect(resolvePluginSelection([{ id: 'a' }], '')).toBe('a');
  });
});

describe('detail rows and LLM display', () => {
  it('maps tool and middleware rows like the old tables', () => {
    expect(
      pluginToolRows({
        tools: [{ name: 'ping', enabled: true, description: '' }],
      }),
    ).toEqual([['ping', true, '-']]);

    expect(
      pluginMiddlewareRows({
        middlewares: [{ name: 'guard', priority: 3, enabled: false, description: 'x' }],
      }),
    ).toEqual([['guard', 3, false, 'x']]);
    expect(pluginToolRows({})).toEqual([]);
  });

  it('renders the unset/selected LLM identifier', () => {
    expect(pluginLlmHasValue(null)).toBe(false);
    expect(pluginLlmHasValue('')).toBe(false);
    expect(pluginLlmHasValue('openai::gpt-4o')).toBe(true);
    expect(pluginLlmDisplayValue('')).toBe(PLUGIN_LLM_UNSET_TEXT);
    expect(pluginLlmDisplayValue(null)).toBe('(未选择 · 缺省使用主对话模型)');
    expect(pluginLlmDisplayValue('openai::gpt-4o')).toBe('openai::gpt-4o');
  });
});
