import { describe, expect, it } from 'vitest';
import { SPEECH_PRIVATE_KEYS, SPEECH_PUBLIC_KEYS } from '@/config/constants';
import {
  filterSpeechPrivateKey,
  filterSpeechPublicKey,
  pickSpeechFieldKeys,
  speechModeFlags,
  splitAdvancedKeys,
} from './filters';
import type { SpeechModeFlags } from './filters';

function configs(publicOverrides: Record<string, unknown> = {}, privateOverrides: Record<string, unknown> = {}) {
  const publicConfig: Record<string, unknown> = {};
  for (const key of SPEECH_PUBLIC_KEYS) publicConfig[key] = '';
  publicConfig.TTS_MODE = 'gpt-sovits';
  publicConfig.ASR_MODE = 'whisper';
  const privateConfig: Record<string, unknown> = {};
  for (const key of SPEECH_PRIVATE_KEYS) privateConfig[key] = '********';
  return {
    publicConfig: { ...publicConfig, ...publicOverrides },
    privateConfig: { ...privateConfig, ...privateOverrides },
  };
}

function pick(publicOverrides: Record<string, unknown> = {}, privateOverrides: Record<string, unknown> = {}) {
  const { publicConfig, privateConfig } = configs(publicOverrides, privateOverrides);
  return pickSpeechFieldKeys(publicConfig, privateConfig);
}

function flagsOf(publicOverrides: Record<string, unknown> = {}): SpeechModeFlags {
  return speechModeFlags(configs(publicOverrides).publicConfig);
}

function hasAny(keys: string[], prefix: string): boolean {
  return keys.some((key) => key.startsWith(prefix));
}

describe('语音模式标志（speechModeFlags）', () => {
  it('模式比较不区分大小写，缺失回落到空串', () => {
    expect(flagsOf({ TTS_MODE: 'GPT-SoVITS', ASR_MODE: 'Whisper' })).toEqual({
      modeTts: 'gpt-sovits',
      modeAsr: 'whisper',
      isCloud: false,
      isLocalTts: true,
      isWhisperAsr: true,
    });
    expect(speechModeFlags({})).toEqual({
      modeTts: '',
      modeAsr: '',
      isCloud: false,
      isLocalTts: false,
      isWhisperAsr: false,
    });
  });

  it('TTS 或 ASR 任一为 faustbot-cloud 都算云端', () => {
    expect(flagsOf({ TTS_MODE: 'faustbot-cloud' }).isCloud).toBe(true);
    expect(flagsOf({ ASR_MODE: 'faustbot-cloud' }).isCloud).toBe(true);
    expect(flagsOf({ TTS_MODE: 'openai', ASR_MODE: 'openai' }).isCloud).toBe(false);
  });
});

describe('公共键过滤（gpt-sovits + whisper 默认组合）', () => {
  const keys = pick().publicKeys;

  it('保留基础键、Whisper 与本地参考音频', () => {
    expect(keys).toContain('TTS_MODE');
    expect(keys).toContain('TTS_CHUNK_IDEAL_TOKENS');
    expect(keys).toContain('ASR_MODE');
    expect(keys).toContain('WHISPER_MODEL');
    expect(keys).toContain('WHISPER_LANGUAGE');
    expect(keys).toContain('WHISPER_INITIAL_PROMPT');
    expect(keys).toContain('TTS_REFER_WAV_PATH');
    expect(keys).toContain('TTS_PROMPT_TEXT');
    expect(keys).toContain('TTS_PROMPT_LANGUAGE');
  });

  it('隐藏其它服务商的参数', () => {
    expect(hasAny(keys, 'OPENAI_TTS_')).toBe(false);
    expect(hasAny(keys, 'OPENAI_ASR_')).toBe(false);
    expect(hasAny(keys, 'MIMO_TTS_')).toBe(false);
    expect(hasAny(keys, 'MIMO_ASR_')).toBe(false);
    expect(hasAny(keys, 'EDGE_TTS_')).toBe(false);
    expect(hasAny(keys, 'FAUSTBOT_CLOUD_')).toBe(false);
  });

  it('默认组合下没有可见的私有键', () => {
    expect(pick().privateKeys).toEqual([]);
  });
});

