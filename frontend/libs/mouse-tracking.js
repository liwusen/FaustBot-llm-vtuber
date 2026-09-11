// 鼠标头部跟踪模块 — 按 LIVE2D_MOUSE_TRACKING_STRENGTH 强度驱动 Live2D focus，
// 并把 focusController 方向换算成参数覆盖值合并进 soullink 注入，保证跟踪最终生效。
// 用法: const mouseTracking = initMouseTracking({ getModel, getApp, getRuntimeLive2DConfig });

export function initMouseTracking({ getModel, getApp, getRuntimeLive2DConfig }) {
  // 状态：跟踪强度（0=关闭头部跟踪，1=完全跟随鼠标），默认 0.5
  let mouseTrackingStrength = 0.5; // LIVE2D_MOUSE_TRACKING_STRENGTH（0=关闭头部跟踪）
  let mouseTrackingInited = false;

  // 读取运行时配置中的跟踪强度，钳制到 [0, 1]，非法值回退 0.5
  function currentMouseTrackingStrength(){
    const cfg = getRuntimeLive2DConfig();
    const raw = cfg && cfg.LIVE2D_MOUSE_TRACKING_STRENGTH;
    const parsed = Number(raw);
    mouseTrackingStrength = Number.isFinite(parsed) ? Math.max(0, Math.min(1, parsed)) : 0.5;
    return mouseTrackingStrength;
  }

  // 鼠标跟踪强度：监听 pixi EventSystem 的 globalpointermove，把目标点按强度向模型中心
  // 收缩后调用 model.focus（强度 0 = 完全不跟随鼠标）。
  // 说明：soullink 无 autoFocus 逻辑；默认跟踪来自 pixi-live2d-display 的 Automator，
  // 已在 Live2DModel.from 里以 autoFocus:false 关闭，这里自建按强度控制的轻量跟踪。
  function init(){
    if (mouseTrackingInited) return;
    mouseTrackingInited = true;
    // 注意：pixi v7 的 globalpointermove 只派发给 interactive 显示对象（EventBoundary.all），
    // renderer.events.on('globalpointermove') 永远不会触发。
    // 因此改用原生 window pointermove（必定触发），用 canvas 布局矩形把 clientX/Y 换算成世界坐标。
    window.addEventListener('pointermove', (e) => {
      const currentModel = getModel();
      if (!currentModel || typeof currentModel.focus !== 'function') return;
      const strength = currentMouseTrackingStrength();
      if (strength <= 0) return;
      const app = getApp();
      const canvas = app && app.renderer && app.renderer.view;
      if (!canvas) return;
      const rect = canvas.getBoundingClientRect();
      if (!rect || rect.width <= 0 || rect.height <= 0) return;
      // autoDensity 下 canvas CSS 尺寸 = pixi 逻辑尺寸 → clientX - rect.left 即世界坐标
      const gx = e.clientX - rect.left;
      const gy = e.clientY - rect.top;
      if (!Number.isFinite(gx) || !Number.isFinite(gy)) return;
      // 目标点按强度向模型中心收缩（模型中心 = 当前舞台坐标）
      const targetX = currentModel.x + (gx - currentModel.x) * strength;
      const targetY = currentModel.y + (gy - currentModel.y) * strength;
      currentModel.focus(targetX, targetY);
    });
  }

  // 鼠标跟踪的最终覆盖参数：pixi 的 updateFocus()（addParameterValueById）发生在
  // beforeModelUpdate 之前，会被 soullink 注入（setParameterValueById）覆盖，导致跟踪不生效。
  // 这里把 focusController 的方向按 pixi 同款公式换算，合并进 inject 的 getParams 结果
  // （作为同键覆盖值），保证渲染的是"跟踪后的头部/视线"。
  function focusParamOverrides(model){
    if (currentMouseTrackingStrength() <= 0) return {};
    const internal = model && model.internalModel;
    if (!internal || !internal.focusController) return {};
    const fc = internal.focusController;
    if (!fc.x && !fc.y) return {};
    const out = {};
    if (internal.idParamEyeBallX) out[internal.idParamEyeBallX] = fc.x;
    if (internal.idParamEyeBallY) out[internal.idParamEyeBallY] = fc.y;
    if (internal.idParamAngleX) out[internal.idParamAngleX] = fc.x * 30;
    if (internal.idParamAngleY) out[internal.idParamAngleY] = fc.y * 30;
    if (internal.idParamAngleZ) out[internal.idParamAngleZ] = fc.x * fc.y * -30;
    if (internal.idParamBodyAngleX) out[internal.idParamBodyAngleX] = fc.x * 10;
    return out;
  }

  return { init, focusParamOverrides };
}
