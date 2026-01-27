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
      // 缩放并定位到右下角初始位置
      model.scale.set(0.5);
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
  const baseScale = Math.min(app.renderer.width / 1600, app.renderer.height / 900);
  model.scale.set(Math.max(0.3, baseScale * 0.6));
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

  // 自动尝试加载默认模型路径
  modelPathInput.value = defaultModel;
  // delay so that page UI is visible
  setTimeout(()=>{
    loadModel(defaultModel);
  }, 200);

  // 窗口尺寸变化时保持模型在屏幕内
  window.addEventListener('resize', ()=>{
    if (!currentModel) return;
    currentModel.x = Math.min(currentModel.x, app.renderer.width - 50);
    currentModel.y = Math.min(currentModel.y, app.renderer.height - 10);
    // auto-scale with resize
    try{
      const baseScale = Math.min(app.renderer.width / 1600, app.renderer.height / 900);
      currentModel.scale.set(Math.max(0.3, baseScale * 0.6));
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

    function anyInteractiveContains(x, y){
      for (const sel of INTERACTIVE_SELECTORS){
        const el = document.querySelector(sel);
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) return true;
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
          // disable ignore so clicks are delivered to the page
          window.api.setIgnoreMouseEvents(false).catch(()=>{});
        }
      } else {
        if (interactiveActive){
          interactiveActive = false;
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
})();
