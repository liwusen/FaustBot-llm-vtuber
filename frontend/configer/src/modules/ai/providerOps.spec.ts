import { describe, expect, it } from 'vitest';
import type { ProviderConfig } from '@/config/types';
import {
  allModelEntries,
  applyLoadModelsResult,
  applyModelSettings,
  applyProviderForm,
  catalogResultText,
  catalogSummary,
  cloneProviders,
  deleteModelFromProvider,
  formatContext,
  formatDecision,
  formatText,
  keyStatus,
  newProviderRecord,
  parseContextLength,
  removeModelSelection,
  removeProviderSelection,
  renameModelConfig,
  renameModelSelection,
  setModelContextLength,
  setModelField,
  splitModelSpec,
  thinkingText,
} from './providerOps';
import type { AiResolvedFormats, ProviderForm } from './providerOps';

function makeForm(overrides: Partial<ProviderForm> = {}): ProviderForm {
  return {
    base_url: 'https://api.example.com/v1',
    key: null,
    thinking_type: 'qwen',
    opencode_go: false,
    api_format: 'auto',
    stream_usage: null,
    format_base_urls: {},
    catalog_provider_id: null,
    ...overrides,
  };
}

function makeProvider(overrides: Partial<ProviderConfig> = {}): ProviderConfig {
  return {
    name: 'p',
    base_url: 'https://api.example.com/v1',
    key: 'sk-1',
    models: [],
    ...overrides,
  };
}

describe('formatContext / keyStatus', () => {
  it('formats token counts exactly like the legacy helper', () => {
    expect(formatContext(undefined)).toBe('默认 128K');
    expect(formatContext(null)).toBe('默认 128K');
    expect(formatContext(0)).toBe('默认 128K');
    expect(formatContext('abc')).toBe('默认 128K');
    expect(formatContext(1048576)).toBe('1M');
    expect(formatContext(2000000)).toBe('2M');
    expect(formatContext(131072)).toBe('128K');
    expect(formatContext(128000)).toBe('125K');
    expect(formatContext(400000)).toBe('400K');
    expect(formatContext(1500000)).toBe('1.4M');
    expect(formatContext(1500)).toBe('2K');
    expect(formatContext(500)).toBe('500');
  });

  it('only reports whether a key exists, never its value', () => {
    expect(keyStatus(makeProvider())).toBe('已设置');
    expect(keyStatus(makeProvider({ key: '' }))).toBe('未设置');
    expect(keyStatus(null)).toBe('未设置');
  });
});

describe('formatDecision', () => {
  it('prefers the local manual override above everything else', () => {
    const provider = makeProvider({
      api_format: 'openai_chat',
      model_api_formats: { m: 'anthropic_messages' },
    });
    const resolved: AiResolvedFormats = { p: { m: { api_format: 'google_gemini', source: 'catalog' } } };
    expect(formatDecision(provider, 'm', resolved)).toEqual({
      format: 'anthropic_messages',
      source: 'manual',
    });
  });

  it('uses the backend resolved formats when present', () => {
    const provider = makeProvider({ api_format: 'openai_chat' });
    const resolved: AiResolvedFormats = { p: { m: { api_format: 'google_gemini', source: 'catalog' } } };
    expect(formatDecision(provider, 'm', resolved)).toEqual({
      format: 'google_gemini',
      source: 'catalog',
    });
  });

  it('turns a backend resolution error into the error badge state', () => {
    const resolved: AiResolvedFormats = { p: { m: { api_format: null, source: 'error', reason: 'boom' } } };
    expect(formatDecision(makeProvider(), 'm', resolved)).toEqual({ format: 'auto', source: 'error' });
  });

  it('falls back to the provider default, then the catalog route, then auto', () => {
    expect(formatDecision(makeProvider({ api_format: 'openai_chat' }), 'm', {})).toEqual({
      format: 'openai_chat',
      source: 'provider_default',
    });
    const routed = makeProvider({
      model_catalog_routes: { m: { format: 'google_gemini' } },
    } as Partial<ProviderConfig>);
    expect(formatDecision(routed, 'm', {})).toEqual({ format: 'google_gemini', source: 'catalog' });
    expect(formatDecision(makeProvider(), 'm', {})).toEqual({ format: 'auto', source: '' });
    expect(formatDecision(null, 'm', {})).toEqual({ format: 'auto', source: '' });
  });
});

