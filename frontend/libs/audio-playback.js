// 音频播放与口型同步模块 — WebAudio 分析和 TTS 合成
// 用法: const audio = initAudioPlayback({ ttsEndpoint, getTtsLang, ... });

import { normalizeTtsText } from './text-utils.js';

export function initAudioPlayback({
  ttsEndpoint,
  getTtsLang,
  getModelType,
  getVrmScene,
  getCurrentModel,
  getLipSyncParamIds,
  showOverlay,
  stopBackgroundAudio,
}) {
  let audioEl = null;
  let audioCtx = null;
  let analyser = null;
  let dataArray = null;
  let sourceNode = null;
  let rafId = null;

  function setModelLipSyncValue(value) {
    const currentModel = getCurrentModel();
    if (!currentModel) return;
    const mouth = Math.max(0, Math.min(1, Number(value) || 0));
    const ids = getLipSyncParamIds();
    const paramIds = Array.isArray(ids) && ids.length ? ids : ['ParamMouthOpenY'];
    try {
      if (currentModel.internalModel && currentModel.internalModel.coreModel && typeof currentModel.internalModel.coreModel.setParameterValueById === 'function') {
        for (const paramId of paramIds) currentModel.internalModel.coreModel.setParameterValueById(paramId, mouth);
        return;
      }
      if (typeof currentModel.setMouthOpenY === 'function') {
        currentModel.setMouthOpenY(mouth);
      }
    } catch (e) { /* ignore if model API differs */ }
  }

  function stopAudio() {
    const modelType = getModelType();
    const vrmScene = getVrmScene();
    const currentModel = getCurrentModel();

    if (audioEl) {
      try { audioEl.pause(); audioEl.currentTime = 0; } catch (e) {}
    }
    if (modelType === 'vrm' && vrmScene) {
      vrmScene.stopLipSync();
    } else if (currentModel) {
      try { setModelLipSyncValue(0); } catch (e) {}
    }
    if (rafId) cancelAnimationFrame(rafId);
    if (sourceNode) { try { sourceNode.disconnect(); } catch (e) {} sourceNode = null; }
    if (analyser) { analyser.disconnect(); analyser = null; }
    if (audioCtx) { try { audioCtx.close(); } catch (e) {} audioCtx = null; }
  }

  function startMouthSyncFromFile(file) {
    stopAudio();
    const modelType = getModelType();
    const vrmScene = getVrmScene();

    if (!file) return;
    audioEl = new Audio(URL.createObjectURL(file));
    audioEl.crossOrigin = 'anonymous';
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    try { audioCtx.resume && audioCtx.resume(); } catch (e) {}
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 2048;
    dataArray = new Uint8Array(analyser.fftSize);
    sourceNode = audioCtx.createMediaElementSource(audioEl);
    sourceNode.connect(analyser);
    analyser.connect(audioCtx.destination);
    audioEl.onended = () => {
      if (modelType === 'vrm' && vrmScene) {
        vrmScene.stopLipSync();
      } else {
        try { setModelLipSyncValue(0); } catch (e) {}
      }
    };
    audioEl.play().catch(() => { /* autoplay may be blocked */ });

    if (modelType === 'vrm' && vrmScene) {
      vrmScene.startLipSync(analyser);
      return;
    }

    function tick() {
      const currentModel = getCurrentModel();
      analyser.getByteTimeDomainData(dataArray);
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) { const v = (dataArray[i] - 128) / 128; sum += v * v; }
      const rms = Math.sqrt(sum / dataArray.length);
      const mouth = Math.min(1, Math.max(0, (rms * 5)));
      if (currentModel) {
        try { setModelLipSyncValue(mouth); } catch (e) { /* ignore if model API differs */ }
      }
      rafId = requestAnimationFrame(tick);
    }
    rafId = requestAnimationFrame(tick);
  }

  async function synthesizeAndPlay(text, lang) {
    if (!text || text.trim().length === 0) return;
    const TTS_SPLIT_LIMIT = 100;
    const endpoint = ttsEndpoint;

    function splitText(input, maxLen) {
      input = normalizeTtsText(input).trim();
      const out = [];
      if (input.length <= maxLen) return [input];
      const splitRe = /([。！？!?；;，,，、\n]+)/g;
      let parts = input.split(splitRe).filter(s => s && s.trim().length > 0);
      let cur = '';
      for (let p of parts) {
        if ((cur + p).length <= maxLen) { cur += p; } else {
          if (cur) out.push(cur);
          if (p.length > maxLen) {
            for (let i = 0; i < p.length; i += maxLen) out.push(p.slice(i, i + maxLen));
            cur = '';
          } else { cur = p; }
        }
      }
      if (cur) out.push(cur);
      if (out.length === 0) {
        for (let i = 0; i < input.length; i += maxLen) out.push(input.slice(i, i + maxLen));
      }
      return out;
    }

    function makeWaiter() {
      let resolveFn = null;
      const p = new Promise((res) => { resolveFn = res; });
      return { promise: p, resolve: resolveFn };
    }

    function playSingleBlob(blob) {
      return new Promise((resolve) => {
        try { stopAudio(); } catch (e) {}
        startMouthSyncFromFile(blob);
        const ttsStatus = document.getElementById('ttsStatus');
        if (ttsStatus) ttsStatus.textContent = '播放中';
        try {
          if (audioEl && typeof audioEl.addEventListener === 'function') {
            const onEnd = () => { try { audioEl.removeEventListener('ended', onEnd); } catch (e) {} resolve(); };
            audioEl.addEventListener('ended', onEnd);
          } else {
            const waiter = setInterval(() => {
              if (!audioEl || audioEl.ended) { clearInterval(waiter); resolve(); }
            }, 200);
          }
        } catch (e) { console.warn('attach onended failed', e); resolve(); }
      });
    }

    const chunks = splitText(text, TTS_SPLIT_LIMIT);
    if (chunks.length === 0) return;

    const ttsBtn = document.getElementById('ttsBtn');
    const ttsStatus = document.getElementById('ttsStatus');
    if (ttsBtn) ttsBtn.disabled = true;
    if (ttsStatus) ttsStatus.textContent = '合成中...';

    let fetchesPending = chunks.length;
    let fetchHadError = false;
    const blobs = new Array();
    const waiters = new Array();
    for (let i = 0; i < chunks.length; i++) { waiters[i] = makeWaiter(); blobs[i] = null; }

    const fetchPromises = chunks.map((chunk, i) => (async () => {
      const ttsLang = typeof getTtsLang === 'function' ? getTtsLang() : (lang || 'zh');
      const payload = { text: chunk, text_language: ttsLang, lang: ttsLang };
      try {
        const r = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        if (!r.ok) {
          const txt = await r.text();
          console.warn('TTS chunk failed', r.status, txt);
          fetchHadError = true;
        } else {
          const contentType = r.headers.get('content-type') || 'audio/wav';
          const ab = await r.arrayBuffer();
          blobs[i] = new Blob([ab], { type: contentType });
        }
      } catch (err) { console.warn('TTS chunk fetch err', err); fetchHadError = true; }
      finally { fetchesPending -= 1; try { waiters[i].resolve(); } catch (e) {} }
    })());

    try {
      for (let i = 0; i < chunks.length; i++) {
        try { await waiters[i].promise; } catch (e) {}
        if (blobs[i]) {
          await playSingleBlob(blobs[i]);
        } else {
          console.warn('Skipping missing TTS chunk', i);
        }
      }
      try { await Promise.all(fetchPromises); } catch (e) {}
      if (fetchHadError && typeof showOverlay === 'function') showOverlay('部分 TTS 分段合成失败，已跳过错误片段');
    } catch (e) { console.warn('TTS allDone err', e); }
    finally {
      if (ttsBtn) ttsBtn.disabled = false;
      if (ttsStatus) ttsStatus.textContent = '已完成';
    }
  }

  function initEvents() {
    const playAudioBtn = document.getElementById('playAudio');
    const stopAudioBtn = document.getElementById('stopAudio');
    const ttsBtn = document.getElementById('ttsBtn');
    const audioFile = document.getElementById('audioFile');

    if (playAudioBtn) {
      playAudioBtn.addEventListener('click', () => {
        const f = audioFile && audioFile.files && audioFile.files[0];
        if (!f) { alert('请选择音频文件'); return; }
        startMouthSyncFromFile(f);
      });
    }
    if (stopAudioBtn) {
      stopAudioBtn.addEventListener('click', () => { stopAudio(); });
    }
    if (ttsBtn) {
      const ttsText = document.getElementById('ttsText');
      const ttsLang = document.getElementById('ttsLang');
      ttsBtn.addEventListener('click', () => {
        const text = ttsText ? ttsText.value : '';
        const lang = ttsLang ? ttsLang.value : 'zh';
        synthesizeAndPlay(text, lang);
      });
    }
  }
  function playOrdered(blob) {
    return new Promise((resolve) => {
      try { stopAudio(); } catch (e) {}
      startMouthSyncFromFile(blob);
      const ttsStatus = document.getElementById('ttsStatus');
      if (ttsStatus) ttsStatus.textContent = '\u64AD\u653E\u4E2D';
      try {
        if (audioEl && typeof audioEl.addEventListener === 'function') {
          const onEnd = () => { try { audioEl.removeEventListener('ended', onEnd); } catch (e) {} resolve(); };
          audioEl.addEventListener('ended', onEnd);
        } else {
          const waiter = setInterval(() => {
            if (!audioEl || audioEl.ended) { clearInterval(waiter); resolve(); }
          }, 200);
        }
      } catch (e) { resolve(); }
    });
  }


  return { stopAudio, startMouthSyncFromFile, synthesizeAndPlay, playOrdered, initEvents };
}
