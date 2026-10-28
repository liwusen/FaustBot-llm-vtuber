import { ADVANCED_KEYS, SPEECH_PRIVATE_KEYS, SPEECH_PUBLIC_KEYS } from '@/config/constants';
import type { ConfigDict } from '@/config/types';

/**
 * 「语音」模块的纯逻辑：按 TTS/ASR 模式过滤 SPEECH_PUBLIC_KEYS / SPEECH_PRIVATE_KEYS。
 * 逐条对齐旧 `libs/configer/field-render.js` 的 pickModuleFields("speech") 分支。
 */

export interface SpeechModeFlags {
  /** 小写后的 TTS 模式；缺失为空串（旧实现相同）。 */
  modeTts: string;
  /** 小写后的 ASR 模式；缺失为空串。 */
  modeAsr: string;
  /** TTS 或 ASR 任一走 FaustBot Cloud。 */
  isCloud: boolean;
  /** 本地 GPT-SoVITS TTS。 */
  isLocalTts: boolean;
  /** 本地 Whisper ASR。 */
  isWhisperAsr: boolean;
}

export function speechModeFlags(publicConfig: ConfigDict): SpeechModeFlags {
  const modeTts = String(publicConfig?.TTS_MODE || '').toLowerCase();
  const modeAsr = String(publicConfig?.ASR_MODE || '').toLowerCase();
  return {
    modeTts,
    modeAsr,
    isCloud: modeTts === 'faustbot-cloud' || modeAsr === 'faustbot-cloud',
    isLocalTts: modeTts === 'gpt-sovits',
    isWhisperAsr: modeAsr === 'whisper',
  };
}

/** 公共配置键当前模式下是否显示。 */
export function filterSpeechPublicKey(key: string, flags: SpeechModeFlags): boolean {
  if (key.startsWith('OPENAI_TTS_') && flags.modeTts !== 'openai') return false;
  if (key.startsWith('OPENAI_ASR_') && flags.modeAsr !== 'openai') return false;
  if (key.startsWith('MIMO_TTS_') && flags.modeTts !== 'mimo') return false;
  if (key.startsWith('MIMO_ASR_') && flags.modeAsr !== 'mimo') return false;
  if (key.startsWith('WHISPER_') && !flags.isWhisperAsr) return false;
  if (key.startsWith('EDGE_TTS_') && flags.modeTts !== 'edge-tts') return false;
  if (key.startsWith('FAUSTBOT_CLOUD_') && !flags.isCloud) return false;
  if (
    (key === 'TTS_REFER_WAV_PATH' || key === 'TTS_PROMPT_TEXT' || key === 'TTS_PROMPT_LANGUAGE')
    && !flags.isLocalTts
  ) {
    return false;
  }
  return true;
}

/** 私有配置键（密钥）当前模式下是否显示。 */
export function filterSpeechPrivateKey(key: string, flags: SpeechModeFlags): boolean {
  if (key.startsWith('OPENAI_TTS_') && flags.modeTts !== 'openai') return false;
  if (key.startsWith('OPENAI_ASR_') && flags.modeAsr !== 'openai') return false;
  if (key === 'MIMO_API_KEY' && flags.modeTts !== 'mimo' && flags.modeAsr !== 'mimo') return false;
  if (key.startsWith('FAUSTBOT_CLOUD_') && !flags.isCloud) return false;
  return true;
}

export interface SpeechFieldKeys {
  publicKeys: string[];
  privateKeys: string[];
}

/** 旧 pickModuleFields 的 speech 分支：顺序沿用常量表，只保留配置里真实存在的键。 */
export function pickSpeechFieldKeys(
  publicConfig: ConfigDict,
  privateConfig: ConfigDict,
): SpeechFieldKeys {
  const flags = speechModeFlags(publicConfig || {});
  const availablePublic = new Set(Object.keys(publicConfig || {}));
  const availablePrivate = new Set(Object.keys(privateConfig || {}));
  return {
    publicKeys: SPEECH_PUBLIC_KEYS.filter(
      (key) => availablePublic.has(key) && filterSpeechPublicKey(key, flags),
    ),
    privateKeys: SPEECH_PRIVATE_KEYS.filter(
      (key) => availablePrivate.has(key) && filterSpeechPrivateKey(key, flags),
    ),
  };
}

/** 基础配置 / 高级配置拆分；顺序保持输入顺序。 */
export function splitAdvancedKeys(keys: string[]): { basic: string[]; advanced: string[] } {
  const basic: string[] = [];
  const advanced: string[] = [];
  for (const key of keys) {
    if (ADVANCED_KEYS.has(key)) advanced.push(key);
    else basic.push(key);
  }
  return { basic, advanced };
}
