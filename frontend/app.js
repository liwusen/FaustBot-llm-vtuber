import { resampleFloat32, concatFloat32Arrays, floatTo16BitPCM, writeString, encodeWAV, interleaveAndEncodeWav } from './libs/audio-utils.js';
import { normalizeTtsText, decodeWsPayload, extractCompletedSentences } from './libs/text-utils.js';
import { formatResultBubbleText, formatToolBubbleValue, escapeHtml, renderResultBubbleHtml, cloneBubbleEntries } from './libs/bubble-utils.js';
import { initLogPanel } from './libs/log-panel.js';
import { initLiveMode } from './libs/live-mode.js';
import { initNimbleWindows } from './libs/nimble-window.js';
import { initHilApproval } from './libs/hil-approval.js';
import { initVRMConfigPanel } from './libs/vrm-config-panel.js';
import { initAudioPlayback } from './libs/audio-playback.js';
import { initAutocomplete } from './libs/autocomplete.js';
import { createUiWidgetManager } from './libs/ui-widget-manager.js';
import { initUiWidgetEditor } from './libs/ui-widget-editor.js';
import { initLayoutSidePanel } from './libs/layout-side-panel.js';
import { clampToViewport } from './libs/ui-widget-manager.js';



(() => {

  const defaultModel = '2D/hiyori_pro_zh/hiyori_pro_t11.model3.json';
  const ADMIN_RUNTIME_ENDPOINT = 'http://127.0.0.1:13900/faust/admin/runtime';
  const ADMIN_CONFIG_ENDPOINT = 'http://127.0.0.1:13900/faust/admin/config';

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
  const asrBubbleEl = document.getElementById('asrBubble');
  const asrTextEl = document.getElementById('asrText');
  const subagentSummaryEl = document.getElementById('subagentSummary');
  const hideAsrBubbleBtn = document.getElementById('hideAsrBubbleBtn');
  const vadProbEl = document.getElementById('vadProb');
  const vadProbLabel = document.getElementById('vadProbLabel');
  const textChatInput = document.getElementById('textChatInput');
  const textChatSendBtn = document.getElementById('textChatSendBtn');
  const textChatStatus = document.getElementById('textChatStatus');
  const trayToggleBtn = document.getElementById('trayToggleBtn');
  const openConfigBtn = document.getElementById('openConfigBtn');
  const openLiveBtn = document.getElementById('openLiveBtn');
  const openVRMConfigBtn = document.getElementById('openVRMConfigBtn');
  const vrmConfigPanel = document.getElementById('vrmConfigPanel');
  const vrmConfigPanelBody = document.getElementById('vrmConfigPanelBody');
  const vrmConfigSaveBtn = document.getElementById('vrmConfigSaveBtn');
  const vrmConfigCloseBtn = document.getElementById('vrmConfigCloseBtn');
  const vrmConfigResetBtn = document.getElementById('vrmConfigResetBtn');
  const subagentPanel = document.getElementById('subagentPanel');
  const subagentPanelHeader = document.getElementById('subagentPanelHeader');
  const subagentPanelTitle = document.getElementById('subagentPanelTitle');
  const subagentPanelBody = document.getElementById('subagentPanelBody');
  const subagentPanelCloseBtn = document.getElementById('subagentPanelCloseBtn');
  const subagentStopBtn = document.getElementById('subagentStopBtn');
  const quickController = document.getElementById('modelQuickController');
  const quickToggleAsrBtn = document.getElementById('quickToggleAsr');
  const quickStopBtn = document.getElementById('quickStopBtn');
  let agentIsProcessing = false;
  const quickRandomMotionBtn = document.getElementById('quickRandomMotion');
  const quickEditLayoutBtn = document.getElementById('quickEditLayoutBtn');
  let Live2DModel=null;
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
  let asrBubbleState = { source: 'ai', entries: [] };
  let subagentStatuses = [];
  let subagentEventCache = {};
  let selectedSubagentName = '';
  let devToolsLikelyOpen = false;
  let asrTextPinnedToBottom = true;
  let currentLipSyncParamIds = ['ParamMouthOpenY'];
  let activeModelLoadRequestId = 0;
  const motionTriggerCooldownMs = 100;
  const recentMotionTriggers = new Map();
  let textChatBarYFactor = 0.53;
  let quickControllerXOffset = -12;
  let vrmScene = null;
  let modelType = 'live2d';
  let _vrmModulePromise = null;
  let appPluginAssetsLoaded = false;
  let persistedUiWidgetSettings = {};
  const uiWidgetManager = createUiWidgetManager({ getModelBounds: () => getModelViewportBounds() });

  async function loadUiWidgetSettings() {
    if (!window.api || typeof window.api.configRequest !== 'function') return;
    try {
      const data = await window.api.configRequest('GET', '/faust/ui-setting');
      const widgets = (data && data.settings && data.settings.widgets) || {};
      persistedUiWidgetSettings = widgets;
      Object.entries(widgets).forEach(([id, payload]) => {
        try { uiWidgetManager.updateWidget(id, payload || {}); } catch (_e) {}
      });
    } catch (e) {
      console.warn('loadUiWidgetSettings failed', e);
    }
  }

  async function saveUiWidgetSettings() {
    if (!window.api || typeof window.api.configRequest !== 'function') return;
    const widgets = {};
    uiWidgetManager.listWidgets().forEach((widget) => {
      // 临时灵动窗口的 widget 不落盘；持久化窗口 id 稳定，允许保存布局
      if (widget.id.startsWith('nimble::') && !widget.id.startsWith('nimble::persistent_')) return;
      widgets[widget.id] = {
        bindingType: widget.bindingType,
        coord: widget.coord,
        offset: widget.offset,
        scale: widget.scale,
        hidden: widget.hidden,
        props: widget.props || {},
      };
    });
    try {
      await window.api.configRequest('POST', '/faust/ui-setting', { widgets });
    } catch (e) {
      console.warn('saveUiWidgetSettings failed', e);
    }
  }

  function registerBuiltinWidgets() {
    uiWidgetManager.registerWidget({
      id: 'quick-controller',
      element: quickController,
      bindingType: 'model',
      coord: { x: 0.4, y: 0.45 },
      offset: { x: quickControllerXOffset, y: 0 },
      scale: 1,
      hidden: false,
      onLayout: () => updateQuickControllerPosition(),
      schema: { bindingType: 'model', coord: 'point', offset: 'point', scale: 'number', hidden: 'boolean' },
    });
    uiWidgetManager.registerWidget({
      id: 'text-chat-bar',
      element: document.getElementById('textChatBar'),
      bindingType: 'model',
      coord: { x: 0.5, y: textChatBarYFactor },
      offset: { x: 0, y: 0 },
      scale: 1,
      hidden: false,
      onLayout: () => updateTextChatBarPosition(),
      schema: { bindingType: 'model', coord: 'point', scale: 'number', hidden: 'boolean' },
    });
    uiWidgetManager.registerWidget({
      id: 'asr-bubble',
      element: asrBubbleEl,
      bindingType: 'model',
      coord: { x: 0.5, y: 0 },
      offset: { x: 0, y: -108 },
      scale: 1,
      hidden: false,
      onLayout: () => updateAsrTextPosition(false),
      schema: {
        bindingType: 'model', coord: 'point', offset: 'point', scale: 'number', hidden: 'boolean',
        props: {
          fontSize: { type: 'number', label: '字体大小' },
          textColor: { type: 'color', label: '文字颜色' },
          whiteBackground: { type: 'boolean', label: '白色背景' },
          showReasoning: { type: 'boolean', label: '显示推理内容' },
          showTools: { type: 'boolean', label: '显示工具调用' },
          showSubagents: { type: 'boolean', label: '显示 Subagents' },
        },
      },
      props: {
        fontSize: 20,
        textColor: '#000000',
        whiteBackground: true,
        aspectRatio: '',
        showReasoning: true,
        showTools: true,
        showSubagents: true,
      },
    });
    uiWidgetManager.registerWidget({
      id: 'vrm-config-panel',
      element: vrmConfigPanel,
      bindingType: 'screen',
      coord: { x: 0.02, y: 0.03 },
      offset: { x: 0, y: 0 },
      scale: 1,
      hidden: false,
      managed: false,
      schema: { bindingType: 'screen', coord: 'point', scale: 'number', hidden: 'boolean' },
    });
    uiWidgetManager.registerWidget({
      id: 'subagent-panel',
      element: subagentPanel,
      bindingType: 'screen',
      coord: { x: 0.02, y: 0.03 },
      offset: { x: 0, y: 0 },
      scale: 1,
      hidden: false,
      managed: false,
      schema: { bindingType: 'screen', coord: 'point', scale: 'number', hidden: 'boolean' },
    });
    uiWidgetManager.registerWidget({
      id: 'log-panel',
      element: document.getElementById('logPanel'),
      bindingType: 'screen',
      coord: { x: 0.02, y: 0.1 },
      offset: { x: 0, y: 0 },
      scale: 1,
      hidden: false,
      managed: false,
      schema: { bindingType: 'screen', coord: 'point', scale: 'number', hidden: 'boolean' },
    });
    Object.entries(persistedUiWidgetSettings || {}).forEach(([id, payload]) => {
      try { uiWidgetManager.updateWidget(id, payload || {}); } catch (_e) {}
    });
  }

  registerBuiltinWidgets();

  async function getVRMModule() {
    if (!_vrmModulePromise) {
      _vrmModulePromise = import('./libs/vrm-renderer.bundle.js');
    }
    return _vrmModulePromise;
  }

  async function resolveFrontendAssetPath(rawPath){
    const normalized = String(rawPath || '').trim().replace(/\\/g, '/');
    if (!normalized) return normalized;
    if (/^(https?:|file:)/i.test(normalized)) return normalized;
    if (window.api && typeof window.api.resolveFrontendAssetPath === 'function') {
      try {
        return await window.api.resolveFrontendAssetPath(normalized);
      } catch (e) {
        console.warn('resolveFrontendAssetPath failed', normalized, e);
      }
    }
    return normalized;
  }

  function emitAppPluginEvent(eventName, payload){
    if (!window.faustAppUI || !(window.faustAppUI._listeners instanceof Map)) return;
    const listeners = window.faustAppUI._listeners.get(eventName);
    if (!Array.isArray(listeners)) return;
    for (const listener of listeners) {
      try {
        listener(payload);
      } catch (e) {
        console.warn('[faustAppUI] listener failed', eventName, e);
      }
    }
  }

  async function runAppPluginCommandHandlers(cmd, arg){
    if (!window.faustAppUI || !Array.isArray(window.faustAppUI._commandHandlers)) return false;
    for (const handler of window.faustAppUI._commandHandlers) {
      try {
        const handled = await handler(cmd, arg);
        if (handled) return true;
      } catch (e) {
        console.warn('[faustAppUI] command handler failed', cmd, e);
      }
    }
    return false;
  }

  async function loadAppPluginAssets(forceReload = false){
    if (appPluginAssetsLoaded && !forceReload) return;
    try{
      if (forceReload) {
        document.querySelectorAll('script[data-plugin], link[data-plugin]').forEach((node) => node.remove());
        appPluginAssetsLoaded = false;
      }
      if (!window.api || typeof window.api.configRequest !== 'function') {
        console.warn('[loadAppPluginAssets] window.api.configRequest not available');
        return;
      }
      const data = await window.api.configRequest('GET', '/faust/admin/plugins/assets');
      if (!data) return;
      const assets = data.assets || [];
      const baseUrl = window.api.backendBaseUrl || 'http://127.0.0.1:13900';
      if (window.faustAppUI) window.faustAppUI.backendBaseUrl = baseUrl;
      const pending = [];
      const cacheBust = 'v=' + Date.now();
      for (const asset of assets) {
        if (asset.type === 'js' && asset.path) {
          const script = document.createElement('script');
          script.src = baseUrl + asset.path + (asset.path.includes('?') ? '&' : '?') + cacheBust;
          script.setAttribute('data-plugin', asset.plugin_id || '');
          pending.push(new Promise((resolve) => {
            script.onload = () => resolve();
            script.onerror = () => { console.warn('[faustAppUI] failed to load JS', asset.path); resolve(); };
          }));
          document.head.appendChild(script);
        } else if (asset.type === 'css' && asset.path) {
          const link = document.createElement('link');
          link.rel = 'stylesheet';
          link.href = baseUrl + asset.path + (asset.path.includes('?') ? '&' : '?') + cacheBust;
          link.setAttribute('data-plugin', asset.plugin_id || '');
          document.head.appendChild(link);
        }
      }
      if (pending.length) {
        await Promise.all(pending);
      }
      appPluginAssetsLoaded = true;
    }catch(e){
      console.warn('[loadAppPluginAssets] Error:', e);
    }
  }

  const layoutSidePanel = initLayoutSidePanel({ manager: uiWidgetManager, saveSettings: saveUiWidgetSettings });

  if (!window.faustAppUI) {
    window.faustAppUI = {
      backendBaseUrl: (window.api && window.api.backendBaseUrl) || 'http://127.0.0.1:13900',
      _listeners: new Map(),
      _commandHandlers: [],

      on(eventName, listener){
        if (!eventName || typeof listener !== 'function') return () => {};
        const listeners = this._listeners.get(eventName) || [];
        listeners.push(listener);
        this._listeners.set(eventName, listeners);
        return () => {
          const current = this._listeners.get(eventName) || [];
          this._listeners.set(eventName, current.filter((item) => item !== listener));
        };
      },

      registerCommandHandler(handler){
        if (typeof handler !== 'function') return () => {};
        this._commandHandlers.push(handler);
        return () => {
          this._commandHandlers = this._commandHandlers.filter((item) => item !== handler);
        };
      },

      getState(){
        return {
          modelType,
          hasLive2DModel: !!currentModel,
          hasVRMModel: !!vrmScene,
          availableMotions: Array.isArray(availableMotions) ? [...availableMotions] : [],
          agentIsProcessing,
        };
      },

      registerWidget(spec){
        const widget = uiWidgetManager.registerWidget(spec);
        const persisted = persistedUiWidgetSettings && persistedUiWidgetSettings[widget.id];
        if (persisted) {
          try { return uiWidgetManager.updateWidget(widget.id, persisted); } catch (_e) {}
        }
        return widget;
      },

      updateWidget(id, patch){
        return uiWidgetManager.updateWidget(id, patch);
      },

      removeWidget(id){
        return uiWidgetManager.removeWidget(id);
      },

      getWidget(id){
        return uiWidgetManager.getWidget(id);
      },

      listWidgets(){
        return uiWidgetManager.listWidgets();
      },

      isWidgetEditMode(){
        return uiWidgetManager.isEditMode();
      },

      getModelBounds(){
        return getModelViewportBounds();
      },

      showBubble(text, source = 'ai'){
        showResultBubble(source, text);
      },

      triggerMotion(name){
        return triggerModelMotion(name);
      },

      communicate(pluginId, payload){
        if (!window.api || typeof window.api.configRequest !== 'function') {
          return Promise.reject(new Error('window.api.configRequest 未实现'));
        }
        return window.api.configRequest(
          'POST',
          `/faust/plugins/${encodeURIComponent(String(pluginId || ''))}/communicate`,
          payload ?? {}
        );
      },

      communicateSSE(pluginId, params){
        const base = (window.api && window.api.backendBaseUrl) || 'http://127.0.0.1:13900';
        const query = new URLSearchParams(params || {}).toString();
        const url = `${base}/faust/plugins/${encodeURIComponent(String(pluginId || ''))}/sse-communicate${query ? '?' + query : ''}`;
        return new EventSource(url);
      },

      registerSidePanelGroup(spec){
        return layoutSidePanel.registerGroup(spec);
      },

      setSidePanelRender(groupId, fn){
        return layoutSidePanel.setGroupRender(groupId, fn);
      },

      holdChat(flag){
        pluginChatHold = !!flag;
        if (!pluginChatHold) flushHeldChat();
        return pluginChatHold;
      },

      isChatHeld(){
        return pluginChatHold;
      },

      attachLipSyncAnalyser(analyser){
        return attachPluginLipSync(analyser);
      },

      detachLipSyncAnalyser(){
        detachPluginLipSync();
      },
    };
  }

  registerBuiltinWidgets();










  // 创建 PIXI 应用
  const app = new PIXI.Application({
    backgroundAlpha: 0,
    resizeTo: window,
    resolution: window.devicePixelRatio || 1,
    autoDensity: true,
  });

  document.getElementById('app').appendChild(app.view);
  loadAppPluginAssets();

  try{ window.PIXI = PIXI; }catch(e){/* ignore in non-browser env */}

  let currentModel = null;
  let dragging = false;
  let dragOffset = {x:0,y:0};
  // scale control: baseScale is determined from renderer/window; scaleFactor from slider
  let baseScale = 1;
  let scaleFactor = parseFloat(modelScaleSlider ? modelScaleSlider.value : 1.0) || 1.0;
  let runtimeLive2DConfig = null;
  let runtimeImageModelConfig = null;
  let lastPersistedModelPosition = null;

  async function loadRuntimeLive2DConfig(){
    try{
      const r = await fetch(ADMIN_RUNTIME_ENDPOINT);
      const j = await r.json().catch(()=>({}));
      if (!r.ok || !j || j.error) throw new Error((j && (j.detail || j.error)) || `HTTP ${r.status}`);
      runtimeLive2DConfig = ((j.runtime || {}).public_config) || {};
      return runtimeLive2DConfig;
    }catch(e){
      console.warn('load runtime live2d config failed', e);
      runtimeLive2DConfig = null;
      return null;
    }
  }

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
    const clamped = Math.max(0.1, Math.min(2.0, Number.isFinite(parsed) ? parsed : scaleFactor));
    scaleFactor = clamped;
    if (modelType === 'vrm' && vrmScene) {
      vrmScene.setScale(clamped);
    } else {
      applyModelScale();
    }
  }

  // 模型位置统一使用相对坐标(0-1)，限制在 0.1-1.0
  function clampModelRelCoord(v){
    return Math.min(1.0, Math.max(0.1, Number(v) || 0));
  }

  function readConfiguredModelRelPosition(){
    const cfg = runtimeLive2DConfig || {};
    const parse = (raw) => {
      if (raw === undefined || raw === null || raw === '') return null;
      const n = Number(raw);
      return Number.isFinite(n) ? clampModelRelCoord(n) : null;
    };
    return { x: parse(cfg.LIVE2D_MODEL_X), y: parse(cfg.LIVE2D_MODEL_Y) };
  }

  async function persistModelPositionToBackend(force = false){
    if (!currentModel || !app || !app.renderer) return;
    const x = Math.round(clampModelRelCoord(currentModel.x / app.renderer.width) * 1000) / 1000;
    const y = Math.round(clampModelRelCoord(currentModel.y / app.renderer.height) * 1000) / 1000;
    if (!force && lastPersistedModelPosition && lastPersistedModelPosition.x === x && lastPersistedModelPosition.y === y) return;
    try{
      const r = await fetch(ADMIN_CONFIG_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          public: {
            LIVE2D_MODEL_X: x,
            LIVE2D_MODEL_Y: y,
          }
        })
      });
      const j = await r.json().catch(()=>({}));
      if (!r.ok || (j && j.error)) throw new Error((j && (j.detail || j.error)) || `HTTP ${r.status}`);
      lastPersistedModelPosition = { x, y };
      if (runtimeLive2DConfig && typeof runtimeLive2DConfig === 'object') {
        runtimeLive2DConfig.LIVE2D_MODEL_X = x;
        runtimeLive2DConfig.LIVE2D_MODEL_Y = y;
      }
    }catch(e){
      console.warn('persistModelPositionToBackend failed', e);
    }
  }

  function nudgeScale(step){
    setScaleFactor(Math.round((scaleFactor + step) * 100) / 100);
  }

  async function readModelDefinition(path){
    if (!path) return null;
    try{
      const resolvedPath = await resolveFrontendAssetPath(path);
      if (!resolvedPath) throw new Error('empty resolved model path');
      const r = await fetch(resolvedPath);
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

  function extractLipSyncParamIds(modelDef){
    const groups = Array.isArray(modelDef && modelDef.Groups) ? modelDef.Groups : [];
    for (const group of groups){
      if (String(group && group.Target || '').trim() !== 'Parameter') continue;
      if (String(group && group.Name || '').trim().toLowerCase() !== 'lipsync') continue;
      const ids = Array.isArray(group && group.Ids) ? group.Ids.map((item)=>String(item || '').trim()).filter(Boolean) : [];
      if (ids.length) return ids;
    }
    return ['ParamMouthOpenY'];
  }

  function getLive2DViewportMetrics(){
    if (!app || !app.renderer || !app.renderer.view) return null;
    const canvasRect = app.renderer.view.getBoundingClientRect();
    const screen = app.screen || app.renderer.screen || null;
    const screenWidth = Number(screen && screen.width) || Number(app.renderer.width) || 0;
    const screenHeight = Number(screen && screen.height) || Number(app.renderer.height) || 0;
    if (!canvasRect || !screenWidth || !screenHeight) return null;
    return {
      canvasRect,
      screenWidth,
      screenHeight,
      scaleX: canvasRect.width / screenWidth,
      scaleY: canvasRect.height / screenHeight,
    };
  }

  function live2DToClient(x, y){
    const metrics = getLive2DViewportMetrics();
    if (!metrics) return null;
    return {
      x: metrics.canvasRect.left + x * metrics.scaleX,
      y: metrics.canvasRect.top + y * metrics.scaleY,
      scaleX: metrics.scaleX,
      scaleY: metrics.scaleY,
    };
  }

  function clientToLive2D(clientX, clientY){
    const metrics = getLive2DViewportMetrics();
    if (!metrics) return null;
    return {
      x: (clientX - metrics.canvasRect.left) * (metrics.screenWidth / metrics.canvasRect.width),
      y: (clientY - metrics.canvasRect.top) * (metrics.screenHeight / metrics.canvasRect.height),
    };
  }

  function getModelViewportBounds(){
    if (modelType === 'vrm' && vrmScene) {
      try {
        const bounds = vrmScene.getBounds();
        return {
          left: bounds.x,
          top: bounds.y,
          width: bounds.width,
          height: bounds.height,
        };
      } catch (e) {
        return null;
      }
    }
    if (!currentModel || !app || !app.renderer) return null;
    try {
      const bounds = currentModel.getBounds();
      const topLeft = live2DToClient(bounds.x, bounds.y);
      if (!topLeft) return null;
      return {
        left: topLeft.x,
        top: topLeft.y,
        width: bounds.width * topLeft.scaleX,
        height: bounds.height * topLeft.scaleY,
      };
    } catch (e) {
      return null;
    }
  }


  function updateQuickAsrButton(){
    if (!quickToggleAsrBtn) return;
    const labelEl = quickToggleAsrBtn.querySelector('.qc-label');
    if (labelEl) labelEl.textContent = asrRunning ? '停听' : '语音识别';
    quickToggleAsrBtn.classList.toggle('active', !!asrRunning);
    quickToggleAsrBtn.title = asrRunning ? '停止语音识别' : '启动语音识别';
  }

  function updateQuickControllerPosition(){
    if (!quickController) return;
    const widget = uiWidgetManager.getWidget('quick-controller');
    const editMode = uiWidgetManager.isEditMode();
    quickController.classList.toggle('ui-widget-hidden-preview', !!(editMode && widget && widget.hidden));
    if (widget && widget.hidden && !editMode) {
      quickController.classList.remove('visible');
      quickController.style.display = 'none';
      return;
    }
    quickController.style.display = 'flex';
    const binding = widget || { coord: { x: 0.4, y: 0.45 }, offset: { x: quickControllerXOffset, y: 0 }, scale: 1 };
    if (modelType === 'vrm' && vrmScene) {
      try{
        const b = vrmScene.getBounds();
        const left = b.x + b.width * binding.coord.x;
        const top = b.y + b.height * binding.coord.y;
        const controllerScale = Math.max(0.72, Math.min(1.2, 1)) * (binding.scale || 1);
        const size = uiWidgetManager.getWidgetSize('quick-controller', { width: 104, height: 340 });
        const clamped = clampToViewport(left + binding.offset.x, top + binding.offset.y, size.width, size.height, window.innerWidth, window.innerHeight, 8);
        quickController.style.left = Math.round(clamped.left) + 'px';
        quickController.style.top = Math.round(clamped.top) + 'px';
        quickController.style.setProperty('--qc-scale', controllerScale.toFixed(3));
      }catch(e){/* ignore */}
      return;
    }
    if (!currentModel || !app || !app.renderer) return;
    try{
      const b = currentModel.getBounds();
      const topLeft = live2DToClient(b.x, b.y);
      if (!topLeft) return;
      const left = topLeft.x + topLeft.scaleX * b.width * binding.coord.x;
      const top = topLeft.y + topLeft.scaleY * b.height * binding.coord.y;
      const controllerScale = Math.max(0.72, Math.min(1.2, topLeft.scaleX)) * (binding.scale || 1);
      const size = uiWidgetManager.getWidgetSize('quick-controller', { width: 104, height: 340 });
      const clamped = clampToViewport(left + binding.offset.x, top + binding.offset.y, size.width, size.height, window.innerWidth, window.innerHeight, 8);
      quickController.style.left = Math.round(clamped.left) + 'px';
      quickController.style.top = Math.round(clamped.top) + 'px';
      quickController.style.setProperty('--qc-scale', controllerScale.toFixed(3));
    }catch(e){/* ignore */}
  }

  function setQuickControllerVisible(visible){
    if (!quickController) return;
    quickController.classList.toggle('visible', !!visible);
  }

  function refreshQuickControllerVisibility(){
    const modelActive = !!(currentModel || (modelType === 'vrm' && vrmScene));
    setQuickControllerVisible(modelActive && (hoverModel || hoverQuickController || dragging || interactionLocked));
    updateQuickControllerPosition();
  }

  function isPointOverQuickController(clientX, clientY){
    if (!quickController) return false;
    const rect = quickController.getBoundingClientRect();
    return rect.width > 0 && clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
  }

  function isPointOverAsrBubble(clientX, clientY){
    if (!asrBubbleEl || asrBubbleEl.style.display === 'none') return false;
    const rect = asrBubbleEl.getBoundingClientRect();
    return rect.width > 0 && clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
  }

  function isPointOverSubagentSummary(clientX, clientY){
    if (!subagentSummaryEl || subagentSummaryEl.style.display === 'none') return false;
    const rect = subagentSummaryEl.getBoundingClientRect();
    return rect.width > 0 && clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
  }

  function isPointOverSubagentPanel(clientX, clientY){
    if (!subagentPanel || subagentPanel.style.display === 'none') return false;
    const rect = subagentPanel.getBoundingClientRect();
    return rect.width > 0 && clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
  }

  function isPointOverVRMConfig(clientX, clientY) {
    if (!vrmConfigPanel || vrmConfigPanel.style.display === 'none') return false;
    const rect = vrmConfigPanel.getBoundingClientRect();
    return rect.width > 0 && clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
  }

  function isPointOverTextChatBar(clientX, clientY) {
    const bar = document.getElementById('textChatBar');
    if (!bar || bar.style.display === 'none') return false;
    const rect = bar.getBoundingClientRect();
    return rect.width > 0 && clientX >= rect.left && clientX <= rect.right && clientY >= rect.top && clientY <= rect.bottom;
  }

  function isPointerOnModel(clientX, clientY){
    if (modelType === 'vrm' && vrmScene) {
      return vrmScene.hitTest(clientX, clientY);
    }
    if (!currentModel || !app || !app.renderer) return false;
    try{
      const point = clientToLive2D(clientX, clientY);
      if (!point) return false;
      const rx = point.x;
      const ry = point.y;
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

  function triggerModelMotion(name){
    const motionName = String(name || '').trim();
    if (!motionName) return false;
    const now = Date.now();
    const cooldownKey = `${modelType}:${motionName}`;
    const lastTs = recentMotionTriggers.get(cooldownKey) || 0;
    if (now - lastTs < motionTriggerCooldownMs) return false;

    let triggered = false;
    if (modelType === 'vrm' && vrmScene) {
      const expressions = Array.isArray(vrmScene.getAvailableExpressions?.()) ? vrmScene.getAvailableExpressions() : [];
      if (expressions.includes(motionName)) {
        triggered = !!vrmScene.setExpression(motionName);
      }
    } else if (modelType === 'images' && currentModel && currentModel._faustImageModel) {
      triggered = !!currentModel._faustImageModel.setEmotion(motionName);
    } else {
      if (availableMotions.includes(motionName)) {
        triggered = playMotionByName(motionName);
      }
    }
    if (triggered) {
      recentMotionTriggers.set(cooldownKey, now);
    }
    return triggered;
  }

  function consumeMotionTokens(request, chunk){
    // 累积未决文本 + 新 chunk，在完整拼接文本上匹配 <{...}> 表情 token。
    // 支持跨 delta 分片送达的 token（如 '<' 与 '{Flick}>' 分两次到达）：
    //  - 匹配到完整 <{xxx}> 即收集到 request.pendingMotions（不立即触发，
    //    等 TTS 播到对应句子时再触发，保证动作穿插在语音中）
    //  - 未闭合的 '<{' 前缀保留到下一个 chunk
    //  - 尾部孤立 '<' 也保留（可能是下一个 chunk 中 '<{' 的开头）
    const combined = String(request.motionTokenBuffer || '') + String(chunk || '');
    let visible = '';
    let cursor = 0;
    const baseLen = request.visibleLen || 0;
    if (!Array.isArray(request.pendingMotions)) request.pendingMotions = [];

    while (cursor < combined.length) {
      const start = combined.indexOf('<{', cursor);
      if (start === -1) {
        // 没有 <{：若文本以孤立 '<' 结尾，可能是下一个 chunk 中 '<{' 的开头，保留它
        const lastLt = combined.lastIndexOf('<', combined.length - 1);
        if (lastLt >= cursor && lastLt === combined.length - 1) {
          visible += combined.slice(cursor, lastLt);
          request.motionTokenBuffer = '<';
        } else {
          visible += combined.slice(cursor);
          request.motionTokenBuffer = '';
        }
        request.visibleLen = baseLen + visible.length;
        return visible;
      }

      visible += combined.slice(cursor, start);
      const end = combined.indexOf('}>', start + 2);
      if (end === -1) {
        // <{ 已出现但未闭合：保留从 <{ 起的未决文本，等待后续 chunk
        request.motionTokenBuffer = combined.slice(start);
        request.visibleLen = baseLen + visible.length;
        return visible;
      }

      const motionName = combined.slice(start + 2, end).trim();
      if (motionName && !/\s/.test(motionName)) {
        // pos = token 在完整可见文本流中的偏移（用于按句子分配触发时机）
        request.pendingMotions.push({ motion: motionName, pos: baseLen + visible.length });
      }
      cursor = end + 2;
    }

    request.motionTokenBuffer = '';
    request.visibleLen = baseLen + visible.length;
    return visible;
  }

  // 取出落在 [globalStart, globalEnd) 区间内的待触发动作（属于该句子的 token）
  function takeMotionsForSentence(request, globalStart, globalEnd){
    const pending = Array.isArray(request.pendingMotions) ? request.pendingMotions : [];
    const taken = [];
    const keep = [];
    for (const item of pending){
      if (item.pos >= globalStart && item.pos < globalEnd) taken.push(item.motion);
      else keep.push(item);
    }
    request.pendingMotions = keep;
    return taken;
  }

  // 取出偏移 >= minPos 的全部待触发动作（用于回复尾部无标点句子的兜底分配）
  function takeMotionsFrom(request, minPos){
    const pending = Array.isArray(request.pendingMotions) ? request.pendingMotions : [];
    const taken = [];
    const keep = [];
    for (const item of pending){
      if (item.pos >= minPos) taken.push(item.motion);
      else keep.push(item);
    }
    request.pendingMotions = keep;
    return taken;
  }

  function stripMotionTokens(text){
    return String(text || '').replace(/<\{[^}]*\}>/g, '');
  }

  function interruptPlayback(){
    try{ audio.stopAudio(); }catch(e){}
    try{ resetStreamTtsState(); }catch(e){}
    try{ if (ttsStatus) ttsStatus.textContent = '已打断'; }catch(e){}
  }

  function interruptAgent(){
    if (chatWs && chatWs.readyState === WebSocket.OPEN) {
      chatWs.send(JSON.stringify({ type: 'interrupt' }));
    }
    if (currentChatRequest) {
      currentChatRequest.reject(new Error('User interrupted'));
      currentChatRequest = null;
    }
    agentIsProcessing = false;
    if (chatStatusEl) chatStatusEl.textContent = '就绪';
    resetStreamTtsState();
  }

  // 等待后端确认打断完成（interrupt_ack）的 Promise 解析器；
  // 由 handleChatWsMessage 的 interrupt_ack 分支触发。
  let interruptAckResolver = null;
  function interruptAgentAndWait(){
    interruptAgent();
    return new Promise((resolve)=>{
      // 后端收到 interrupt 后会回 interrupt_ack；500ms 超时兜底，
      // 避免后端异常时发送的消息被旧流干扰
      const timer = setTimeout(()=>{
        interruptAckResolver = null;
        resolve();
      }, 500);
      interruptAckResolver = () => {
        clearTimeout(timer);
        interruptAckResolver = null;
        resolve();
      };
    });
  }

  function interruptAll(){
    interruptPlayback();
    interruptAgent();
  }

  function toggleAsr(){
    if (asrRunning) stopRecording();
    else startRecording();
  }

  function focusTextChatInput(){
    if (!textChatInput) return false;
    try{
      textChatInput.focus();
      if (typeof textChatInput.select === 'function') {
        textChatInput.select();
      }
      if (window.api && window.api.focusMainWindow) {
        window.api.focusMainWindow().catch(()=>{});
      }
      return true;
    }catch(e){
      console.warn('focusTextChatInput failed', e);
      return false;
    }
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
  const BACKEND_HOST = '127.0.0.1';
  const BACKEND_PORT = 13900;
  const ASR_ENDPOINT = `http://${BACKEND_HOST}:${BACKEND_PORT}/faust/audio/asr`;
  const TTS_ENDPOINT = `http://${BACKEND_HOST}:${BACKEND_PORT}/faust/audio/tts`;
  const SPEECH_CONFIG_ENDPOINT = `http://${BACKEND_HOST}:${BACKEND_PORT}/faust/audio/config`;
  const audio = initAudioPlayback({
    ttsEndpoint: TTS_ENDPOINT,
    getTtsLang: () => getCurrentTtsLang(),
    getModelType: () => modelType,
    getVrmScene: () => vrmScene,
    getCurrentModel: () => currentModel,
    getLipSyncParamIds: () => currentLipSyncParamIds,
    showOverlay,
    stopBackgroundAudio: () => stopBackgroundAudio(),
  });

  // ── 插件口型同步 / 聊天暂挂（供 faustAppUI 使用，如歌台演唱期间）──
  let pluginLipSync = null;
  let pluginChatHold = false;
  const heldChatQueue = [];

  function attachPluginLipSync(analyser){
    if (!analyser) return false;
    detachPluginLipSync();
    if (modelType === 'vrm' && vrmScene) {
      try { vrmScene.startLipSync(analyser); } catch (e) { return false; }
      pluginLipSync = { mode: 'vrm' };
      return true;
    }
    const data = new Uint8Array(analyser.fftSize || 2048);
    const state = { mode: 'raf', rafId: 0 };
    const tick = () => {
      try {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i++) { const v = (data[i] - 128) / 128; sum += v * v; }
        const rms = Math.sqrt(sum / data.length);
        audio.setLipSyncValue(Math.min(1, rms * 5));
      } catch (e) {}
      state.rafId = requestAnimationFrame(tick);
    };
    state.rafId = requestAnimationFrame(tick);
    pluginLipSync = state;
    return true;
  }

  function detachPluginLipSync(){
    if (!pluginLipSync) return;
    if (pluginLipSync.mode === 'raf' && pluginLipSync.rafId) cancelAnimationFrame(pluginLipSync.rafId);
    if (pluginLipSync.mode === 'vrm' && vrmScene) { try { vrmScene.stopLipSync(); } catch (e) {} }
    try { audio.setLipSyncValue(0); } catch (e) {}
    pluginLipSync = null;
  }

  async function flushHeldChat(){
    while (heldChatQueue.length && !pluginChatHold) {
      const text = heldChatQueue.shift();
      try { await sendToChat(text); } catch (e) { console.warn('flushHeldChat err', e); }
    }
  }
  
  // VAD websocket state
  const DEFAULT_VAD_WS_PATH = '/faust/audio/ws/vad';
  let vadWs = null;
  let useVAD = true;
  const VAD_WINDOW_SIZE = 512; // must match backend WINDOW_SIZE
  let speechRuntimeConfig = {
    tts_mode: 'gpt-sovits',
    asr_mode: 'whisper',
    asr_detection_mode: 'vad',
    vad_ws_path: DEFAULT_VAD_WS_PATH,
    frontend_default_tts_lang: 'zh',
    openai_asr_energy_threshold: 0.02,
    openai_asr_silence_ms: 700,
    openai_asr_min_speech_ms: 250,
    openai_asr_preroll_ms: 250,
  };
  // streaming buffers: leftover resampled samples, pre-roll frames, and current speech frames
  let leftoverResampled = new Float32Array(0);
  let preBufferFrames = []; // small ring of recent frames to include as pre-roll
  let preRollFrameLimit = 8; // each frame is 512 samples -> ~0.256s at 16k
  let uploadFrames = []; // frames collected during speech
  let inSpeech = false;
  let vadEndTimer = null;
  let noVoiceCnt=0;
  let speechFrameCnt = 0;
  let silenceFrameLimit = 22;
  let minSpeechFrameLimit = 8;
  const VAD_END_DEBOUNCE_MS = 300;
  function getVadWsUrl(){
    const path = String((speechRuntimeConfig && speechRuntimeConfig.vad_ws_path) || DEFAULT_VAD_WS_PATH).trim() || DEFAULT_VAD_WS_PATH;
    return `ws://${BACKEND_HOST}:${BACKEND_PORT}${path.startsWith('/') ? path : `/${path}`}`;
  }

  function applySpeechRuntimeConfig(config){
    speechRuntimeConfig = Object.assign({}, speechRuntimeConfig, config || {});
    const frameMs = (VAD_WINDOW_SIZE / TARGET_SAMPLE_RATE) * 1000;
    preRollFrameLimit = Math.max(1, Math.ceil((Number(speechRuntimeConfig.openai_asr_preroll_ms) || 250) / frameMs));
    silenceFrameLimit = Math.max(1, Math.ceil((Number(speechRuntimeConfig.openai_asr_silence_ms) || 700) / frameMs));
    minSpeechFrameLimit = Math.max(1, Math.ceil((Number(speechRuntimeConfig.openai_asr_min_speech_ms) || 250) / frameMs));
    useVAD = true;
    if (ttsLang && speechRuntimeConfig.frontend_default_tts_lang){
      ttsLang.value = speechRuntimeConfig.frontend_default_tts_lang;
    }
  }

  async function refreshSpeechRuntimeConfig(force = false){
    if (!force && speechRuntimeConfig && speechRuntimeConfig._loaded) return speechRuntimeConfig;
    try{
      const r = await fetch(SPEECH_CONFIG_ENDPOINT);
      const j = await r.json().catch(()=>({}));
      if (!r.ok || !j || j.error){
        throw new Error((j && (j.detail || j.error)) || `HTTP ${r.status}`);
      }
      applySpeechRuntimeConfig(Object.assign({}, j.config || {}, { _loaded: true }));
    }catch(e){
      console.warn('load speech config failed, fallback to defaults', e);
      applySpeechRuntimeConfig(Object.assign({}, speechRuntimeConfig, { _loaded: true }));
    }
    return speechRuntimeConfig;
  }

  function getCurrentTtsLang(){
    return (speechRuntimeConfig && speechRuntimeConfig.frontend_default_tts_lang) || ((ttsLang && ttsLang.value) ? ttsLang.value : 'zh');
  }

  function updateSpeechProbabilityUi(probability){
    try{
      const clamped = Math.max(0, Math.min(1, Number(probability) || 0));
      if (vadProbEl) vadProbEl.value = clamped;
      if (vadProbLabel) vadProbLabel.textContent = Math.round(clamped * 100) + '%';
    }catch(e){}
  }

  function finalizeSpeechSegment(probability){
    inSpeech = false;
    vadEndTimer = null;
    const spokenEnough = speechFrameCnt >= minSpeechFrameLimit;
    speechFrameCnt = 0;
    if (!spokenEnough){
      uploadFrames = [];
      asrStatusEl.textContent = '语音过短，已忽略';
      return;
    }
    asrStatusEl.textContent = '上传识别中...';
    if (uploadFrames.length > 0){
      const concat = concatFloat32Arrays(uploadFrames);
      uploadFrames = [];
      console.log('Uploading detected speech segment, length:', concat.length, 'probability:', probability);
      uploadBufferAndShowResult(concat, TARGET_SAMPLE_RATE);
    }
  }

  function handleSpeechActivity(active, probability){
    updateSpeechProbabilityUi(probability);
    if (active){
      noVoiceCnt = 0;
      speechFrameCnt += 1;
      asrStatusEl.textContent = '检测到语音...';
      if (!inSpeech){
        inSpeech = true;
        speechFrameCnt = 1;
        uploadFrames = preBufferFrames.slice();
        preBufferFrames = [];
        asrStatusEl.textContent = '开始录音...';
      }
      if (vadEndTimer){ clearTimeout(vadEndTimer); vadEndTimer = null; }
      return;
    }

    noVoiceCnt += 1;
    asrStatusEl.textContent = '没有语音';
    if (inSpeech && noVoiceCnt >= silenceFrameLimit && !vadEndTimer){
      vadEndTimer = setTimeout(()=> finalizeSpeechSegment(probability), VAD_END_DEBOUNCE_MS);
    }
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
  const CHAT_HOST = BACKEND_HOST;
  const CHAT_PORT = BACKEND_PORT;
  const CHAT_ENDPOINT = `ws://${CHAT_HOST}:${CHAT_PORT}/faust/chat`;
  const SUBAGENT_STATUS_ENDPOINT = `http://${CHAT_HOST}:${CHAT_PORT}/faust/subagents-status`;
  const SUBAGENT_DELETE_ENDPOINT = `http://${CHAT_HOST}:${CHAT_PORT}/faust/subagents`;
  const NIMBLE_MESSAGE_ENDPOINT = `http://${CHAT_HOST}:${CHAT_PORT}/faust/nimble/message`;
  const NIMBLE_CLOSE_ENDPOINT = `http://${CHAT_HOST}:${CHAT_PORT}/faust/nimble/close`;
  const HIL_FEEDBACK_ENDPOINT = `http://${CHAT_HOST}:${CHAT_PORT}/faust/humanInLoop/feedback`;
  const nimbleWin = initNimbleWindows({
    messageEndpoint: NIMBLE_MESSAGE_ENDPOINT,
    closeEndpoint: NIMBLE_CLOSE_ENDPOINT,
    widgetManager: uiWidgetManager,
    saveSettings: saveUiWidgetSettings,
    getPersistedWidgetSettings: (id) => (persistedUiWidgetSettings && persistedUiWidgetSettings[id]) || null,
  });
  const hil = initHilApproval({ feedbackEndpoint: HIL_FEEDBACK_ENDPOINT });
  let chatWs = null;
  let chatWsReady = null;
  let currentChatRequest = null;
  let passiveChatRequest = null;
  let streamTtsDrainPromise = null;
  let streamTtsSentenceId = 0;
  let streamTtsNextPlayId = 0;
  const streamTtsPending = new Map();
  let streamTtsPlaybackPromise = null;
  let streamTtsSessionId = 0;
  const streamTtsSentenceEndRe = /[。！？!?；;]+$/;

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
    emitAppPluginEvent('frontend_command', { cmd, arg });
    if (await runAppPluginCommandHandlers(cmd, arg)) return;
    try{
      if (cmd === 'PLAYMUSIC'){
        if (!arg) return;
        // fetch the file (relative or absolute) and play with mouth-sync
        try{
          const r = await fetch(arg);
          const blob = await r.blob();
          audio.startMouthSyncFromFile(blob);
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
      } else if (cmd === 'TOGGLE_WIDGET_EDIT'){
        if (window.faustAppUI && typeof window.faustAppUI.toggleWidgetEditMode === 'function') {
          window.faustAppUI.toggleWidgetEditMode();
        }
      } else if (cmd === 'SAY'){
        if (!arg) return;
        // use existing synthesizeAndPlay TTS function; prefer UI-selected lang
        const lang = getCurrentTtsLang();
        useVAD = false;
        showResultBubble('ai', arg);
        await audio.synthesizeAndPlay(arg, lang);
      } else if (cmd === 'STOP'){
        // stop audio and optionally stop asr
        try{ audio.stopAudio(); }catch(e){}
        try{ stopBackgroundAudio(); }catch(e){}
      } else if (cmd === 'NIMBLE_SHOW'){
        if (!arg) return;
        let payload = null;
        try{ payload = JSON.parse(arg); }catch(e){ console.warn('Invalid NIMBLE_SHOW payload', e, arg); return; }
        nimbleWin.show(payload);
      } else if (cmd === 'NIMBLE_CLOSE'){
        if (!arg) return;
        let payload = null;
        try{ payload = JSON.parse(arg); }catch(e){ console.warn('Invalid NIMBLE_CLOSE payload', e, arg); return; }
        if (payload && payload.callback_id) {
          nimbleWin.close(payload.callback_id, false);
          hil.close(payload.callback_id);
        }
      } else if (cmd === 'NIMBLE_MESSAGE'){
        if (!arg) return;
        let payload = null;
        try{ payload = JSON.parse(arg); }catch(e){ console.warn('Invalid NIMBLE_MESSAGE payload', e, arg); return; }
        nimbleWin.handleMessage(payload);
      } else if (cmd === 'HIL_APPROVAL'){
        if (!arg) return;
        let payload = null;
        try{ payload = JSON.parse(arg); }catch(e){ console.warn('Invalid HIL_APPROVAL payload', e, arg); return; }
        hil.enqueue({
          request_id: String(payload?.request_id || payload?.ID || '').trim(),
          title: String(payload?.title || payload?.request || '需要人工确认').trim(),
          summary: String(payload?.summary || '').trim(),
          severity: String(payload?.severity || 'warning').trim().toLowerCase(),
        });
      } else if (cmd === 'MD_BLOCK'){
        if (!arg) return;
        let payload = null;
        try{ payload = JSON.parse(arg); }catch(e){ console.warn('Invalid MD_BLOCK payload', e, arg); return; }
        const content = String(payload?.content || '').trim();
        if (!content) return;
        const entry = { type: 'md', text: content };
        if (currentChatRequest && Array.isArray(currentChatRequest.entries)) {
          currentChatRequest.entries.push(entry);
          showResultBubble('ai', currentChatRequest.entries);
        } else {
          const bubbleVisible = asrBubbleEl && asrBubbleEl.style.display !== 'none';
          const base = (bubbleVisible && asrBubbleState.source === 'ai') ? asrBubbleState.entries : [];
          showResultBubble('ai', base.concat([entry]));
        }
      } else if (cmd=="SET_MOTION"){
        if (!arg) return;
        if (modelType === 'vrm' && vrmScene) {
          vrmScene.setExpression(arg);
        } else {
          playMotionByName(arg);
        }
      } else if (cmd === 'LOAD_MODEL' || cmd === 'SET_MODEL_PATH'){
        if (!arg) return;
        if (modelPathInput) modelPathInput.value = arg;
        loadModel(arg);
      } else if (cmd === 'SET_MODEL_SCALE'){
        if (!arg) return;
        if (modelType === 'vrm' && vrmScene) {
          vrmScene.setScale(parseFloat(arg));
        } else {
          setScaleFactor(parseFloat(arg));
        }
      } else if (cmd === 'SET_TEXT_CHAT_Y_FACTOR'){
        const next = Number(arg);
        if (!Number.isFinite(next)) return;
        textChatBarYFactor = Math.min(2.0, Math.max(-1.0, next));
        try { uiWidgetManager.updateWidget('text-chat-bar', { coord: { x: 0.5, y: textChatBarYFactor } }); } catch (e) {}
        updateTextChatBarPosition();
      } else if (cmd === 'SET_QUICK_CONTROLLER_X_OFFSET'){
        const next = Number(arg);
        if (!Number.isFinite(next)) return;
        quickControllerXOffset = Math.max(-400, Math.min(400, next));
        try { uiWidgetManager.updateWidget('quick-controller', { offset: { x: quickControllerXOffset, y: 0 } }); } catch (e) {}
        updateQuickControllerPosition();
      } else if (cmd === 'SET_MODEL_POSITION'){
        if (!arg) return;
        if (modelType === 'vrm' && vrmScene) {
          const [xRaw, yRaw] = arg.split(/\s+/);
          const x = Number(xRaw);
          const y = Number(yRaw);
          if (Number.isFinite(x) && Number.isFinite(y)) {
            vrmScene.setPosition(x, y);
          }
        } else if (currentModel) {
          const [xRaw, yRaw] = arg.split(/\s+/);
          const x = Number(xRaw);
          const y = Number(yRaw);
          if (Number.isFinite(x)) currentModel.x = clampModelRelCoord(x) * app.renderer.width;
          if (Number.isFinite(y)) currentModel.y = clampModelRelCoord(y) * app.renderer.height;
          updateQuickControllerPosition();
          persistModelPositionToBackend(true);
        }
      } else if (cmd === 'START_ASR'){
        startRecording();
      } else if (cmd === 'STOP_ASR'){
        stopRecording();
      } else if (cmd === 'TOGGLE_ASR'){
        toggleAsr();
      } else if (cmd === 'STOP_AUDIO'){
        interruptAll();
      } else if (cmd === 'INTERRUPT_SPEECH'){
        interruptAll();
      } else if (cmd === 'INTERRUPT_CHAT'){
        interruptAll();
      } else if (cmd === 'FOCUS_TEXT_CHAT'){
        focusTextChatInput();
      } else if (cmd === 'RANDOM_MOTION'){
        if (modelType === 'vrm' && vrmScene) {
          const exps = vrmScene.getAvailableExpressions();
          const picked = exps[Math.floor(Math.random() * exps.length)];
          vrmScene.setExpression(picked);
        } else {
          playRandomMotion();
        }
      } else if (cmd === 'SCALE_UP'){
        nudgeScale(0.05);
      } else if (cmd === 'SCALE_DOWN'){
        nudgeScale(-0.05);
      } else if (cmd === 'VRM_GESTURE'){
        if (modelType !== 'vrm' || !vrmScene) return;
        const parts = arg.split(/\s+/);
        const name = parts[0];
        const duration = parts.length > 1 ? parseFloat(parts[1]) : undefined;
        const autoReset = parts.length > 2 ? parts[2] !== 'false' : undefined;
        vrmScene.executeGesture(name, duration, autoReset);
      } else if (cmd === 'VRM_BONE_ROT'){
        if (modelType !== 'vrm' || !vrmScene) return;
        const parts = arg.split(/\s+/);
        if (parts.length < 3) return;
        vrmScene.rotateBone(parts[0], parts[1], parseFloat(parts[2]));
      } else if (cmd === 'VRM_RESET_POSE'){
        if (modelType !== 'vrm' || !vrmScene) return;
        vrmScene.resetPose();
      } else if (cmd === 'VRM_POSE'){
        if (modelType !== 'vrm' || !vrmScene) return;
        const parts = arg.split(/\s+/);
        const poseName = parts[0];
        if (!poseName) return;
        const transition = parts.length > 1 ? parseFloat(parts[1]) : undefined;
        (async () => {
          try {
            const resp = await fetch('http://127.0.0.1:13900/faust/admin/vrm-poses');
            const data = await resp.json();
            const poses = (data && data.poses) || {};
            const entry = poses[poseName];
            if (!entry) { console.warn('VRM_POSE: preset not found', poseName); return; }
            const pose = entry.pose || {};
            const trans = Number.isFinite(transition) ? transition : (Number(pose.transition) >= 0 ? Number(pose.transition) : 600);
            await vrmScene.applyPoseSnapshot(pose, trans);
          } catch (e) {
            console.warn('VRM_POSE failed:', e);
          }
        })();
      } else if (cmd === 'VRM_LOOKAT'){
        if (modelType !== 'vrm' || !vrmScene) return;
        const parts = arg.split(/\s+/);
        if (parts.length === 1) {
          vrmScene.setLookAtTarget(parts[0]);
        } else if (parts.length >= 3) {
          vrmScene.setLookAtTarget(parseFloat(parts[0]), parseFloat(parts[1]), parseFloat(parts[2]));
        }
      } else if (cmd === 'RELOAD_PLUGIN_ASSETS'){
        //window.location.reload();
        window.api.recreateFrontendWindow();
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
    streamTtsSessionId += 1;
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

  function openChatWs(){
    if (chatWs && (chatWs.readyState === WebSocket.OPEN || chatWs.readyState === WebSocket.CONNECTING)){
      return chatWsReady || Promise.resolve();
    }
    chatWsReady = new Promise((resolve, reject)=>{
      try{
        chatWs = new WebSocket(CHAT_ENDPOINT);
        chatWs.onopen = ()=>{
          if (chatStatusEl) chatStatusEl.textContent = '已连接';
          resolve();
        };
        chatWs.onerror = (e)=> reject(e);
        chatWs.onmessage = handleChatWsMessage;
        chatWs.onclose = ()=>{
          chatWs = null;
          chatWsReady = null;
          if (chatStatusEl) chatStatusEl.textContent = chatWsPersistent ? '重连中...' : '聊天未连接';
          // 常驻模式下断线自动重连（带退避），保证后端触发器/被动流始终有 WS 可推送
          if (chatWsPersistent){
            setTimeout(()=>{ ensureChatWsPersistent(); }, 2000);
          }
        };
      }catch(e){ reject(e); }
    });
    return chatWsReady;
  }

  let chatWsPersistent = false;
  function ensureChatWsPersistent(){
    // 保持 /faust/chat WS 常驻连接：后端触发器唤醒（Nimble 事件、Public API、
    // 定时任务等）需要靠这个连接向前端推送 start/delta/done 流。
    chatWsPersistent = true;
    // 连接失败/断开由 onclose 统一调度重连（含退避），这里不重复调度
    openChatWs().catch((e)=>{
      console.warn('常驻 chat WS 连接失败（onclose 将重试）', e);
    });
  }

  async function requestTtsBlob(text, lang){
    if (!text || !text.trim()) return null;
    const payload = { text, text_language: lang || getCurrentTtsLang(), lang: lang || getCurrentTtsLang() };
    const r = await fetch(TTS_ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!r.ok){
      const txt = await r.text();
      throw new Error(`TTS服务错误: ${r.status} ${txt}`);
    }
    const contentType = r.headers.get('content-type') || 'audio/wav';
    const ab = await r.arrayBuffer();
    return new Blob([ab], { type: contentType });
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
          // 动作穿插在 TTS 中：播到这一句时触发该句挂载的情绪动作
          for (const m of (item.motions || [])) triggerModelMotion(m);
          await audio.playOrdered(item.blob);
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

  async function enqueueStreamTtsSentence(sentence, lang, motions){
    sentence = normalizeTtsText(sentence).trim();
    if (!sentence) return;
    const sessionId = streamTtsSessionId;
    const id = streamTtsSentenceId++;
    streamTtsPending.set(id, { status: 'pending', text: sentence, blob: null, motions: motions || [] });
    void flushStreamTtsQueue();
    try{
      const blob = await requestTtsBlob(sentence, lang);
      if (sessionId !== streamTtsSessionId) return;
      if (!blob){
        streamTtsPending.set(id, { status: 'failed', text: sentence, blob: null, motions: motions || [] });
        await flushStreamTtsQueue();
        return;
      }
      streamTtsPending.set(id, { status: 'ready', blob, text: sentence, motions: motions || [] });
      await flushStreamTtsQueue();
    }catch(e){
      if (sessionId !== streamTtsSessionId) return;
      console.warn('stream TTS sentence failed', sentence, e);
      streamTtsPending.set(id, { status: 'failed', text: sentence, blob: null, motions: motions || [] });
      await flushStreamTtsQueue();
    }
  }

  async function handleChatWsMessage(ev){
    let raw = ev.data;
    if (raw && typeof raw !== 'string') {
      raw = await decodeWsPayload(raw);
    }
    console.info(":Chat Websocket:",raw)
    let msg = null;
    try{ msg = JSON.parse(raw); }catch(e){ msg = { type: 'error', error: String(e) }; }
    if (!msg) return;
    emitAppPluginEvent('chat_message', msg);
    const agentId = msg.agent_id;
    // step 1: subagents 概况 → 更新摘要栏
    if (msg.agent_id === 'subagents') {
      if (msg.type === 'subagents_summary') {
        console.debug(' MSG ==> Subagents summary received');
        setSubagentStatuses(Array.isArray(msg.items) ? msg.items : []);
      }
      return;
    }

    // step 2: subagent 流式事件 → 暂存到缓存，不进入主气泡
    if (agentId !== 'main') {
      if (agentId && agentId.startsWith('subagent-')) {
        console.debug(' MSG ==> Subagent event received');
        const name = agentId.slice('subagent-'.length);
        if (!subagentEventCache[name]) subagentEventCache[name] = [];
        const cached = subagentEventCache[name];
        const last = cached[cached.length - 1];
        if ((msg.type === 'reasoning_delta' || msg.type === 'delta') && last && last.type === msg.type) {
          last.content = (last.content || '') + (msg.content || '');
          last.ts = msg.ts || Date.now();
        } else {
          cached.push({ ...msg, ts: msg.ts || Date.now() });
        }
        if (cached.length > 500) cached.splice(0, cached.length - 500);
        if (selectedSubagentName === name) {
          renderSubagentPanelFromCache(name);
        }
      }
      return;
    }

    // 后端主动推送的流（触发器唤醒、Nimble 消息等）没有对应的主动请求。
    // 此时为流式消息创建一个临时被动会话，复用同一套累积/气泡/TTS 逻辑，
    // 否则 start/delta/done 会被直接丢弃，既不显示也不发声。
    if (!currentChatRequest) {
      if (msg.type === 'start' || msg.type === 'delta' || msg.type === 'reasoning_delta'
          || msg.type === 'tool_start' || msg.type === 'tool_result' || msg.type === 'done'
          || msg.type === 'interrupted') {
        // 仅当被动会话不存在时才创建；流进行中 currentChatRequest 仍为 null，
        // 若每次消息都重建会清空已累积的 replyText/pendingBuffer/entries，
        // 导致气泡只显示当前单个 chunk 且不断变化
        if (!passiveChatRequest) {
          passiveChatRequest = {
            passive: true,
            replyText: '',
            pendingBuffer: '',
            motionTokenBuffer: '',
            pendingMotions: [],
            visibleLen: 0,
            entries: [],
          };
        }
      } else {
        return;
      }
    }
    const req = currentChatRequest || passiveChatRequest;

    if (msg.type === 'start'){
      // 收到新消息：立刻停止上一条消息的 TTS（清空待播队列并停掉正在播放的音频）
      interruptPlayback();
      req.replyText = '';
      req.pendingBuffer = '';
      req.motionTokenBuffer = '';
      req.pendingMotions = [];
      req.visibleLen = 0;
      req.entries = [];
      if (chatStatusEl) chatStatusEl.textContent = '聊天流式响应中...';
      // 被动流（后端主动推送）不代表用户在等待回复，不占用 processing 标志，
      // 避免影响用户主动发消息时的自动中断逻辑
      if (!req.passive) agentIsProcessing = true;
      return;
    }

    if (msg.type === 'tool_start'){
      const toolName = String(msg.tool_name || '未知工具');
      if (!req.entries) req.entries = [];
      req.entries.push({
        type: 'tool',
        callId: String(msg.call_id || ''),
        toolName,
        args: msg.args || {},
        output: '',
        done: false,
        expanded: false,
      });
      showResultBubble('ai', req.entries);
      return;
    }

    if (msg.type === 'tool_result'){
      if (!req.entries) req.entries = [];
      const callId = String(msg.call_id || '');
      let target = null;
      if (callId) {
        for (let i = req.entries.length - 1; i >= 0; i -= 1){
          const item = req.entries[i];
          if (item && item.type === 'tool' && String(item.callId || '') === callId) {
            target = item;
            break;
          }
        }
      }
      if (!target) {
        for (let i = req.entries.length - 1; i >= 0; i -= 1){
          const item = req.entries[i];
          if (item && item.type === 'tool' && String(item.toolName || '') === String(msg.tool_name || '') && !item.done) {
            target = item;
            break;
          }
        }
      }
      if (!target) {
        target = {
          type: 'tool',
          callId,
          toolName: String(msg.tool_name || '未知工具'),
          args: {},
          output: '',
          done: false,
          expanded: false,
        };
        req.entries.push(target);
      }
      target.output = String(msg.output || '');
      target.done = true;
      showResultBubble('ai', req.entries);
      return;
    }

    if (msg.type === 'reasoning_delta'){
      if (!req.entries) req.entries = [];
      const lastEntry = req.entries[req.entries.length - 1];
      if (lastEntry && lastEntry.type === 'reasoning') {
        lastEntry.text = String(lastEntry.text || '') + (msg.content || '');
        // Preserve expanded state from asrBubbleState
        if (Array.isArray(asrBubbleState.entries)) {
          const idx = req.entries.indexOf(lastEntry);
          if (idx >= 0 && asrBubbleState.entries[idx]) {
            lastEntry.expanded = !!asrBubbleState.entries[idx].expanded;
          }
        }
      } else {
        req.entries.push({ type: 'reasoning', text: msg.content || '', expanded: false });
      }
      showResultBubble('ai', req.entries);
      return;
    }

    if (msg.type === 'delta'){
      const chunk = normalizeTtsText(msg.content || '');
      const visibleChunk = consumeMotionTokens(req, chunk);
      if (!visibleChunk) {
        return;
      }
      req.replyText += visibleChunk;
      req.pendingBuffer += visibleChunk;
      if (!req.entries) req.entries = [];
      const lastEntry = req.entries[req.entries.length - 1];
      if (lastEntry && lastEntry.type === 'text') {
        lastEntry.text = String(lastEntry.text || '') + visibleChunk;
      } else {
        req.entries.push({ type: 'text', text: visibleChunk });
      }
      showResultBubble('ai', req.entries);
      const beforeSplitLen = req.pendingBuffer.length;
      const split = extractCompletedSentences(req.pendingBuffer);
      req.pendingBuffer = split.rest;
//      console.log("收到增量回复，当前累计文本：", req.replyText);
      // pendingBuffer 起点的全局可见文本偏移：已处理总长 - 本次切分前残余长度
      const baseOffset = (req.visibleLen || 0) - beforeSplitLen;
      for (const sentence of split.completed){
        const motions = takeMotionsForSentence(req, baseOffset + sentence.start, baseOffset + sentence.end);
        enqueueStreamTtsSentence(sentence.text, getCurrentTtsLang(), motions);
      }
      return;
    }

    if (msg.type === 'done'){
      const request = currentChatRequest || passiveChatRequest;
      let reply = stripMotionTokens(request.replyText || '');
      request.replyText = reply;
      request.motionTokenBuffer = '';

      // If there were no delta messages (entries empty), chunk the final reply
      // into sentences, display them and enqueue TTS for each chunk.
      if ((!request.entries || request.entries.length === 0) && reply && reply.trim()){
        try{
          const split = extractCompletedSentences(reply);
          request.entries = [];
          for (const sentence of split.completed){
            const visible = stripMotionTokens(sentence.text);
            if (!visible) continue;
            request.entries.push({ type: 'text', text: visible });
            // show bubble immediately
            showResultBubble('ai', request.entries);
            // fire-and-forget TTS so UI updates are immediate
            enqueueStreamTtsSentence(visible, getCurrentTtsLang(), takeMotionsForSentence(request, sentence.start, sentence.end)).catch((e)=>{ console.warn('enqueue TTS failed', e); });
          }
          if (split.rest && split.rest.trim()){
            const visible = stripMotionTokens(split.rest);
            if (visible){
              request.entries.push({ type: 'text', text: visible });
              showResultBubble('ai', request.entries);
              enqueueStreamTtsSentence(visible, getCurrentTtsLang(), takeMotionsFrom(request, split.completed.length ? split.completed[split.completed.length - 1].end : 0)).catch((e)=>{ console.warn('enqueue TTS failed', e); });
            }
          }
        }catch(e){ console.warn('chunking done reply failed', e); }
      } else {
        if (request.pendingBuffer && request.pendingBuffer.trim()){
          // 尾部句子无标点结束：分配偏移在 pendingBuffer 起点之后的所有剩余动作
          const baseOffset = (request.visibleLen || 0) - request.pendingBuffer.length;
          await enqueueStreamTtsSentence(request.pendingBuffer.trim(), getCurrentTtsLang(), takeMotionsFrom(request, baseOffset));
        }
      }

      // 兜底：回复末尾仍未分配的动作（无对应句子的 token）立即触发
      for (const item of (request.pendingMotions || [])) triggerModelMotion(item.motion);
      request.pendingMotions = [];
      request.pendingBuffer = '';
      if (!request.passive) agentIsProcessing = false;
      showResultBubble('ai', request.entries);
      if (request.passive){
        // 后端主动推送的流（触发器/Nimble）：展示完成后仅清被动会话，
        // 不触碰主动聊天的 currentChatRequest，也不 resolve。
        if (chatStatusEl) chatStatusEl.textContent = '就绪';
        passiveChatRequest = null;
      } else {
        if (chatStatusEl) chatStatusEl.textContent = '聊天完成';
        if (textChatStatus) textChatStatus.textContent = '文字已发送';
        currentChatRequest = null;
        request.resolve(reply);
        if (request.resumeAfter){
          waitForStreamTtsDrain()
            .then(()=>{ resumeRecording(); })
            .catch((e)=>{ console.warn('stream TTS drain failed', e); resumeRecording(); });
        }
      }
      return;
    }

    if (msg.type === 'interrupted'){
      if (chatStatusEl) chatStatusEl.textContent = '已中断';
      if (textChatStatus) textChatStatus.textContent = '已中断';
      const reqI = currentChatRequest || passiveChatRequest;
      if (reqI && !reqI.passive) agentIsProcessing = false;
      if (reqI && reqI.resumeAfter){
        resumeRecording();
        reqI.resumeAfter = false;
      }
      if (reqI && reqI.passive){
        passiveChatRequest = null;
      } else if (currentChatRequest){
        currentChatRequest.reject(new Error('Stream interrupted'));
        currentChatRequest = null;
      }
      return;
    }

    if (msg.type === 'interrupt_ack'){
      if (interruptAckResolver){
        interruptAckResolver();
      }
      return;
    }

    if (msg.type === 'error'){
      if (chatStatusEl) chatStatusEl.textContent = '聊天错误';
      if (textChatStatus) textChatStatus.textContent = '聊天错误';
      showResultBubble('error', msg.error || '未知聊天错误');
      const reqE = currentChatRequest || passiveChatRequest;
      if (reqE && reqE.resumeAfter){
        resumeRecording();
      }
      if (reqE && reqE.passive){
        passiveChatRequest = null;
      } else if (currentChatRequest){
        currentChatRequest.reject(new Error(msg.error || '未知聊天错误'));
        currentChatRequest = null;
      }
    }
  }

  async function sendToChat(text){
    if (!text) return;
    if (pluginChatHold) {
      heldChatQueue.push(text);
      if (textChatStatus) textChatStatus.textContent = '演唱中，消息已排队';
      return '';
    }
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
          motionTokenBuffer: '',
          pendingMotions: [],
          visibleLen: 0,
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
    if (text === '/config') {
      textChatInput.value = '';
      if (textChatStatus) textChatStatus.textContent = '正在打开配置中心';
      try {
        showResultBubble('user', text);
        if (window.api && window.api.openConfigWindow) {
          await window.api.openConfigWindow();
          showResultBubble('ai', '已打开配置中心');
        } else {
          showResultBubble('error', '当前环境不支持打开配置中心');
        }
      } finally {
        if (textChatStatus) textChatStatus.textContent = '文字待命';
      }
      return;
    }
    // Auto-interrupt if agent is currently processing
    if (agentIsProcessing) {
      await interruptAgentAndWait();
    }
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

  function handleResultBubbleToggle(ev){
    const details = ev.target;
    if (!details || !details.classList) return;
    // Tool call details
    if (details.dataset && details.dataset.callId) {
      const callId = String(details.dataset.callId || '');
      if (!callId || !Array.isArray(asrBubbleState.entries)) return;
      for (const entry of asrBubbleState.entries){
        if (entry && entry.type === 'tool' && String(entry.callId || '') === callId) {
          entry.expanded = details.open;
          break;
        }
      }
      return;
    }
    // Reasoning card details
    if (details.dataset && details.dataset.r !== undefined) {
      const rIdx = parseInt(details.dataset.r, 10);
      if (!isNaN(rIdx) && Array.isArray(asrBubbleState.entries)) {
        let count = -1;
        for (const entry of asrBubbleState.entries) {
          if (entry && entry.type === 'reasoning') {
            count++;
            if (count === rIdx) {
              entry.expanded = details.open;
              break;
            }
          }
        }
      }
      return;
    }
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

  function hideResultBubble(){
    if (!asrBubbleEl) return;
    asrBubbleEl.style.display = 'none';
    if (subagentSummaryEl) subagentSummaryEl.style.display = 'none';
    asrBubbleInitialized = false;
  }

  // AsrBubble 用户可调属性（布景台可编辑，持久化到 /faust/ui-setting）
  function getAsrBubbleProps(){
    const widget = uiWidgetManager.getWidget('asr-bubble');
    const p = (widget && widget.props) || {};
    return {
      fontSize: Number(p.fontSize) > 0 ? Number(p.fontSize) : 20,
      textColor: String(p.textColor || '#000000'),
      whiteBackground: p.whiteBackground !== false,
      aspectRatio: String(p.aspectRatio || '').trim(),
      showReasoning: p.showReasoning !== false,
      showTools: p.showTools !== false,
      showSubagents: p.showSubagents !== false,
    };
  }

  // 把 AsrBubble 属性应用到元素（字体大小 / 白色背景 / 长宽比）
  function applyAsrBubbleProps(){
    if (!asrBubbleEl || !asrTextEl) return;
    const props = getAsrBubbleProps();
    asrTextEl.style.fontSize = props.fontSize + 'px';
    asrTextEl.style.color = props.textColor;
    asrBubbleEl.classList.toggle('asr-bubble-no-bg', !props.whiteBackground);
    // 长宽比：CSS aspect-ratio 对由内容撑开的 flex 容器不生效，
    // 改为按固定宽度(350px)显式计算高度
    const parts = String(props.aspectRatio || '').split('/').map((s) => parseFloat(s.trim()));
    if (parts.length === 2 && parts[0] > 0 && parts[1] > 0) {
      const w = asrBubbleEl.offsetWidth || 350;
      asrBubbleEl.style.height = Math.round(w * parts[1] / parts[0]) + 'px';
      asrBubbleEl.style.aspectRatio = 'auto';
    } else {
      asrBubbleEl.style.height = '';
      asrBubbleEl.style.aspectRatio = '';
    }
  }

  function showResultBubble(source, entries){
    if (!asrTextEl || !asrBubbleEl) return;
    const widget = uiWidgetManager.getWidget('asr-bubble');
    if (widget && widget.hidden && !uiWidgetManager.isEditMode()) return;
    asrBubbleSource = source || 'ai';
    asrBubbleEl.dataset.source = asrBubbleSource;
    const normalizedEntries = Array.isArray(entries)
      ? entries
      : (String(entries || '').trim() ? [{ type: 'text', text: String(entries || '') }] : []);
    asrBubbleState = {
      source: asrBubbleSource,
      entries: cloneBubbleEntries(normalizedEntries),
    };
    // 按用户属性过滤渲染内容（推理 / 工具调用）
    const props = getAsrBubbleProps();
    let renderEntries = asrBubbleState.entries;
    if (props.showReasoning === false) renderEntries = renderEntries.filter((e) => e.type !== 'reasoning');
    if (props.showTools === false) renderEntries = renderEntries.filter((e) => e.type !== 'tool');
    const html = renderResultBubbleHtml(asrBubbleSource, renderEntries);
    rememberAsrScrollIntent();
    asrBubbleEl.style.display = html ? 'flex' : 'none';
    asrTextEl.innerHTML = html;
    if (html) {
      try {
        hydrateMermaidBlocks(asrTextEl);
      } catch (e) {
        console.warn('[md-block] hydrate failed（不影响显示）', e);
      }
      applyAsrBubbleProps();
      updateAsrTextPosition(true);
      scrollAsrTextToBottom(true);
    }
  }

  let mermaidInitialized = false;
  let mermaidSeq = 0;

  // 把备份的 <pre><code> 元素重新插回 DOM（mermaid 渲染失败/节点脱离时恢复原文）
  function restoreMermaidCodeBlock(holder, backupHtml){
    if (!holder || !backupHtml) return;
    try {
      const tmp = document.createElement('div');
      tmp.innerHTML = backupHtml;
      const restored = tmp.firstElementChild;
      if (restored) holder.replaceWith(restored);
    } catch (e) {
      console.warn('[md-block] restore code block failed', e);
    }
  }

  function hydrateMermaidBlocks(root){
    const fm = window.FaustMarkdown;
    if (!root || !fm || !fm.mermaid) return;
    const codes = root.querySelectorAll('.md-block code.language-mermaid');
    if (!codes.length) return;
    if (!mermaidInitialized) {
      fm.mermaid.initialize({ startOnLoad: false, theme: 'neutral' });
      mermaidInitialized = true;
    }
    const nodes = [];
    const backups = [];
    for (const code of codes) {
      const holder = document.createElement('div');
      holder.className = 'mermaid';
      holder.id = `md-mermaid-${++mermaidSeq}`;
      holder.textContent = code.textContent || '';
      const pre = code.closest('pre') || code;
      const backupHtml = pre.outerHTML;
      pre.replaceWith(holder);
      nodes.push(holder);
      backups.push({ holder, backupHtml });
    }
    fm.mermaid.run({ nodes })
      .then(() => {
        // 渲染完成：若节点已脱离文档（气泡被后续消息覆盖），SVG 不可见，
        // 把原代码块恢复回当前 DOM，保证内容可见
        for (const { holder, backupHtml } of backups) {
          if (!holder.isConnected) restoreMermaidCodeBlock(holder, backupHtml);
        }
      })
      .catch((err) => {
        console.warn('[md-block] mermaid render failed:', err);
        // 渲染失败：恢复所有占位节点为原代码块（内容不丢失）
        for (const { holder, backupHtml } of backups) {
          if (holder.isConnected) restoreMermaidCodeBlock(holder, backupHtml);
        }
      });
  }

  function formatSubagentEventSummary(item){
    if (!item) return '';
    return String(item.last_event_summary || item.last_error || '').trim();
  }

  function setSubagentStatuses(items){
    subagentStatuses = Array.isArray(items) ? items.map((item)=> ({ ...item })) : [];
    renderSubagentSummary();
    if (selectedSubagentName) {
      const next = subagentStatuses.find((item)=> String(item.name || '') === selectedSubagentName);
      if (next) {
        // WS 推送的 subagents_summary 是轻量状态（不含 recent_events），
        // 直接用 next 渲染会把事件列表清空为"暂无事件"。
        // 优先合并事件缓存，保留已显示的流式事件。
        renderSubagentPanel({
          ...next,
          recent_events: subagentEventCache[selectedSubagentName] || next.recent_events || [],
        });
      }
    }
  }

  function renderSubagentSummary(){
    if (!subagentSummaryEl) return;
    // 用户可在布景台关闭 Subagents Summary 组件
    if (getAsrBubbleProps().showSubagents === false) {
      subagentSummaryEl.style.display = 'none';
      subagentSummaryEl.innerHTML = '';
      return;
    }
    const STATUS_CN= {
      idle: '空闲', pending: '排队中', running: '运行中',
      stopping: '停止中', stopped: '已停止', error: '错误',
    };
    const visibleItems = Array.isArray(subagentStatuses) ? subagentStatuses : [];
    if (!visibleItems.length){
      subagentSummaryEl.style.display = 'none';
      subagentSummaryEl.innerHTML = '';
      // 如果 asrText 也没有内容，隐藏整个气泡
      if (asrBubbleEl && asrBubbleEl.style.display !== 'none' && asrTextEl && !asrTextEl.textContent.trim()) {
        asrBubbleEl.style.display = 'none';
      }
      return;
    }
    subagentSummaryEl.style.display = 'flex';
    if (asrBubbleEl && asrBubbleEl.style.display === 'none'){
      asrBubbleEl.style.display = 'flex';
    }
    subagentSummaryEl.innerHTML = visibleItems.map((item)=>{
      const name = escapeHtml(String(item.name || 'Unnamed'));
      const rawStatus = String(item.status || 'unknown').trim().toLowerCase();
      const status = escapeHtml(STATUS_CN[rawStatus] || rawStatus);
      const title = escapeHtml(formatSubagentEventSummary(item));
      return `<div class="subagent-summary-item" data-subagent-name="${name}" title="${title}"><span class="subagent-summary-name">${name}:${status}</span></div>`;
    }).join('');
    console.log('[SubagentSummary] rendered', visibleItems.length, 'items, html:', subagentSummaryEl.innerHTML.substring(0, 200));
    console.log('[SubagentSummary] display:', subagentSummaryEl.style.display, 'parent display:', asrBubbleEl ? asrBubbleEl.style.display : '?', 'data-source:', asrBubbleEl ? asrBubbleEl.dataset.source : '?');
  }

  function renderSubagentPanelFromCache(name){
    const status = subagentStatuses.find(s => String(s.name || '') === name);
    const events = subagentEventCache[name] || [];
    if (status) {
      renderSubagentPanel({ ...status, recent_events: events });
    } else {
      // subagent 已不在状态列表（如已移除/尚未推送 summary），
      // 仍用缓存事件渲染，避免事件被静默丢弃。
      renderSubagentPanel({ name, status: 'unknown', recent_events: events });
    }
  }

  function normalizeSubagentPanelEvents(events){
    const source = Array.isArray(events) ? events : [];
    const normalized = [];
    for (const event of source){
      if (!event || typeof event !== 'object') continue;
      const eventType = String(event.type || '').trim();
      if (!eventType) continue;
      const last = normalized[normalized.length - 1];
      if ((eventType === 'reasoning_delta' || eventType === 'delta') && last && last.type === eventType) {
        last.content = String(last.content || '') + String(event.content || '');
        last.ts = event.ts;
        continue;
      }
      normalized.push({ ...event });
    }
    return normalized;
  }

  function formatSubagentPanelEvent(event){
    const eventType = String(event.type || '').trim();
    if (eventType === 'reasoning_delta') return { label: '思考', body: String(event.content || '') };
    if (eventType === 'delta') return { label: '输出', body: String(event.content || '') };
    if (eventType === 'tool_start') return { label: '调用工具', body: String(event.tool_name || '') };
    if (eventType === 'queued') {
      const content = (((event.message || {}).messages || [])[0] || {}).content || '';
      return { label: '排队中', body: String(content) };
    }
    if (eventType === 'input') {
      const content = (((event.message || {}).messages || [])[0] || {}).content || '';
      return { label: '主Agent消息', body: String(content) };
    }
    if (eventType === 'error') return { label: '错误', body: String(event.content || event.error || '') };
    if (eventType === 'stopping') return { label: '停止中', body: '已发送停止请求' };
    if (eventType === 'stopped') return { label: '已停止', body: 'Subagent 已停止' };
    return { label: eventType || 'event', body: typeof event === 'object' ? JSON.stringify(event, null, 2) : String(event || '') };
  }

  function renderSubagentPanel(item){
    if (!subagentPanel || !subagentPanelBody || !item) return;
    selectedSubagentName = String(item.name || '');
    if (subagentPanelTitle) subagentPanelTitle.textContent = `Subagent: ${selectedSubagentName}`;
    const events = normalizeSubagentPanelEvents(item.recent_events);
    subagentPanelBody.innerHTML = [
      '<div class="subagent-panel-meta">',
      `<div class="subagent-panel-meta-key">状态</div><div class="subagent-panel-meta-value">${escapeHtml(String(item.status || 'unknown'))}</div>`,
      `<div class="subagent-panel-meta-key">工具组</div><div class="subagent-panel-meta-value">${escapeHtml((item.toolsets || []).join(', ') || '(none)')}</div>`,
      `<div class="subagent-panel-meta-key">Prompt</div><div class="subagent-panel-meta-value">${escapeHtml(String(item.system_prompt_summary || ''))}</div>`,
      `<div class="subagent-panel-meta-key">错误</div><div class="subagent-panel-meta-value">${escapeHtml(String(item.last_error || ''))}</div>`,
      '</div>',
      '<div class="subagent-panel-events">',
      (events.length ? events.map((event)=>{
        const formatted = formatSubagentPanelEvent(event);
        return `<div class="subagent-panel-event"><div class="subagent-panel-event-type">${escapeHtml(String(formatted.label || 'event'))}</div><div class="subagent-panel-event-body">${escapeHtml(String(formatted.body || ''))}</div></div>`;
      }).join('') : '<div class="subagent-panel-event"><div class="subagent-panel-event-body">暂无事件</div></div>'),
      '</div>',
    ].join('');
    subagentPanel.style.display = 'flex';
    if (clickThroughController) clickThroughController.forceInteractive();
  }

  function hideSubagentPanel(){
    if (!subagentPanel) return;
    subagentPanel.style.display = 'none';
  }

  async function openSubagentPanelByName(name){
    if (subagentEventCache[name] && subagentEventCache[name].length > 0) {
      renderSubagentPanelFromCache(name);
      return;
    }
    try {
      const r = await fetch(SUBAGENT_STATUS_ENDPOINT);
      const j = await r.json();
      console.info(":subagent status:",j)
      const items = Array.isArray(j.items) ? j.items : [];
      const target = items.find(item => String(item.name || '') === name);
      if (target) {
        subagentEventCache[name] = Array.isArray(target.recent_events) ? target.recent_events.map(e => ({...e})) : [];
        renderSubagentPanel(target);
      }
    } catch(e) {
      console.warn('openSubagentPanelByName failed', e);
    }
  }

  async function stopSelectedSubagent(){
    if (!selectedSubagentName) return;
    const r = await fetch(`${SUBAGENT_DELETE_ENDPOINT}/${encodeURIComponent(selectedSubagentName)}`, { method: 'DELETE' });
    if (!r.ok){
      const txt = await r.text();
      throw new Error(txt || `HTTP ${r.status}`);
    }
    await refreshSubagentStatuses();
    hideSubagentPanel();
  }

  async function refreshSubagentStatuses(){
    try{
      const r = await fetch(SUBAGENT_STATUS_ENDPOINT);
      const j = await r.json().catch(()=>({}));
      console.log(":subagent status:",j)
      setSubagentStatuses(Array.isArray(j.items) ? j.items : []);
    }catch(e){
      console.warn('refreshSubagentStatuses failed', e);
    }
  }

  function initSubagentPanelDrag(){
    if (!subagentPanel || !subagentPanelHeader) return;
    let draggingPanel = false;
    let offsetX = 0;
    let offsetY = 0;

    const onMove = (ev)=>{
      if (!draggingPanel) return;
      subagentPanel.style.left = `${Math.max(8, ev.clientX - offsetX)}px`;
      subagentPanel.style.top = `${Math.max(8, ev.clientY - offsetY)}px`;
      subagentPanel.style.right = 'auto';
    };
    const onUp = ()=>{
      draggingPanel = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    subagentPanelHeader.addEventListener('mousedown', (ev)=>{
      if (ev.target && ev.target.closest('button')) return;
      const rect = subagentPanel.getBoundingClientRect();
      draggingPanel = true;
      offsetX = ev.clientX - rect.left;
      offsetY = ev.clientY - rect.top;
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
    });
  }

  function showAsrText(text){
    if (!asrTextEl || !asrBubbleEl) return;
    rememberAsrScrollIntent();
    asrBubbleEl.style.display = text ? 'flex' : 'none';
    asrBubbleEl.dataset.source = 'ai';
    asrBubbleSource = 'ai';
    asrTextEl.textContent = formatResultBubbleText('ai', text || '');
    updateAsrTextPosition(true);
    scrollAsrTextToBottom(true);
  }

  function updateAsrTextPosition(forceSnap = false){
    if (!asrBubbleEl || !asrTextEl) return;
    applyAsrBubbleProps();
    const widget = uiWidgetManager.getWidget('asr-bubble') || { coord: { x: 0.5, y: 0 }, offset: { x: 0, y: -108 }, scale: 1 };
    const editMode = uiWidgetManager.isEditMode();
    asrBubbleEl.classList.toggle('ui-widget-hidden-preview', !!(editMode && widget.hidden));
    if (widget.hidden && !editMode) {
      asrBubbleEl.style.display = 'none';
      return;
    }
    if (modelType === 'vrm' && vrmScene) {
      try{
        const b = vrmScene.getBounds();
        const clientX = b.x + b.width * widget.coord.x;
        const clientY = b.y + b.height * widget.coord.y;
        const bubbleWidth = Math.max(uiWidgetManager.getWidgetSize('asr-bubble', { width: 220, height: 120 }).width, 220);
        asrBubbleTargetX = clientX - bubbleWidth / 2;
        asrBubbleTargetY = clientY + widget.offset.y;
        if (!asrBubbleInitialized || forceSnap){
          asrBubbleCurrentX = asrBubbleTargetX;
          asrBubbleCurrentY = asrBubbleTargetY;
          asrBubbleInitialized = true;
        } else {
          const smooth = 0.2;
          asrBubbleCurrentX += (asrBubbleTargetX - asrBubbleCurrentX) * smooth;
          asrBubbleCurrentY += (asrBubbleTargetY - asrBubbleCurrentY) * smooth;
        }
        asrBubbleEl.style.left = Math.round(asrBubbleCurrentX) + 'px';
        asrBubbleEl.style.top = Math.round(asrBubbleCurrentY) + 'px';
        asrBubbleEl.style.transform = `translate3d(0,0,0) scale(${widget.scale || 1})`;
        hil.updatePosition();
      }catch(e){/*ignore*/}
      return;
    }
    if (!currentModel || !app || !app.renderer) return;
    try{
      const b = currentModel.getBounds();
      const anchor = live2DToClient(b.x + b.width * widget.coord.x, b.y + b.height * widget.coord.y);
      if (!anchor) return;
      const clientX = anchor.x;
      const clientY = anchor.y;
      const bubbleWidth = Math.max(uiWidgetManager.getWidgetSize('asr-bubble', { width: 220, height: 120 }).width, 220);
      asrBubbleTargetX = clientX - bubbleWidth / 2;
      asrBubbleTargetY = clientY + widget.offset.y;
      if (!asrBubbleInitialized || forceSnap){
        asrBubbleCurrentX = asrBubbleTargetX;
        asrBubbleCurrentY = asrBubbleTargetY;
        asrBubbleInitialized = true;
      } else {
        const smooth = 0.2;
        asrBubbleCurrentX += (asrBubbleTargetX - asrBubbleCurrentX) * smooth;
        asrBubbleCurrentY += (asrBubbleTargetY - asrBubbleCurrentY) * smooth;
      }
      asrBubbleEl.style.left = Math.round(asrBubbleCurrentX) + 'px';
      asrBubbleEl.style.top = Math.round(asrBubbleCurrentY) + 'px';
      asrBubbleEl.style.transform = `translate3d(0,0,0) scale(${widget.scale || 1})`;
      hil.updatePosition();
    }catch(e){/*ignore*/}
  }

  function updateTextChatBarPosition(){
    const textChatBar = document.getElementById('textChatBar');
    if (!textChatBar) return;
    const widget = uiWidgetManager.getWidget('text-chat-bar') || { coord: { x: 0.5, y: textChatBarYFactor }, offset: { x: 0, y: 0 }, scale: 1 };
    const editMode = uiWidgetManager.isEditMode();
    textChatBar.classList.toggle('ui-widget-hidden-preview', !!(editMode && widget.hidden));
    if (widget.hidden && !editMode) {
      textChatBar.style.display = 'none';
      return;
    }
    textChatBar.style.display = 'flex';
    if (modelType === 'vrm' && vrmScene) {
      try{
        const b = vrmScene.getBounds();
        const clientX = b.x + b.width * widget.coord.x + widget.offset.x;
        const waistY = b.y + b.height * widget.coord.y + widget.offset.y;
        const size = uiWidgetManager.getWidgetSize('text-chat-bar', { width: 420, height: 64 });
        const clamped = clampToViewport(clientX, waistY, size.width, size.height, window.innerWidth, window.innerHeight, 12);
        textChatBar.style.left = Math.round(clamped.left) + 'px';
        textChatBar.style.top = Math.round(clamped.top) + 'px';
        textChatBar.style.bottom = 'auto';
        textChatBar.style.transform = `translate(-50%, -50%) scale(${widget.scale || 1})`;
      }catch(e){/*ignore*/}
      return;
    }
    if (!currentModel || !app || !app.renderer) return;
    try{
      const b = currentModel.getBounds();
      const anchor = live2DToClient(b.x + b.width * widget.coord.x, b.y + b.height * widget.coord.y);
      if (!anchor) return;
      const clientX = anchor.x + widget.offset.x;
      const waistY = anchor.y + widget.offset.y;
      const size = uiWidgetManager.getWidgetSize('text-chat-bar', { width: 420, height: 64 });
      const clamped = clampToViewport(clientX, waistY, size.width, size.height, window.innerWidth, window.innerHeight, 12);
      textChatBar.style.left = Math.round(clamped.left) + 'px';
      textChatBar.style.top = Math.round(clamped.top) + 'px';
      textChatBar.style.bottom = 'auto';
      textChatBar.style.transform = `translate(-50%, -50%) scale(${widget.scale || 1})`;
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
      await refreshSpeechRuntimeConfig(true);
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
          try{
            if (useVAD && vadWs && vadWs.readyState === WebSocket.OPEN){
              const start = frame.byteOffset || 0;
              const end = start + (frame.byteLength || frame.length * 4);
              const slice = frame.buffer.slice(start, end);
              vadWs.send(slice);
            }
          }catch(e){
            console.log("fail to send VAD frame:", e);
          }
          // maintain pre-roll ring buffer
          preBufferFrames.push(frame.slice(0));
          if (preBufferFrames.length > preRollFrameLimit) preBufferFrames.shift();
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
      speechFrameCnt = 0;
      try{
        vadWs = new WebSocket(getVadWsUrl());
        vadWs.binaryType = 'arraybuffer';
        vadWs.onopen = ()=>{ asrStatusEl.textContent = '已连接到主后端 VAD'; useVAD=true; console.log('VAD ws opened'); };
        vadWs.onmessage = (ev)=>{
          try{
            const msg = typeof ev.data === 'string' ? JSON.parse(ev.data) : JSON.parse(new TextDecoder().decode(ev.data));
            const p = (typeof msg.probability !== 'undefined') ? (Number(msg.probability) || 0) : (msg.is_speech ? 1 : 0);
            handleSpeechActivity(p > 0.5, p);
          }catch(err){ console.warn('VAD ws message parse err', err); }
        };
        vadWs.onerror = (ev)=>{ console.warn('VAD ws error', ev); useVAD = false; asrStatusEl.textContent = '主后端 VAD 连接错误'; vadWs = null; };
        vadWs.onclose = ()=>{ if (useVAD){ useVAD = false; asrStatusEl.textContent = '主后端 VAD 已断开'; vadWs = null; } };
      }catch(e){ console.warn('open vad ws failed', e); useVAD = false; asrStatusEl.textContent = '无法连接主后端 VAD'; }
    }catch(err){
      console.error('start mic failed', err);
      asrStatusEl.textContent = '麦克风权限或错误';
    }
  }

  function stopMicAsr(){
    if (!asrRunning) return;
    asrRunning = false;
    inSpeech = false;
    speechFrameCnt = 0;
    noVoiceCnt = 0;
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
    preBufferFrames = [];
    leftoverResampled = new Float32Array(0);
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
    // 若 AI 正在流式输出，先自动打断并等待确认，再开始录音，
    // 避免用户说话内容与 AI 回复在 TTS/流式输出上冲突
    if (agentIsProcessing) {
      await interruptAgentAndWait();
    }
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
  if (textChatSendBtn) textChatSendBtn.addEventListener('click', () => { sendTextChatMessage().catch(()=>{}); });
  document.addEventListener('keydown', (e)=>{
    if (e.ctrlKey && e.shiftKey && (e.key === 'T' || e.key === 't')){
      e.preventDefault();
      focusTextChatInput();
    }
  });

  // ── Slash-command autocomplete (extracted to libs/autocomplete.js) ──
  initAutocomplete(textChatInput, sendTextChatMessage);

  // 每帧统一布局：由小组件管理器驱动所有 managed 组件的定位/显隐
  // （内建 quick-controller/text-chat-bar/asr-bubble 通过各自 onLayout 钩子接入）
  uiWidgetManager.startLayoutLoop();

  function showOverlay(msg){
    const o = document.getElementById('overlay');
    if (!o) return;
    o.style.display = 'block';
    o.textContent = msg;
  }

  function clearOverlay(){
    const o = document.getElementById('overlay');
    if (!o) return;
    o.style.display = 'none';
    o.textContent = '';
  }

  let vrmDragCleanup = null;

  async function loadVRMModel(path) {
    const loadRequestId = ++activeModelLoadRequestId;
    showOverlay('加载 VRM 模型: ' + path);
    try {
      const resolvedPath = await resolveFrontendAssetPath(path);
      if (!resolvedPath) throw new Error('模型路径解析失败');
      if (loadRequestId !== activeModelLoadRequestId) throw new Error('stale model load request');
      const { VRMScene } = await getVRMModule();
      if (loadRequestId !== activeModelLoadRequestId) throw new Error('stale model load request');
      if (currentModel && currentModel.parent) {
        currentModel.parent.removeChild(currentModel);
        currentModel = null;
      }
      if (!vrmScene) {
        if (app && app.view) app.view.style.display = 'none';
        vrmScene = new VRMScene();
        vrmScene.init(document.getElementById('app'));
      } else {
        if (app && app.view) app.view.style.display = 'none';
        const vrmCanvas = vrmScene.getCanvas();
        if (vrmCanvas) vrmCanvas.style.display = '';
      }
      await vrmScene.loadVRM(resolvedPath);
      if (loadRequestId !== activeModelLoadRequestId) throw new Error('stale model load request');
      modelType = 'vrm';
      clearOverlay();
      if (modelPathInput) modelPathInput.value = path;

      const configuredScale = runtimeLive2DConfig && runtimeLive2DConfig.LIVE2D_MODEL_SCALE !== undefined && runtimeLive2DConfig.LIVE2D_MODEL_SCALE !== null && runtimeLive2DConfig.LIVE2D_MODEL_SCALE !== ''
        ? Number(runtimeLive2DConfig.LIVE2D_MODEL_SCALE)
        : 0.3;
      vrmScene.setScale(configuredScale);

      // attach drag handlers to VRM canvas
      if (vrmDragCleanup) vrmDragCleanup();
      const canvas = vrmScene.getCanvas();
      if (canvas) {
        let vrmDragButton = 0;
        const onDown = (e) => {
          if (e.button === 1) e.preventDefault();
          vrmDragButton = e.button;
          if (clickThroughController) clickThroughController.forceInteractive();
          setInteractionLock(true);
          dragging = true;
          hoverModel = true;
          vrmScene.pointerDown(e.clientX, e.clientY);
        };
        const onUp = () => {
          const wasRotating = dragging && vrmDragButton === 1;
          dragging = false;
          hoverModel = false;
          vrmScene.pointerUp();
          setInteractionLock(false);
          refreshQuickControllerVisibility();
          if (wasRotating) {
            try {
              const t = vrmScene.getModelTransform();
              fetch('http://127.0.0.1:13900/faust/admin/vrm-config/model-state', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(t),
              }).catch(() => {});
            } catch (err) {}
          }
        };
        const onMove = (e) => {
          if (!dragging) return;
          const editMode = vrmScene.isEditMode ? vrmScene.isEditMode() : false;
          const editDragMode = vrmScene.editDragMode || 'drag';
          if (editMode && vrmDragButton !== 1 && !(e.ctrlKey || e.metaKey)) {
            // 编辑模式下左键按面板模式路由；'drag' 由面板的 IK handler 处理
            if (editDragMode === 'orbit') {
              vrmScene.orbitCamera(e.clientX, e.clientY);
            } else if (editDragMode === 'move') {
              vrmScene.moveModel(e.clientX, e.clientY);
            }
          } else if (vrmDragButton === 1) {
            vrmScene.rotateModel(e.clientX, e.clientY);
          } else if (e.ctrlKey || e.metaKey) {
            vrmScene.orbitCamera(e.clientX, e.clientY);
          } else {
            vrmScene.moveModel(e.clientX, e.clientY);
          }
          updateQuickControllerPosition();
          updateTextChatBarPosition();
          updateAsrTextPosition();
        };
        const onWheel = (e) => {
          e.preventDefault();
          const step = e.deltaY > 0 ? -0.05 : 0.05;
          setScaleFactor(scaleFactor + step);
          refreshQuickControllerVisibility();
        };
        canvas.addEventListener('pointerdown', onDown);
        canvas.addEventListener('pointerup', onUp);
        canvas.addEventListener('pointerupoutside', onUp);
        canvas.addEventListener('pointermove', onMove);
        canvas.addEventListener('wheel', onWheel, { passive: false });
        vrmDragCleanup = () => {
          canvas.removeEventListener('pointerdown', onDown);
          canvas.removeEventListener('pointerup', onUp);
          canvas.removeEventListener('pointerupoutside', onUp);
          canvas.removeEventListener('pointermove', onMove);
          canvas.removeEventListener('wheel', onWheel);
          vrmDragCleanup = null;
        };
      }

      refreshQuickControllerVisibility();

      if (openVRMConfigBtn) openVRMConfigBtn.style.display = '';

      try {
        const vrmResp = await fetch('http://127.0.0.1:13900/faust/admin/vrm-config');
        const vrmData = await vrmResp.json();
        if (vrmData && vrmData.config) {
          vrmScene.setConfig(vrmData.config);
          if (vrmData.config.modelState) {
            vrmScene.setModelTransform(vrmData.config.modelState);
          }
        }
      } catch (e) {
        console.warn('Failed to load VRM config:', e);
      }
    } catch (err) {
      if (String(err && err.message || '') === 'stale model load request') return;
      showResultBubble('error', 'VRM 模型加载失败：' + String(err && err.message ? err.message : err));
      console.error(err);
      if (loadRequestId === activeModelLoadRequestId) showModelLoadFallback();
    }
  }

  function switchToLive2DRenderer() {
    if (vrmDragCleanup) { vrmDragCleanup(); vrmDragCleanup = null; }
    if (vrmScene && vrmScene.isActive) {
      vrmScene.destroy();
      vrmScene = null;
    }
    if (vrmConfigPanel) vrmConfigPanel.style.display = 'none';
    if (openVRMConfigBtn) openVRMConfigBtn.style.display = 'none';
    if (app && app.view) app.view.style.display = '';
    modelType = 'live2d';
  }

  function normalizeImageModelConfig(rawConfig){
    const cfg = rawConfig && typeof rawConfig === 'object' ? rawConfig : {};
    return {
      baseImages: Array.isArray(cfg.baseImages) ? cfg.baseImages.filter(Boolean).map(String) : [],
      emotions: Array.isArray(cfg.emotions) ? cfg.emotions.map((item) => ({
        name: String(item && item.name || '').trim(),
        images: Array.isArray(item && item.images) ? item.images.filter(Boolean).map(String) : [],
      })).filter((item) => item.name) : [],
      tapImages: Array.isArray(cfg.tapImages) ? cfg.tapImages.filter(Boolean).map(String) : [],
      mouthShapes: Array.isArray(cfg.mouthShapes) ? cfg.mouthShapes.map((item) => ({
        path: String(item && item.path || '').trim(),
        openness: Math.max(0, Math.min(1, Number(item && item.openness) || 0)),
      })).filter((item) => item.path) : [],
      scale: Math.max(0.1, Math.min(4, Number(cfg.scale) || 1.0)),
      motionDurationMs: Math.max(200, Number(cfg.motionDurationMs) || 3000),
      tapDurationMs: Math.max(100, Number(cfg.tapDurationMs) || 700),
    };
  }

  async function resolveImageModelConfig(rawConfig){
    const cfg = normalizeImageModelConfig(rawConfig);
    const resolveList = async (items) => {
      const resolved = [];
      for (const item of items) {
        const path = await resolveFrontendAssetPath(item);
        if (path) resolved.push(path);
      }
      return resolved;
    };
    const emotions = [];
    for (const emotion of cfg.emotions) {
      emotions.push({ name: emotion.name, images: await resolveList(emotion.images) });
    }
    const mouthShapes = [];
    for (const shape of cfg.mouthShapes) {
      const path = await resolveFrontendAssetPath(shape.path);
      if (path) mouthShapes.push({ path, openness: shape.openness });
    }
    return {
      baseImages: await resolveList(cfg.baseImages),
      emotions,
      tapImages: await resolveList(cfg.tapImages),
      mouthShapes,
      scale: cfg.scale,
      motionDurationMs: cfg.motionDurationMs,
      tapDurationMs: cfg.tapDurationMs,
    };
  }

  function pickRandomItem(items){
    if (!Array.isArray(items) || !items.length) return '';
    return items[Math.floor(Math.random() * items.length)] || '';
  }

  function createMissingModelTexture(){
    const canvas = document.createElement('canvas');
    canvas.width = 256;
    canvas.height = 256;
    const ctx = canvas.getContext('2d');
    const cell = 128;
    for (let row = 0; row < 2; row++) {
      for (let col = 0; col < 2; col++) {
        ctx.fillStyle = (row + col) % 2 === 0 ? '#f800f8' : '#000000';
        ctx.fillRect(col * cell, row * cell, cell, cell);
      }
    }
    return PIXI.Texture.from(canvas);
  }

  // 模型加载失败时的兜底：显示紫黑错误方块，保持模型锚定的组件正常工作
  function showModelLoadFallback(){
    try {
      if (modelType !== 'live2d') switchToLive2DRenderer();
      if (!window.PIXI || !app || !app.renderer) return;
      if (currentModel && currentModel.parent) app.stage.removeChild(currentModel);
      const sprite = new PIXI.Sprite(createMissingModelTexture());
      currentModel = sprite;
      availableMotions = [];
      currentLipSyncParamIds = [];
      sprite._faustFallback = true;
      sprite.anchor.set(0.5, 1.0);
      sprite.x = app.renderer.width - 200;
      sprite.y = app.renderer.height - 20;
      sprite.interactive = true;
      sprite.buttonMode = true;
      sprite.cursor = 'grab';
      sprite.on('pointerdown', (e) => {
        if (clickThroughController) clickThroughController.forceInteractive();
        setInteractionLock(true);
        dragging = true;
        sprite.cursor = 'grabbing';
        const pos = e.data.global;
        dragOffset.x = pos.x - sprite.x;
        dragOffset.y = pos.y - sprite.y;
      });
      const endDrag = () => {
        dragging = false;
        sprite.cursor = 'grab';
        setInteractionLock(false);
        persistModelPositionToBackend();
      };
      sprite.on('pointerup', endDrag);
      sprite.on('pointerupoutside', endDrag);
      sprite.on('pointermove', (e) => {
        if (!dragging) return;
        const pos = e.data.global;
        let rawX = pos.x - dragOffset.x;
        let rawY = pos.y - dragOffset.y;
        rawX = Math.max(app.renderer.width * 0.1, Math.min(app.renderer.width * 1.0, rawX));
        rawY = Math.max(app.renderer.height * 0.1, Math.min(app.renderer.height * 1.0, rawY));
        sprite.x = rawX;
        sprite.y = rawY;
        updateQuickControllerPosition();
      });
      app.stage.addChild(sprite);
      baseScale = Math.min(app.renderer.width / 1600, app.renderer.height / 900);
      const configuredPos = readConfiguredModelRelPosition();
      if (configuredPos.x !== null) sprite.x = configuredPos.x * app.renderer.width;
      if (configuredPos.y !== null) sprite.y = configuredPos.y * app.renderer.height;
      applyModelScale();
      clearOverlay();
      updateTextChatBarPosition();
      refreshQuickControllerVisibility();
    } catch (e) {
      console.error('showModelLoadFallback failed', e);
    }
  }

  async function loadImageModel(rawConfig){
    if (modelType !== 'images') {
      switchToLive2DRenderer();
      modelType = 'images';
    }
    const loadRequestId = ++activeModelLoadRequestId;
    const resolvedConfig = await resolveImageModelConfig(rawConfig);
    if (loadRequestId !== activeModelLoadRequestId) return;
    const initialPath = pickRandomItem(resolvedConfig.baseImages)
      || pickRandomItem(resolvedConfig.tapImages)
      || (resolvedConfig.mouthShapes[0] && resolvedConfig.mouthShapes[0].path)
      || '';
    if (!initialPath) {
      console.warn('Images 模型未配置图片，使用空白纹理');
    }
    const texture = initialPath ? PIXI.Texture.from(initialPath) : PIXI.Texture.WHITE;
    const sprite = new PIXI.Sprite(texture);
    if (currentModel && currentModel.parent) app.stage.removeChild(currentModel);
    currentModel = sprite;
    runtimeImageModelConfig = resolvedConfig;
    availableMotions = resolvedConfig.emotions.map((item) => item.name);
    currentLipSyncParamIds = [];
    let pointerDownTime = 0;
    sprite.anchor.set(0.5, 1.0);
    sprite.x = app.renderer.width - 200;
    sprite.y = app.renderer.height - 20;
    sprite.interactive = true;
    sprite.buttonMode = true;
    sprite.cursor = 'grab';
    sprite._faustImageModel = {
      config: resolvedConfig,
      emotionUntil: 0,
      tapUntil: 0,
      currentEmotion: '',
      currentEmotionImage: '',
      currentTapImage: '',
      mouthOpen: 0,
      lastTexturePath: initialPath,
      setEmotion(name) {
        const group = this.config.emotions.find((item) => item.name === name && item.images.length);
        if (!group) return false;
        this.currentEmotion = name;
        this.currentEmotionImage = pickRandomItem(group.images);
        this.emotionUntil = Date.now() + this.config.motionDurationMs;
        this.refreshTexture();
        return true;
      },
      triggerTap() {
        if (!this.config.tapImages.length) return;
        this.currentTapImage = pickRandomItem(this.config.tapImages);
        this.tapUntil = Date.now() + this.config.tapDurationMs;
        this.refreshTexture();
      },
      setMouthOpen(value) {
        this.mouthOpen = Math.max(0, Math.min(1, Number(value) || 0));
        this.refreshTexture();
      },
      getCurrentImagePath() {
        const now = Date.now();
        if (this.tapUntil > now && this.currentTapImage) return this.currentTapImage;
        if (this.config.mouthShapes.length && this.mouthOpen > 0.01) {
          let best = this.config.mouthShapes[0];
          let bestDist = Math.abs(best.openness - this.mouthOpen);
          for (const item of this.config.mouthShapes) {
            const dist = Math.abs(item.openness - this.mouthOpen);
            if (dist < bestDist) { best = item; bestDist = dist; }
          }
          if (best && best.path) return best.path;
        }
        if (this.emotionUntil > now && this.currentEmotionImage) return this.currentEmotionImage;
        return pickRandomItem(this.config.baseImages) || this.lastTexturePath || initialPath;
      },
      refreshTexture() {
        const nextPath = this.getCurrentImagePath();
        if (!nextPath) { sprite.texture = PIXI.Texture.WHITE; return; }
        if (!nextPath || nextPath === this.lastTexturePath) return;
        this.lastTexturePath = nextPath;
        sprite.texture = PIXI.Texture.from(nextPath);
      },
    };

    sprite.on('pointerdown', (e) => {
      pointerDownTime = Date.now();
      if (clickThroughController) clickThroughController.forceInteractive();
      setInteractionLock(true);
      dragging = true;
      sprite.cursor = 'grabbing';
      const pos = e.data.global;
      dragOffset.x = pos.x - sprite.x;
      dragOffset.y = pos.y - sprite.y;
      sprite._faustImageModel.triggerTap();
    });
    sprite.on('pointerup', () => {
      if (Date.now() - pointerDownTime < 300 && !dragging) {
        sprite._faustImageModel.triggerTap();
      }
      dragging = false;
      sprite.cursor = 'grab';
      setInteractionLock(false);
      persistModelPositionToBackend();
    });
    sprite.on('pointerupoutside', () => {
      if (Date.now() - pointerDownTime < 300 && !dragging) {
        sprite._faustImageModel.triggerTap();
      }
      dragging = false;
      sprite.cursor = 'grab';
      setInteractionLock(false);
      persistModelPositionToBackend();
    });
    sprite.on('pointermove', (e) => {
      if (!dragging) return;
      const pos = e.data.global;
      let rawX = pos.x - dragOffset.x;
      let rawY = pos.y - dragOffset.y;
      rawX = Math.max(app.renderer.width * 0.1, Math.min(app.renderer.width * 1.0, rawX));
      rawY = Math.max(app.renderer.height * 0.1, Math.min(app.renderer.height * 1.0, rawY));
      sprite.x = rawX;
      sprite.y = rawY;
      updateQuickControllerPosition();
    });

    app.stage.addChild(sprite);
    clearOverlay();
    baseScale = Math.min(app.renderer.width / 1600, app.renderer.height / 900);
    baseScale *= Math.max(0.1, Number(resolvedConfig.scale) || 1.0);
    const configuredPos = readConfiguredModelRelPosition();
    if (configuredPos.x !== null) sprite.x = configuredPos.x * app.renderer.width;
    if (configuredPos.y !== null) sprite.y = configuredPos.y * app.renderer.height;
    applyModelScale();
    updateTextChatBarPosition();
    refreshQuickControllerVisibility();
    if (modelPathInput) modelPathInput.value = '__faust_images__';
  }

  function loadModel(path){
    const ext = String(path || '').toLowerCase().trim();
    if (ext === '__faust_images__') {
      loadImageModel(runtimeImageModelConfig || (runtimeLive2DConfig && runtimeLive2DConfig.IMAGE_MODEL_CONFIG) || {}).catch((err) => {
        showResultBubble('error', 'Images 模型加载失败：' + String(err && err.message ? err.message : err));
        console.error(err);
        showModelLoadFallback();
      });
      return;
    }
    if (ext.endsWith('.vrm')) {
      loadVRMModel(path);
      return;
    }
    if (modelType !== 'live2d') {
      switchToLive2DRenderer();
    }
    const loadRequestId = ++activeModelLoadRequestId;
    console.log('Loading model:', path);
    // determine Live2DModel constructor (try window.Live2DModel, then PIXI.live2d)
    Live2DModel = (typeof window !== 'undefined' && window.Live2DModel) ? window.Live2DModel : (PIXI && PIXI.live2d && PIXI.live2d.Live2DModel);
    if (!Live2DModel) {
      showResultBubble('error', '未检测到 pixi-live2d-display 库，请检查网络或依赖。');
      showModelLoadFallback();
      return;
    }
    showOverlay('加载模型: ' + path);
    resolveFrontendAssetPath(path).then((resolvedPath)=>{
      console.log('Resolved model path:', resolvedPath);
      if (!resolvedPath) throw new Error('模型路径解析失败');
      return readModelDefinition(resolvedPath).then((modelDef)=> ({ modelDef, resolvedPath }));
    }).then(({ modelDef, resolvedPath })=>{
      if (loadRequestId !== activeModelLoadRequestId) throw new Error('stale model load request');
      if (!modelDef) throw new Error('无法读取模型定义文件');
      availableMotions = extractMotionNames(modelDef);
      currentLipSyncParamIds = extractLipSyncParamIds(modelDef);
      return Live2DModel.from(resolvedPath);
    }).then(model => {
      if (loadRequestId !== activeModelLoadRequestId) return;
      // 移除上个模型
      if (currentModel && currentModel.parent) app.stage.removeChild(currentModel);
      currentModel = model;
      // 缩放并定位到右下角初始位置 (scale will be applied via baseScale * slider)
      model.scale.set(1.0);
      model.anchor.set(0.5, 1.0);
      model.x = app.renderer.width - 200;
      model.y = app.renderer.height - 20;
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
        persistModelPositionToBackend();
      });
      model.on('pointerupoutside', () => {
        dragging = false;
        model.cursor = 'grab';
        setInteractionLock(false);
        persistModelPositionToBackend();
      });
      model.on('pointermove', (e) => {
        if (!dragging) return;
        const pos = e.data.global;
        let rawX = pos.x - dragOffset.x;
        let rawY = pos.y - dragOffset.y;
        rawX = Math.max(app.renderer.width * 0.1, Math.min(app.renderer.width * 1.0, rawX));
        rawY = Math.max(app.renderer.height * 0.1, Math.min(app.renderer.height * 1.0, rawY));
        model.x = rawX;
        model.y = rawY;
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
      const configuredPos = readConfiguredModelRelPosition();
      if (configuredPos.x !== null) model.x = configuredPos.x * app.renderer.width;
      if (configuredPos.y !== null) model.y = configuredPos.y * app.renderer.height;
      // apply user-selected scale factor
      applyModelScale();
      // keep reference for mouth sync
      model._faustLive2D = { mouthValue: 0 };

      updateTextChatBarPosition();
      refreshQuickControllerVisibility();
      if (modelPathInput) modelPathInput.value = path;
      if (configuredPos.x !== null && configuredPos.y !== null) {
        lastPersistedModelPosition = { x: configuredPos.x, y: configuredPos.y };
      }
    }).catch(err => {
      if (String(err && err.message || '') === 'stale model load request') return;
      showResultBubble('error', 'Live2D 模型加载失败：' + String(err && err.message ? err.message : err));
      console.error(err);
      if (loadRequestId === activeModelLoadRequestId) showModelLoadFallback();
    });
  }

  if (loadBtn) loadBtn.addEventListener('click', () => {
    const p = modelPathInput.value.trim() || defaultModel;
    loadModel(p);
  });

  if (resetBtn) resetBtn.addEventListener('click', () => {
    if (!currentModel) return;
    currentModel.x = app.renderer.width - 200;
    currentModel.y = app.renderer.height - 20;
    updateQuickControllerPosition();
    persistModelPositionToBackend();
  });

  // 自动尝试加载后端配置指定的模型与布局
  modelPathInput.value = defaultModel;
  (async ()=>{
    await refreshSpeechRuntimeConfig(true);
    await loadUiWidgetSettings();
    const runtimeCfg = await loadRuntimeLive2DConfig();
    const configuredModel = runtimeCfg && runtimeCfg.LIVE2D_MODEL_PATH ? String(runtimeCfg.LIVE2D_MODEL_PATH).trim() : '';
    const configuredScale = runtimeCfg && runtimeCfg.LIVE2D_MODEL_SCALE !== undefined && runtimeCfg.LIVE2D_MODEL_SCALE !== null && runtimeCfg.LIVE2D_MODEL_SCALE !== ''
      ? Number(runtimeCfg.LIVE2D_MODEL_SCALE)
      : null;
    const configuredTextChatYFactor = runtimeCfg && runtimeCfg.TEXT_CHAT_BAR_Y_FACTOR !== undefined && runtimeCfg.TEXT_CHAT_BAR_Y_FACTOR !== null && runtimeCfg.TEXT_CHAT_BAR_Y_FACTOR !== ''
      ? Number(runtimeCfg.TEXT_CHAT_BAR_Y_FACTOR)
      : null;
    const configuredQuickControllerXOffset = runtimeCfg && runtimeCfg.FRONTEND_QUICK_CONTROLLER_X_OFFSET !== undefined && runtimeCfg.FRONTEND_QUICK_CONTROLLER_X_OFFSET !== null && runtimeCfg.FRONTEND_QUICK_CONTROLLER_X_OFFSET !== ''
      ? Number(runtimeCfg.FRONTEND_QUICK_CONTROLLER_X_OFFSET)
      : null;
    runtimeImageModelConfig = runtimeCfg && runtimeCfg.IMAGE_MODEL_CONFIG ? runtimeCfg.IMAGE_MODEL_CONFIG : null;
    if (Number.isFinite(configuredScale) && configuredScale > 0) {
      scaleFactor = configuredScale;
      if (modelScaleSlider) modelScaleSlider.value = String(scaleFactor);
      if (modelScaleValue) modelScaleValue.textContent = scaleFactor.toFixed(2) + 'x';
    }
    if (Number.isFinite(configuredTextChatYFactor)) {
      textChatBarYFactor = Math.min(2.0, Math.max(-1.0, configuredTextChatYFactor));
      try { uiWidgetManager.updateWidget('text-chat-bar', { coord: { x: 0.5, y: textChatBarYFactor } }); } catch (e) {}
    }
    if (Number.isFinite(configuredQuickControllerXOffset)) {
      quickControllerXOffset = Math.max(-400, Math.min(400, configuredQuickControllerXOffset));
      try { uiWidgetManager.updateWidget('quick-controller', { offset: { x: quickControllerXOffset, y: 0 } }); } catch (e) {}
    }
    const configuredModelType = runtimeCfg && runtimeCfg.MODEL_TYPE ? String(runtimeCfg.MODEL_TYPE).trim().toLowerCase() : 'live2d';
    const vrmModelPath = configuredModelType === 'vrm'
      ? (runtimeCfg && runtimeCfg.VRM_MODEL_PATH ? String(runtimeCfg.VRM_MODEL_PATH).trim() : '')
      : '';
    const toLoad = configuredModelType === 'vrm'
      ? (vrmModelPath || configuredModel || defaultModel)
      : (configuredModelType === 'images' ? '__faust_images__' : (configuredModel || defaultModel));
    modelPathInput.value = configuredModelType === 'images' ? '__faust_images__' : toLoad;
    // small delay so UI visible
    setTimeout(()=>{ loadModel(toLoad); }, 120);
  })();

  // 窗口尺寸变化时保持模型在屏幕内
  window.addEventListener('resize', ()=>{
    if (!currentModel) return;
    currentModel.x = Math.min(currentModel.x, app.renderer.width - 50);
    currentModel.y = Math.min(currentModel.y, app.renderer.height - 20);
    // auto-scale with resize
    try{
      baseScale = Math.min(app.renderer.width / 1600, app.renderer.height / 900);
      applyModelScale();
      hil.updatePosition();
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
      let interactiveActive = false;
      let pendingTimeout = null;
      let listenersAttached = false;

      function detectDevToolsLikelyOpen(){
        try {
          const widthGap = Math.abs(window.outerWidth - window.innerWidth);
          const heightGap = Math.abs(window.outerHeight - window.innerHeight);
          devToolsLikelyOpen = widthGap > 160 || heightGap > 160;
        } catch (_e) {
          devToolsLikelyOpen = false;
        }
      }

      function setIgnore(ignore){
        if (devToolsLikelyOpen) ignore = false;
        try{ app.renderer.view.style.pointerEvents = ignore ? 'none' : 'auto'; }catch(e){}
        window.api.setIgnoreMouseEvents(ignore).catch(()=>{});
      }

      function scheduleEnableIgnore(){
        if (pendingTimeout) clearTimeout(pendingTimeout);
        pendingTimeout = setTimeout(()=>{
          pendingTimeout = null;
          if (!interactiveActive && !interactionLocked){
            detectDevToolsLikelyOpen();
            setIgnore(true);
          }
        }, 140);
      }

      function isPointOverAnyWidget(x, y){
        try {
          for (const widget of uiWidgetManager.listWidgets()){
            const el = widget.element;
            if (!el || widget.hidden || !el.isConnected) continue;
            const rect = el.getBoundingClientRect();
            if (!rect.width || !rect.height) continue;
            if (x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom) return true;
          }
        } catch (_e) {}
        return false;
      }

      function onGlobalMouseMove(e){
        detectDevToolsLikelyOpen();
        hoverQuickController = isPointOverQuickController(e.clientX, e.clientY);
        hoverModel = isPointerOnModel(e.clientX, e.clientY);
        const overAsrBubble = isPointOverAsrBubble(e.clientX, e.clientY);
        const overSubagentSummary = isPointOverSubagentSummary(e.clientX, e.clientY);
        const overSubagentPanel = isPointOverSubagentPanel(e.clientX, e.clientY);
        const overHilApproval = hil.isPointOver(e.clientX, e.clientY);
        const overVRMConfig = isPointOverVRMConfig(e.clientX, e.clientY);
        const overTextChatBar = isPointOverTextChatBar(e.clientX, e.clientY);
        const overNimble = nimbleWin.isPointOverNimble(e.clientX, e.clientY);
        const onNimbleWindow = nimbleWin.isPointOverWindow(e.clientX, e.clientY);
        const overWidget = isPointOverAnyWidget(e.clientX, e.clientY);
        const overInteractive = hoverQuickController||hoverModel || overAsrBubble || overSubagentSummary || overSubagentPanel || overHilApproval || overVRMConfig || overTextChatBar || overNimble || onNimbleWindow || overWidget || dragging || interactionLocked || uiWidgetManager.isEditMode();
        if (devToolsLikelyOpen) {
          interactiveActive = true;
          setIgnore(false);
          refreshQuickControllerVisibility();
          return;
        }
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

      function ensureListeners(){
        if (listenersAttached) return;
        listenersAttached = true;
        window.addEventListener('mousemove', onGlobalMouseMove, { passive: true });
        window.addEventListener('resize', detectDevToolsLikelyOpen, { passive: true });
      }

      return {
        // click-through is always enabled — no toggle state
        enable(){
          document.body.classList.add('click-through');
          ensureListeners();
          detectDevToolsLikelyOpen();
          setIgnore(true);
          interactiveActive = false;
        },
        disable(){
          // no-op: click-through is always on
          log.warining('click-through disable() called but ignored');
        },
        setInteractiveLock(locked){
          if (locked){
            interactiveActive = true;
            setIgnore(false);
          } else {
            scheduleEnableIgnore();
          }
        },
        forceInteractive(){
          interactiveActive = true;
          setIgnore(false);
        }
      };
    }

    clickThroughController = createClickThroughController();
    clickThroughController.enable();
    // click-through is always on — checkbox changes are ignored
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

  audio.initEvents();
  
  if (quickToggleAsrBtn) quickToggleAsrBtn.addEventListener('click', ()=>{
    toggleAsr();
  });
  if (quickStopBtn) quickStopBtn.addEventListener('click', ()=>{ interruptAll(); });
  if (quickRandomMotionBtn) quickRandomMotionBtn.addEventListener('click', ()=>{ playRandomMotion(); });
  if (quickEditLayoutBtn) {
    quickEditLayoutBtn.addEventListener('click', () => {
      if (window.faustAppUI && typeof window.faustAppUI.toggleWidgetEditMode === 'function') {
        window.faustAppUI.toggleWidgetEditMode();
      }
    });
  }
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
  if (asrBubbleEl){
    asrBubbleEl.addEventListener('toggle', handleResultBubbleToggle, true);
    asrTextEl.addEventListener('scroll', ()=>{ rememberAsrScrollIntent(); });
    asrBubbleEl.addEventListener('mouseenter', ()=>{
      if (clickThroughController) clickThroughController.forceInteractive();
    });
    asrBubbleEl.addEventListener('wheel', ()=>{
      if (clickThroughController) clickThroughController.forceInteractive();
    }, { passive: true });
  }
  if (subagentSummaryEl){
    subagentSummaryEl.addEventListener('mouseenter', ()=>{
      if (clickThroughController) clickThroughController.forceInteractive();
    });
    subagentSummaryEl.addEventListener('click', (ev)=>{
      const item = ev.target && ev.target.closest ? ev.target.closest('[data-subagent-name]') : null;
      if (!item) return;
      const name = String(item.getAttribute('data-subagent-name') || '');
      renderSubagentPanelFromCache(name);
    });
  }
  if (hideAsrBubbleBtn){
    hideAsrBubbleBtn.addEventListener('click', ()=>{
      hideResultBubble();
    });
  }
  if (subagentPanelCloseBtn) subagentPanelCloseBtn.addEventListener('click', ()=>{ hideSubagentPanel(); });
  if (subagentStopBtn) subagentStopBtn.addEventListener('click', async ()=>{
    try{ await stopSelectedSubagent(); }catch(e){ showResultBubble('error', '停止 Subagent 失败: ' + String(e && e.message ? e.message : e)); }
  });
  initSubagentPanelDrag();
  refreshSubagentStatuses();
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
  if (openLiveBtn) openLiveBtn.addEventListener('click', async ()=>{
    try{
      if (window.api && window.api.openLiveWindow) await window.api.openLiveWindow();
    }catch(e){ console.warn('openLiveWindow failed', e); }
  });

  // ── VRM 配置面板（已抽取到 libs/vrm-config-panel.js） ──
  const vrmCfg = initVRMConfigPanel({ getVrmScene: () => vrmScene });
  vrmCfg.init();
  // Config mode pointer handling for gizmo interaction
  let vrmConfigGizmoCleanup = null;
  updateQuickAsrButton();

  // ── 日志面板（已抽取到 libs/log-panel.js） ──
  const logPanelCtrl = initLogPanel();
  logPanelCtrl.init();

  function refreshUiWidgetLayout() {
    uiWidgetManager.applyLayout();
    updateAsrTextPosition(true);
    refreshQuickControllerVisibility();
    nimbleWin.layoutWindows();
  }

  const uiWidgetEditor = initUiWidgetEditor({
    manager: uiWidgetManager,
    saveSettings: saveUiWidgetSettings,
    onEditModeChange: (enabled) => {
      if (clickThroughController) clickThroughController.setInteractiveLock(enabled);
      layoutSidePanel.setVisible(enabled);
    },
    refreshLayout: refreshUiWidgetLayout,
    onPropChange: () => {
      // 编辑模式属性面板修改 AsrBubble props 后：样式已由 refreshLayout 应用，
      // 这里补 HTML 过滤（推理/工具）与 Subagents 摘要重渲染
      applyAsrBubbleProps();
      showResultBubble(asrBubbleSource, asrBubbleState.entries);
      renderSubagentSummary();
    },
  });

  if (window.faustAppUI) {
    window.faustAppUI.toggleWidgetEditMode = () => uiWidgetEditor.toggle();
    window.faustAppUI.saveWidgetSettings = () => saveUiWidgetSettings();
  }

  // ── 布景台：系统组件组 ──
  layoutSidePanel.registerGroup({ id: 'system-widgets', label: '系统组件', order: 0 });
  layoutSidePanel.setGroupRender('system-widgets', (container) => {
    const items = [
      ['quick-controller', '快捷控制器'],
      ['text-chat-bar', '文字聊天条'],
      ['asr-bubble', 'ASR 气泡'],
      ['log-panel', '日志面板'],
      ['subagent-panel', '子代理面板'],
    ];
    for (const [widgetId, label] of items) {
      const widget = uiWidgetManager.getWidget(widgetId);
      if (!widget) continue;
      const row = document.createElement('div');
      row.className = 'lsp-row';
      const text = document.createElement('span');
      text.textContent = label;
      const switchWrap = document.createElement('label');
      switchWrap.className = 'lsp-switch';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = !widget.hidden;
      const slider = document.createElement('span');
      slider.className = 'lsp-switch-slider';
      input.addEventListener('change', () => {
        uiWidgetManager.updateWidget(widgetId, { hidden: !input.checked });
        refreshUiWidgetLayout();
        uiWidgetEditor.refreshGhostState();
        saveUiWidgetSettings();
      });
      switchWrap.append(input, slider);
      row.append(text, switchWrap);
      container.appendChild(row);
      if (widgetId === 'asr-bubble') {
        container.appendChild(buildAsrBubblePropPanel());
      }
    }
  });

  // AsrBubble 属性编辑面板：字体大小 / 白色背景 / 长宽比 / 推理 / 工具 / Subagents
  function buildAsrBubblePropPanel(){
    const panel = document.createElement('div');
    panel.className = 'lsp-props';
    panel.innerHTML =
      '<div class="lsp-prop-row"><span>字体大小</span>' +
        '<input type="number" min="10" max="48" step="1" data-k="fontSize"></div>' +
      '<div class="lsp-prop-row"><span>文字颜色</span>' +
        '<input type="color" data-k="textColor" style="width:44px;height:26px;padding:0;border:none;background:none;cursor:pointer"></div>' +
      '<div class="lsp-prop-row"><span>白色背景</span>' +
        '<label class="lsp-switch"><input type="checkbox" data-k="whiteBackground"><span class="lsp-switch-slider"></span></label></div>' +
      '<div class="lsp-prop-row"><span>长宽比</span>' +
        '<select data-k="aspectRatio">' +
          '<option value="">默认</option><option value="4 / 3">4:3</option>' +
          '<option value="3 / 4">3:4</option><option value="1 / 1">1:1</option>' +
          '<option value="16 / 9">16:9</option></select></div>' +
      '<div class="lsp-prop-row"><span>显示推理内容</span>' +
        '<label class="lsp-switch"><input type="checkbox" data-k="showReasoning"><span class="lsp-switch-slider"></span></label></div>' +
      '<div class="lsp-prop-row"><span>显示工具调用</span>' +
        '<label class="lsp-switch"><input type="checkbox" data-k="showTools"><span class="lsp-switch-slider"></span></label></div>' +
      '<div class="lsp-prop-row"><span>显示 Subagents</span>' +
        '<label class="lsp-switch"><input type="checkbox" data-k="showSubagents"><span class="lsp-switch-slider"></span></label></div>';
    const props = getAsrBubbleProps();
    panel.querySelectorAll('[data-k]').forEach((el) => {
      const k = el.dataset.k;
      if (el.type === 'checkbox') el.checked = !!props[k];
      else el.value = props[k] === undefined || props[k] === null ? '' : String(props[k]);
    });
    panel.querySelectorAll('input,select').forEach((el) => {
      el.addEventListener('change', () => {
        const k = el.dataset.k;
        const widget = uiWidgetManager.getWidget('asr-bubble') || {};
        const next = { ...(widget.props || {}) };
        if (el.type === 'checkbox') next[k] = el.checked;
        else if (el.type === 'number') next[k] = Number(el.value) > 0 ? Number(el.value) : 20;
        else next[k] = el.value;
        uiWidgetManager.updateWidget('asr-bubble', { props: next });
        applyAsrBubbleProps();
        showResultBubble(asrBubbleSource, asrBubbleState.entries);
        renderSubagentSummary();
        refreshUiWidgetLayout();
        saveUiWidgetSettings();
      });
    });
    return panel;
  }

  // ── 直播模式（已抽取到 libs/live-mode.js） ──
  const liveModeCtrl = initLiveMode();
  liveModeCtrl.start();

  // ── 常驻聊天 WebSocket ──
  // 保持 /faust/chat 连接，使后端触发器（Nimble / Public API / 定时任务等）
  // 唤醒 Agent 的回复能实时推送到前端显示与 TTS。
  ensureChatWsPersistent();

})();