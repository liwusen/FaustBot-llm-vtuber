// 冒烟：加载 soullink.bundle.js（node 环境打桩），验证 performance.js 的
// 覆盖槽 / TTL / 优先级 / 交互分级 / tap 三级回退 / 能力快照
const assert = require('assert');

// ── 可控时钟（performance.js 以 performance.now() 为 rAF 基准） ──
let fakeNow = 0;
const perfShim = global.performance || require('perf_hooks').performance;
perfShim.now = () => fakeNow;
global.performance = perfShim;

global.window = global;
global.self = global;
global.document = {
  createElement: () => ({ set src(v) {}, set async(v) {}, onload: null, onerror: null }),
  head: { appendChild() {} },
  body: { appendChild() {} },
  addEventListener() {},
};
global.Live2DCubismCore = { _isStarted: true, Version: { csmGetVersion: () => 0 }, Logging: {}, Memory: {} };
global.navigator = { userAgent: 'node', platform: 'node' };

require('../libs/soullink.bundle.js');

assert.ok(global.Soullink, 'window.Soullink missing');
assert.strictEqual(typeof global.Soullink.createAvatarPerformance, 'function', 'createAvatarPerformance missing');
const create = global.Soullink.createAvatarPerformance;
console.log('bundle OK: window.Soullink.createAvatarPerformance');

function makeHarness({
  modelType = 'live2d',
  motions = [],
  expressions = [],
  parameterMap = {},
  profileSource = 'generated',
  withInjection = true,
} = {}) {
  const manualWrites = [];
  const clears = { count: 0 };
  const nativeCalls = [];
  const vadCalls = [];
  const injection = withInjection ? { applied: [], applyNativeAnimation(directive) { this.applied.push(directive); } } : null;
  const layer = {
    runtime: {
      applyVADTarget(target, amount) { vadCalls.push({ target, amount }); },
      getSnapshot() { return { profile: { parameterMap } }; },
    },
    setManualFACS(facs) { manualWrites.push(facs); },
    clearManualFACS() { clears.count += 1; },
  };
  const perf = create({
    getModelType: () => modelType,
    getLayer: () => (modelType === 'images' ? null : layer),
    getInjection: () => injection,
    getAvailableMotions: () => motions,
    getAvailableExpressions: () => expressions,
    getModelPath: () => (modelType === 'images' ? '__faust_images__' : '2D/yumi/yumi.model3.json'),
    getProfileSource: () => profileSource,
    requestNativeImpl: (request) => { nativeCalls.push(request); return true; },
  });
  return { perf, manualWrites, clears, nativeCalls, vadCalls, injection };
}

function approx(actual, expected, label) {
  assert.ok(Math.abs(actual - expected) < 1e-9, `${label}: ${actual} != ${expected}`);
}

function tap(perf, t, extra = {}) {
  perf.onPointer('down', { t, dx: 0, dy: 0 });
  return perf.onPointer('up', { t: t + 120, dx: 0, dy: 0, hitArea: 'unknown', position: { x: 0.5, y: 0.5 }, ...extra });
}

// ── 能力快照 ──
{
  const h = makeHarness({
    motions: ['Idle', '', '  ', 'wave'],
    expressions: ['星星眼', ''],
    parameterMap: { headX: {}, gazeX: {}, mouthPucker: {}, sweat: {}, notAFacsKey: {}, eyeBlinkL: {} },
  });
  const caps = h.perf.getCapabilities();
  assert.strictEqual(caps.model_type, 'live2d');
  assert.strictEqual(caps.model_path, '2D/yumi/yumi.model3.json');
  assert.strictEqual(caps.profile_source, 'generated');
  // 空 Motions 组名必须过滤：model.motion("") 是无意义请求
  assert.deepStrictEqual(caps.motions, ['Idle', 'wave']);
  assert.deepStrictEqual(caps.expressions, ['星星眼']);
  assert.deepStrictEqual(caps.facs_keys.slice().sort(), ['eyeBlinkL', 'gazeX', 'headX', 'mouthPucker', 'sweat']);
  assert.ok(caps.emotions.includes('happy') && caps.emotions.includes('excited'), 'emotions 应来自引擎情绪预设表');
  assert.ok(typeof caps.at === 'number' && caps.at > 0);

  const images = makeHarness({ modelType: 'images', motions: ['平静', '开心'], profileSource: 'none', parameterMap: { headX: {} } });
  const imageCaps = images.perf.getCapabilities();
  assert.strictEqual(imageCaps.model_type, 'images');
  assert.deepStrictEqual(imageCaps.emotions, ['平静', '开心'], 'images 的情绪即用户配置的图组名');
  assert.deepStrictEqual(imageCaps.expressions, []);
  assert.deepStrictEqual(imageCaps.motions, []);
  assert.deepStrictEqual(imageCaps.facs_keys, [], 'images 无参数层');

  const none = makeHarness({ profileSource: 'none', parameterMap: { headX: {} } });
  assert.deepStrictEqual(none.perf.getCapabilities().facs_keys, [], 'profile_source=none → 无可用 FACS 键');
  console.log('getCapabilities OK: 键集交集 / 空组名过滤 / images 与 none 分支');
}