describe('formatText / thinkingText', () => {
  it('renders format badges and thinking shapes verbatim', () => {
    expect(formatText('openai_chat', 'manual')).toBe('OpenAI Chat · 手动指定');
    expect(formatText('anthropic_messages', 'error')).toBe('Anthropic · 格式异常');
    expect(formatText('auto', '')).toBe('自动');
    expect(formatText('weird', '')).toBe('weird');
    expect(thinkingText({ mode: 'effort', source: 'models_dev' })).toBe('思维等级 · 模型目录');
    expect(thinkingText({ mode: 'budget_tokens' })).toBe('思维预算');
    expect(thinkingText(null)).toBe('未提供');
  });
});

describe('parseContextLength', () => {
  it('accepts only positive integers and treats everything else as unset', () => {
    expect(parseContextLength('128000')).toBe(128000);
    expect(parseContextLength(200000)).toBe(200000);
    expect(parseContextLength('')).toBeNull();
    expect(parseContextLength('   ')).toBeNull();
    expect(parseContextLength(null)).toBeNull();
    expect(parseContextLength(undefined)).toBeNull();
    expect(parseContextLength('0')).toBeNull();
    expect(parseContextLength('-1')).toBeNull();
    expect(parseContextLength('1.5')).toBeNull();
    expect(parseContextLength('abc')).toBeNull();
  });
});

describe('model field helpers', () => {
  it('writes and clears model_context_lengths without touching other models', () => {
    const providers = [makeProvider({ model_context_lengths: { keep: 5 } })];
    setModelContextLength(providers, 'p', 'm', 128000);
    expect(providers[0].model_context_lengths).toEqual({ keep: 5, m: 128000 });
    setModelContextLength(providers, 'p', 'm', null);
    expect(providers[0].model_context_lengths).toEqual({ keep: 5 });
    setModelContextLength(providers, 'missing', 'm', 1);
    expect(providers[0].model_context_lengths).toEqual({ keep: 5 });
  });

  it('creates the index map on demand and deletes empty values', () => {
    const providers = [makeProvider()];
    setModelField(providers, 'p', 'm', 'model_server_tools', ['web_search']);
    expect(providers[0].model_server_tools).toEqual({ m: ['web_search'] });
    setModelField(providers, 'p', 'm', 'model_server_tools', null);
    expect(providers[0].model_server_tools).toEqual({});
    setModelField(providers, 'missing', 'm', 'model_server_tools', ['x']);
  });
});

describe('renameModelConfig', () => {
  it('migrates every model-name-indexed dictionary and drops the old key', () => {
    const providers = [
      makeProvider({
        model_context_lengths: { old: 1000, other: 5 },
        model_max_output_tokens: { old: 100 },
        model_api_formats: { old: 'openai_chat' },
        model_server_tools: { old: ['web_search'] },
        model_api_capabilities: { old: { vision: true } },
        model_thinking_caps: { old: { mode: 'effort' } },
        model_thinking_modes: { old: 'effort' },
      } as Partial<ProviderConfig>),
    ];
    renameModelConfig(providers, 'p', 'old', 'new');
    const provider = providers[0] as ProviderConfig & Record<string, Record<string, unknown>>;
    expect(provider.model_context_lengths).toEqual({ new: 1000, other: 5 });
    expect(provider.model_max_output_tokens).toEqual({ new: 100 });
    expect(provider.model_api_formats).toEqual({ new: 'openai_chat' });
    expect(provider.model_server_tools).toEqual({ new: ['web_search'] });
    expect(provider.model_api_capabilities).toEqual({ new: { vision: true } });
    expect(provider.model_thinking_caps).toEqual({ new: { mode: 'effort' } });
    expect(provider.model_thinking_modes).toEqual({ new: 'effort' });
  });

  it('deletes the indexed entries when the new name is null', () => {
    const providers = [
      makeProvider({
        model_context_lengths: { old: 1000 },
        model_api_formats: { old: 'openai_chat' },
      }),
    ];
    renameModelConfig(providers, 'p', 'old', null);
    expect(providers[0].model_context_lengths).toEqual({});
    expect(providers[0].model_api_formats).toEqual({});
  });

  it('is a no-op for an unknown provider or model', () => {
    const providers = [makeProvider({ model_context_lengths: { old: 1 } })];
    renameModelConfig(providers, 'missing', 'old', 'new');
    renameModelConfig(providers, 'p', 'nope', 'new');
    expect(providers[0].model_context_lengths).toEqual({ old: 1 });
  });
});

