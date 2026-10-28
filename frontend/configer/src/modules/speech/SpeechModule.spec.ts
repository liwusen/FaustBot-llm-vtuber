import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { mount } from '@vue/test-utils';
import type { FrontendApi } from '@/api/types';
import { SPEECH_PRIVATE_KEYS, SPEECH_PUBLIC_KEYS } from '@/config/constants';
import { useConfigStore } from '@/stores/config';
import { useUiStore } from '@/stores/ui';
import SpeechModule from './SpeechModule.vue';
import EdgeTtsVoicePicker from './EdgeTtsVoicePicker.vue';

interface Call {
  method: string;
  path: string;
  payload?: unknown;
  query?: unknown;
}

function installApi(publicOverrides: Record<string, unknown> = {}): { calls: Call[] } {
  const publicConfig: Record<string, unknown> = {};
  for (const key of SPEECH_PUBLIC_KEYS) publicConfig[key] = '';
  publicConfig.TTS_MODE = 'gpt-sovits';
  publicConfig.ASR_MODE = 'whisper';
  publicConfig.TTS_CHUNK_IDEAL_TOKENS = 30;
  Object.assign(publicConfig, publicOverrides);

  const privateConfig: Record<string, unknown> = {};
  for (const key of SPEECH_PRIVATE_KEYS) privateConfig[key] = '********';

  const calls: Call[] = [];
  const api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string, payload?: unknown, query?: unknown) => {
      calls.push({ method, path, payload, query });
      if (path === '/faust/admin/config') return { public: publicConfig, private: privateConfig };
      if (path === '/faust/edge-tts/languages') return { languages: ['en-US', 'zh-CN'] };
      if (path === '/faust/edge-tts/genders') return { genders: ['Female', 'Male'] };
      if (path === '/faust/edge-tts/voices/search') {
        return {
          voices: [
            {
              name: 'Microsoft Xiaoxiao',
              voice_id: 'zh-CN-XiaoxiaoNeural',
              language: 'zh-CN',
              gender: 'Female',
              voice_personalities: 'Warm',
            },
          ],
        };
      }
      return { ok: true };
    }),
    configHttpRequest: vi.fn(async () => ({ ok: true })),
  } as unknown as FrontendApi;
  window.api = api;
  return { calls };
}

async function makeConfig(overrides: Record<string, unknown> = {}): Promise<{ calls: Call[] }> {
  const handle = installApi(overrides);
  setActivePinia(createPinia());
  await useConfigStore().loadConfig();
  return handle;
}

function cardKeys(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper.findAll('.card-key').map((item) => item.text());
}

describe('SpeechModule smoke', () => {
  beforeEach(async () => {
    await makeConfig();
  });

  it('renders default gpt-sovits + whisper fields and the two action cards', () => {
    const wrapper = mount(SpeechModule);
    const keys = cardKeys(wrapper);
    expect(keys).toContain('TTS_MODE');
    expect(keys).toContain('WHISPER_MODEL');
    expect(keys).toContain('TTS_REFER_WAV_PATH');
    expect(keys).not.toContain('EDGE_TTS_VOICE');
    expect(keys).not.toContain('OPENAI_TTS_MODEL');
    expect(wrapper.text()).toContain('TTS 服务即时应用');
    expect(wrapper.text()).toContain('local TTS 模式下可把参考音频参数即时同步到 5000 端口服务。');
    expect(wrapper.text()).toContain('应用参考音频到 TTS 服务');
    expect(wrapper.text()).toContain('Edge TTS 语音选择器');
    expect(wrapper.text()).toContain('点击打开语音选择器，浏览和选择可用的 Edge TTS 语音。');
    expect(wrapper.text()).toContain('选择 Edge TTS 语音');
  });

  it('re-filters fields when the TTS mode changes', async () => {
    const wrapper = mount(SpeechModule);
    useConfigStore().updateValue('public', 'TTS_MODE', 'edge-tts');
    await wrapper.vm.$nextTick();
    const keys = cardKeys(wrapper);
    expect(keys).toContain('EDGE_TTS_VOICE');
    expect(keys).not.toContain('TTS_REFER_WAV_PATH');
  });

  it('opens the advanced fold and applies the TTS refer to the local service', async () => {
    const { calls } = installApi();
    setActivePinia(createPinia());
    await useConfigStore().loadConfig();
    const wrapper = mount(SpeechModule);
    const divider = wrapper.find('.advanced-divider');
    expect(divider.exists()).toBe(true);
    await divider.trigger('click');
    expect(wrapper.find('.advanced-body').classes()).toContain('open');

    const ui = useUiStore();
    const button = wrapper.findAll('button').find((item) => item.text() === '应用参考音频到 TTS 服务');
    await button!.trigger('click');
    await vi.waitFor(() => expect(ui.bannerText).toBe('TTS 参考音频已同步到本地服务。'));
    expect(window.api?.configHttpRequest).toHaveBeenCalledWith(
      'POST',
      'http://127.0.0.1:5000/change_refer',
      expect.any(Object),
    );
    expect(calls.some((call) => call.method === 'GET' && call.path === '/faust/admin/config')).toBe(true);
  });

  it('opens the Edge TTS picker modal', async () => {
    const wrapper = mount(SpeechModule);
    const ui = useUiStore();
    const button = wrapper.findAll('button').find((item) => item.text() === '选择 Edge TTS 语音');
    await button!.trigger('click');
    expect(ui.modalOpen).toBe(true);
    expect(ui.modalTitle).toBe('Edge TTS 语音管理');
  });
});

describe('EdgeTtsVoicePicker smoke', () => {
  beforeEach(async () => {
    await makeConfig();
  });

  it('loads filters and voices, then writes the chosen voice', async () => {
    const wrapper = mount(EdgeTtsVoicePicker);
    await vi.waitFor(() => expect(wrapper.text()).toContain('Microsoft Xiaoxiao'));
    expect(wrapper.findAll('.filter-select option').length).toBeGreaterThan(2);
    expect(wrapper.text()).toContain('标识：zh-CN-XiaoxiaoNeural');
    expect(wrapper.text()).toContain('语言：zh-CN | 性别：Female');
    expect(wrapper.text()).toContain('特征: Warm');

    await wrapper.find('.voice-item').trigger('click');
    expect(wrapper.find('.voice-item').classes()).toContain('is-selected');
    expect(wrapper.text()).toContain('已选择: Microsoft Xiaoxiao');

    const confirm = wrapper.findAll('button').find((item) => item.text() === '确认选择');
    await confirm!.trigger('click');
    const config = useConfigStore();
    const ui = useUiStore();
    expect(config.publicConfig.EDGE_TTS_VOICE).toBe('zh-CN-XiaoxiaoNeural');
    expect(config.dirtyPublic.get('EDGE_TTS_VOICE')).toBe('zh-CN-XiaoxiaoNeural');
    expect(ui.bannerText).toBe('已选择语音: Microsoft Xiaoxiao');
  });

  it('refreshes the cache then re-searches', async () => {
    const wrapper = mount(EdgeTtsVoicePicker);
    await vi.waitFor(() => expect(wrapper.text()).toContain('Microsoft Xiaoxiao'));
    const refresh = wrapper.findAll('button').find((item) => item.text() === '刷新');
    await refresh!.trigger('click');
    await vi.waitFor(() => (
      expect(window.api?.configRequest).toHaveBeenCalledWith(
        'POST',
        '/faust/edge-tts/cache/refresh',
        {},
        undefined,
        undefined,
      )
    ));
  });
});