// ── AVATAR_FACS：manual 层写入 + TTL 释放 ──
{
  fakeNow = 0;
  const h = makeHarness({ parameterMap: { headX: {} } });
  const res = h.perf.applyCommand('AVATAR_FACS', { facs: { headZ: 0.4 }, hold_ms: 1000 });
  assert.strictEqual(res.ok, true);
  assert.deepStrictEqual(h.manualWrites, [{ headZ: 0.4 }]);
  fakeNow = 999;
  h.perf.tick(fakeNow / 1000);
  assert.strictEqual(h.clears.count, 0, '未到期不得释放');
  fakeNow = 1000;
  h.perf.tick(fakeNow / 1000);
  assert.strictEqual(h.clears.count, 1, 'hold_ms 到期应 clearManualFACS');

  // 越界值夹取 + 非法值拒绝
  assert.deepStrictEqual(h.perf.applyCommand('AVATAR_FACS', { facs: { headX: 9 } }).facs, { headX: 1 });
  assert.strictEqual(h.perf.applyCommand('AVATAR_FACS', { facs: { headX: NaN } }).ok, false);
  assert.strictEqual(h.perf.applyCommand('AVATAR_FACS', {}).ok, false);
  assert.strictEqual(h.perf.applyCommand('AVATAR_NOPE', {}).ok, false);
  assert.strictEqual(h.perf.applyCommand('AVATAR_CLEAR', { scope: 'nope' }).ok, false);
  console.log('AVATAR_FACS OK: manual 写入 / TTL 释放 / 越界夹取');
}

// ── 优先级：agent 覆盖 > 交互脉冲 > 引擎 ──
{
  fakeNow = 0;
  const engineDirective = { token: 7, expression: 'f01', motion: null, suppressParamIds: ['ParamX'] };
  const h = makeHarness({ parameterMap: { headX: {} }, motions: ['tear'] });
  assert.strictEqual(h.perf.resolveNativeAnimation(engineDirective), engineDirective, '无覆盖时原样返回引擎 directive');

  h.perf.applyCommand('AVATAR_FACS', { facs: { headZ: 0.4 }, hold_ms: 5000 });
  assert.strictEqual(h.manualWrites.length, 1);

  // 无 tap 组（tear）→ 落到 FACS 脉冲；agentFacs 存活期间不写 manual 层
  const up = tap(h.perf, 100);
  assert.strictEqual(up.tier, 'light');
  assert.strictEqual(up.payload.kind, 'tap');
  assert.strictEqual(h.manualWrites.length, 1, 'agentFacs 存活期间交互脉冲不覆盖');

  // 交互脉冲自身 TTL 到期后 agent 覆盖仍在：manual 层内容不变、不重复下发
  fakeNow = 800;
  h.perf.tick(fakeNow / 1000);
  assert.strictEqual(h.clears.count, 0, 'agentFacs 仍在，不得清空 manual 层');
  assert.strictEqual(h.manualWrites.length, 1);
  assert.deepStrictEqual(h.manualWrites[0], { headZ: 0.4 });

  fakeNow = 5000;
  h.perf.tick(fakeNow / 1000);
  assert.strictEqual(h.clears.count, 1, 'agentFacs 到期应 clearManualFACS');
  console.log('优先级 OK: agentFacs > 交互脉冲 > 引擎');
}