describe('applyModelSettings', () => {
  it('renames the model array and writes context / output / format / tools', () => {
    const providers = [
      makeProvider({
        models: ['old', 'keep'],
        model_context_lengths: { old: 1000 },
        model_api_formats: { old: 'auto' },
        model_server_tools: { old: ['x'] },
      }),
    ];
    const applied = applyModelSettings(providers, 'p', 'old', {
      modelName: 'new',
      contextLength: 200000,
      maxOutputTokens: 8000,
      apiFormat: 'openai_chat',
      thinkingMode: 'effort',
      serverTools: ['web_search'],
    });
    expect(applied).toBe(true);
    expect(providers[0].models).toEqual(['new', 'keep']);
    expect(providers[0].model_context_lengths).toEqual({ new: 200000 });
    expect(providers[0].model_max_output_tokens).toEqual({ new: 8000 });
    expect(providers[0].model_api_formats).toEqual({ new: 'openai_chat' });
    expect((providers[0] as ProviderConfig & Record<string, unknown>).model_thinking_modes)
      .toEqual({ new: 'effort' });
    expect(providers[0].model_server_tools).toEqual({ new: ['web_search'] });
  });

  it('clears overrides when the form selects auto and no tools', () => {
    const providers = [
      makeProvider({
        models: ['m'],
        model_api_formats: { m: 'openai_chat' },
        model_max_output_tokens: { m: 10 },
        model_server_tools: { m: ['web_search'] },
        model_thinking_modes: { m: 'effort' },
      } as Partial<ProviderConfig>),
    ];
    applyModelSettings(providers, 'p', 'm', {
      modelName: 'm',
      contextLength: null,
      maxOutputTokens: null,
      apiFormat: 'auto',
      thinkingMode: 'auto',
      serverTools: [],
    });
    expect(providers[0].model_api_formats).toEqual({});
    expect(providers[0].model_max_output_tokens).toEqual({});
    expect(providers[0].model_server_tools).toEqual({});
    expect((providers[0] as ProviderConfig & Record<string, unknown>).model_thinking_modes).toEqual({});
  });

  it('returns false when the provider no longer exists', () => {
    expect(applyModelSettings([], 'p', 'm', {
      modelName: 'n',
      contextLength: null,
      maxOutputTokens: null,
      apiFormat: 'auto',
      thinkingMode: 'auto',
      serverTools: [],
    })).toBe(false);
  });
});

describe('model selection sync', () => {
  it('rewrites main and subagent specs on rename, including array entries', () => {
    const selection = {
      mainModel: 'p::old',
      subagentModels: ['p::old', 'q::x', 'p::other'],
    };
    expect(renameModelSelection(selection, 'p', 'old', 'new')).toEqual({
      mainModel: 'p::new',
      subagentModels: ['p::new', 'q::x', 'p::other'],
    });
  });

  it('clears the main spec and removes subagent entries on delete', () => {
    const selection = { mainModel: 'p::m', subagentModels: ['p::m', 'p::k', 'q::m'] };
    expect(removeModelSelection(selection, 'p', 'm')).toEqual({
      mainModel: '',
      subagentModels: ['p::k', 'q::m'],
    });
  });

  it('clears every spec of a deleted provider', () => {
    const selection = { mainModel: 'p::a', subagentModels: ['p::a', 'p::b', 'q::b'] };
    expect(removeProviderSelection(selection, 'p')).toEqual({
      mainModel: '',
      subagentModels: ['q::b'],
    });
    expect(removeProviderSelection({ mainModel: 'q::b', subagentModels: [] }, 'p')).toEqual({
      mainModel: 'q::b',
      subagentModels: [],
    });
  });
});

