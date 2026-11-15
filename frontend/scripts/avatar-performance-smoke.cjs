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

  // 来回抚摸：move 阶段即时判定，up 不重复上报
  h.perf.onPointer('down', { t: 20000, dx: 0, dy: 0, buttons: 1 });
  const strokeMoves = [
    { t: 20060, dx: 60, dy: 0 },
    { t: 20120, dx: 60, dy: 0 },
    { t: 20180, dx: -60, dy: 0 },
    { t: 20240, dx: -60, dy: 0 },
    { t: 20300, dx: 60, dy: 0 },
  ];
  let stroke = null;
  for (const step of strokeMoves) {
    const judged = h.perf.onPointer('move', { ...step, buttons: 1 });
    if (judged) stroke = judged;
  }
  assert.strictEqual(stroke.tier, 'heavy');
  assert.strictEqual(stroke.payload.kind, 'stroke');
  assert.strictEqual(stroke.payload.distance_px, 300);
  assert.strictEqual(stroke.payload.excursion_px, 120);
  assert.strictEqual(stroke.payload.reversals, 2);
  assert.strictEqual(h.perf.onPointer('up', { t: 20400, dx: 0, dy: 0, buttons: 0 }), null, '已上报的抚摸不得重复上报');

  // 位移 <6px 且 300~800ms：不判定
  h.perf.onPointer('down', { t: 30000, dx: 0, dy: 0, buttons: 1 });
  assert.strictEqual(h.perf.onPointer('up', { t: 30500, dx: 2, dy: 1, buttons: 0 }), null);

  // hover → 必须驻留 ≥600ms（掠过包围盒不算），且有 10s 冷却
  fakeNow = 0;
  assert.strictEqual(h.perf.onPointer('hover', { t: 0, hitArea: 'Body', position: { x: 0.2, y: 0.3 } }), null,
    '进入瞬间不算交互');
  fakeNow = 599;
  assert.strictEqual(h.perf.tick(fakeNow / 1000), null, '驻留不足不得上报');
  fakeNow = 600;
  const hover = h.perf.tick(fakeNow / 1000);
  assert.strictEqual(hover.tier, 'light');
  assert.strictEqual(hover.payload.kind, 'hover');
  assert.strictEqual(hover.payload.duration_ms, 0);
  assert.strictEqual(hover.payload.summary, '用户盯着你看了好一会');
  // 冷却窗内再次驻留：不重复上报
  h.perf.onPointer('hover', { t: 0, hitArea: 'Body' });
  fakeNow = 2000;
  assert.strictEqual(h.perf.tick(fakeNow / 1000), null, 'hover 冷却窗内不得重复上报');
  // 冷却结束后恢复
  h.perf.onPointer('hover', { t: 0, hitArea: 'Body' });
  fakeNow = 11700;
  assert.ok(h.perf.tick(fakeNow / 1000), '冷却结束后可再次上报');
  // hoverend / 按压都会取消驻留
  h.perf.onPointer('hover', { t: 0, hitArea: 'Body' });
  h.perf.onPointer('hoverend', { t: 0 });
  fakeNow = 30000;
  assert.strictEqual(h.perf.tick(fakeNow / 1000), null, 'hoverend 后不得上报');
  h.perf.onPointer('hover', { t: 0, hitArea: 'Body' });
  h.perf.onPointer('down', { t: 0, dx: 0, dy: 0, buttons: 1 });
  fakeNow = 40000;
  assert.strictEqual(h.perf.tick(fakeNow / 1000), null, '按压期间不得上报 hover');
  console.log('分级 OK: tap/light, multi_tap+long_press+stroke/heavy, hover 需驻留+冷却');
}

