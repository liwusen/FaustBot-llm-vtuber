// beforeModelUpdate 参数注入 + native 动画分发
// 参照 @soullink-emotion/live2d-pixi 的 Live2DRenderer.applyParametersNow / applyNativeAnimation 模式
const PRIORITY = Object.freeze({ idle: 1, normal: 2, force: 3 });

function r3(v) {
  return typeof v === 'number' ? Number(v.toFixed(3)) : v;
}

export function attachModelInjection(model, getParams, log = false) {
  if (!model || !model.internalModel) {
    if (log) console.warn('[soullink:inject] 挂载失败: model 或 internalModel 缺失', { hasModel: !!model, hasInternal: !!(model && model.internalModel) });
    return null;
  }
  const internal = model.internalModel;
  if (typeof internal.on !== 'function') {
    if (log) console.warn('[soullink:inject] 挂载失败: internalModel.on 不是函数');
    return null;
  }

  let suppressedParamIds = new Set();
  let lastNativeAnimToken = -1;
  let injectFrameCount = 0;

  const beforeModelUpdate = () => {
    injectFrameCount += 1;
    const coreModel = internal.coreModel;
    if (!coreModel || typeof coreModel.setParameterValueById !== 'function') {
      if (log && injectFrameCount % 60 === 1) console.warn('[soullink:inject] coreModel 不可用，无法注入', { hasCore: !!coreModel });
      return;
    }
    const params = (typeof getParams === 'function' ? getParams() : null) || {};
    let written = 0;
    for (const [id, value] of Object.entries(params)) {
      if (suppressedParamIds.has(id)) continue;
      try {
        coreModel.setParameterValueById(id, value, 1);
        written += 1;
      } catch (e) {
        // 参数不存在等：忽略
      }
    }
    if (log && injectFrameCount % 120 === 0) {
      console.info('[soullink:inject] 帧#' + injectFrameCount, {
        written,
        total: Object.keys(params).length,
        suppressed: suppressedParamIds.size,
        eyeL: r3(params.ParamEyeLOpen),
        eyeR: r3(params.ParamEyeROpen),
        breath: r3(params.ParamBreath),
        angleX: r3(params.ParamAngleX),
        angleY: r3(params.ParamAngleY),
        bodyX: r3(params.ParamBodyAngleX),
        mouthOpen: r3(params.ParamMouthOpenY),
        mouthForm: r3(params.ParamMouthForm),
      });
    }
  };

  const applyExpression = (name) => {
    if (typeof model.expression !== 'function') return;
    try {
      const result = name === undefined ? model.expression() : model.expression(name);
      if (result && typeof result.catch === 'function') result.catch(() => {});
    } catch (e) {
      // 表达式不存在：忽略
    }
  };

  const applyMotion = (group, index, priority) => {
    if (typeof model.motion !== 'function') return;
    try {
      const result = model.motion(group, index, PRIORITY[priority] || 2);
      if (result && typeof result.catch === 'function') result.catch(() => {});
    } catch (e) {
      // 动作组不存在：忽略
    }
  };

  const applyNativeAnimation = (directive) => {
    suppressedParamIds = new Set((directive && directive.suppressParamIds) || []);
    if (!directive) {
      if (lastNativeAnimToken !== 0) {
        applyExpression(undefined);
        lastNativeAnimToken = 0;
      }
      return;
    }
    if (!model) return;
    if (directive.token === lastNativeAnimToken) return;
    if (directive.expression !== null) applyExpression(directive.expression);
    if (directive.motion !== null) {
      applyMotion(directive.motion.group, directive.motion.index ?? 0, directive.motion.priority ?? 'normal');
    }
    lastNativeAnimToken = directive.token;
  };

  internal.on('beforeModelUpdate', beforeModelUpdate);
  // 禁用内部眨眼 → 由 engine 的 IdleEngine 接管随机眨眼
  if (log) console.info('[soullink:inject] 挂载成功:', { hasCoreModel: !!internal.coreModel, eyeBlinkBefore: internal.eyeBlink ? 'enabled' : 'absent' });
  if (internal.eyeBlink !== undefined) internal.eyeBlink = undefined;
  if (log) console.info('[soullink:inject] 内部眨眼已禁用（engine 接管）');

  return {
    detach() {
      try {
        internal.off('beforeModelUpdate', beforeModelUpdate);
      } catch (e) {
        // 忽略
      }
    },
    applyNativeAnimation,
  };
}