// ── resolveNativeAnimation：稳定 token + 到期交还 ──
{
  fakeNow = 0;
  const engineDirective = { token: 7, expression: 'f01', motion: null, suppressParamIds: [] };
  const h = makeHarness({ motions: ['Idle'] });
  assert.strictEqual(h.perf.requestNative({ expression: '星星眼', motion: null, source: 'tool', holdMs: 1000 }), true);
  const d1 = h.perf.resolveNativeAnimation(engineDirective);
  const d2 = h.perf.resolveNativeAnimation(engineDirective);
  assert.notStrictEqual(d1, engineDirective);
  assert.strictEqual(d1.expression, '星星眼');
  assert.strictEqual(d1.motion, null);
  assert.deepStrictEqual(d1.suppressParamIds, []);
  assert.strictEqual(d1.token, d2.token, '覆盖存活期间 token 必须稳定（inject.js 靠它去重）');
  assert.ok(d1.token > 1e9, 'agent token 必须避开引擎的自增小整数');
  assert.deepStrictEqual(h.nativeCalls, [], '有注入通道时不直连模型');

  fakeNow = 1001;
  h.perf.tick(fakeNow / 1000);
  assert.strictEqual(h.perf.resolveNativeAnimation(engineDirective), engineDirective, '到期后交还引擎 directive');
  console.log('resolveNativeAnimation OK: 稳定 token / 到期交还');
}

// ── requestNative：同槽后写覆盖前写 / interaction 在 agent 槽存活期间被拒 ──
{
  fakeNow = 0;
  const h = makeHarness({ motions: ['Idle', 'Tap'] });
  assert.strictEqual(h.perf.requestNative({ motion: { group: 'Idle' }, source: 'tool', holdMs: 5000 }), true);
  const first = h.perf.resolveNativeAnimation(null);
  assert.strictEqual(h.perf.requestNative({ expression: 'f01', source: 'token', holdMs: 5000 }), true);
  const second = h.perf.resolveNativeAnimation(null);
  assert.strictEqual(first.motion.group, 'Idle');
  assert.strictEqual(second.expression, 'f01');
  assert.strictEqual(second.motion, null, '同槽后写覆盖前写');
  assert.notStrictEqual(first.token, second.token);

  assert.strictEqual(h.perf.requestNative({ motion: { group: 'Tap' }, source: 'interaction', holdMs: 600 }), false,
    'agent 槽存活期间交互不改原生动画');
  assert.strictEqual(h.perf.requestNative({ expression: null, motion: null, source: 'tool', holdMs: 100 }), false,
    '空请求不占槽');

  fakeNow = 5001;
  h.perf.tick(fakeNow / 1000);
  assert.strictEqual(h.perf.resolveNativeAnimation(null), null, 'holdMs 到期后槽释放');

  // 无注入通道（图片模型）→ 立即直连模型
  const images = makeHarness({ modelType: 'images', motions: ['开心'] });
  assert.strictEqual(images.perf.requestNative({ expression: '开心', source: 'tool', holdMs: 1000 }), true);
  assert.deepStrictEqual(images.nativeCalls, [{ expression: '开心', motion: null }]);
  console.log('requestNative OK: 后写覆盖前写 / interaction 拒绝 / 到期释放 / images 直连');
}

// ── 交互分级阈值 ──
{
  fakeNow = 0;
  const h = makeHarness({ withInjection: false });
  const first = tap(h.perf, 0);
  assert.strictEqual(first.tier, 'light');
  assert.strictEqual(first.payload.kind, 'tap');
  assert.strictEqual(first.payload.count, 1);
  assert.strictEqual(first.payload.area, 'unknown');
  assert.strictEqual(tap(h.perf, 300).tier, 'light');
  const third = tap(h.perf, 600);
  assert.strictEqual(third.tier, 'heavy');
  assert.strictEqual(third.payload.kind, 'multi_tap');
  assert.strictEqual(third.payload.count, 3);
  assert.strictEqual(third.payload.summary, '用户连续戳了你 3 下');
  // 触发后窗口清空，第 4 次重新计数
  assert.strictEqual(tap(h.perf, 900).payload.kind, 'tap');

  // 长按 ≥800ms
  h.perf.onPointer('down', { t: 10000, dx: 0, dy: 0 });
  const longPress = h.perf.onPointer('up', { t: 10800, dx: 0, dy: 0, hitArea: 'Head', position: { x: 0.4, y: 0.7 } });
  assert.strictEqual(longPress.tier, 'heavy');
  assert.strictEqual(longPress.payload.kind, 'long_press');
  assert.strictEqual(longPress.payload.area, 'Head');
  assert.strictEqual(longPress.payload.duration_ms, 800);
  assert.deepStrictEqual(longPress.payload.position, { x: 0.4, y: 0.7 });

  // 拖动累计 ≥120px：在 move 时即判定，up 不重复上报
  h.perf.onPointer('down', { t: 20000, dx: 0, dy: 0 });
  const stroke = h.perf.onPointer('move', { t: 20100, dx: 130, dy: 0 });
  assert.strictEqual(stroke.tier, 'heavy');
  assert.strictEqual(stroke.payload.kind, 'stroke');
  assert.strictEqual(stroke.payload.distance_px, 130);
  assert.strictEqual(h.perf.onPointer('up', { t: 20200, dx: 0, dy: 0 }), null, '已上报的拖动不得重复上报');

  // 位移 <6px 且 300~800ms：不判定
  h.perf.onPointer('down', { t: 30000, dx: 0, dy: 0 });
  assert.strictEqual(h.perf.onPointer('up', { t: 30500, dx: 2, dy: 1 }), null);

  // hover → light，无本地反应
  const hover = h.perf.onPointer('hover', { t: 40000, hitArea: 'Body', position: { x: 0.2, y: 0.3 } });
  assert.strictEqual(hover.tier, 'light');
  assert.strictEqual(hover.payload.kind, 'hover');
  assert.strictEqual(hover.payload.duration_ms, 0);
  assert.strictEqual(hover.payload.summary, '用户的鼠标掠过了你');
  console.log('分级 OK: tap/light, multi_tap+long_press+stroke/heavy, hover/light');
}

