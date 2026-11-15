// 模型表演覆盖层 — agent 指令 / 交互脉冲 / soullink 引擎 三级仲裁
//
// 过去有三套互不知道对方存在的表演驱动源：引擎的 nativeAnimation 注入、agent 文本 token
// （model-motion.js 直连 currentModel）、以及硬编码的 tap_body。本模块把它们收敛到一条链：
//   agent 覆盖（工具 + token）> 交互脉冲 > 引擎
// 纯计算层：无 PIXI / DOM 依赖，可在 node 冒烟测试中运行（requestNativeImpl 由 app.js 注入）。
export function createAvatarPerformance({
  getModelType,
  getLayer,
  getInjection,
  getAvailableMotions,
  getAvailableExpressions,
  getModelPath,
  getProfileSource,
  requestNativeImpl,
}) {
  // 与后端 avatar-performance 插件的 FACS_KEYS 一致；用于把 profile.parameterMap 收窄成
  // "这个模型真实可用的语义键"，而不是标准键全集
  const FACS_KEYS = [
    'browInnerUp', 'browOuterUp', 'browDown',
    'eyeOpen', 'eyeSmile', 'eyeSquint', 'eyeBlinkL', 'eyeBlinkR',
    'mouthSmile', 'mouthFrown', 'mouthOpen', 'mouthPucker',
    'gazeX', 'gazeY',
    'headX', 'headY', 'headZ',
    'bodyX', 'bodyY', 'bodyZ',
    'blush', 'tear', 'sweat', 'breath',
  ];

  // ── 交互分级阈值（唯一判定点；需要调参时只改这里） ──
  // tap / long_press 只在"抬起"时判定：按下阶段任何一次小位移都不足以证明这是点击，
  // 否则正常拖动的第一帧（1~6px）就会把整次拖动判成 tap 并把后续事件全部吞掉。
  const TAP_MAX_MS = 300;
  const TAP_MAX_MOVE_PX = 6;           // 抬起时的累计路径上限（手抖容差）
  const MULTI_TAP_WINDOW_MS = 1500;
  const MULTI_TAP_COUNT = 3;
  const LONG_PRESS_MS = 800;
  // stroke（抚摸）必须是"来回"：单向拖动是在搬模型，不是抚摸
  const STROKE_MIN_PX = 120;           // 累计路径下限
  const STROKE_MIN_EXCURSION_PX = 60;  // 离开按下点的最大距离下限：原地抖动不算抚摸
  const STROKE_MIN_REVERSALS = 2;      // 沿主运动轴的方向反转次数下限
  const STROKE_DIR_DEADBAND_PX = 4;    // 方向判定死区
  const STROKE_MIN_MS = 250;
  const MAX_PRESS_MS = 30000;          // 按住超过此值视为 up 丢失（窗口外松手 / 失焦）
  const HOVER_MIN_DWELL_MS = 600;      // 鼠标停留多久才算"在看"
  const HOVER_COOLDOWN_MS = 10000;     // 两次 hover 上报的最小间隔
  const TAP_PULSE_MS = 600;
  const DEFAULT_TOOL_HOLD_MS = 3000;
  const VAD_APPLY_AMOUNT = 0.65;
  const VAD_DRIFT_AMOUNT = 0.4;
  const CLEAR_SCOPES = ['all', 'facs', 'native'];
  // 引擎的原生动画 token 是模块级自增小整数（从 1 起），agent 覆盖必须避开这一段，
  // 否则 token 撞车会让 inject.js 的去重逻辑跳过本次下发
  const NATIVE_TOKEN_BASE = 1e9;

  const INTERACTION_PULSES = {
    tap: { browInnerUp: 0.25, headY: -0.08 },
    multi_tap: { browInnerUp: 0.45, eyeOpen: 0.2, headY: -0.15 },
    long_press: { browDown: 0.3, eyeSquint: 0.3 },
  };
  const SUMMARIES = {
    tap: '用户戳了你一下',
    stroke: '用户来回抚摸了你的模型',
    long_press: '用户长按了你',
    hover: '用户盯着你看了好一会',
  };

  // ── 覆盖状态 ──
  let agentFacs = null;         // {facs, expiresAt}
  let agentNative = null;       // {expression, motion, token, expiresAt}
  let interactionFacs = null;   // {facs, expiresAt}
  let interactionNative = null; // {expression, motion, token, expiresAt}
  let nativeToken = NATIVE_TOKEN_BASE;

  const pointer = {
    active: false,
    startAt: 0,
    path: 0,        // 累计路径（逐事件 hypot 之和）
    netDx: 0,       // 相对按下点的净位移
    netDy: 0,
    excursion: 0,   // 相对按下点的最大距离：区分"来回抚摸"与"原地抖动"
    reversals: 0,   // 沿主运动轴的方向反转次数（带死区）
    dirSign: 0,     // 当前主运动方向（1 / -1 / 0）
    done: false,
    tapTimes: [],  // 跨 down/up 周期保留，用于 multi_tap 窗口判定
  };

  // hover 驻留：pointerover 只记起点，由 tick() 按驻留时长结算（鼠标掠过包围盒不算交互）
  // area 在进入瞬间求值并缓存字符串：PIXI 的 FederatedPointerEvent 是池化对象，不能长期持有
  const hover = { active: false, startedAt: 0, area: 'unknown', position: null };
  let lastHoverReportAt = -Infinity;

  // rAF 时钟：与 soullinkTick 的 nowSeconds 同基准（tick 内部换算成毫秒）
  function clockMs() {
    return (typeof performance !== 'undefined' && typeof performance.now === 'function')
      ? performance.now()
      : Date.now();
  }

  function clamp01(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return 0;
    return Math.max(0, Math.min(1, number));
  }

  function modelType() {
    try {
      return String((typeof getModelType === 'function' ? getModelType() : '') || '').trim().toLowerCase();
    } catch (e) {
      return '';
    }
  }

  function runtimeLayer() {
    try {
      return (typeof getLayer === 'function' ? getLayer() : null) || null;
    } catch (e) {
      return null;
    }
  }

  function availableMotions() {
    try {
      const value = typeof getAvailableMotions === 'function' ? getAvailableMotions() : null;
      return Array.isArray(value) ? value.filter((name) => String(name || '').trim()) : [];
    } catch (e) {
      return [];
    }
  }

  function availableExpressions() {
    try {
      const value = typeof getAvailableExpressions === 'function' ? getAvailableExpressions() : null;
      return Array.isArray(value) ? value.filter((name) => String(name || '').trim()) : [];
    } catch (e) {
      return [];
    }
  }

  // 原生动画由 inject.js 的 applyNativeAnimation 统一落地（token 去重）；
  // 没有注入通道时（图片模型 / 表演层挂载失败）才直连模型。
  function usesInjection() {
    if (modelType() !== 'live2d') return false;
    const injection = typeof getInjection === 'function' ? getInjection() : null;
    return !!(injection && typeof injection.applyNativeAnimation === 'function');
  }

  function dispatchNative(entry) {
    if (typeof requestNativeImpl !== 'function') return false;
    try {
      return !!requestNativeImpl({
        expression: entry.expression || null,
        motion: entry.motion || null,
      });
    } catch (e) {
      console.warn('[avatar] 原生动画下发失败', e);
      return false;
    }
  }

  function dispatchTap() {
    if (typeof requestNativeImpl !== 'function') return false;
    try {
      return !!requestNativeImpl({ tap: true });
    } catch (e) {
      console.warn('[avatar] 图片模型 tap 反应失败', e);
      return false;
    }
  }

  // ── FACS 覆盖：agent > 交互脉冲 ──

  let lastAppliedFacs = null;  // null = 已清空；否则是已下发的 FACS 快照（JSON）

  function activeFacs() {
    if (agentFacs) return agentFacs.facs;
    if (interactionFacs) return interactionFacs.facs;
    return null;
  }

  // 幂等下发：引擎的 manual 层是持久状态（setManualFACS 覆盖、clearManualFACS 清空），
  // 内容没变就不重复调用，避免每次交互脉冲都往引擎塞一遍 agent 的覆盖
  function syncFacsLayer() {
    const layer = runtimeLayer();
    if (!layer) return;
    const facs = activeFacs();
    if (!facs) {
      if (lastAppliedFacs === null) return;
      lastAppliedFacs = null;
      try {
        if (typeof layer.clearManualFACS === 'function') layer.clearManualFACS();
      } catch (e) {
        console.warn('[avatar] manual FACS 清理失败', e);
      }
      return;
    }
    const signature = JSON.stringify(facs);
    if (signature === lastAppliedFacs) return;
    lastAppliedFacs = signature;
    try {
      if (typeof layer.setManualFACS === 'function') layer.setManualFACS({ ...facs });
    } catch (e) {
      console.warn('[avatar] manual FACS 下发失败', e);
    }
  }

  // ── 原生覆盖：单一入口 + 单一落地 ──

  function requestNative(request) {
    const req = request || {};
    const source = String(req.source || 'tool');
    const motion = req.motion && req.motion.group
      ? {
          group: String(req.motion.group),
          index: Number(req.motion.index) || 0,
          priority: req.motion.priority || 'force',
        }
      : null;
    const expression = req.expression ? String(req.expression) : null;
    if (!expression && !motion) return false;

    const entry = {
      expression,
      motion,
      token: ++nativeToken,
      expiresAt: clockMs() + Math.max(0, Number(req.holdMs) || 0),
    };

    if (source === 'interaction') {
      // agent 覆盖存活期间交互不改原生动画
      if (agentNative) return false;
      interactionNative = entry;
    } else {
      agentNative = entry;
    }

    if (usesInjection()) return true;
    return dispatchNative(entry);
  }

  function resolveNativeAnimation(engineDirective) {
    const active = agentNative || interactionNative;
    if (!active) return engineDirective;
    // token 在同一覆盖存活期间保持稳定：inject.js 靠 token 去重，每帧换 token 会不停重启动作
    return {
      token: active.token,
      expression: active.expression,
      motion: active.motion,
      suppressParamIds: [],
    };
  }

  function releaseNativeImmediately() {
    const injection = typeof getInjection === 'function' ? getInjection() : null;
    if (!injection || typeof injection.applyNativeAnimation !== 'function') return;
    try {
      injection.applyNativeAnimation(null);
    } catch (e) {
      // 模型可能已卸载
    }
  }

  // ── TTL 释放（由已有 soullinkTick rAF 循环驱动，不新增定时器） ──

  function releaseExpired(nowMs) {
    let facsChanged = false;
    if (agentFacs && agentFacs.expiresAt <= nowMs) { agentFacs = null; facsChanged = true; }
    if (interactionFacs && interactionFacs.expiresAt <= nowMs) { interactionFacs = null; facsChanged = true; }
    if (agentNative && agentNative.expiresAt <= nowMs) agentNative = null;
    if (interactionNative && interactionNative.expiresAt <= nowMs) interactionNative = null;
    if (facsChanged) syncFacsLayer();
  }

  // hover 驻留结算：停留够久才算"在看"，且冷却窗内只报一次。
  // 时钟基准必须是 clockMs()（performance.now），与 pointerover 记下的 startedAt 一致。
  function settleHover(nowMs) {
    if (!hover.active || pointer.active) return null;
    if (nowMs - hover.startedAt < HOVER_MIN_DWELL_MS) return null;
    if (nowMs - lastHoverReportAt < HOVER_COOLDOWN_MS) {
      hover.active = false;
      return null;
    }
    hover.active = false;
    lastHoverReportAt = nowMs;
    // at 字段用墙钟（后端会以自己的时钟覆盖，但载荷里不能是页面相对时间）
    return emit('hover', Date.now(), { path: 0, duration: 0 }, { hitArea: hover.area, position: hover.position });
  }

  function tick(nowSeconds) {
    const nowMs = Number(nowSeconds) * 1000;
    releaseExpired(nowMs);
    return settleHover(nowMs);
  }

  function clearAll(scope = 'all') {
    const target = String(scope || 'all').toLowerCase();
    if (target === 'all' || target === 'facs') {
      agentFacs = null;
      interactionFacs = null;
    }
    if (target === 'all' || target === 'native') {
      const hadNative = !!(agentNative || interactionNative);
      agentNative = null;
      interactionNative = null;
      if (hadNative) releaseNativeImmediately();
    }
    resetPointer();
    // 多击历史属于"上一份模型/上一段会话"，切换模型后不得继续累计
    pointer.tapTimes = [];
    hover.active = false;
    syncFacsLayer();
  }

  // ── 情绪：VAD 连续漂移（不节流、不打断 idle/眨眼） ──

  function vadTargetFor(emotion, intensity) {
    const presets = (typeof window !== 'undefined' && window.Soullink && window.Soullink.EMOTION_PRESETS) || null;
    const preset = presets ? presets[emotion] : null;
    if (!preset) return null;
    return {
      valence: Number(preset.valence) || 0,
      arousal: (Number(preset.arousal) || 0) * clamp01(intensity),
      dominance: Number(preset.dominance) || 0,
    };
  }

  function applyVadTarget(target, amount) {
    const layer = runtimeLayer();
    const runtime = layer && layer.runtime;
    if (!runtime || typeof runtime.applyVADTarget !== 'function') return false;
    try {
      runtime.applyVADTarget(target, amount);
      return true;
    } catch (e) {
      console.warn('[avatar] applyVADTarget 失败', e);
      return false;
    }
  }

  // emotion-engine 的 6 维主导情绪 → soullink 情绪预设 → VAD 目标（每轮轮询调用，不节流）
  function applyEmotionDrift(dominantKey, dominantValue) {
    const bridge = (typeof window !== 'undefined' && window.Soullink && window.Soullink.mapEmotionToIntent) || null;
    if (typeof bridge !== 'function') return { ok: false, detail: 'Soullink 未加载' };
    const intent = bridge(dominantKey, dominantValue);
    const target = intent ? vadTargetFor(intent.emotion, intent.intensity) : null;
    if (!target) return { ok: false, detail: `无情绪预设: ${intent && intent.emotion}` };
    if (!applyVadTarget(target, VAD_DRIFT_AMOUNT)) return { ok: false, detail: '表演层未激活' };
    return { ok: true, emotion: intent.emotion, intensity: intent.intensity, target };
  }

  // ── 命令分发（AVATAR_*） ──

  function normalizeHoldMs(value, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number) || number < 0) return fallback;
    return Math.round(number);
  }

  function applyEmotionCommand(body) {
    const emotion = String(body.emotion || '').trim();
    if (!emotion) return { ok: false, detail: 'emotion 不能为空' };
    const intensity = clamp01(body.intensity === undefined ? 0.6 : body.intensity);
    const target = vadTargetFor(emotion, intensity);
    if (!target) return { ok: false, detail: `未知情绪: ${emotion}` };
    if (!applyVadTarget(target, VAD_APPLY_AMOUNT)) return { ok: false, detail: '表演层未激活' };
    return { ok: true, emotion, intensity, target };
  }

  function applyExpressionCommand(body) {
    const name = String(body.name || '').trim();
    if (!name) return { ok: false, detail: 'name 不能为空' };
    const holdMs = normalizeHoldMs(body.hold_ms, DEFAULT_TOOL_HOLD_MS);
    if (!requestNative({ expression: name, motion: null, source: 'tool', holdMs })) {
      return { ok: false, detail: '表演层未就绪或模型拒绝了该表情' };
    }
    return { ok: true, name, hold_ms: holdMs };
  }

  function applyMotionCommand(body) {
    const name = String(body.name || '').trim();
    if (!name) return { ok: false, detail: 'name 不能为空' };
    const holdMs = normalizeHoldMs(body.hold_ms, DEFAULT_TOOL_HOLD_MS);
    const accepted = requestNative({
      expression: null,
      motion: { group: name, index: 0, priority: 'force' },
      source: 'tool',
      holdMs,
    });
    if (!accepted) return { ok: false, detail: '表演层未就绪或模型拒绝了该动作' };
    return { ok: true, name, hold_ms: holdMs };
  }

  function applyFacsCommand(body) {
    const raw = body.facs && typeof body.facs === 'object' ? body.facs : null;
    if (!raw) return { ok: false, detail: 'facs 必须是对象' };
    const facs = {};
    for (const [key, value] of Object.entries(raw)) {
      const number = Number(value);
      if (!Number.isFinite(number)) return { ok: false, detail: `参数值非法: ${key}` };
      facs[key] = Math.max(-1, Math.min(1, number));
    }
    if (!Object.keys(facs).length) return { ok: false, detail: 'facs 不能为空' };
    const holdMs = normalizeHoldMs(body.hold_ms, DEFAULT_TOOL_HOLD_MS);
    agentFacs = { facs, expiresAt: clockMs() + holdMs };
    syncFacsLayer();
    return { ok: true, facs, hold_ms: holdMs };
  }

  function applyClearCommand(body) {
    const scope = String(body.scope || 'all').trim().toLowerCase();
    if (!CLEAR_SCOPES.includes(scope)) return { ok: false, detail: `未知 scope: ${scope}` };
    clearAll(scope);
    return { ok: true, scope };
  }

  function applyCommand(cmd, payload) {
    const command = String(cmd || '').trim().toUpperCase();
    const body = payload && typeof payload === 'object' ? payload : {};
    if (command === 'AVATAR_EMOTION') return applyEmotionCommand(body);
    if (command === 'AVATAR_EXPRESSION') return applyExpressionCommand(body);
    if (command === 'AVATAR_MOTION') return applyMotionCommand(body);
    if (command === 'AVATAR_FACS') return applyFacsCommand(body);
    if (command === 'AVATAR_CLEAR') return applyClearCommand(body);
    return { ok: false, detail: `未知命令: ${command}` };
  }

  // ── 交互分级 + 本地即时反应 ──

  function resetPointer() {
    pointer.active = false;
    pointer.startAt = 0;
    pointer.path = 0;
    pointer.netDx = 0;
    pointer.netDy = 0;
    pointer.excursion = 0;
    pointer.reversals = 0;
    pointer.dirSign = 0;
    pointer.done = false;
  }

  // 累加一段位移：路径、净位移、最大偏离、主运动轴方向反转
  function accumulate(dx, dy) {
    pointer.path += Math.hypot(dx, dy);
    pointer.netDx += dx;
    pointer.netDy += dy;
    const excursion = Math.hypot(pointer.netDx, pointer.netDy);
    if (excursion > pointer.excursion) pointer.excursion = excursion;
    // 主运动轴取本次位移较大的轴；死区避免手抖被反复记成反转
    const axis = Math.abs(dx) >= Math.abs(dy) ? dx : dy;
    if (axis >= STROKE_DIR_DEADBAND_PX) {
      if (pointer.dirSign < 0) pointer.reversals += 1;
      pointer.dirSign = 1;
    } else if (axis <= -STROKE_DIR_DEADBAND_PX) {
      if (pointer.dirSign > 0) pointer.reversals += 1;
      pointer.dirSign = -1;
    }
  }

  // 抚摸 = 走得够远 + 真的离开过按下点 + 来回至少两次。
  // 单向拖动（把模型从 A 搬到 B）路径再长也不成立——那是布局操作，不是抚摸。
  function isStroke(duration) {
    return duration >= STROKE_MIN_MS
      && pointer.path >= STROKE_MIN_PX
      && pointer.excursion >= STROKE_MIN_EXCURSION_PX
      && pointer.reversals >= STROKE_MIN_REVERSALS;
  }

  // 抬起时的离散判定：tap（几乎没动且很快松手）/ long_press（按住不动）。
  // 300~800ms 且几乎没动：既不是 tap 也不是长按，不判定。
  function classifyRelease(duration) {
    if (pointer.path <= TAP_MAX_MOVE_PX && duration <= TAP_MAX_MS) return 'tap';
    if (pointer.excursion <= TAP_MAX_MOVE_PX && duration >= LONG_PRESS_MS) return 'long_press';
    return null;
  }

  // tap 反应三级回退：① 命中区名精确配对（Tap@{area}）② 任意含 tap 的组 ③ FACS 脉冲
  function pickTapMotion(area) {
    const groups = availableMotions();
    if (!groups.length) return null;
    const hit = String(area || '').trim().toLowerCase();
    if (hit && hit !== 'unknown') {
      const exact = groups.find((group) => String(group).toLowerCase() === `tap@${hit}`);
      if (exact) return exact;
    }
    return groups.find((group) => String(group).toLowerCase().includes('tap')) || null;
  }

  function pulseFor(kind, ctx) {
    if (kind === 'stroke') {
      // 方向随拖动：往哪边拖就往哪边偏头、往哪边看
      return { headZ: 0.12, gazeX: (ctx.dx || 0) >= 0 ? 0.3 : -0.3 };
    }
    return INTERACTION_PULSES[kind] || null;
  }

  function applyReaction(kind, ctx) {
    if (kind === 'hover') return;
    if (modelType() === 'images') {
      // 图片模型没有参数层，只能换图
      dispatchTap();
      return;
    }
    if (kind === 'tap') {
      const group = pickTapMotion(ctx.area);
      if (group) {
        requestNative({
          motion: { group, index: 0, priority: 'force' },
          source: 'interaction',
          holdMs: TAP_PULSE_MS,
        });
        return;
      }
    }
    const pulse = pulseFor(kind, ctx);
    if (!pulse) return;
    interactionFacs = { facs: pulse, expiresAt: clockMs() + TAP_PULSE_MS };
    syncFacsLayer();
  }

  function normalizePosition(value) {
    const x = Number(value && value.x);
    const y = Number(value && value.y);
    const round = (number) => (Number.isFinite(number) ? Math.round(number * 1000) / 1000 : 0);
    return { x: round(x), y: round(y) };
  }

  function summarize(kind, count) {
    if (kind === 'multi_tap') return `用户连续戳了你 ${count} 下`;
    return SUMMARIES[kind] || `用户与模型交互（${kind}）`;
  }

  // metrics.hitArea 支持字符串或惰性函数（app.js 传函数，避免每次 pointermove 都做 hitTest）
  function resolveHitArea(info) {
    const raw = info ? info.hitArea : null;
    let value = raw;
    if (typeof raw === 'function') {
      try {
        value = raw();
      } catch (e) {
        value = '';
      }
    }
    return String(value || '').trim() || 'unknown';
  }

  // gesture = {path, duration, dx, dy, excursion, reversals}
  function emit(kind, t, gesture, info) {
    const g = gesture || {};
    let finalKind = kind;
    let count = 1;
    if (kind === 'tap') {
      pointer.tapTimes = pointer.tapTimes.filter((ts) => t - ts <= MULTI_TAP_WINDOW_MS);
      pointer.tapTimes.push(t);
      count = pointer.tapTimes.length;
      if (count >= MULTI_TAP_COUNT) {
        finalKind = 'multi_tap';
        pointer.tapTimes = [];
      }
    }
    const ctx = { dx: Number(g.dx) || 0, dy: Number(g.dy) || 0, area: resolveHitArea(info) };
    if (kind !== 'hover') pointer.done = true;
    applyReaction(finalKind, ctx);

    const tier = (finalKind === 'tap' || finalKind === 'hover') ? 'light' : 'heavy';
    return {
      tier,
      payload: {
        tier,
        kind: finalKind,
        count,
        duration_ms: Math.max(0, Math.round(Number(g.duration) || 0)),
        distance_px: Math.max(0, Math.round(Number(g.path) || 0)),
        // 手势形状：搬模型（excursion 大、reversals 0）与抚摸（来回）靠这两项区分
        excursion_px: Math.max(0, Math.round(Number(g.excursion) || 0)),
        reversals: Math.max(0, Math.round(Number(g.reversals) || 0)),
        area: ctx.area,
        position: normalizePosition(info && info.position),
        model_type: modelType() || 'unknown',
        at: t / 1000,
        summary: summarize(finalKind, count),
      },
    };
  }

  // kind ∈ {down, move, up, upoutside, hover, hoverend}；metrics = {t, dx, dy, buttons, hitArea, position}
  // hitArea 可为字符串或惰性函数（app.js 传函数）；返回 {tier, payload}（交互已完成）或 null。
  // 本地反应已在内部执行。tap / long_press 只在抬起时判定；move 阶段只可能产出 stroke。
  function onPointer(kind, metrics) {
    const info = metrics || {};
    const rawT = Number(info.t);
    const t = Number.isFinite(rawT) ? rawT : Date.now();
    const action = String(kind || '').toLowerCase();

    if (action === 'hover') {
      // 只记驻留起点，由 tick() 结算：鼠标掠过模型包围盒（含大片透明区）不算交互
      hover.active = true;
      hover.startedAt = clockMs();
      hover.area = resolveHitArea(info);
      hover.position = info.position;
      return null;
    }
    if (action === 'hoverend') {
      hover.active = false;
      return null;
    }
    if (action === 'down') {
      resetPointer();
      hover.active = false;
      pointer.active = true;
      pointer.startAt = t;
      return null;
    }
    if (!pointer.active) return null;

    const dx = Number(info.dx) || 0;
    const dy = Number(info.dy) || 0;
    const duration = t - pointer.startAt;

    // up 丢失自愈：move 时指针已经松开（buttons === 0，窗口外松手 / 失焦）说明我们没收到
    // pointerup——这次按压的状态是脏的，直接丢弃，绝不用陈旧的 startAt 去判定长按/抚摸。
    // 只在 move 上判定：pointerup 自身的 buttons 恒为 0，不能拿它当"丢失"信号。
    if (action === 'move' && info.buttons === 0) {
      resetPointer();
      return null;
    }
    if (duration > MAX_PRESS_MS) {
      resetPointer();
      return null;
    }

    if (action === 'move') {
      if (pointer.done) return null;
      accumulate(dx, dy);
      // 抚摸在移动过程中即时判定（本地反应要跟手）；tap / long_press 一律等抬起
      if (!isStroke(duration)) return null;
      return emit('stroke', t, {
        path: pointer.path,
        duration,
        dx: pointer.netDx,
        dy: pointer.netDy,
        excursion: pointer.excursion,
        reversals: pointer.reversals,
      }, info);
    }

    if (action === 'up' || action === 'upoutside') {
      const alreadyDone = pointer.done;
      // 抬起事件自带的最后一段位移也要计入，否则快速拖动会少算
      if (!alreadyDone) accumulate(dx, dy);
      const verdict = alreadyDone
        ? null
        : (isStroke(duration) ? 'stroke' : classifyRelease(duration));
      const gesture = {
        path: pointer.path,
        duration,
        dx: pointer.netDx,
        dy: pointer.netDy,
        excursion: pointer.excursion,
        reversals: pointer.reversals,
      };
      resetPointer();
      if (!verdict) return null;
      return emit(verdict, t, gesture, info);
    }

    return null;
  }

  // ── 能力上报快照 ──

  function emotionKeys() {
    const presets = (typeof window !== 'undefined' && window.Soullink && window.Soullink.EMOTION_PRESETS) || null;
    return presets ? Object.keys(presets) : [];
  }

  function getCapabilities() {
    const type = modelType() || 'unknown';
    const isImages = type === 'images';
    const profileSource = String((typeof getProfileSource === 'function' ? getProfileSource() : '') || 'none');
    const layer = runtimeLayer();
    const runtime = layer && layer.runtime;
    const parameterMap = (runtime && typeof runtime.getSnapshot === 'function')
      ? (((runtime.getSnapshot() || {}).profile || {}).parameterMap || {})
      : {};
    const facsKeys = (!isImages && profileSource !== 'none')
      ? Object.keys(parameterMap).filter((key) => FACS_KEYS.includes(key))
      : [];
    return {
      model_type: type,
      model_path: String((typeof getModelPath === 'function' ? getModelPath() : '') || ''),
      emotions: isImages ? availableMotions() : emotionKeys(),
      expressions: isImages ? [] : availableExpressions(),
      motions: isImages ? [] : availableMotions(),
      facs_keys: facsKeys,
      profile_source: profileSource,
      at: Date.now() / 1000,
    };
  }

  return {
    applyCommand,
    requestNative,
    resolveNativeAnimation,
    onPointer,
    tick,
    getCapabilities,
    clearAll,
    applyEmotionDrift,
  };
}
