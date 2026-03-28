

// 简单的 Live2D 展示 demo，依赖 PIXI 和 pixi-live2d-display
(() => {

  const defaultModel = '2D/hiyori_pro_zh/hiyori_pro_t11.model3.json';

  const modelPathInput = document.getElementById('modelPath');
  const loadBtn = document.getElementById('loadBtn');
  const resetBtn = document.getElementById('resetBtn');
  const clickThrough = document.getElementById('clickThrough');
  const audioFile = document.getElementById('audioFile');
  const playAudioBtn = document.getElementById('playAudio');
  const stopAudioBtn = document.getElementById('stopAudio');
  const modelScaleSlider = document.getElementById('modelScaleSlider');
  const modelScaleValue = document.getElementById('modelScaleValue');
  const ttsText = document.getElementById('ttsText');
  const ttsBtn = document.getElementById('ttsBtn');
  const ttsLang = document.getElementById('ttsLang');
  const ttsStatus = document.getElementById('ttsStatus');
  const startAsrBtn = document.getElementById('startAsrBtn');
  const stopAsrBtn = document.getElementById('stopAsrBtn');
  const asrStatusEl = document.getElementById('asrStatus');
  const chatStatusEl = document.getElementById('chatStatus');
  const asrTextEl = document.getElementById('asrText');
  const vadProbEl = document.getElementById('vadProb');
  const vadProbLabel = document.getElementById('vadProbLabel');
  const textChatInput = document.getElementById('textChatInput');
  const textChatSendBtn = document.getElementById('textChatSendBtn');
  const textChatStatus = document.getElementById('textChatStatus');
  const trayToggleBtn = document.getElementById('trayToggleBtn');
  const openConfigBtn = document.getElementById('openConfigBtn');
  const quickController = document.getElementById('modelQuickController');
  const quickToggleAsrBtn = document.getElementById('quickToggleAsr');
  const quickStopMediaBtn = document.getElementById('quickStopMedia');
  const quickRandomMotionBtn = document.getElementById('quickRandomMotion');
  const quickScaleUpBtn = document.getElementById('quickScaleUp');
  const quickScaleDownBtn = document.getElementById('quickScaleDown');
  let Live2DModel=null;
  let nimbleWindows = new Map();
  let activeNimbleContext = null;
  let textChatSending = false;
  let availableMotions = [];
  let hoverModel = false;
  let hoverQuickController = false;
  let interactionLocked = false;
  let clickThroughController = null;
  let asrBubbleCurrentX = 0;
  let asrBubbleCurrentY = 0;
  let asrBubbleTargetX = 0;
  let asrBubbleTargetY = 0;
  let asrBubbleInitialized = false;
  let asrBubbleSource = 'ai';
  let asrTextPinnedToBottom = true;

  function ensureNimbleHost(){
    let host = document.getElementById('nimble-host');
    if (host) return host;
    host = document.createElement('div');
    host.id = 'nimble-host';
    host.style.position = 'fixed';
    host.style.right = '24px';
    host.style.top = '120px';
    host.style.zIndex = '1600';
    host.style.display = 'flex';
    host.style.flexDirection = 'column';
    host.style.gap = '12px';
    host.style.pointerEvents = 'auto';
    document.body.appendChild(host);
    return host;
  }

  function installNimbleAPI(callbackId){
    activeNimbleContext = { callbackId };
    window.nimble = {
      submit: async (data)=>{
        const currentId = activeNimbleContext && activeNimbleContext.callbackId ? activeNimbleContext.callbackId : callbackId;
        const r = await fetch(NIMBLE_CALLBACK_ENDPOINT, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ callback_id: currentId, data, close: true })
        });
        const j = await r.json().catch(()=>({}));
        if (!r.ok || j.error) throw new Error(j.error || `nimble submit failed: ${r.status}`);
        closeNimbleWindow(currentId, false);
        return j;
      },
      close: async (reason='closed_by_user')=>{
        const currentId = activeNimbleContext && activeNimbleContext.callbackId ? activeNimbleContext.callbackId : callbackId;
        const r = await fetch(NIMBLE_CLOSE_ENDPOINT, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ callback_id: currentId, reason })
        });
        const j = await r.json().catch(()=>({}));
        if (!r.ok || j.error) throw new Error(j.error || `nimble close failed: ${r.status}`);
        closeNimbleWindow(currentId, false);
        return j;
      }
    };
  }

  function closeNimbleWindow(callbackId, notifyBackend = true, reason = 'closed_locally'){
    const win = nimbleWindows.get(callbackId);
    if (win && win.parentNode) win.parentNode.removeChild(win);
    nimbleWindows.delete(callbackId);
    if (activeNimbleContext && activeNimbleContext.callbackId === callbackId){
      activeNimbleContext = null;
    }
    if (!notifyBackend) return;
    fetch(NIMBLE_CLOSE_ENDPOINT, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ callback_id: callbackId, reason })
    }).catch((e)=>console.warn('nimble close notify failed', e));
  }

  function showNimbleWindow(payload){
    if (!payload || !payload.callback_id) return;
    const host = ensureNimbleHost();
    closeNimbleWindow(payload.callback_id, false);

    const shell = document.createElement('div');
    shell.className = 'nimble-window';
    shell.dataset.callbackId = payload.callback_id;
    shell.style.width = '360px';
    shell.style.maxWidth = '40vw';
    shell.style.maxHeight = '70vh';
    shell.style.overflow = 'hidden';
    shell.style.background = 'rgba(20,24,30,0.92)';
    shell.style.border = '1px solid rgba(255,255,255,0.12)';
    shell.style.borderRadius = '14px';
    shell.style.boxShadow = '0 10px 30px rgba(0,0,0,0.4)';
    shell.style.color = '#fff';
    shell.style.backdropFilter = 'blur(8px)';

    const header = document.createElement('div');
    header.style.display = 'flex';
    header.style.justifyContent = 'space-between';
    header.style.alignItems = 'center';
    header.style.padding = '10px 12px';
    header.style.background = 'rgba(255,255,255,0.06)';
    header.style.fontWeight = '700';
    header.textContent = payload.title || '灵动交互';

    const closeBtn = document.createElement('button');
    closeBtn.textContent = '×';
    closeBtn.style.marginLeft = '12px';
    closeBtn.style.background = 'transparent';
    closeBtn.style.color = '#fff';
    closeBtn.style.border = 'none';
    closeBtn.style.fontSize = '20px';
    closeBtn.style.cursor = 'pointer';
    closeBtn.onclick = ()=> closeNimbleWindow(payload.callback_id, true, 'closed_by_user');
    header.appendChild(closeBtn);

    const body = document.createElement('div');
    body.style.padding = '12px';
    body.style.overflow = 'auto';
    body.style.maxHeight = 'calc(70vh - 48px)';
    installNimbleAPI(payload.callback_id);
    try{
      body.innerHTML = payload.html || '<div>空窗口</div>';
    }catch(e){
      body.textContent = '灵动窗口 HTML 渲染失败: ' + String(e);
    }

    shell.appendChild(header);
    shell.appendChild(body);
    host.appendChild(shell);
    nimbleWindows.set(payload.callback_id, shell);
  }

  // 创建 PIXI 应用
  const app = new PIXI.Application({
    backgroundAlpha: 0,
    resizeTo: window,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true,
  });

  document.getElementById('app').appendChild(app.view);

  try{ window.PIXI = PIXI; }catch(e){/* ignore in non-browser env */}

  let currentModel = null;
  let dragging = false;
  let dragOffset = {x:0,y:0};
  // scale control: baseScale is determined from renderer/window; scaleFactor from slider
  let baseScale = 1;
  let scaleFactor = parseFloat(modelScaleSlider ? modelScaleSlider.value : 1.0) || 1.0;
  let _savedModelState = null;

  function applyModelScale(){
    if (!currentModel) return;
    try{
      const s = Math.max(0.1, baseScale * scaleFactor);
      currentModel.scale.set(s);
      if (modelScaleSlider) modelScaleSlider.value = String(scaleFactor);
      if (modelScaleValue) modelScaleValue.textContent = scaleFactor.toFixed(2) + 'x';
      updateQuickControllerPosition();
    }catch(e){console.warn('applyModelScale err', e);}
  }

  function setScaleFactor(nextScale){
    const parsed = Number(nextScale);
    scaleFactor = Math.max(0.1, Math.min(2.0, Number.isFinite(parsed) ? parsed : scaleFactor));
    applyModelScale();
    try{ saveModelState(); }catch(e){}
  }

  function nudgeScale(step){
    setScaleFactor(Math.round((scaleFactor + step) * 100) / 100);
  }

  async function readModelDefinition(path){
    if (!path) return null;
    try{
      const r = await fetch(path);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return await r.json();
    }catch(e){
      console.warn('读取 model3.json 失败', path, e);
      return null;
    }
  }

  function extractMotionNames(modelDef){
    const motions = (((modelDef || {}).FileReferences || {}).Motions) || {};
    return Object.keys(motions);
  }

  function updateQuickAsrButton(){
    if (!quickToggleAsrBtn) return;
    const labelEl = quickToggleAsrBtn.querySelector('.qc-label');
    if (labelEl) labelEl.textContent = asrRunning ? '停听' : 'ASR';
    quickToggleAsrBtn.classList.toggle('active', !!asrRunning);
    quickToggleAsrBtn.title = asrRunning ? '停止语音识别' : '启动语音识别';
  }

  function updateQuickControllerPosition(){
    if (!quickController || !currentModel || !app || !app.renderer) return;
    try{
      const canvasRect = app.renderer.view.getBoundingClientRect();
      const b = currentModel.getBounds();
      const scaleX = canvasRect.width / app.renderer.width;
      const scaleY = canvasRect.height / app.renderer.height;
      const left = canvasRect.left + b.x * scaleX+scaleX * b.width * 0.4;
      const top = canvasRect.top + b.y * scaleY;
      const height = b.height * scaleY;
      quickController.style.left = Math.round(left - 12) + 'px';
      quickController.style.top = Math.round(top + height * 0.45) + 'px';
      quickController.style.transform = 'translate(-50%, -50%)';
      quickController.style.scale= String(1 * scaleX);
    }catch(e){/* ignore */}
  }

  function setQuickControllerVisible(visible){
    if (!quickController) return;
    quickController.classList.toggle('visible', !!visible);
  }

  function refreshQuickControllerVisibility(){
    setQuickControllerVisible(!!currentModel && (hoverModel || hoverQuickController || dragging || interactionLocked));
    updateQuickControllerPosition();
  }

  function isPointOverQuickController(clientX, clientY){
    if (!quickController) return false;
    const rect = quickController.getBoundingClientRect();
    return rect.width > 0 && clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
  }

  function isPointerOnModel(clientX, clientY){
    if (!currentModel || !app || !app.renderer) return false;
    try{
      const canvasRect = app.renderer.view.getBoundingClientRect();
      const rx = (clientX - canvasRect.left) * (app.renderer.width / canvasRect.width);
      const ry = (clientY - canvasRect.top) * (app.renderer.height / canvasRect.height);
      const b = currentModel.getBounds();
      const inBounds = rx >= b.x && rx <= b.x + b.width && ry >= b.y && ry <= b.y + b.height;
      if (!inBounds) return false;
      if (typeof currentModel.hitTest === 'function'){
        const hits = currentModel.hitTest(rx, ry);
        if (Array.isArray(hits) && hits.length > 0) return true;
      }
      return !!(currentModel.containsPoint && currentModel.containsPoint(new PIXI.Point(rx, ry)));
    }catch(e){
      return false;
    }
  }

  function setInteractionLock(locked){
    interactionLocked = !!locked;
    if (clickThroughController) clickThroughController.setInteractiveLock(interactionLocked);
    refreshQuickControllerVisibility();
  }

  function stopBackgroundAudio(){
    if (!bgAudio) return;
    try{ bgAudio.pause(); }catch(e){}
    try{ bgAudio.currentTime = 0; }catch(e){}
    bgAudio = null;
  }

  function playMotionByName(name){
    if (!currentModel || !name) return false;
    try{
      currentModel.motion(name);
      return true;
    }catch(e){
      console.warn('播放 motion 失败', name, e);
      return false;
    }
  }

  function playRandomMotion(){
    const pool = availableMotions.length ? availableMotions : ['Idle'];
    const picked = pool[Math.floor(Math.random() * pool.length)];
    return playMotionByName(picked);
  }

  function interruptPlayback(){
    try{ stopAudio(); }catch(e){}
    try{ stopBackgroundAudio(); }catch(e){}
    try{ resetStreamTtsState(); }catch(e){}
    try{ if (ttsStatus) ttsStatus.textContent = '已打断'; }catch(e){}
  }

  function toggleAsr(){
    if (asrRunning) stopRecording();
    else startRecording();
  }

  function loadSavedModelState(){
    loadModelState();
    if (!currentModel || !_savedModelState) return;
    try{
      const st = _savedModelState;
      const modelPath = (modelPathInput && modelPathInput.value) ? modelPathInput.value.trim() : defaultModel;
      if (st.modelPath && st.modelPath === modelPath){
        if (typeof st.x === 'number') currentModel.x = st.x;
        else console.warn('Saved model state missing x coordinate');
        if (typeof st.y === 'number') currentModel.y = st.y;
        else console.warn('Saved model state missing y coordinate');
        if (typeof st.scaleFactor === 'number'){
          scaleFactor = st.scaleFactor;
          if (modelScaleSlider){
            modelScaleSlider.value = String(scaleFactor);
            modelScaleValue.textContent = scaleFactor.toFixed(2);
          }
          applyModelScale();
        }
      }
    }catch(e){
      console.warn('apply saved model state err', e);
    }
  }

  // Save current model state (path/x/y/scaleFactor) via electron API
  function saveModelState(){
    if (!window.api || !window.api.saveModelState) return;
    try{
      const modelPath = (modelPathInput && modelPathInput.value) ? modelPathInput.value.trim() : defaultModel;
      const st = {
        modelPath: modelPath,
        x: currentModel ? currentModel.x : null,
        y: currentModel ? currentModel.y : null,
        scaleFactor: scaleFactor
      };
      window.api.saveModelState(st).then(res=>{ if (!res || !res.ok) console.warn('saveModelState failed', res); }).catch(e=>console.warn('saveModelState err', e));
    }catch(e){/*ignore*/}
  }

  async function loadModelState(){
    if (!window.api || !window.api.loadModelState) return null;
    try{
      const st = await window.api.loadModelState();
      _savedModelState = st;
      return st;
    }catch(e){ console.warn('loadModelState err', e); return null; }
  }

  // --- ASR / mic recognition state ---
  let micStream = null;
  let micAudioCtx = null;
  let scriptNode = null;
  let micBuffer = [];
  let micBufLen = 0;
  let asrRunning = false;
  const ASR_UPLOAD_INTERVAL_MS = 1200; // 每隔 ~1.2s 上传一段音频
  const TARGET_SAMPLE_RATE = 16000;
  let asrTimer = null;
  const ASR_ENDPOINT = 'http://127.0.0.1:1000/v1/upload_audio';
  // VAD websocket state
  const VAD_WS_URL = 'ws://127.0.0.1:1000/v1/ws/vad';
  let vadWs = null;
  let useVAD = true; // try to use websocket VAD by default
  const VAD_WINDOW_SIZE = 512; // must match backend WINDOW_SIZE
  // streaming buffers: leftover resampled samples, pre-roll frames, and current speech frames
  let leftoverResampled = new Float32Array(0);
  let preBufferFrames = []; // small ring of recent frames to include as pre-roll
  const PRE_ROLL_FRAMES = 8; // each frame is 512 samples -> ~0.256s at 16k
  let uploadFrames = []; // frames collected during speech
  let inSpeech = false;
  let vadEndTimer = null;
  let noVoiceCnt=0;
  const VAD_END_DEBOUNCE_MS = 300;
  loadSavedModelState();
  // convert Float32Array -> Int16 WAV blob at TARGET_SAMPLE_RATE
  function interleaveAndEncodeWav(float32Array, inputSampleRate){
    // resample to TARGET_SAMPLE_RATE
    const resampled = resampleFloat32(float32Array, inputSampleRate, TARGET_SAMPLE_RATE);
    const wavBuffer = encodeWAV(resampled, TARGET_SAMPLE_RATE);
    return new Blob([wavBuffer], { type: 'audio/wav' });
  }

  function resampleFloat32(buffer, srcRate, dstRate){
    if (srcRate === dstRate) return buffer;
    const ratio = srcRate / dstRate;
    const newLen = Math.round(buffer.length / ratio);
    const out = new Float32Array(newLen);
    for (let i = 0; i < newLen; i++){
      const idx = i * ratio;
      const i0 = Math.floor(idx);
      const i1 = Math.min(Math.ceil(idx), buffer.length - 1);
      const t = idx - i0;
      out[i] = (1 - t) * buffer[i0] + t * buffer[i1];
    }
    return out;
  }

  function concatFloat32Arrays(arrays){
    let total = 0;
    for (const a of arrays) total += a.length;
    const out = new Float32Array(total);
    let offset = 0;
    for (const a of arrays){ out.set(a, offset); offset += a.length; }
    return out;
  }

  function floatTo16BitPCM(output, offset, input){
    for (let i = 0; i < input.length; i++, offset += 2) {
      let s = Math.max(-1, Math.min(1, input[i]));
      s = s < 0 ? s * 0x8000 : s * 0x7FFF;
      output.setInt16(offset, s, true);
    }
  }

  function writeString(view, offset, string){
    for (let i = 0; i < string.length; i++){
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  }

  function encodeWAV(samples, sampleRate){
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    /* RIFF identifier */ writeString(view, 0, 'RIFF');
    /* file length */ view.setUint32(4, 36 + samples.length * 2, true);
    /* RIFF type */ writeString(view, 8, 'WAVE');
    /* format chunk identifier */ writeString(view, 12, 'fmt ');
    /* format chunk length */ view.setUint32(16, 16, true);
    /* sample format (raw) */ view.setUint16(20, 1, true);
    /* channel count */ view.setUint16(22, 1, true);
    /* sample rate */ view.setUint32(24, sampleRate, true);
    /* byte rate (sampleRate * blockAlign) */ view.setUint32(28, sampleRate * 2, true);
    /* block align (channelCount * bytesPerSample) */ view.setUint16(32, 2, true);
    /* bits per sample */ view.setUint16(34, 16, true);
    /* data chunk identifier */ writeString(view, 36, 'data');
    /* data chunk length */ view.setUint32(40, samples.length * 2, true);
    floatTo16BitPCM(view, 44, samples);
    return view;
  }

  async function uploadBufferAndShowResult(float32Arr, sampleRate){
    try{
      const blob = interleaveAndEncodeWav(float32Arr, sampleRate);
      console.debug('Uploading WAV blob', { size: blob.size, sampleRate });
      const fd = new FormData();
      fd.append('file', blob, 'chunk.wav');
      asrStatusEl.textContent = '上传识别中...';
      const r = await fetch(ASR_ENDPOINT, { method: 'POST', body: fd });
      const raw = await r.text();
      console.debug('ASR raw response text:', raw, 'status:', r.status);
      let j = null;
      try{ j = JSON.parse(raw); }catch(e){ j = null }
      if (!r.ok){
        asrStatusEl.textContent = `识别服务错误 (${r.status})`;
        showResultBubble('error', 'ASR服务返回错误: ' + raw);
        return;
      }
      if (j && j.status === 'success'){
        const text = j.text || '';
        if (text && text.length > 0){
          showResultBubble('user', text);
          asrStatusEl.textContent = '识别成功';
          // send recognized text to chat websocket if available
          try{ sendToChat(text); }catch(e){}
        } else {
          asrStatusEl.textContent = '识别成功但无文本';
          showResultBubble('error', 'ASR返回但文本为空');
        }
      } else if (j && j.status === 'error'){
        asrStatusEl.textContent = '识别失败';
        showResultBubble('error', 'ASR失败: ' + (j.message || JSON.stringify(j)));
      } else if (j && j.text){
        showResultBubble('user', j.text);
        asrStatusEl.textContent = '识别完成';
      } else {
        asrStatusEl.textContent = '无返回或未知格式';
        showResultBubble('error', 'ASR返回未知格式: ' + raw);
      }
    }catch(err){
      console.error('upload error', err);
      asrStatusEl.textContent = '网络或服务错误';
      showResultBubble('error', '上传或网络错误: ' + String(err));
    }
  }
  //console.log("ASR Result:", asrResult);
  //return;
  // --- Chat via WebSocket to backend (/faust/chat) ---
  const CHAT_HOST = '127.0.0.1';
  const CHAT_PORT = 13900;
  const CHAT_ENDPOINT = `ws://${CHAT_HOST}:${CHAT_PORT}/faust/chat`;
  const NIMBLE_CALLBACK_ENDPOINT = `http://${CHAT_HOST}:${CHAT_PORT}/faust/nimble/callback`;
  const NIMBLE_CLOSE_ENDPOINT = `http://${CHAT_HOST}:${CHAT_PORT}/faust/nimble/close`;
  let chatWs = null;
  let chatWsReady = null;
  let currentChatRequest = null;
  let streamTtsDrainPromise = null;
  let streamTtsSentenceId = 0;
  let streamTtsNextPlayId = 0;
  const streamTtsPending = new Map();
  let streamTtsPlaybackPromise = null;
  const streamTtsSentenceEndRe = /[。！？!?；;]+$/;

  function normalizeTtsText(text){
    return String(text ?? '')
      .replace(/\r\n/g, '\n')
      .replace(/\r/g, '\n')
      .replace(/\\n/g, '\n');
  }

  function decodeWsPayload(data){
    if (typeof data === 'string') return data;
    try{
      if (data instanceof ArrayBuffer) return new TextDecoder('utf-8').decode(data);
      if (ArrayBuffer.isView(data)) return new TextDecoder('utf-8').decode(data);
      if (data && typeof Blob !== 'undefined' && data instanceof Blob) {
        return data.text();
      }
    }catch(e){
      console.warn('decodeWsPayload failed, fallback to String(data)', e);
    }
    return String(data ?? '');
  }

  // --- handle incoming faust commands forwarded from main process ---
  // Commands are simple text payloads like:
  //   PLAYMUSIC <filename>
  //   PLAYBG <filename>
  //   SAY <text>
  //   STOP
  let bgAudio = null;

  async function handleFaustCommand(raw){
    if (!raw || typeof raw !== 'string') return;
    const parts = raw.trim().split(' ');
    const cmd = parts[0].toUpperCase();
    const arg = parts.slice(1).join(' ').trim();
    console.log('Faust command received:', cmd, arg);
    try{
      if (cmd === 'PLAYMUSIC'){
        if (!arg) return;
        // fetch the file (relative or absolute) and play with mouth-sync
        try{
          const r = await fetch(arg);
          const blob = await r.blob();
          startMouthSyncFromFile(blob);
        }catch(e){
          console.error('PLAYMUSIC fetch/play failed', e);
        }
      } else if (cmd === 'PLAYBG'){
        if (!arg) return;
        try{
          if (bgAudio){ bgAudio.pause(); bgAudio.src = ''; bgAudio = null; }
          bgAudio = new Audio(arg);
          // play once in background (no looping)
          bgAudio.loop = false;
          bgAudio.crossOrigin = 'anonymous';
          bgAudio.onended = () => { try{ bgAudio = null; }catch(e){} };
          await bgAudio.play().catch(e=>{console.warn('bg play error',e)});
        }catch(e){ console.error('PLAYBG failed', e); }
      } else if (cmd === 'SAY'){
        if (!arg) return;
        // use existing synthesizeAndPlay TTS function; prefer UI-selected lang
        const lang = (ttsLang && ttsLang.value) ? ttsLang.value : 'zh-CN';
        useVAD=false; // disable VAD during TTS playback
        showResultBubble('ai', arg);
        await synthesizeAndPlay(arg, lang);
        useVAD=true; // re-enable VAD after TTS playback
      } else if (cmd === 'STOP'){
        // stop audio and optionally stop asr
        try{ stopAudio(); }catch(e){}
        try{ stopMicAsr(); }catch(e){}
        try{ stopBackgroundAudio(); }catch(e){}
      } else if (cmd === 'NIMBLE_SHOW'){
        if (!arg) return;
        let payload = null;
        try{ payload = JSON.parse(arg); }catch(e){ console.warn('Invalid NIMBLE_SHOW payload', e, arg); return; }
        showNimbleWindow(payload);
      } else if (cmd === 'NIMBLE_CLOSE'){
        if (!arg) return;
        let payload = null;
        try{ payload = JSON.parse(arg); }catch(e){ console.warn('Invalid NIMBLE_CLOSE payload', e, arg); return; }
        if (payload && payload.callback_id) closeNimbleWindow(payload.callback_id, false);
      } else if (cmd=="SET_MOTION"){
        if (!arg) return;
        playMotionByName(arg);
      } else if (cmd === 'LOAD_MODEL' || cmd === 'SET_MODEL_PATH'){
        if (!arg) return;
        if (modelPathInput) modelPathInput.value = arg;
        loadModel(arg);
      } else if (cmd === 'SET_MODEL_SCALE'){
        if (!arg) return;
        setScaleFactor(parseFloat(arg));
      } else if (cmd === 'SET_MODEL_POSITION'){
        if (!currentModel || !arg) return;
        const [xRaw, yRaw] = arg.split(/\s+/);
        const x = Number(xRaw);
        const y = Number(yRaw);
        if (Number.isFinite(x)) currentModel.x = x;
        if (Number.isFinite(y)) currentModel.y = y;
        updateQuickControllerPosition();
        saveModelState();
      } else if (cmd === 'START_ASR'){
        startRecording();
      } else if (cmd === 'STOP_ASR'){
        stopRecording();
      } else if (cmd === 'TOGGLE_ASR'){
        toggleAsr();
      } else if (cmd === 'STOP_AUDIO'){
        interruptPlayback();
      } else if (cmd === 'INTERRUPT_SPEECH'){
        interruptPlayback();
      } else if (cmd === 'RANDOM_MOTION'){
        playRandomMotion();
      } else if (cmd === 'SCALE_UP'){
        nudgeScale(0.05);
      } else if (cmd === 'SCALE_DOWN'){
        nudgeScale(-0.05);
      }
      else {
        console.warn('Unknown faust command', raw);
      }
    }catch(e){ console.error('handleFaustCommand error', e); }
  }

  // register handler from preload-exposed API
  if (window.faust && window.faust.onCommand){
    window.faust.onCommand((cmd)=>{ handleFaustCommand(cmd); });
  }

  function resetStreamTtsState(){
    streamTtsDrainPromise = null;
    streamTtsSentenceId = 0;
    streamTtsNextPlayId = 0;
    streamTtsPending.clear();
    streamTtsPlaybackPromise = null;
  }

  async function waitForStreamTtsDrain(){
    if (streamTtsDrainPromise) return streamTtsDrainPromise;
    streamTtsDrainPromise = (async ()=>{
      while (streamTtsPending.size > 0){
        await flushStreamTtsQueue();
        if (streamTtsPending.size > 0){
          await new Promise((resolve)=> setTimeout(resolve, 50));
        }
      }
    })();
    try{
      await streamTtsDrainPromise;
    }finally{
      streamTtsDrainPromise = null;
    }
  }

  function extractCompletedSentences(buffer){
    buffer = normalizeTtsText(buffer);
    const results = [];
    let start = 0;
    for (let i = 0; i < buffer.length; i++){
      const ch = buffer[i];
      if ('。！？!?；;\n'.includes(ch)){
        const sentence = buffer.slice(start, i + 1).trim();
        if (sentence) results.push(sentence);
        start = i + 1;
      }
    }
    console.log('extractCompletedSentences', { buffer, completed: results, rest: buffer.slice(start) });
    return { completed: results, rest: buffer.slice(start) };
  }

  function openChatWs(){
    if (chatWs && (chatWs.readyState === WebSocket.OPEN || chatWs.readyState === WebSocket.CONNECTING)){
      return chatWsReady || Promise.resolve();
    }
    chatWsReady = new Promise((resolve, reject)=>{
      try{
        chatWs = new WebSocket(CHAT_ENDPOINT);
        chatWs.onopen = ()=> resolve();
        chatWs.onerror = (e)=> reject(e);
        chatWs.onmessage = handleChatWsMessage;
        chatWs.onclose = ()=>{ chatWs = null; chatWsReady = null; };
      }catch(e){ reject(e); }
    });
    return chatWsReady;
  }

  async function requestTtsBlob(text, lang){
    if (!text || !text.trim()) return null;
    const endpoint = (window.location && window.location.hostname) ? `http://${window.location.hostname}:5000/` : 'http://127.0.0.1:5000/';
    const payload = { text, text_language: lang || 'zh' };
    const r = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!r.ok){
      const txt = await r.text();
      throw new Error(`TTS服务错误: ${r.status} ${txt}`);
    }
    const contentType = r.headers.get('content-type') || 'audio/wav';
    const ab = await r.arrayBuffer();
    return new Blob([ab], { type: contentType });
  }

  function playSingleBlobOrdered(blob){
    return new Promise((resolve)=>{
      try{ stopAudio(); }catch(e){}
      startMouthSyncFromFile(blob);
      if (ttsStatus) ttsStatus.textContent = '播放中';
      try{
        if (audioEl && typeof audioEl.addEventListener === 'function'){
          const onEnd = ()=>{ try{ audioEl.removeEventListener('ended', onEnd); }catch(e){} resolve(); };
          audioEl.addEventListener('ended', onEnd);
        } else {
          const waiter = setInterval(()=>{
            if (!audioEl || audioEl.ended){ clearInterval(waiter); resolve(); }
          }, 200);
        }
      }catch(e){ resolve(); }
    });
  }

  async function flushStreamTtsQueue(){
    if (streamTtsPlaybackPromise) return streamTtsPlaybackPromise;
    streamTtsPlaybackPromise = (async ()=>{
      while (streamTtsPending.has(streamTtsNextPlayId)){
      const item = streamTtsPending.get(streamTtsNextPlayId);
        if (!item) break;
        if (item.status === 'pending') break;
        streamTtsPending.delete(streamTtsNextPlayId);
        if (item.status === 'ready' && item.blob){
          await playSingleBlobOrdered(item.blob);
        }
        streamTtsNextPlayId += 1;
      }
    })();
    try{
      await streamTtsPlaybackPromise;
    }finally{
      streamTtsPlaybackPromise = null;
    }
  }

  async function enqueueStreamTtsSentence(sentence, lang){
    sentence = normalizeTtsText(sentence).trim();
    if (!sentence) return;
    const id = streamTtsSentenceId++;
    streamTtsPending.set(id, { status: 'pending', text: sentence, blob: null });
    void flushStreamTtsQueue();
    try{
      const blob = await requestTtsBlob(sentence, lang);
      if (!blob){
        streamTtsPending.set(id, { status: 'failed', text: sentence, blob: null });
        await flushStreamTtsQueue();
        return;
      }
      streamTtsPending.set(id, { status: 'ready', blob, text: sentence });
      await flushStreamTtsQueue();
    }catch(e){
      console.warn('stream TTS sentence failed', sentence, e);
      streamTtsPending.set(id, { status: 'failed', text: sentence, blob: null });
      await flushStreamTtsQueue();
    }
  }

  async function handleChatWsMessage(ev){
    if (!currentChatRequest) return;
    let raw = ev.data;
    if (raw && typeof raw !== 'string') {
      raw = await decodeWsPayload(raw);
    }
    let msg = null;
    try{ msg = JSON.parse(raw); }catch(e){ msg = { type: 'error', error: String(e) }; }
    if (!msg) return;

    if (msg.type === 'start'){
      currentChatRequest.replyText = '';
      currentChatRequest.pendingBuffer = '';
      if (chatStatusEl) chatStatusEl.textContent = '聊天流式响应中...';
      return;
    }

    if (msg.type === 'delta'){
      const chunk = normalizeTtsText(msg.content || '');
      currentChatRequest.replyText += chunk;
      currentChatRequest.pendingBuffer += chunk;
      showResultBubble('ai', currentChatRequest.replyText);
      const split = extractCompletedSentences(currentChatRequest.pendingBuffer);
      currentChatRequest.pendingBuffer = split.rest;
      console.log("收到增量回复，当前累计文本：", currentChatRequest.replyText);
      for (const sentence of split.completed){
        if (!sentence.includes('<NO_TTS_OUTPUT>')){
          enqueueStreamTtsSentence(sentence, 'zh');
        }
      }
      return;
    }

    if (msg.type === 'done'){
      const reply = msg.reply || currentChatRequest.replyText || '';
      const request = currentChatRequest;
      currentChatRequest.replyText = reply;
      if (currentChatRequest.pendingBuffer && currentChatRequest.pendingBuffer.trim() && !reply.includes('<NO_TTS_OUTPUT>')){
        await enqueueStreamTtsSentence(currentChatRequest.pendingBuffer.trim(), 'zh');
      }
      currentChatRequest.pendingBuffer = '';
      if (chatStatusEl) chatStatusEl.textContent = '聊天完成';
      if (textChatStatus) textChatStatus.textContent = '文字已发送';
      currentChatRequest = null;
      request.resolve(reply);
      if (request.resumeAfter){
        waitForStreamTtsDrain()
          .then(()=>{ resumeRecording(); })
          .catch((e)=>{ console.warn('stream TTS drain failed', e); resumeRecording(); });
      }
      return;
    }

    if (msg.type === 'error'){
      if (chatStatusEl) chatStatusEl.textContent = '聊天错误';
      if (textChatStatus) textChatStatus.textContent = '聊天错误';
      showResultBubble('error', msg.error || '未知聊天错误');
      if (currentChatRequest.resumeAfter){
        resumeRecording();
      }
      currentChatRequest.reject(new Error(msg.error || '未知聊天错误'));
      currentChatRequest = null;
    }
  }

  async function sendToChat(text){
    if (!text) return;
    try{
      if (textChatStatus) textChatStatus.textContent = '发送中...';
      if (chatStatusEl) chatStatusEl.textContent = '正在连接聊天流...';
      await openChatWs();
      resetStreamTtsState();
      const resumeAfter = !!(asrRunning && !voiceBargeInEnabled);
      if (resumeAfter) pauseRecording();
      const reply = await new Promise((resolve, reject)=>{
        currentChatRequest = {
          resolve,
          reject,
          text,
          replyText: '',
          pendingBuffer: '',
          resumeAfter,
        };
        chatWs.send(JSON.stringify({ text }));
      });
      return reply;
    }catch(e){
      console.warn('sendToChat err', e);
      chatStatusEl && (chatStatusEl.textContent = '聊天网络错误');
      if (textChatStatus) textChatStatus.textContent = '网络错误';
      throw e;
    }
  }

  async function sendTextChatMessage(){
    if (!textChatInput || !textChatSendBtn) return;
    const text = (textChatInput.value || '').trim();
    if (!text || textChatSending) return;
    textChatSending = true;
    textChatSendBtn.disabled = true;
    try{
      showResultBubble('user', text);
      await sendToChat(text);
      textChatInput.value = '';
    }finally{
      textChatSending = false;
      textChatSendBtn.disabled = false;
      if (textChatStatus && textChatStatus.textContent === '发送中...') textChatStatus.textContent = '文字待命';
    }
  }

  function formatResultBubbleText(source, text){
    const raw = String(text || '').trim();
    if (!raw) return '';
    if (source === 'user') return `用户:${raw}`;
    if (source === 'error') return `!错误!:${raw}`;
    return `AI:${raw}`;
  }

  function rememberAsrScrollIntent(){
    if (!asrTextEl) return;
    const threshold = 18;
    const distanceToBottom = asrTextEl.scrollHeight - asrTextEl.scrollTop - asrTextEl.clientHeight;
    asrTextPinnedToBottom = distanceToBottom <= threshold;
  }

  function scrollAsrTextToBottom(force = false){
    if (!asrTextEl) return;
    if (force || asrTextPinnedToBottom){
      asrTextEl.scrollTop = asrTextEl.scrollHeight;
      asrTextPinnedToBottom = true;
    }
  }

  function showResultBubble(source, text){
    if (!asrTextEl) return;
    asrBubbleSource = source || 'ai';
    asrTextEl.dataset.source = asrBubbleSource;
    const formatted = formatResultBubbleText(asrBubbleSource, text);
    rememberAsrScrollIntent();
    asrTextEl.style.display = formatted ? 'block' : 'none';
    asrTextEl.textContent = formatted;
    if (formatted) {
      updateAsrTextPosition(true);
      scrollAsrTextToBottom();
    }
  }

  function showAsrText(text){
    if (!asrTextEl) return;
    rememberAsrScrollIntent();
    asrTextEl.style.display = text ? 'block' : 'none';
    asrTextEl.dataset.source = 'ai';
    asrBubbleSource = 'ai';
    asrTextEl.textContent = formatResultBubbleText('ai', text || '');
    updateAsrTextPosition(true);
    scrollAsrTextToBottom();
  }

  function updateAsrTextPosition(forceSnap = false){
    if (!asrTextEl || !currentModel || !app || !app.renderer) return;
    try{
      const canvasRect = app.renderer.view.getBoundingClientRect();
      const b = currentModel.getBounds();
      // b.x/b.y are renderer coordinates; map to client
      const clientX = canvasRect.left + (b.x + b.width/2) * (canvasRect.width / app.renderer.width);
      const clientY = canvasRect.top + (b.y) * (canvasRect.height / app.renderer.height);
      // position slightly above head
      const offsetY = -36;
      const bubbleWidth = Math.max(asrTextEl.offsetWidth, 180);
      asrBubbleTargetX = clientX - bubbleWidth / 2;
      asrBubbleTargetY = clientY + offsetY;
      if (!asrBubbleInitialized || forceSnap){
        asrBubbleCurrentX = asrBubbleTargetX;
        asrBubbleCurrentY = asrBubbleTargetY;
        asrBubbleInitialized = true;
      } else {
        const smooth = 0.2;
        asrBubbleCurrentX += (asrBubbleTargetX - asrBubbleCurrentX) * smooth;
        asrBubbleCurrentY += (asrBubbleTargetY - asrBubbleCurrentY) * smooth;
      }
      asrTextEl.style.left = Math.round(asrBubbleCurrentX) + 'px';
      asrTextEl.style.top = Math.round(asrBubbleCurrentY) + 'px';
      asrTextEl.style.fontSize = '20px';
    }catch(e){/*ignore*/}
  }

  function updateTextChatBarPosition(){
    const textChatBar = document.getElementById('textChatBar');
    if (!textChatBar || !currentModel || !app || !app.renderer) return;
    try{
      const canvasRect = app.renderer.view.getBoundingClientRect();
      const b = currentModel.getBounds();
      const scaleX = canvasRect.width / app.renderer.width;
      const scaleY = canvasRect.height / app.renderer.height;
      const clientX = canvasRect.left + (b.x + b.width * 0.5) * scaleX;
      const waistY = canvasRect.top + (b.y + b.height * 0.62) * scaleY;
      const horizontalOffset = Math.min(150, Math.max(0, b.width * scaleX * 0.55));
      textChatBar.style.left = Math.round(clientX + horizontalOffset) + 'px';
      textChatBar.style.top = Math.round(waistY) + 'px';
      textChatBar.style.bottom = 'auto';
      textChatBar.style.transform = 'translate(-50%, -42%)';
      updateQuickControllerPosition();
    }catch(e){/*ignore*/}
  }

  function accumulateAndSend(){
    // Fallback periodic upload logic removed. This function is intentionally
    // left empty to avoid falling back to fixed-interval uploads when VAD
    // is unavailable. Uploads are handled only after VAD detects end-of-speech.
    return;
  }

  async function startMicAsr(){
    if (asrRunning) return;
    try{
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micStream = stream;
      micAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
      const src = micAudioCtx.createMediaStreamSource(stream);
      // pick scriptProcessor buffer size 4096 for moderate latency
      const bufferSize = 4096;
      scriptNode = micAudioCtx.createScriptProcessor(bufferSize, 1, 1);
      // streaming handler: resample input to TARGET_SAMPLE_RATE, emit 512-sample frames to VAD ws
      scriptNode.onaudioprocess = (e) => {
        const input = e.inputBuffer.getChannelData(0);
        // resample this block to target rate
        const resampled = resampleFloat32(input, micAudioCtx.sampleRate, TARGET_SAMPLE_RATE);
        // combine with leftover
        let combined;
        if (leftoverResampled.length > 0){
          combined = new Float32Array(leftoverResampled.length + resampled.length);
          combined.set(leftoverResampled, 0);
          combined.set(resampled, leftoverResampled.length);
        } else {
          combined = resampled;
        }

        // slice into 512-sample frames and send
        let offset = 0;
        while (combined.length - offset >= VAD_WINDOW_SIZE){
          const frame = combined.subarray(offset, offset + VAD_WINDOW_SIZE);
          // send to VAD websocket if available
          try{
            if (vadWs && vadWs.readyState === WebSocket.OPEN && useVAD){
              // send exactly the slice bytes for this frame (avoid sending whole underlying buffer)
              console.log("Sending VAD frame");
              const start = frame.byteOffset || 0;
              const end = start + (frame.byteLength || frame.length * 4);
              const slice = frame.buffer.slice(start, end);
              vadWs.send(slice);
            }
          }catch(e){
            console.log("fail to send VAD frame:", e) /* ignore send errors */ }
          // maintain pre-roll ring buffer
          preBufferFrames.push(frame.slice(0));
          if (preBufferFrames.length > PRE_ROLL_FRAMES) preBufferFrames.shift();
          // if speech active, also collect into uploadFrames
          if (inSpeech) uploadFrames.push(frame.slice(0));
          offset += VAD_WINDOW_SIZE;
        }
        // leftover samples
        const rem = combined.subarray(offset);
        leftoverResampled = new Float32Array(rem.length);
        leftoverResampled.set(rem);
        // Removed fallback periodic upload: we rely solely on VAD to trigger uploads.
      };
      src.connect(scriptNode);
      scriptNode.connect(micAudioCtx.destination);
      asrRunning = true;
      asrStatusEl.textContent = '正在监听...';
      startAsrBtn.disabled = true;
      stopAsrBtn.disabled = false;
  updateQuickAsrButton();
      // try to open VAD websocket if enabled
      noVoiceCnt=0;
      if (true){
        try{
          vadWs = new WebSocket(VAD_WS_URL);
          vadWs.binaryType = 'arraybuffer';
          vadWs.onopen = ()=>{ asrStatusEl.textContent = '已连接到语音识别服务'; useVAD=true; console.log('VAD ws opened'); };
          vadWs.onmessage = (ev)=>{
            try{
              const msg = typeof ev.data === 'string' ? JSON.parse(ev.data) : JSON.parse(new TextDecoder().decode(ev.data));
              // prefer probability for decisions; fall back to is_speech if probability missing
              const p = (typeof msg.probability !== 'undefined') ? (Number(msg.probability) || 0) : (msg.is_speech ? 1 : 0);
              // update realtime probability UI if present
              try{
                if (vadProbEl) vadProbEl.value = Math.max(0, Math.min(1, p));
                if (vadProbLabel) vadProbLabel.textContent = Math.round(p*100) + '%';
              }catch(e){}

              // New logic: start recording when probability > 50%, stop when < 50%
              // We still use a debounce timer (VAD_END_DEBOUNCE_MS) to avoid flapping when dropping below threshold.
              const START_THRESHOLD = 0.5;
              const STOP_THRESHOLD = 0.5;
              const STOP_NO_VOICE_COUNT = 30;
              console.log("VAD prob:", p);
              console.log("In Speech:", inSpeech);
              console.log("No Voice Count:", noVoiceCnt);
              if (p > START_THRESHOLD){
                // speech started or continuing
                asrStatusEl.textContent = '有语音喵';
                noVoiceCnt=0;
                if (!inSpeech){
                  inSpeech = true;
                  // include pre-roll frames collected earlier
                  uploadFrames = preBufferFrames.slice();
                  preBufferFrames = [];
                  asrStatusEl.textContent = '检测到语音-开始录音...';
                }
                // cancel pending end timer if any
                if (vadEndTimer){ clearTimeout(vadEndTimer); vadEndTimer = null; }
              }
              if (p < STOP_THRESHOLD){
                // probability dropped below stop threshold
                asrStatusEl.textContent = '没有语音喵';
                noVoiceCnt+=1;
                if (inSpeech && noVoiceCnt>=STOP_NO_VOICE_COUNT){
                  console.log("VAD end detected, starting debounce timer");
                  //if (vadEndTimer) clearTimeout(vadEndTimer);
                  vadEndTimer = setTimeout(()=>{
                    // consider speech ended
                    inSpeech = false;
                    vadEndTimer = null;
                    asrStatusEl.textContent = '没有语音喵-上传识别中...';
                    if (uploadFrames.length > 0){
                      const concat = concatFloat32Arrays(uploadFrames);
                      uploadFrames = [];
                      console.log("Uploading detected speech segment, length:", concat.length);
                      console.log("Probability:", p);
                      uploadBufferAndShowResult(concat, TARGET_SAMPLE_RATE);
                    }
                  }, VAD_END_DEBOUNCE_MS);
                }
              } // else p == threshold: treat as no change
            }catch(err){ console.warn('VAD ws message parse err', err); }
          };
          vadWs.onerror = (ev)=>{ console.warn('VAD ws error', ev); useVAD = false; asrStatusEl.textContent = 'VAD连接错误'; vadWs = null; };
          vadWs.onclose = ()=>{ if (useVAD){ useVAD = false; asrStatusEl.textContent = 'VAD断开'; vadWs = null; } };
        }catch(e){ console.warn('open vad ws failed', e); useVAD = false; }
      } else {
        // VAD disabled: do not fall back to periodic uploads. ASR will not
        // collect audio for periodic upload — rely on manual control or
        // re-enable VAD.
      }
    }catch(err){
      console.error('start mic failed', err);
      asrStatusEl.textContent = '麦克风权限或错误';
    }
  }

  function stopMicAsr(){
    if (!asrRunning) return;
    asrRunning = false;
    if (asrTimer) { clearInterval(asrTimer); asrTimer = null; }
    if (vadEndTimer){ clearTimeout(vadEndTimer); vadEndTimer = null; }
    // if we have collected frames in uploadFrames (speech not yet sent), send them
    if (uploadFrames.length > 0){
      try{
        const concat = concatFloat32Arrays(uploadFrames);
        uploadFrames = [];
        uploadBufferAndShowResult(concat, TARGET_SAMPLE_RATE);
      }catch(e){ console.warn('upload pending frames failed', e); }
    }
    if (vadWs){ try{ vadWs.close(); }catch(e){} vadWs = null; }
    if (vadProbEl) { try{ vadProbEl.value = 0; }catch(e){} }
    if (vadProbLabel) { try{ vadProbLabel.textContent = '0%'; }catch(e){} }
    if (scriptNode){ try{ scriptNode.disconnect(); scriptNode.onaudioprocess = null; }catch(e){} scriptNode=null }
    if (micAudioCtx){ try{ micAudioCtx.close(); }catch(e){} micAudioCtx=null }
    if (micStream){ micStream.getTracks().forEach(t => t.stop()); micStream = null }
    micBuffer = []; micBufLen = 0;
    asrStatusEl.textContent = '已停止';
    startAsrBtn.disabled = false;
    stopAsrBtn.disabled = true;
    updateQuickAsrButton();
  }

  // --- ASRController-like API (start/stop/pause/resume) ---
  let paused = false;
  let pausedStopped = false; // whether pause triggered a stop (non-barge-in mode)
  let voiceBargeInEnabled = false; // if true, keep VAD listening during TTS/pause

  async function startRecording(){
    paused = false;
    pausedStopped = false;
    await startMicAsr();
  }

  function stopRecording(){
    paused = false;
    pausedStopped = false;
    stopMicAsr();
  }

  // pause: if voiceBargeInEnabled keep VAD running, otherwise stop to free resources
  function pauseRecording(){
    paused = true;
    if (!asrRunning) return;
    if (!voiceBargeInEnabled){
      // stop capturing but remember to resume
      stopMicAsr();
      pausedStopped = true;
      asrStatusEl.textContent = '已暂停';
    } else {
      asrStatusEl.textContent = '已暂停（保留VAD）';
    }
  }

  function resumeRecording(){
    paused = false;
    if (pausedStopped){
      // restart capture
      startMicAsr();
      pausedStopped = false;
    }
    asrStatusEl.textContent = asrRunning ? '正在监听...' : '未启动';
    updateQuickAsrButton();
  }

  function setVoiceBargeIn(enabled){
    voiceBargeInEnabled = !!enabled;
  }

  function getVoiceBargeInStatus(){
    return { enabled: !!voiceBargeInEnabled };
  }

  // expose a small API so other modules can call into this controller
  window.ASRControllerAPI = {
    startRecording,
    stopRecording,
    pauseRecording,
    resumeRecording,
    setVoiceBargeIn,
    getVoiceBargeInStatus
  };

  // wire up buttons (use the ASRController-like API)
  if (startAsrBtn) startAsrBtn.addEventListener('click', ()=> startRecording());
  if (stopAsrBtn) stopAsrBtn.addEventListener('click', ()=> stopRecording());
  if (textChatSendBtn) textChatSendBtn.addEventListener('click', ()=>{ sendTextChatMessage(); });
  if (textChatInput) textChatInput.addEventListener('keydown', (e)=>{
    if (e.key === 'Enter' && !e.shiftKey){
      e.preventDefault();
      sendTextChatMessage();
    }
  });

  // update asrText position each frame if visible
  function rafUpdate(){
    if (asrTextEl && asrTextEl.style.display !== 'none') updateAsrTextPosition();
    updateTextChatBarPosition();
    requestAnimationFrame(rafUpdate);
  }
  requestAnimationFrame(rafUpdate);

  function showOverlay(msg){
    const o = document.getElementById('overlay');
    if (!o) return;
    o.style.display = 'none';
    o.textContent = msg;
  }

  function clearOverlay(){
    const o = document.getElementById('overlay');
    if (!o) return;
    o.style.display = 'none';
    o.textContent = '';
  }

  function loadModel(path){
    console.log('Loading model:', path);
    // determine Live2DModel constructor (try window.Live2DModel, then PIXI.live2d)
    Live2DModel = (typeof window !== 'undefined' && window.Live2DModel) ? window.Live2DModel : (PIXI && PIXI.live2d && PIXI.live2d.Live2DModel);
    if (!Live2DModel) {
      showOverlay('未检测到 pixi-live2d-display 库，请检查网络或依赖。');
      return;
    }
    showOverlay('加载模型: ' + path);
    readModelDefinition(path).then((modelDef)=>{
      availableMotions = extractMotionNames(modelDef);
      return Live2DModel.from(path);
    }).then(model => {
      // 移除上个模型
      if (currentModel && currentModel.parent) app.stage.removeChild(currentModel);
      currentModel = model;
      // 缩放并定位到右下角初始位置 (scale will be applied via baseScale * slider)
      model.scale.set(1.0);
      model.anchor.set(0.5, 1.0);
      model.x = app.renderer.width - 200;
      model.y = app.renderer.height - 10;
      model.interactive = true;
      model.buttonMode = true;
      model.cursor = 'grab';

      // 基本拖拽
      model.on('pointerdown', (e) => {
        if (clickThroughController) clickThroughController.forceInteractive();
        setInteractionLock(true);
        dragging = true;
        model.cursor = 'grabbing';
        const pos = e.data.global;
        dragOffset.x = pos.x - model.x;
        dragOffset.y = pos.y - model.y;
      });
      model.on('pointerup', () => {
        dragging = false;
        model.cursor = 'grab';
        setInteractionLock(false);
      });
      model.on('pointerupoutside', () => {
        dragging = false;
        model.cursor = 'grab';
        setInteractionLock(false);
      });
      // save state after dragging ends
      model.on('pointerup', () => { saveModelState(); });
      model.on('pointermove', (e) => {
        if (!dragging) return;
        const pos = e.data.global;
        model.x = pos.x - dragOffset.x;
        model.y = pos.y - dragOffset.y;
        updateQuickControllerPosition();
      });

      // 官方示例支持的 hit 事件（例如点击 body 区域触发动作）
      try{
        model.on && model.on('hit', (hitAreas) => {
          try{
            model.motion('tap_body');
          }catch(e){}
        });
      }catch(e){ /* ignore if event not supported */ }

      app.stage.addChild(model);
      clearOverlay();
      // 自动缩放示例：根据窗口尺寸调整基础缩放
      baseScale = Math.min(app.renderer.width / 1600, app.renderer.height / 900);
      // apply user-selected scale factor
      applyModelScale();
      // keep reference for mouth sync
      model._faustLive2D = { mouthValue: 0 };

      updateTextChatBarPosition();
      refreshQuickControllerVisibility();
    }).catch(err => {
      showOverlay('加载模型失败：' + err);
      console.error(err);
    });
  }

  if (loadBtn) loadBtn.addEventListener('click', () => {
    const p = modelPathInput.value.trim() || defaultModel;
    loadModel(p);
  });

  if (resetBtn) resetBtn.addEventListener('click', () => {
    if (!currentModel) return;
    currentModel.x = app.renderer.width - 200;
    currentModel.y = app.renderer.height - 10;
    updateQuickControllerPosition();
  });

  // 自动尝试加载默认或保存的模型路径/状态
  modelPathInput.value = defaultModel;
  (async ()=>{
    const st = await loadModelState();
    const toLoad = (st && st.modelPath) ? st.modelPath : defaultModel;
    modelPathInput.value = toLoad;
    // small delay so UI visible
    setTimeout(()=>{ loadModel(toLoad); }, 120);
  })();

  // 窗口尺寸变化时保持模型在屏幕内
  window.addEventListener('resize', ()=>{
    if (!currentModel) return;
    currentModel.x = Math.min(currentModel.x, app.renderer.width - 50);
    currentModel.y = Math.min(currentModel.y, app.renderer.height - 10);
    // auto-scale with resize
    try{
      baseScale = Math.min(app.renderer.width / 1600, app.renderer.height / 900);
      applyModelScale();
    }catch(e){}
  });

  // click-through (mouse penetration) - use Electron API if available
  if (window.api && window.api.setIgnoreMouseEvents) {
    // click-through behavior with temporary interactive regions
    // When enabled we setIgnoreMouseEvents(true, {forward:true}) so renderer still
    // receives mousemove events. On mousemove we check whether the pointer is
    // over an interactive element (controls/overlay). If so we temporarily
    // disable ignore so clicks are delivered to the window; when it leaves we
    // re-enable ignore after a short debounce.

    function createClickThroughController(){
      let clickThroughEnabled = false;
      let interactiveActive = false;
      let pendingTimeout = null;

      function setIgnore(ignore){
        try{ app.renderer.view.style.pointerEvents = ignore ? 'none' : 'auto'; }catch(e){}
        window.api.setIgnoreMouseEvents(ignore).catch(()=>{});
      }

      function scheduleEnableIgnore(){
        if (pendingTimeout) clearTimeout(pendingTimeout);
        pendingTimeout = setTimeout(()=>{
          pendingTimeout = null;
          if (clickThroughEnabled && !interactiveActive && !interactionLocked){
            setIgnore(true);
          }
        }, 140);
      }

      function onGlobalMouseMove(e){
        hoverQuickController = isPointOverQuickController(e.clientX, e.clientY);
        hoverModel = isPointerOnModel(e.clientX, e.clientY);
        const overInteractive = hoverQuickController || hoverModel || dragging || interactionLocked;
        if (overInteractive){
          if (!interactiveActive){
            interactiveActive = true;
            setIgnore(false);
          }
        } else if (interactiveActive) {
          interactiveActive = false;
          scheduleEnableIgnore();
        }
        refreshQuickControllerVisibility();
      }

      return {
        enable(){
          clickThroughEnabled = true;
          document.body.classList.add('click-through');
          setIgnore(true);
          window.addEventListener('mousemove', onGlobalMouseMove, { passive: true });
        },
        disable(){
          clickThroughEnabled = false;
          interactiveActive = false;
          if (pendingTimeout) { clearTimeout(pendingTimeout); pendingTimeout = null; }
          document.body.classList.remove('click-through');
          window.removeEventListener('mousemove', onGlobalMouseMove);
          setIgnore(false);
        },
        setInteractiveLock(locked){
          if (!clickThroughEnabled) return;
          if (locked){
            interactiveActive = true;
            setIgnore(false);
          } else {
            scheduleEnableIgnore();
          }
        },
        forceInteractive(){
          if (!clickThroughEnabled) return;
          interactiveActive = true;
          setIgnore(false);
        }
      };
    }

    clickThroughController = createClickThroughController();

    function setClickThroughOnRenderer(val){
      if (val) clickThroughController.enable();
      else clickThroughController.disable();
    }

    if (clickThrough) clickThrough.addEventListener('change', (e)=>{
      const val = !!e.target.checked;
      setClickThroughOnRenderer(val);
    });

    if (clickThrough && clickThrough.checked) setClickThroughOnRenderer(true);
    else setClickThroughOnRenderer(false);
  } else {
    console.warn('未找到鼠标穿透 IPC API');
  }

  // --- model scale slider handling ---
  if (modelScaleSlider){
    modelScaleSlider.addEventListener('input', (e)=>{
      setScaleFactor(parseFloat(e.target.value) || 1.0);
    });
    // initialize display
    if (modelScaleValue) modelScaleValue.textContent = scaleFactor.toFixed(2) + 'x';
  }

  // --- hotkey to toggle controls visibility ---
  // Ctrl+Shift+H toggles, Esc hides
  document.addEventListener('keydown', (e) => {
    const isToggle = e.ctrlKey && e.shiftKey && (e.key === 'H' || e.key === 'h');
    if (isToggle){
      document.body.classList.toggle('controls-hidden');
    } else if (e.key === 'Escape'){
      document.body.classList.add('controls-hidden');
    }
  });

  // Audio mouth-sync: setup audio element and WebAudio analyser
  let audioEl = null;
  let audioCtx = null;
  let analyser = null;
  let dataArray = null;
  let sourceNode = null;
  let rafId = null;

  function stopAudio(){
    if (audioEl){
      try{ audioEl.pause(); audioEl.currentTime = 0; }catch(e){}
    }
    if (currentModel){
      try{
        if (currentModel.internalModel && currentModel.internalModel.coreModel && typeof currentModel.internalModel.coreModel.setParameterValueById === 'function'){
          currentModel.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', 0);
        } else if (typeof currentModel.setMouthOpenY === 'function'){
          currentModel.setMouthOpenY(0);
        }
      }catch(e){}
    }
    if (rafId) cancelAnimationFrame(rafId);
    if (sourceNode){ try{ sourceNode.disconnect(); }catch(e){} sourceNode=null }
    if (analyser){ analyser.disconnect(); analyser=null }
    if (audioCtx){ try{ audioCtx.close(); }catch(e){} audioCtx=null }
  }

  // TTS: call backend API (port 5000) to synthesize text and play the returned audio
  async function synthesizeAndPlay(text, lang){
    // Splits text into chunks and sends parallel TTS requests, playing chunks
    // progressively as they arrive to reduce latency. Returns a promise that
    // resolves after all playback has finished.
    if (!text || text.trim().length === 0) return;
    const TTS_SPLIT_LIMIT = 100; // characters per chunk (tunable)
    const endpoint = (window.location && window.location.hostname) ? `http://${window.location.hostname}:5000/` : 'http://127.0.0.1:5000/';

    // helper: split text into chunks trying to respect sentence boundaries
    function splitText(input, maxLen){
      input = normalizeTtsText(input).trim();
      const out = [];
      if (input.length <= maxLen) return [input];
      // prefer splitting on Chinese/Japanese/English sentence punctuation or commas/space
      const splitRe = /([。！？!?；;，,，、\n]+)/g;
      let parts = input.split(splitRe).filter(s=>s && s.trim().length>0);
      // recombine parts into chunks under maxLen
      let cur = '';
      for (let p of parts){
        if ((cur + p).length <= maxLen){ cur += p; }
        else {
          if (cur) out.push(cur);
          if (p.length > maxLen){
            // fallback: hard-split long fragment
            for (let i=0;i<p.length;i+=maxLen){ out.push(p.slice(i,i+maxLen)); }
            cur = '';
          } else {
            cur = p;
          }
        }
      }
      if (cur) out.push(cur);
      // if nothing produced, fallback to naive split
      if (out.length === 0){
        for (let i=0;i<input.length;i+=maxLen) out.push(input.slice(i,i+maxLen));
      }
      return out;
    }

    // We'll fetch chunks in parallel but play them in original order.
    // Prepare per-index blobs and waiters so we can start playback as soon
    // as chunk 0 is ready while later chunks continue downloading.
    const blobs = new Array();
    const waiters = new Array();
    for (let i=0;i<0;i++){} // keep block structure

    function makeWaiter(){
      let resolveFn = null;
      const p = new Promise((res)=>{ resolveFn = res; });
      return { promise: p, resolve: resolveFn };
    }

    // helper to play a single blob and wait until it finishes
    function playSingleBlob(blob){
      return new Promise((resolve)=>{
        try{ stopAudio(); }catch(e){}
        startMouthSyncFromFile(blob);
        if (ttsStatus) ttsStatus.textContent = '播放中';
        try{
          if (audioEl && typeof audioEl.addEventListener === 'function'){
            const onEnd = ()=>{ try{ audioEl.removeEventListener('ended', onEnd); }catch(e){} resolve(); };
            audioEl.addEventListener('ended', onEnd);
          } else {
            const waiter = setInterval(()=>{
              if (!audioEl || audioEl.ended){ clearInterval(waiter); resolve(); }
            }, 200);
          }
        }catch(e){ console.warn('attach onended failed', e); resolve(); }
      });
    }

    // start: split text and issue parallel fetches
    const chunks = splitText(text, TTS_SPLIT_LIMIT);
    if (chunks.length === 0) return;

    if (ttsBtn) ttsBtn.disabled = true;
    if (ttsStatus) ttsStatus.textContent = '合成中...';

    // track fetch completion and playback completion
    let fetchesPending = chunks.length;
    let fetchHadError = false;

    // create waiters for each chunk so we can play chunks in order
    for (let i=0;i<chunks.length;i++){ waiters[i] = makeWaiter(); blobs[i] = null; }

    const fetchPromises = chunks.map((chunk, i) => (async ()=>{
      const payload = { text: chunk, text_language: lang || 'zh' };
      try{
        const r = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        if (!r.ok){
          const txt = await r.text();
          console.warn('TTS chunk failed', r.status, txt);
          fetchHadError = true;
        } else {
          const contentType = r.headers.get('content-type') || 'audio/wav';
          const ab = await r.arrayBuffer();
          const blob = new Blob([ab], { type: contentType });
          blobs[i] = blob;
        }
      }catch(err){ console.warn('TTS chunk fetch err', err); fetchHadError = true; }
      finally{ fetchesPending -= 1; try{ waiters[i].resolve(); }catch(e){} }
    })());

    try{
      // play chunks strictly in original order; wait for each chunk's fetch to finish
      for (let i=0;i<chunks.length;i++){
        try{ await waiters[i].promise; }catch(e){}
        if (blobs[i]){
          await playSingleBlob(blobs[i]);
        } else {
          console.warn('Skipping missing TTS chunk', i);
        }
      }

      try{ await Promise.all(fetchPromises); }catch(e){}
      if (fetchHadError) showOverlay('部分 TTS 分段合成失败，已跳过错误片段');
    }catch(e){ console.warn('TTS allDone err', e); }
    finally{
      if (ttsBtn) ttsBtn.disabled = false;
      if (ttsStatus) ttsStatus.textContent = '已完成';
    }

    return; // resolved when playback finished
  }

  function startMouthSyncFromFile(file){
    stopAudio();
    if (!file) return;
    audioEl = new Audio(URL.createObjectURL(file));
    audioEl.crossOrigin = 'anonymous';
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    try{ audioCtx.resume && audioCtx.resume(); }catch(e){}
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 2048;
    dataArray = new Uint8Array(analyser.fftSize);
    sourceNode = audioCtx.createMediaElementSource(audioEl);
    sourceNode.connect(analyser);
    analyser.connect(audioCtx.destination);
    audioEl.onended = ()=>{
      try{
        if (currentModel && currentModel.internalModel && currentModel.internalModel.coreModel && typeof currentModel.internalModel.coreModel.setParameterValueById === 'function'){
          currentModel.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', 0);
        } else if (currentModel && typeof currentModel.setMouthOpenY === 'function'){
          currentModel.setMouthOpenY(0);
        }
      }catch(e){}
    };
    audioEl.play().catch(()=>{ /* autoplay may be blocked */ });

    function tick(){
      analyser.getByteTimeDomainData(dataArray);
      // compute RMS
      let sum=0;
      for(let i=0;i<dataArray.length;i++){ const v = (dataArray[i]-128)/128; sum+=v*v }
      const rms = Math.sqrt(sum / dataArray.length);
      // map rms to mouth open parameter (0..1)
      const mouth = Math.min(1, Math.max(0, (rms*5)));
      if (currentModel){
        try{
          // Try common parameter id
          if (currentModel.internalModel && currentModel.internalModel.coreModel && typeof currentModel.internalModel.coreModel.setParameterValueById === 'function'){
            currentModel.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', mouth);
          } else if (currentModel.internalModel && currentModel.internalModel.coreModel && currentModel.internalModel.coreModel.parameters){
            // fallback: attempt to set by parameter object
            const p = currentModel.internalModel.coreModel.parameters['ParamMouthOpenY'];
            if (p && typeof p.setValue === 'function') p.setValue(mouth);
          } else if (typeof currentModel.setMouthOpenY === 'function'){
            currentModel.setMouthOpenY(mouth);
          }
        }catch(e){ /* ignore if model API differs */ }
      }
      rafId = requestAnimationFrame(tick);
    }
    rafId = requestAnimationFrame(tick);
  }

  if (playAudioBtn) playAudioBtn.addEventListener('click', ()=>{
    const f = audioFile.files && audioFile.files[0];
    if (!f){ alert('请选择音频文件'); return }
    startMouthSyncFromFile(f);
  });
  if (stopAudioBtn) stopAudioBtn.addEventListener('click', ()=>{ stopAudio(); });
  // TTS button
  if (ttsBtn){
    ttsBtn.addEventListener('click', ()=>{
      const text = ttsText ? ttsText.value : '';
      const lang = ttsLang ? ttsLang.value : 'zh';
      synthesizeAndPlay(text, lang);
    });
  }
  // save model state on unload
  window.addEventListener('beforeunload', ()=>{
    try{ saveModelState(); }catch(e){}
  });

  if (quickToggleAsrBtn) quickToggleAsrBtn.addEventListener('click', ()=>{
    toggleAsr();
  });
  if (quickStopMediaBtn) quickStopMediaBtn.addEventListener('click', ()=>{
    interruptPlayback();
  });
  if (quickRandomMotionBtn) quickRandomMotionBtn.addEventListener('click', ()=>{ playRandomMotion(); });
  if (quickScaleUpBtn) quickScaleUpBtn.addEventListener('click', ()=>{ nudgeScale(0.05); });
  if (quickScaleDownBtn) quickScaleDownBtn.addEventListener('click', ()=>{ nudgeScale(-0.05); });
  if (quickController){
    quickController.addEventListener('mouseenter', ()=>{
      hoverQuickController = true;
      refreshQuickControllerVisibility();
      if (clickThroughController) clickThroughController.forceInteractive();
    });
    quickController.addEventListener('mouseleave', ()=>{
      hoverQuickController = false;
      refreshQuickControllerVisibility();
    });
  }
  if (asrTextEl){
    asrTextEl.addEventListener('scroll', ()=>{ rememberAsrScrollIntent(); });
    asrTextEl.addEventListener('mouseenter', ()=>{
      if (clickThroughController) clickThroughController.forceInteractive();
    });
    asrTextEl.addEventListener('wheel', ()=>{
      if (clickThroughController) clickThroughController.forceInteractive();
    }, { passive: true });
  }
  if (trayToggleBtn) trayToggleBtn.addEventListener('click', async ()=>{
    try{
      if (window.api && window.api.hideToTray) await window.api.hideToTray();
    }catch(e){ console.warn('hideToTray failed', e); }
  });

  if (openConfigBtn) openConfigBtn.addEventListener('click', async ()=>{
    try{
      if (window.api && window.api.openConfigWindow) await window.api.openConfigWindow();
    }catch(e){ console.warn('openConfigWindow failed', e); }
  });
  updateQuickAsrButton();
})();