describe('公共键过滤（OpenAI 组合）', () => {
  const keys = pick({ TTS_MODE: 'openai', ASR_MODE: 'openai' }).publicKeys;

  it('保留 OpenAI TTS / ASR 参数', () => {
    expect(keys).toContain('OPENAI_TTS_BASE_URL');
    expect(keys).toContain('OPENAI_TTS_MODEL');
    expect(keys).toContain('OPENAI_TTS_VOICE');
    expect(keys).toContain('OPENAI_ASR_BASE_URL');
    expect(keys).toContain('OPENAI_ASR_MODEL');
  });

  it('隐藏 Whisper、Edge、MiMo、Cloud 与本地参考音频', () => {
    expect(hasAny(keys, 'WHISPER_')).toBe(false);
    expect(hasAny(keys, 'EDGE_TTS_')).toBe(false);
    expect(hasAny(keys, 'MIMO_')).toBe(false);
    expect(hasAny(keys, 'FAUSTBOT_CLOUD_')).toBe(false);
    expect(keys).not.toContain('TTS_REFER_WAV_PATH');
    expect(keys).not.toContain('TTS_PROMPT_TEXT');
    expect(keys).not.toContain('TTS_PROMPT_LANGUAGE');
  });

  it('只开 OpenAI TTS 时 ASR 参数仍隐藏', () => {
    const onlyTts = pick({ TTS_MODE: 'openai', ASR_MODE: 'whisper' }).publicKeys;
    expect(hasAny(onlyTts, 'OPENAI_TTS_')).toBe(true);
    expect(hasAny(onlyTts, 'OPENAI_ASR_')).toBe(false);
    expect(hasAny(onlyTts, 'WHISPER_')).toBe(true);
  });
});

describe('公共键过滤（MiMo 组合）', () => {
  it('MiMo TTS + MiMo ASR：两组参数与私有密钥都可见', () => {
    const { publicKeys, privateKeys } = pick({ TTS_MODE: 'mimo', ASR_MODE: 'mimo' });
    expect(publicKeys).toContain('MIMO_TTS_BASE_URL');
    expect(publicKeys).toContain('MIMO_TTS_MODEL');
    expect(publicKeys).toContain('MIMO_TTS_REFER_WAV_PATH');
    expect(publicKeys).toContain('MIMO_ASR_MODEL');
    expect(publicKeys).toContain('MIMO_ASR_LANGUAGE');
    expect(privateKeys).toEqual(['MIMO_API_KEY']);
  });

  it('只有 TTS 是 MiMo 时保留密钥但隐藏 MiMo ASR 参数', () => {
    const { publicKeys, privateKeys } = pick({ TTS_MODE: 'mimo', ASR_MODE: 'funasr' });
    expect(hasAny(publicKeys, 'MIMO_TTS_')).toBe(true);
    expect(hasAny(publicKeys, 'MIMO_ASR_')).toBe(false);
    expect(privateKeys).toEqual(['MIMO_API_KEY']);
  });

  it('只有 ASR 是 MiMo 时同样保留密钥', () => {
    const { publicKeys, privateKeys } = pick({ TTS_MODE: 'gpt-sovits', ASR_MODE: 'mimo' });
    expect(hasAny(publicKeys, 'MIMO_ASR_')).toBe(true);
    expect(hasAny(publicKeys, 'MIMO_TTS_')).toBe(false);
    expect(privateKeys).toEqual(['MIMO_API_KEY']);
  });

  it('MiMo 语音克隆参考音频属于 MiMo TTS 前缀', () => {
    expect(pick({ TTS_MODE: 'mimo' }).publicKeys).toContain('MIMO_TTS_REFER_WAV_PATH');
    expect(pick({ TTS_MODE: 'gpt-sovits' }).publicKeys).not.toContain('MIMO_TTS_REFER_WAV_PATH');
  });
});

describe('公共键过滤（Edge TTS）', () => {
  const publicKeys = pick({ TTS_MODE: 'edge-tts' }).publicKeys;

  it('保留 Edge 语音与细调参数', () => {
    expect(publicKeys).toContain('EDGE_TTS_VOICE');
    expect(publicKeys).toContain('EDGE_TTS_RATE');
    expect(publicKeys).toContain('EDGE_TTS_PITCH');
    expect(publicKeys).toContain('EDGE_TTS_TIMEOUT_SECONDS');
  });

  it('隐藏参考音频、Whisper 之外的其它模式参数', () => {
    expect(publicKeys).not.toContain('TTS_REFER_WAV_PATH');
    expect(hasAny(publicKeys, 'OPENAI_')).toBe(false);
    expect(hasAny(publicKeys, 'MIMO_')).toBe(false);
    expect(hasAny(publicKeys, 'FAUSTBOT_CLOUD_')).toBe(false);
    expect(hasAny(publicKeys, 'WHISPER_')).toBe(true);
  });
});

describe('公共键过滤（FaustBot Cloud）', () => {
  it('TTS 走云端：显示地址与超时，并显示 Service Key', () => {
    const { publicKeys, privateKeys } = pick({ TTS_MODE: 'faustbot-cloud' });
    expect(publicKeys).toContain('FAUSTBOT_CLOUD_BASE_URL');
    expect(publicKeys).toContain('FAUSTBOT_CLOUD_TIMEOUT_SECONDS');
    expect(privateKeys).toEqual(['FAUSTBOT_CLOUD_SERVICE_KEY']);
  });

  it('ASR 走云端同样触发云端键', () => {
    const { publicKeys, privateKeys } = pick({ ASR_MODE: 'faustbot-cloud' });
    expect(publicKeys).toContain('FAUSTBOT_CLOUD_BASE_URL');
    expect(privateKeys).toEqual(['FAUSTBOT_CLOUD_SERVICE_KEY']);
  });

  it('云端模式不影响本地参考音频（TTS 仍是 gpt-sovits 时保留）', () => {
    const { publicKeys } = pick({ ASR_MODE: 'faustbot-cloud' });
    expect(publicKeys).toContain('TTS_REFER_WAV_PATH');
    expect(publicKeys).toContain('TTS_PROMPT_TEXT');
  });
});