// ── tap 反应三级回退 ──
{
  fakeNow = 0;
  const exact = makeHarness({ motions: ['Tap', 'Tap@Body'] });
  exact.perf.onPointer('down', { t: 0, dx: 0, dy: 0 });
  const upExact = exact.perf.onPointer('up', { t: 100, dx: 0, dy: 0, hitArea: 'Body', position: { x: 0.5, y: 0.5 } });
  assert.strictEqual(upExact.payload.kind, 'tap');
  assert.strictEqual(exact.perf.resolveNativeAnimation(null).motion.group, 'Tap@Body', '① 命中区精确配对');
  assert.deepStrictEqual(exact.nativeCalls, [], 'live2d + 注入通道 → 不直连模型');

  const anyTap = makeHarness({ motions: ['Tap'] });
  anyTap.perf.onPointer('down', { t: 0, dx: 0, dy: 0 });
  anyTap.perf.onPointer('up', { t: 100, dx: 0, dy: 0, hitArea: 'unknown', position: { x: 0.5, y: 0.5 } });
  assert.strictEqual(anyTap.perf.resolveNativeAnimation(null).motion.group, 'Tap', '② 任意含 tap 的组');

  // hitArea 允许惰性函数（app.js 只在判定成立时才做 hitTest）
  const lazy = makeHarness({ motions: ['Tap@Body'] });
  let hitAreaCalls = 0;
  lazy.perf.onPointer('down', { t: 0, dx: 0, dy: 0, hitArea: () => { hitAreaCalls += 1; return 'Body'; } });
  assert.strictEqual(hitAreaCalls, 0, 'down 阶段不得求值 hitArea');
  lazy.perf.onPointer('up', { t: 100, dx: 0, dy: 0, hitArea: () => { hitAreaCalls += 1; return 'Body'; }, position: { x: 0.5, y: 0.5 } });
  assert.strictEqual(hitAreaCalls, 1, '判定成立时才求值 hitArea');
  assert.strictEqual(lazy.perf.resolveNativeAnimation(null).motion.group, 'Tap@Body');

  const pulse = makeHarness({ motions: ['tear'] });
  pulse.perf.onPointer('down', { t: 0, dx: 0, dy: 0 });
  pulse.perf.onPointer('up', { t: 100, dx: 0, dy: 0, hitArea: 'unknown', position: { x: 0.5, y: 0.5 } });
  assert.strictEqual(pulse.perf.resolveNativeAnimation(null), null, '③ 无 tap 组 → 不占原生槽');
  assert.deepStrictEqual(pulse.manualWrites, [{ browInnerUp: 0.25, headY: -0.08 }], '③ FACS 脉冲');

  // stroke 脉冲方向随拖动
  const stroke = makeHarness({ motions: [] });
  stroke.perf.onPointer('down', { t: 0, dx: 0, dy: 0 });
  stroke.perf.onPointer('move', { t: 100, dx: -130, dy: 0 });
  assert.deepStrictEqual(stroke.manualWrites, [{ headZ: 0.12, gazeX: -0.3 }]);

  // 图片模型：只换图
  const images = makeHarness({ modelType: 'images', motions: ['开心'] });
  images.perf.onPointer('down', { t: 0, dx: 0, dy: 0 });
  images.perf.onPointer('up', { t: 100, dx: 0, dy: 0, hitArea: 'unknown', position: { x: 0.5, y: 0.5 } });
  assert.deepStrictEqual(images.nativeCalls, [{ tap: true }], '图片模型 tap → triggerTap');
  assert.deepStrictEqual(images.manualWrites, [], '图片模型无参数层');
  console.log('tap 三级回退 OK: Tap@Body / Tap / FACS 脉冲 + 图片模型换图');
}

