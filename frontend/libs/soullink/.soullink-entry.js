// esbuild 入口：组装 libs/soullink 模块，挂载 window.Soullink
// 产物 libs/soullink.bundle.js（iife），在 app.js 之前加载
import { createSoullinkLayer } from './runtime.js';
import { mapEmotionToIntent, EMOTION_BRIDGE } from './bridge.js';
import { createAudioLevelAnalyzer } from './audio-level.js';
import { fetchProfileViaIpc, validateProfileObject, deriveFallbackProfile, withStandardFallbackMappings } from './profile.js';
import { attachModelInjection } from './inject.js';

window.Soullink = {
  createLayer: createSoullinkLayer,
  mapEmotionToIntent,
  createAudioLevelAnalyzer,
  fetchProfileViaIpc,
  validateProfile: validateProfileObject,
  deriveFallbackProfile,
  withStandardFallbackMappings,
  attachModelInjection,
  EMOTION_BRIDGE,
};