describe('公共键过滤（本地 ASR）', () => {
  it('FunASR 下隐藏 Whisper 参数，TTS 参数不受影响', () => {
    const { publicKeys } = pick({ ASR_MODE: 'funasr' });
    expect(hasAny(publicKeys, 'WHISPER_')).toBe(false);
    expect(publicKeys).toContain('TTS_MODE');
    expect(publicKeys).toContain('TTS_REFER_WAV_PATH');
  });

  it('未知 / 空模式只剩基础键', () => {
    const publicKeys = pick({ TTS_MODE: '', ASR_MODE: '' }).publicKeys;
    expect(publicKeys).toEqual(['TTS_MODE', 'TTS_CHUNK_IDEAL_TOKENS', 'ASR_MODE']);
  });
});

describe('键存在性过滤与高级拆分', () => {
  it('只保留配置里真实存在的键', () => {
    const { publicConfig, privateConfig } = configs({ TTS_MODE: 'edge-tts' });
    delete publicConfig.EDGE_TTS_VOICE;
    delete privateConfig.FAUSTBOT_CLOUD_SERVICE_KEY;
    const { publicKeys } = pickSpeechFieldKeys(publicConfig, privateConfig);
    expect(publicKeys).not.toContain('EDGE_TTS_VOICE');
    expect(publicKeys).toContain('EDGE_TTS_RATE');
  });

  it('高级键按常量顺序归入高级区', () => {
    const local = splitAdvancedKeys(pick({ TTS_MODE: 'gpt-sovits', ASR_MODE: 'openai' }).publicKeys);
    expect(local.advanced).toContain('TTS_PROMPT_LANGUAGE');
    expect(local.advanced).toContain('OPENAI_ASR_LANGUAGE');
    expect(local.advanced).toContain('OPENAI_ASR_TEMPERATURE');
    expect(local.basic).toContain('TTS_MODE');
    expect(local.basic).toContain('TTS_CHUNK_IDEAL_TOKENS');
    expect(local.basic).toContain('ASR_MODE');
    expect(local.basic).toContain('TTS_REFER_WAV_PATH');
    expect(local.basic).not.toContain('TTS_PROMPT_LANGUAGE');

    const edge = splitAdvancedKeys(pick({ TTS_MODE: 'edge-tts' }).publicKeys);
    expect(edge.advanced).toContain('EDGE_TTS_RATE');
    expect(edge.advanced).toContain('EDGE_TTS_PITCH');
    expect(edge.advanced).toContain('EDGE_TTS_TIMEOUT_SECONDS');
    expect(edge.basic).toContain('EDGE_TTS_VOICE');
    expect(edge.basic).not.toContain('EDGE_TTS_RATE');
  });

  it('TTS_CHUNK_IDEAL_TOKENS 始终是基础键', () => {
    expect(splitAdvancedKeys(['TTS_CHUNK_IDEAL_TOKENS']).basic).toEqual(['TTS_CHUNK_IDEAL_TOKENS']);
  });
});

describe('单键判定与旧实现逐条对齐', () => {
  const flags = flagsOf({ TTS_MODE: 'gpt-sovits', ASR_MODE: 'whisper' });

  it('本地参考音频只在 gpt-sovits 下可见', () => {
    expect(filterSpeechPublicKey('TTS_REFER_WAV_PATH', flags)).toBe(true);
    const openai = flagsOf({ TTS_MODE: 'openai' });
    expect(filterSpeechPublicKey('TTS_REFER_WAV_PATH', openai)).toBe(false);
    expect(filterSpeechPublicKey('TTS_PROMPT_TEXT', openai)).toBe(false);
    expect(filterSpeechPublicKey('TTS_PROMPT_LANGUAGE', openai)).toBe(false);
  });

  it('私有键：OpenAI 前缀 / MiMo 密钥 / Cloud 服务密钥', () => {
    const openai = flagsOf({ TTS_MODE: 'openai', ASR_MODE: 'openai' });
    expect(filterSpeechPrivateKey('OPENAI_TTS_API_KEY', openai)).toBe(true);
    expect(filterSpeechPrivateKey('OPENAI_ASR_API_KEY', flags)).toBe(false);
    expect(filterSpeechPrivateKey('MIMO_API_KEY', flags)).toBe(false);
    expect(filterSpeechPrivateKey('FAUSTBOT_CLOUD_SERVICE_KEY', flags)).toBe(false);
    const cloud = flagsOf({ TTS_MODE: 'faustbot-cloud' });
    expect(filterSpeechPrivateKey('FAUSTBOT_CLOUD_SERVICE_KEY', cloud)).toBe(true);
  });
});