// ── AVATAR_MOTION / AVATAR_EXPRESSION / AVATAR_EMOTION / AVATAR_CLEAR ──
{
  fakeNow = 0;
  const h = makeHarness({ motions: ['Idle'] });
  assert.strictEqual(h.perf.applyCommand('AVATAR_MOTION', { name: 'Idle', hold_ms: 2000 }).ok, true);
  assert.strictEqual(h.perf.resolveNativeAnimation(null).motion.group, 'Idle');
  assert.strictEqual(h.perf.applyCommand('AVATAR_MOTION', { name: '   ' }).ok, false);
  assert.deepStrictEqual(h.nativeCalls, []);

  assert.strictEqual(h.perf.applyCommand('AVATAR_EXPRESSION', { name: '星星眼' }).ok, true);
  assert.strictEqual(h.perf.resolveNativeAnimation(null).expression, '星星眼');

  // scope=native 只放原生槽，FACS 覆盖保留
  h.perf.applyCommand('AVATAR_FACS', { facs: { headZ: 0.4 }, hold_ms: 5000 });
  assert.strictEqual(h.perf.applyCommand('AVATAR_CLEAR', { scope: 'native' }).ok, true);
  assert.strictEqual(h.perf.resolveNativeAnimation(null), null);
  assert.strictEqual(h.clears.count, 0, 'scope=native 不应清掉 FACS 覆盖');
  assert.deepStrictEqual(h.injection.applied, [null], 'scope=native 立即交还引擎');

  // clearAll 立即交还引擎（注入通道收到 null）
  h.injection.applied.length = 0;
  h.perf.requestNative({ expression: 'f01', source: 'tool', holdMs: 5000 });
  h.perf.clearAll();
  assert.strictEqual(h.perf.resolveNativeAnimation(null), null);
  assert.deepStrictEqual(h.injection.applied, [null]);
  assert.strictEqual(h.clears.count, 1);

  // 情绪：语义名 → VAD 目标
  const emotion = makeHarness({});
  assert.strictEqual(emotion.perf.applyCommand('AVATAR_EMOTION', { emotion: 'excited', intensity: 1 }).ok, true);
  assert.strictEqual(emotion.vadCalls.length, 1);
  assert.strictEqual(emotion.vadCalls[0].amount, 0.65);
  approx(emotion.vadCalls[0].target.valence, 0.85, 'excited.valence');
  approx(emotion.vadCalls[0].target.arousal, 0.85, 'excited.arousal');
  approx(emotion.vadCalls[0].target.dominance, 0.45, 'excited.dominance');
  assert.strictEqual(emotion.perf.applyCommand('AVATAR_EMOTION', { emotion: '不存在' }).ok, false);
  assert.strictEqual(emotion.perf.applyCommand('AVATAR_EMOTION', { emotion: 'happy', intensity: 5 }).intensity, 1, 'intensity 夹到 0~1');
  console.log('命令 OK: AVATAR_MOTION/EXPRESSION/CLEAR/EMOTION');
}

// ── 连续情绪漂移（不节流） ──
{
  const h = makeHarness({});
  const drift = h.perf.applyEmotionDrift('joy', 8);
  assert.strictEqual(drift.ok, true);
  assert.strictEqual(drift.emotion, 'excited', 'joy@0.8 应映射到 excited');
  assert.strictEqual(h.vadCalls.length, 1);
  assert.strictEqual(h.vadCalls[0].amount, 0.4, '漂移强度固定 0.4，不节流');
  approx(h.vadCalls[0].target.arousal, 0.68, 'arousal 按强度缩放');
  h.perf.applyEmotionDrift('joy', 8);
  assert.strictEqual(h.vadCalls.length, 2, '连续漂移不得节流');
  assert.strictEqual(h.perf.applyEmotionDrift('boredom', 6).emotion, 'tired');

  const noLayer = makeHarness({ modelType: 'images', motions: ['平静'] });
  assert.strictEqual(noLayer.perf.applyEmotionDrift('joy', 8).ok, false, '无表演层时明确失败');
  console.log('applyEmotionDrift OK: VAD 目标 + 不节流');
}

console.log('SMOKE_OK');
