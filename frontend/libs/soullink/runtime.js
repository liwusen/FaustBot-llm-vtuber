// soullink 表演层封装 — 持有 SoullinkRuntime，驱动 update 并缓存快照/参数
// 纯计算层：无 DOM 依赖，可在 node 冒烟测试中运行
import { SoullinkRuntime } from '@soullink-emotion/engine';

export function createSoullinkLayer({ profile, personality, motionStyle, audioLevelAnalyzer, seed }) {
  const runtime = new SoullinkRuntime({
    profile,
    ...(personality ? { personality } : {}),
    motionStyle: { seed: seed || 20240601, ...(motionStyle || {}) },
    audioLevelAnalyzer: audioLevelAnalyzer || null,
  });
  let lastSnapshot = null;
  let lastParams = {};

  return {
    runtime,

    update(nowSeconds, deltaSeconds) {
      lastSnapshot = runtime.update(nowSeconds, deltaSeconds);
      lastParams = (lastSnapshot && lastSnapshot.live2dParams) || {};
      return lastSnapshot;
    },

    getSnapshot() {
      return lastSnapshot || runtime.getSnapshot();
    },

    getParams() {
      return lastParams;
    },

    triggerIntent(intent, nowSeconds) {
      runtime.triggerIntent(intent, nowSeconds);
    },

    setAudioLevelAnalyzer(analyzer) {
      runtime.setAudioLevelAnalyzer(analyzer);
    },

    setVoicePlaybackActive(active) {
      runtime.setVoicePlaybackActive(active);
    },

    startVoiceWaitingMotion(nowSeconds, seedValue, options) {
      return runtime.startVoiceWaitingMotion(nowSeconds, seedValue, options);
    },

    setProfile(profile) {
      runtime.setProfile(profile);
    },

    setLipSyncEnabled(enabled) {
      runtime.setLipSyncEnabled(enabled);
    },

    setIdleEnabled(enabled) {
      runtime.setIdleEnabled(enabled);
    },

    destroy() {
      // 纯计算引擎：无定时器/监听器需要释放
    },
  };
}
