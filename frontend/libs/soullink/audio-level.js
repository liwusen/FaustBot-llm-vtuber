// 现有 analyser（getByteTimeDomainData）→ engine AudioLevelAnalyzer 适配
// 口型增益与现有实现一致：mouth = min(1, rms * 5)
export function createAudioLevelAnalyzer(analyser) {
  if (!analyser || typeof analyser.getByteTimeDomainData !== 'function') return null;
  const data = new Uint8Array(analyser.fftSize || 2048);
  let peak = 0;
  let lastLevel = 0;

  function readLevel() {
    try {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        const v = (data[i] - 128) / 128;
        sum += v * v;
      }
      lastLevel = Math.min(1, Math.sqrt(sum / data.length) * 5);
    } catch (e) {
      lastLevel = 0;
    }
    return lastLevel;
  }

  return {
    // engine 每帧先调 getLevel() 再调 getPeak()：getLevel 计算一次，getPeak 复用结果
    getLevel() {
      return readLevel();
    },
    // 上升沿峰值 + 缓慢衰减（对齐 soullink 语音峰值重音 accent）
    getPeak() {
      if (lastLevel > peak) peak = lastLevel;
      else peak = Math.max(0, peak - 0.04);
      return peak;
    },
    isAvailable() {
      try {
        const ctx = analyser.context;
        return !!(ctx && ctx.state && ctx.state !== 'closed');
      } catch (e) {
        return false;
      }
    },
    reset() {
      peak = 0;
      lastLevel = 0;
    },
  };
}
