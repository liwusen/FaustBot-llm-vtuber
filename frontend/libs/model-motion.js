// 模型动作/表情模块 — 触发 motion/expression 与解析 <{...}> 动作 token
// 用法: const motion = initModelMotion({ getModel, getModelType, getVrmScene, getAvailableMotions, getAvailableExpressions });

export function initModelMotion({ getModel, getModelType, getVrmScene, getAvailableMotions, getAvailableExpressions }) {
  // 触发冷却：同一 model:name / model:expr:name 在 100ms 内只触发一次
  const motionTriggerCooldownMs = 100;
  const recentMotionTriggers = new Map();

  function playMotionByName(name){
    const currentModel = getModel();
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
    const availableMotions = getAvailableMotions();
    const pool = availableMotions.length ? availableMotions : ['Idle'];
    const picked = pool[Math.floor(Math.random() * pool.length)];
    return playMotionByName(picked);
  }

  function triggerModelMotion(name){
    const currentModel = getModel();
    const modelType = getModelType();
    const vrmScene = getVrmScene();
    const availableMotions = getAvailableMotions();
    const motionName = String(name || '').trim();
    if (!motionName) return false;
    // EXPRESSION: 前缀 → 触发表情（Live2D Expression / VRM Expression）
    if (motionName.toUpperCase().startsWith('EXPRESSION:')) {
      return triggerModelExpression(motionName.slice('EXPRESSION:'.length).trim());
    }
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

  // 触发 Live2D Expression / VRM Expression（带冷却，与 motion 共用触发频率控制）
  function triggerModelExpression(name){
    const currentModel = getModel();
    const modelType = getModelType();
    const vrmScene = getVrmScene();
    const availableExpressions = getAvailableExpressions();
    const exprName = String(name || '').trim();
    if (!exprName) return false;
    const now = Date.now();
    const cooldownKey = `${modelType}:expr:${exprName}`;
    const lastTs = recentMotionTriggers.get(cooldownKey) || 0;
    if (now - lastTs < motionTriggerCooldownMs) return false;

    let triggered = false;
    if (modelType === 'vrm' && vrmScene) {
      const expressions = Array.isArray(vrmScene.getAvailableExpressions?.()) ? vrmScene.getAvailableExpressions() : [];
      if (expressions.includes(exprName)) {
        triggered = !!vrmScene.setExpression(exprName);
      }
    } else if (modelType === 'live2d' && currentModel) {
      if (availableExpressions.includes(exprName) && typeof currentModel.expression === 'function') {
        try {
          const result = currentModel.expression(exprName);
          if (result && typeof result.catch === 'function') result.catch(() => {});
          triggered = true;
        } catch (e) {
          console.warn('播放 expression 失败', exprName, e);
        }
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

  return {
    playMotionByName,
    playRandomMotion,
    triggerModelMotion,
    triggerModelExpression,
    consumeMotionTokens,
    takeMotionsForSentence,
    takeMotionsFrom,
    stripMotionTokens
  };
}
