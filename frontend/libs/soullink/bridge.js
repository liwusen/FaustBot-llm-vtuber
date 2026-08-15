// FaustBot 6维主导情绪 → soullink EmotionIntent 桥接（§3.3 默认映射，可配置）
// emotion-engine 插件的情绪向量取值 0~10（默认 curiosity=4.0）→ 归一化 0~1
import { clamp } from '@soullink-emotion/engine';

export const EMOTION_BRIDGE = Object.freeze({
  joy: { emotion: 'happy', highIntensity: { threshold: 0.7, emotion: 'excited' } },
  irritation: { emotion: 'anger' },
  pride: { emotion: 'happy' },
  curiosity: { emotion: 'curious' },
  sharpness: { emotion: 'anger' },
  boredom: { emotion: 'tired' },
});

const FALLBACK = Object.freeze({ emotion: 'calm' });

export function mapEmotionToIntent(dominantKey, dominantValue, contextTags) {
  const key = String(dominantKey || '').toLowerCase();
  const entry = EMOTION_BRIDGE[key] || FALLBACK;
  let emotion = entry.emotion;
  const intensity = clamp(Number(dominantValue) / 10, 0, 1);
  if (entry.highIntensity && intensity >= entry.highIntensity.threshold) {
    emotion = entry.highIntensity.emotion;
  }
  return {
    emotion,
    intensity,
    contextTags: Array.isArray(contextTags) ? contextTags.map(String) : [],
  };
}
