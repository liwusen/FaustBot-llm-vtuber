

// 简单的 Live2D 展示 demo，依赖 PIXI 和 pixi-live2d-display
(() => {
  const defaultModel = '2D/hiyori_pro_mic.model3.json';

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

  // 创建 PIXI 应用
  const app = new PIXI.Application({
    backgroundAlpha: 0,
    resizeTo: window,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true,
  });

  document.getElementById('app').appendChild(app.view);

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
      if (typeof currentModel.scale.set === 'function') currentModel.scale.set(s);
      else currentModel.scale = { x: s, y: s };
    }catch(e){/*ignore*/}
  }

  function loadSavedModelState(){
    loadModelState();
    if (!currentModel || !_savedModelState) return;
    try{
      const st = _savedModelState;
      const modelPath = (modelPathInput && modelPathInput.value) ? modelPathInput.value.trim() : defaultModel;
      if (st.modelPath && st.modelPath === modelPath){
        if (typeof st.x === 'number') currentModel.x = st.x;
        if (typeof st.y === 'number') currentModel.y = st.y;
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
        showOverlay('ASR服务返回错误: ' + raw);
        return;
      }
      if (j && j.status === 'success'){
        const text = j.text || '';
        if (text && text.length > 0){
          showAsrText(text);
          asrStatusEl.textContent = '识别成功';
          // send recognized text to chat websocket if available
          try{ sendToChat(text); }catch(e){}
        } else {
          asrStatusEl.textContent = '识别成功但无文本';
          showOverlay('ASR返回但文本为空');
        }
      } else if (j && j.status === 'error'){
        asrStatusEl.textContent = '识别失败';
        showOverlay('ASR失败: ' + (j.message || JSON.stringify(j)));
      } else if (j && j.text){
        showAsrText(j.text);
        asrStatusEl.textContent = '识别完成';
      } else {
        asrStatusEl.textContent = '无返回或未知格式';
        showOverlay('ASR返回未知格式: ' + raw);
      }
    }catch(err){
      console.error('upload error', err);
      asrStatusEl.textContent = '网络或服务错误';
      showOverlay('上传或网络错误: ' + String(err));
    }
  }
  //console.log("ASR Result:", asrResult);
  //return;
  // --- Chat via HTTP POST to backend (/faust/chat) ---
  const CHAT_HOST = '127.0.0.1';
  const CHAT_PORT = 13900;
  const CHAT_ENDPOINT = `http://${CHAT_HOST}:${CHAT_PORT}/faust/chat`;

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
        await synthesizeAndPlay(arg, lang);
        useVAD=true; // re-enable VAD after TTS playback
      } else if (cmd === 'STOP'){
        // stop audio and optionally stop asr
        try{ stopAudio(); }catch(e){}
        try{ stopMicAsr(); }catch(e){}
      } else {
        console.warn('Unknown faust command', raw);
      }
    }catch(e){ console.error('handleFaustCommand error', e); }
  }

  // register handler from preload-exposed API
  if (window.faust && window.faust.onCommand){
    window.faust.onCommand((cmd)=>{ handleFaustCommand(cmd); });
  }

  async function sendToChat(text){
    if (!text) return;
    try{
      if (chatStatusEl) chatStatusEl.textContent = '聊天请求中...';
      const r = await fetch(CHAT_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
      });
      const raw = await r.text();
      let j = null;
      try{ j = JSON.parse(raw); }catch(e){ j = null }
      if (!r.ok){
        chatStatusEl && (chatStatusEl.textContent = '聊天服务错误');
        console.warn('chat POST failed', r.status, raw);
        return;
      }
      if (j && j.reply){
        const reply = j.reply;
        console.log('chat reply:', reply);
        showAsrText(reply);
        // pause ASR if running and not in voice-barge-in mode
        let resumeAfter = true;
        if (asrRunning && !voiceBargeInEnabled){ pauseRecording(); resumeAfter = true; }
        //if <NO_TTS_OUTPUT> in output, skip TTS playback
        if (reply.includes('<NO_TTS_OUTPUT>')){
          chatStatusEl && (chatStatusEl.textContent = '聊天完成（无语音输出）');
        }else{
          synthesizeAndPlay(reply, 'zh');
        }
        if (resumeAfter){
          startMicAsr(); // simpler to just restart ASR
          // Prefer event-driven resume when audio ends. startMouthSyncFromFile
          // assigns `audioEl` and begins playback; attach a one-time
          // 'ended' listener to resume recording. Also add a safety timeout
          // in case 'ended' does not fire (autoplay/codec issues).
          try{
            if (audioEl && typeof audioEl.addEventListener === 'function'){
              const onEnd = () => { try{ resumeRecording(); }catch(e){} finally { audioEl.removeEventListener('ended', onEnd); if (safetyTimer) clearTimeout(safetyTimer); } };
              audioEl.addEventListener('ended', onEnd);
              // safety timeout: resume after 30s if ended event didn't fire
              var safetyTimer = setTimeout(()=>{ try{ resumeRecording(); }catch(e){} }, 30000);
            } else {
              // fallback to polling if audioEl is not yet available
              const waiter = setInterval(()=>{
                if (!audioEl || audioEl.ended){
                  try{ resumeRecording(); }catch(e){}
                  clearInterval(waiter);
                }
              }, 250);
            }
          }catch(e){ console.warn('resumeAfter attach err', e); }
        }
        chatStatusEl && (chatStatusEl.textContent = '聊天完成');
      } else if (j && j.error){
        chatStatusEl && (chatStatusEl.textContent = '聊天错误');
        console.warn('chat returned error', j.error);
      } else {
        chatStatusEl && (chatStatusEl.textContent = '聊天未知响应');
        console.warn('chat unknown response', raw);
      }
    }catch(e){
      console.warn('sendToChat err', e);
      chatStatusEl && (chatStatusEl.textContent = '聊天网络错误');
    }
  }

  function showAsrText(text){
    if (!asrTextEl) return;
    asrTextEl.style.display = text ? 'block' : 'none';
    asrTextEl.textContent = text || '';
    updateAsrTextPosition();
  }

  function updateAsrTextPosition(){
    if (!asrTextEl || !currentModel || !app || !app.renderer) return;
    try{
      const canvasRect = app.renderer.view.getBoundingClientRect();
      const b = currentModel.getBounds();
      // b.x/b.y are renderer coordinates; map to client
      const clientX = canvasRect.left + (b.x + b.width/2) * (canvasRect.width / app.renderer.width);
      const clientY = canvasRect.top + (b.y) * (canvasRect.height / app.renderer.height);
      // position slightly above head
      const offsetY = -20;
      asrTextEl.style.left = Math.round(clientX - asrTextEl.offsetWidth/2) + 'px';
      asrTextEl.style.top = Math.round(clientY + offsetY) + 'px';
      asrTextEl.style.fontSize=30
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

  // update asrText position each frame if visible
  function rafUpdate(){
    if (asrTextEl && asrTextEl.style.display !== 'none') updateAsrTextPosition();
    requestAnimationFrame(rafUpdate);
  }
  requestAnimationFrame(rafUpdate);

  function showOverlay(msg){
    const o = document.getElementById('overlay');
    o.textContent = msg;
  }

  function clearOverlay(){
    const o = document.getElementById('overlay');
    o.textContent = '';
  }

  function loadModel(path){
    console.log('Loading model:', path);
    if (!PIXI.live2d) {
      showOverlay('未检测到 pixi-live2d-display 库，请检查网络或依赖。')
      return;
    }
    showOverlay('加载模型: ' + path);
    PIXI.live2d.Live2DModel.from(path).then(model => {
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

      // 基本拖拽
      model.on('pointerdown', (e) => {
        dragging = true;
        const pos = e.data.global;
        dragOffset.x = pos.x - model.x;
        dragOffset.y = pos.y - model.y;
      });
      model.on('pointerup', () => { dragging = false; });
      model.on('pointerupoutside', () => { dragging = false; });
      // save state after dragging ends
      model.on('pointerup', () => { saveModelState(); });
      model.on('pointermove', (e) => {
        if (!dragging) return;
        const pos = e.data.global;
        model.x = pos.x - dragOffset.x;
        model.y = pos.y - dragOffset.y;
      });

      // 鼠标悬停时张嘴示意（若模型支持）
      model.on('mouseover', () => {
        try{ model.internalModel.motionManager.startRandomMotion('hit_areas'); }catch(e){}
      });

      app.stage.addChild(model);
      clearOverlay();
      // 自动缩放示例：根据窗口尺寸调整基础缩放
      baseScale = Math.min(app.renderer.width / 1600, app.renderer.height / 900);
      // apply user-selected scale factor
      applyModelScale();
      // keep reference for mouth sync
      model._faustLive2D = { mouthValue: 0 };
    }).catch(err => {
      showOverlay('加载模型失败：' + err);
      console.error(err);
    });
  }

  loadBtn.addEventListener('click', () => {
    const p = modelPathInput.value.trim() || defaultModel;
    loadModel(p);
  });

  resetBtn.addEventListener('click', () => {
    if (!currentModel) return;
    currentModel.x = app.renderer.width - 200;
    currentModel.y = app.renderer.height - 10;
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

    let clickThroughEnabled = false;
    let interactiveActive = false; // whether ignore is currently false due to hover
    let pendingTimeout = null;
    const INTERACTIVE_SELECTORS = ['#controls', '#overlay'];

    function anyInteractiveContains(clientX, clientY){
      // check DOM interactive selectors first
      for (const sel of INTERACTIVE_SELECTORS){
        const el = document.querySelector(sel);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom) return true;
      }

      // check model hit area (if model exists)
      if (currentModel && app && app.renderer && app.renderer.view){
        try{
          const canvasRect = app.renderer.view.getBoundingClientRect();
          // map client coords to renderer space
          const rx = (clientX - canvasRect.left) * (app.renderer.width / canvasRect.width);
          const ry = (clientY - canvasRect.top) * (app.renderer.height / canvasRect.height);
          // model bounds in renderer coordinates
          const b = currentModel.getBounds();
          if (rx >= b.x && rx <= b.x + b.width && ry >= b.y && ry <= b.y + b.height) return true;
        }catch(e){
          // swallow errors and fall through
        }
      }

      return false;
    }

    // debounce helper to avoid rapid IPC toggles
    function scheduleEnableIgnore(){
      if (pendingTimeout) clearTimeout(pendingTimeout);
      pendingTimeout = setTimeout(()=>{
        pendingTimeout = null;
        if (clickThroughEnabled && !interactiveActive){
          window.api.setIgnoreMouseEvents(true).catch(()=>{});
        }
      }, 180);
    }

    function enableClickThroughRenderer(){
      document.body.classList.add('click-through');
      clickThroughEnabled = true;
      interactiveActive = false;
      // set ignore with forward so we still get mousemove events
      window.api.setIgnoreMouseEvents(true).catch(()=>{});
      window.addEventListener('mousemove', onGlobalMouseMove, { passive: true });
    }

    function disableClickThroughRenderer(){
      document.body.classList.remove('click-through');
      clickThroughEnabled = false;
      interactiveActive = false;
      if (pendingTimeout) { clearTimeout(pendingTimeout); pendingTimeout = null; }
      window.removeEventListener('mousemove', onGlobalMouseMove);
      // ensure window receives events
      window.api.setIgnoreMouseEvents(false).catch(()=>{});
    }

    function onGlobalMouseMove(e){
      const x = e.clientX;
      const y = e.clientY;
      const over = anyInteractiveContains(x, y);
      if (over){
        if (!interactiveActive){
          interactiveActive = true;
          // enable pointer events on canvas so PIXI can receive clicks when needed
          try{ app.renderer.view.style.pointerEvents = 'auto'; }catch(e){}
          // disable ignore so clicks are delivered to the page
          window.api.setIgnoreMouseEvents(false).catch(()=>{});
        }
      } else {
        if (interactiveActive){
          interactiveActive = false;
          // disable pointer events on canvas so clicks pass through
          try{ app.renderer.view.style.pointerEvents = 'none'; }catch(e){}
          // schedule re-enable of ignore so we don't toggle on tiny movements
          scheduleEnableIgnore();
        }
      }
    }

    function setClickThroughOnRenderer(val){
      if (val) enableClickThroughRenderer();
      else disableClickThroughRenderer();
    }

    clickThrough.addEventListener('change', (e)=>{
      const val = !!e.target.checked;
      setClickThroughOnRenderer(val);
    });

    // ensure initial state
    if (clickThrough.checked) setClickThroughOnRenderer(true);
    else setClickThroughOnRenderer(false);
  } else {
    // hide control if API not present
    clickThrough.parentElement.style.display = 'inline-block';
  }

  // --- model scale slider handling ---
  if (modelScaleSlider){
    modelScaleSlider.addEventListener('input', (e)=>{
      scaleFactor = parseFloat(e.target.value) || 1.0;
      if (modelScaleValue) modelScaleValue.textContent = scaleFactor.toFixed(2) + 'x';
      applyModelScale();
      try{ saveModelState(); }catch(e){}
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
    if (rafId) cancelAnimationFrame(rafId);
    if (sourceNode){ try{ sourceNode.disconnect(); }catch(e){} sourceNode=null }
    if (analyser){ analyser.disconnect(); analyser=null }
    if (audioCtx){ try{ audioCtx.close(); }catch(e){} audioCtx=null }
  }

  // TTS: call backend API (port 5000) to synthesize text and play the returned audio
  async function synthesizeAndPlay(text, lang){
    if (!text || text.trim().length === 0) return;
    const endpoint = (window.location && window.location.hostname) ? `http://${window.location.hostname}:5000/` : 'http://127.0.0.1:5000/';
    try{
      if (ttsBtn) ttsBtn.disabled = true;
      if (ttsStatus) ttsStatus.textContent = '合成中...';
      const payload = { text: text, text_language: lang || 'zh' };
      const r = await fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!r.ok){
        const txt = await r.text();
        showOverlay('TTS服务错误: ' + r.status + ' ' + txt);
        if (ttsStatus) ttsStatus.textContent = '合成失败';
        return;
      }
      const contentType = r.headers.get('content-type') || 'audio/wav';
      const ab = await r.arrayBuffer();
      const blob = new Blob([ab], { type: contentType });
      // stop any existing audio and play synthesized audio (also start mouth sync)
      try{ stopAudio(); }catch(e){}
      startMouthSyncFromFile(blob);
      if (ttsStatus) ttsStatus.textContent = '播放中';
      // hook end event to update status
      try{
        if (audioEl){
          audioEl.onended = ()=>{ if (ttsStatus) ttsStatus.textContent = '已完成'; };
        }
      }catch(e){}
    }catch(err){
      console.error('TTS synth err', err);
      showOverlay('TTS 错误: ' + String(err));
      if (ttsStatus) ttsStatus.textContent = '合成失败';
    }finally{
      if (ttsBtn) ttsBtn.disabled = false;
    }
  }

  function startMouthSyncFromFile(file){
    stopAudio();
    if (!file) return;
    audioEl = new Audio(URL.createObjectURL(file));
    audioEl.crossOrigin = 'anonymous';
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 2048;
    dataArray = new Uint8Array(analyser.fftSize);
    sourceNode = audioCtx.createMediaElementSource(audioEl);
    sourceNode.connect(analyser);
    analyser.connect(audioCtx.destination);
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

  playAudioBtn.addEventListener('click', ()=>{
    const f = audioFile.files && audioFile.files[0];
    if (!f){ alert('请选择音频文件'); return }
    startMouthSyncFromFile(f);
  });
  stopAudioBtn.addEventListener('click', ()=>{ stopAudio(); });
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
})();