describe('provider form apply', () => {
  it('applies every provider field but keeps the stored key when blank', () => {
    const target = makeProvider({ key: 'old-key' });
    applyProviderForm(target, makeForm({
      thinking_type: 'deepseek',
      opencode_go: true,
      api_format: '',
      stream_usage: false,
      format_base_urls: { openai_chat: 'https://chat.example.com/v1' },
      catalog_provider_id: 'deepseek',
    }));
    expect(target.base_url).toBe('https://api.example.com/v1');
    expect(target.key).toBe('old-key');
    expect(target.thinking_type).toBe('deepseek');
    expect(target.opencode_go).toBe(true);
    expect(target.api_format).toBe('auto');
    expect(target.stream_usage).toBe(false);
    expect(target.format_base_urls).toEqual({ openai_chat: 'https://chat.example.com/v1' });
    expect(target.catalog_provider_id).toBe('deepseek');
  });

  it('replaces the key when a new one is typed', () => {
    const target = makeProvider({ key: 'old-key' });
    applyProviderForm(target, makeForm({ key: 'sk-new' }));
    expect(target.key).toBe('sk-new');
  });

  it('builds a new provider record with backend-compatible defaults', () => {
    const record = newProviderRecord('fresh', makeForm({ key: 'sk-new' }));
    expect(record.name).toBe('fresh');
    expect(record.base_url).toBe('https://api.example.com/v1');
    expect(record.key).toBe('sk-new');
    expect(record.models).toEqual([]);
    expect(record.model_context_lengths).toEqual({});
    expect(record.model_api_formats).toEqual({});
    expect(record.model_max_output_tokens).toEqual({});
    expect(record.model_api_capabilities).toEqual({});
    expect(record.model_thinking_caps).toEqual({});
    expect(record.model_thinking_modes).toEqual({});
    expect(record.model_server_tools).toEqual({});
    expect(record.model_catalog_routes).toEqual({});
    expect(record.format_base_urls).toEqual({});
    expect(record.catalog_provider_id).toBeNull();
    expect(record.catalog_synced_at).toBeNull();
    expect(record.stream_usage).toBeNull();
    expect(record.opencode_go).toBe(false);
  });
});

describe('applyLoadModelsResult', () => {
  it('replaces models and derived dictionaries, and adopts a recognized catalog id', () => {
    const providers = [
      makeProvider({
        models: ['old'],
        catalog_provider_id: 'old-catalog',
        model_context_lengths: { old: 1 },
      }),
    ];
    applyLoadModelsResult(providers, 'p', {
      models: ['a', 'b'],
      model_context_lengths: { a: 128000 },
      model_max_output_tokens: { a: 4096 },
      model_api_capabilities: { a: { vision: true } },
      model_thinking_caps: { a: { mode: 'effort' } },
      catalog: { provider_id: 'new-catalog', routed: 2 },
    });
    expect(providers[0].models).toEqual(['a', 'b']);
    expect(providers[0].model_context_lengths).toEqual({ a: 128000 });
    expect(providers[0].model_max_output_tokens).toEqual({ a: 4096 });
    expect((providers[0] as ProviderConfig & Record<string, unknown>).model_api_capabilities)
      .toEqual({ a: { vision: true } });
    expect(providers[0].catalog_provider_id).toBe('new-catalog');
  });

  it('keeps the catalog id when the pull did not recognize one', () => {
    const providers = [makeProvider({ catalog_provider_id: 'keep-me', models: ['old'] })];
    applyLoadModelsResult(providers, 'p', { models: ['a'], catalog: { provider_id: null } });
    expect(providers[0].catalog_provider_id).toBe('keep-me');
    expect(providers[0].models).toEqual(['a']);
  });
});

