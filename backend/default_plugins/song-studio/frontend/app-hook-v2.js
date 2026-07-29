// 歌台 Song Studio — 主窗口挂钩：SING/SINGSTOP 播放、口型同步、TTS 闪避、LRC 歌词、消息暂挂
(function () {
  const api = window.faustAppUI;
  if (!api) return;
  const PLUGIN_ID = 'song-studio';
  const DUCK_GAIN = 0.18;

  const state = {
    audioEl: null,
    audioCtx: null,
    gain: null,
    analyser: null,
    lrc: [],
    lrcIdx: -1,
    ui: null,
    active: false,
  };

  function communicate(payload) {
    return api.communicate(PLUGIN_ID, payload || {}).catch((e) => {
      console.warn('[song-studio] communicate failed', e);
    });
  }

  function parseLrc(text) {
    const out = [];
    if (!text) return out;
    const re = /\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]/g;
    for (const raw of String(text).split(/\r?\n/)) {
      let match;
      const times = [];
      let lastIndex = 0;
      re.lastIndex = 0;
      while ((match = re.exec(raw)) !== null) {
        const ms = match[3] ? Number(String(match[3]).padEnd(3, '0')) : 0;
        times.push(Number(match[1]) * 60 + Number(match[2]) + ms / 1000);
        lastIndex = re.lastIndex;
      }
      const line = raw.slice(lastIndex).trim();
      if (!times.length || !line) continue;
      for (const t of times) out.push({ t, line });
    }
    out.sort((a, b) => a.t - b.t);
    return out;
  }

  function buildUI(title) {
    removeUI();
    const bar = document.createElement('div');
    bar.id = 'song-studio-player';
    bar.innerHTML =
      '<span class="song-studio-note">♪</span>' +
      '<span class="song-studio-title"></span>' +
      '<span class="song-studio-lyric"></span>' +
      '<button class="song-studio-stop" title="停止演唱">■</button>';
    bar.querySelector('.song-studio-title').textContent = title || '';
    bar.querySelector('.song-studio-stop').addEventListener('click', () => {
      stopLocal();
      communicate({ action: 'stop_sing' });
    });
    document.body.appendChild(bar);
    state.ui = bar;
  }

  function removeUI() {
    if (state.ui) {
      try { state.ui.remove(); } catch (e) {}
      state.ui = null;
    }
  }

  function updateLyric(currentTime) {
    if (!state.ui || !state.lrc.length) return;
    let idx = -1;
    for (let i = 0; i < state.lrc.length; i++) {
      if (state.lrc[i].t <= currentTime) idx = i; else break;
    }
    if (idx !== state.lrcIdx) {
      state.lrcIdx = idx;
      const el = state.ui.querySelector('.song-studio-lyric');
      if (el) el.textContent = idx >= 0 ? state.lrc[idx].line : '';
    }
  }

  function cleanupAudio() {
    if (state.audioEl) {
      try { state.audioEl.pause(); state.audioEl.src = ''; } catch (e) {}
    }
    if (state.audioCtx) {
      try { state.audioCtx.close(); } catch (e) {}
    }
    state.audioEl = null;
    state.audioCtx = null;
    state.gain = null;
    state.analyser = null;
    state.lrc = [];
    state.lrcIdx = -1;
  }

  function stopLocal() {
    if (!state.active && !state.audioEl) return;
    state.active = false;
    try { api.detachLipSyncAnalyser(); } catch (e) {}
    try { api.holdChat(false); } catch (e) {}
    cleanupAudio();
    removeUI();
  }

  function finishSong() {
    stopLocal();
    communicate({ action: 'song_finished' });
  }

  async function startSong(payload) {
    stopLocal();
    if (!payload || !payload.url) return;
    try {
      const audioEl = new Audio(payload.url);
      audioEl.crossOrigin = 'anonymous';
      const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
      try { audioCtx.resume && audioCtx.resume(); } catch (e) {}
      const source = audioCtx.createMediaElementSource(audioEl);
      const gain = audioCtx.createGain();
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(gain);
      gain.connect(analyser);
      analyser.connect(audioCtx.destination);

      state.audioEl = audioEl;
      state.audioCtx = audioCtx;
      state.gain = gain;
      state.analyser = analyser;
      state.lrc = parseLrc(payload.lyrics);
      state.lrcIdx = -1;
      state.active = true;

      buildUI(payload.title);
      audioEl.addEventListener('timeupdate', () => updateLyric(audioEl.currentTime));
      audioEl.onended = () => finishSong();

      await audioEl.play();
      api.attachLipSyncAnalyser(analyser);
      api.holdChat(true);
    } catch (e) {
      console.error('[song-studio] play failed', e);
      stopLocal();
      communicate({ action: 'song_finished' });
    }
  }

  function setDuck(ducked) {
    if (!state.gain || !state.audioCtx) return;
    const target = ducked ? DUCK_GAIN : 1.0;
    try {
      const now = state.audioCtx.currentTime;
      state.gain.gain.cancelScheduledValues(now);
      state.gain.gain.linearRampToValueAtTime(target, now + 0.25);
    } catch (e) {
      state.gain.gain.value = target;
    }
  }

  window.addEventListener('faust-tts-start', () => {
    if (state.active) setDuck(true);
  });
  window.addEventListener('faust-tts-end', () => {
    if (!state.active) return;
    setDuck(false);
    // TTS 会接管口型，结束后把口型交还给歌声
    if (state.analyser) {
      try { api.attachLipSyncAnalyser(state.analyser); } catch (e) {}
    }
  });

  api.registerCommandHandler(async (cmd, arg) => {
    if (cmd === 'SING') {
      let payload = null;
      try { payload = JSON.parse(arg); } catch (e) {
        console.warn('[song-studio] invalid SING payload', arg);
        return true;
      }
      startSong(payload);
      return true;
    }
    if (cmd === 'SINGSTOP') {
      stopLocal();
      return true;
    }
    return false;
  });

  if (typeof api.registerSidePanelGroup === 'function' && typeof api.setSidePanelRender === 'function') {
    api.registerSidePanelGroup({ id: PLUGIN_ID, label: '歌台', plugin: PLUGIN_ID, order: 220 });
    api.setSidePanelRender(PLUGIN_ID, function (container) {
      const statusRow = document.createElement('div');
      statusRow.className = 'lsp-row';
      const statusText = document.createElement('span');
      statusText.textContent = '加载中...';
      const stopBtn = document.createElement('button');
      stopBtn.className = 'song-studio-lsp-btn';
      stopBtn.textContent = '停止';
      stopBtn.style.display = 'none';
      stopBtn.addEventListener('click', function () {
        stopLocal();
        communicate({ action: 'stop_sing' }).then(load);
      });
      statusRow.append(statusText, stopBtn);
      container.appendChild(statusRow);

      const listWrap = document.createElement('div');
      container.appendChild(listWrap);

      async function load() {
        try {
          const [status, songs] = await Promise.all([
            api.communicate(PLUGIN_ID, { action: 'status' }),
            api.communicate(PLUGIN_ID, { action: 'list_songs' }),
          ]);
          const singing = status && status.singing;
          statusText.textContent = singing ? ('演唱中：' + (singing.song || '')) : '未在演唱';
          stopBtn.style.display = singing ? '' : 'none';
          listWrap.innerHTML = '';
          const ready = ((songs && songs.items) || []).filter(function (s) { return s.cached; });
          if (!ready.length) {
            const empty = document.createElement('div');
            empty.className = 'lsp-row';
            empty.textContent = '暂无已转换歌曲';
            listWrap.appendChild(empty);
            return;
          }
          for (const song of ready) {
            const row = document.createElement('div');
            row.className = 'lsp-row';
            const name = document.createElement('span');
            name.textContent = song.name;
            const playBtn = document.createElement('button');
            playBtn.className = 'song-studio-lsp-btn';
            playBtn.textContent = '演唱';
            playBtn.addEventListener('click', function () {
              api.communicate(PLUGIN_ID, { action: 'sing', name: song.name }).then(load).catch(function (e) {
                console.warn('[song-studio] sing failed', e);
              });
            });
            row.append(name, playBtn);
            listWrap.appendChild(row);
          }
        } catch (e) {
          statusText.textContent = '歌台状态不可用';
        }
      }

      load();
    });
  }
})();