// ── 交互误报回归：拖动 / 手抖 / up 丢失 / 连发 ──
{
  // ① 慢起手拖动（搬模型）：第一帧只有 3px，过去会被判成 tap 并吞掉整次拖动
  {
    const h = makeHarness({ motions: ['Idle'] });
    const results = [
      h.perf.onPointer('down', { t: 0, dx: 0, dy: 0, buttons: 1 }),
      h.perf.onPointer('move', { t: 16, dx: 3, dy: 2, buttons: 1 }),
      h.perf.onPointer('move', { t: 32, dx: 8, dy: 2, buttons: 1 }),
      h.perf.onPointer('move', { t: 48, dx: 40, dy: 0, buttons: 1 }),
      h.perf.onPointer('move', { t: 64, dx: 60, dy: 0, buttons: 1 }),
      h.perf.onPointer('up', { t: 200, dx: 0, dy: 0, buttons: 0 }),
    ];
    assert.deepStrictEqual(results.filter(Boolean), [], '单向拖动（搬模型）不得上报任何交互');
  }
  // ② 快起手单向拖动 130px：过去直接判成 stroke（heavy → 打断 agent）
  {
    const h = makeHarness({ motions: ['Idle'] });
    h.perf.onPointer('down', { t: 0, dx: 0, dy: 0, buttons: 1 });
    assert.strictEqual(h.perf.onPointer('move', { t: 16, dx: 30, dy: 0, buttons: 1 }), null);
    assert.strictEqual(h.perf.onPointer('move', { t: 32, dx: 50, dy: 0, buttons: 1 }), null);
    assert.strictEqual(h.perf.onPointer('move', { t: 48, dx: 50, dy: 0, buttons: 1 }), null);
    assert.strictEqual(h.perf.onPointer('up', { t: 120, dx: 0, dy: 0, buttons: 0 }), null, '130px 单向拖动不是抚摸');
  }
  // ③ 长按 + 手抖 4px → long_press（不是 stroke）
  {
    const h = makeHarness({});
    h.perf.onPointer('down', { t: 0, dx: 0, dy: 0, buttons: 1 });
    assert.strictEqual(h.perf.onPointer('move', { t: 100, dx: 3, dy: 1, buttons: 1 }), null);
    const longPress = h.perf.onPointer('up', { t: 1500, dx: 0, dy: 0, buttons: 0 });
    assert.strictEqual(longPress.payload.kind, 'long_press');
  }
  // ④ 长按 + 手抖 11px：过去判成 stroke（heavy），现在不判定（宁可不报）
  {
    const h = makeHarness({});
    h.perf.onPointer('down', { t: 0, dx: 0, dy: 0, buttons: 1 });
    h.perf.onPointer('move', { t: 100, dx: 10, dy: 0, buttons: 1 });
    h.perf.onPointer('move', { t: 1500, dx: 1, dy: 1, buttons: 1 });
    assert.strictEqual(h.perf.onPointer('up', { t: 2000, dx: 0, dy: 0, buttons: 0 }), null, '11px 抖动不得判成 stroke');
  }
  // ⑤ up 丢失（窗口外松手）：move 的 buttons === 0 直接丢弃脏状态
  {
    const h = makeHarness({});
    h.perf.onPointer('down', { t: 0, dx: 0, dy: 0, buttons: 1 });
    assert.strictEqual(h.perf.onPointer('move', { t: 16, dx: 10, dy: 0, buttons: 1 }), null);
    assert.strictEqual(h.perf.onPointer('move', { t: 9000, dx: 1, dy: 0, buttons: 0 }), null, 'up 丢失后不得判成 stroke');
    assert.strictEqual(h.perf.onPointer('move', { t: 9016, dx: 2, dy: 0, buttons: 0 }), null);
    assert.strictEqual(h.perf.onPointer('up', { t: 9100, dx: 0, dy: 0, buttons: 0 }), null);
  }
  // ⑥ 没有 buttons 信息时用按住时长兜底
  {
    const h = makeHarness({});
    h.perf.onPointer('down', { t: 0, dx: 0, dy: 0 });
    assert.strictEqual(h.perf.onPointer('move', { t: 40000, dx: 1, dy: 0 }), null, '按住超时视为 up 丢失');
  }
  // ⑦ 1.5s 内三次重定位：过去第三次变成 multi_tap（heavy → 打断 agent）
  {
    const h = makeHarness({ motions: ['Idle'] });
    const results = [];
    for (let i = 0; i < 3; i += 1) {
      const base = i * 500;
      results.push(h.perf.onPointer('down', { t: base, dx: 0, dy: 0, buttons: 1 }));
      results.push(h.perf.onPointer('move', { t: base + 16, dx: 3, dy: 1, buttons: 1 }));
      results.push(h.perf.onPointer('move', { t: base + 32, dx: 50, dy: 0, buttons: 1 }));
      results.push(h.perf.onPointer('up', { t: base + 300, dx: 0, dy: 0, buttons: 0 }));
    }
    assert.deepStrictEqual(results.filter(Boolean), [], '连续搬模型不得凑出 multi_tap');
  }
  // ⑧ 真实三连击仍然成立（release-only 不影响 multi_tap 聚合）
  {
    const h = makeHarness({ motions: ['Idle'] });
    assert.strictEqual(tap(h.perf, 0).payload.kind, 'tap');
    assert.strictEqual(tap(h.perf, 300).payload.kind, 'tap');
    const third = tap(h.perf, 600);
    assert.strictEqual(third.payload.kind, 'multi_tap');
    assert.strictEqual(third.tier, 'heavy');
  }
  // ⑨ clearAll 清空多击历史：换模型后不继承上一份模型的连击
  {
    const h = makeHarness({ motions: ['Idle'] });
    tap(h.perf, 0);
    tap(h.perf, 300);
    h.perf.clearAll();
    assert.strictEqual(tap(h.perf, 600).payload.kind, 'tap', 'clearAll 后连击重新计数');
  }
  console.log('误报回归 OK: 拖动不上报 / 手抖不判 stroke / up 丢失自愈 / 连发不凑 multi_tap');
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

  // stroke 脉冲方向随拖动（净位移向左 → 往左偏头/看）
  const stroke = makeHarness({ motions: [] });
  stroke.perf.onPointer('down', { t: 0, dx: 0, dy: 0, buttons: 1 });
  for (const step of [{ t: 60, dx: -60 }, { t: 120, dx: -60 }, { t: 180, dx: 60 }, { t: 240, dx: 60 }, { t: 300, dx: -60 }]) {
    stroke.perf.onPointer('move', { t: step.t, dx: step.dx, dy: 0, buttons: 1 });
  }
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