describe('deleteModelFromProvider', () => {
  it('removes the model and its indexed configuration', () => {
    const providers = [
      makeProvider({
        models: ['m', 'keep'],
        model_context_lengths: { m: 10, keep: 20 },
        model_api_formats: { m: 'openai_chat' },
      }),
    ];
    deleteModelFromProvider(providers, 'p', 'm');
    expect(providers[0].models).toEqual(['keep']);
    expect(providers[0].model_context_lengths).toEqual({ keep: 20 });
    expect(providers[0].model_api_formats).toEqual({});
  });
});

describe('catalog summaries', () => {
  it('describes a provider that is not yet recognized', () => {
    expect(catalogSummary(null)).toBe('添加服务商并拉取模型后，这里会显示模型目录的识别结果。');
    expect(catalogSummary(makeProvider())).toBe(
      '还没有识别出对应的目录条目。可以点「自动识别服务商」，或直接在下面填目录条目名。',
    );
  });

  it('mentions a host mismatch and the number of routed models', () => {
    const provider = makeProvider({
      catalog_provider_id: 'deepseek',
      model_catalog_routes: { a: { format: 'openai_chat' }, b: { format: 'openai_chat' } },
    } as Partial<ProviderConfig>);
    expect(catalogSummary(provider, { p: { provider_id: 'deepseek', host_matched: false } })).toBe(
      '已识别为目录里的「deepseek」：接口地址与目录不同（只采用格式，地址仍用你填的），2 个模型有格式结论。',
    );
  });

  it('reports routed models that never got a catalog entry', () => {
    const provider = makeProvider({
      model_catalog_routes: { a: { format: 'openai_chat' } },
    } as Partial<ProviderConfig>);
    expect(catalogSummary(provider)).toContain('已有 1 个模型按模型名对上了目录');
  });

  it('renders the catalog-match result text verbatim', () => {
    expect(catalogResultText(null)).toBe('没有返回结果。');
    expect(catalogResultText({})).toBe(
      '没找到对应的目录条目。可以直接填目录条目名，或在模型设置里手动指定接口格式。',
    );
    expect(catalogResultText({ missing: ['a', 'b'] })).toBe(
      '没找到对应的目录条目（2 个模型在目录里没有记录）。可以直接填目录条目名，或在模型设置里手动指定接口格式。',
    );
    expect(catalogResultText({
      provider_id: 'openai',
      routed: 3,
      host_matched: false,
      missing: ['m'],
    })).toBe(
      '识别为目录条目「openai」；3 个模型有格式结论；接口地址与目录不同，只采用了格式；1 个模型目录里没有。',
    );
  });
});

describe('model entries and spec parsing', () => {
  it('flattens models in provider order and keeps the provider::model spec', () => {
    const providers = [
      makeProvider({ name: 'a', models: ['x', 'y'] }),
      makeProvider({ name: 'b', models: ['z'] }),
      makeProvider({ name: 'c' }),
    ];
    expect(allModelEntries(providers)).toEqual([
      { provider: 'a', model: 'x', spec: 'a::x' },
      { provider: 'a', model: 'y', spec: 'a::y' },
      { provider: 'b', model: 'z', spec: 'b::z' },
    ]);
    expect(allModelEntries(null)).toEqual([]);
  });

  it('splits provider::model and keeps a bare model name intact', () => {
    expect(splitModelSpec('p::m')).toEqual({ provider: 'p', model: 'm' });
    expect(splitModelSpec('p::a::b')).toEqual({ provider: 'p', model: 'a::b' });
    expect(splitModelSpec('m')).toEqual({ provider: '', model: 'm' });
  });

  it('deep-clones providers so incoming IPC payloads stay plain objects', () => {
    const providers = [makeProvider({ models: ['m'], model_context_lengths: { m: 1 } })];
    const clone = cloneProviders(providers);
    expect(clone).toEqual(providers);
    expect(clone).not.toBe(providers);
    expect(clone[0]).not.toBe(providers[0]);
    expect(clone[0].models).not.toBe(providers[0].models);
  });
});
