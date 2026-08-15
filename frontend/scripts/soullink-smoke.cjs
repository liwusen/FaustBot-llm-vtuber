// 冒烟：加载 pixi-live2d.bundle.js + soullink.bundle.js（node 环境，document/Live2DCubismCore 打桩）
const assert = require('assert');

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
if (!global.performance) global.performance = { now: () => Date.now() };

require('../libs/pixi-live2d.bundle.js');
require('../libs/soullink.bundle.js');

assert.ok(global.PIXI, 'window.PIXI missing');
assert.strictEqual(typeof global.PIXI.Application, 'function', 'PIXI.Application is not a function (v7 expected)');
assert.ok(global.PIXI.live2d && global.PIXI.live2d.Live2DModel, 'PIXI.live2d.Live2DModel missing');
assert.ok(global.PIXI.live2d.HitAreaFrames, 'PIXI.live2d.HitAreaFrames missing');
assert.ok(global.Soullink, 'window.Soullink missing');
console.log('globals OK: PIXI v7 Application, PIXI.live2d.{Live2DModel,HitAreaFrames}, window.Soullink');

const profile = {
  modelId: 'smoke', displayName: 'Smoke', version: '1.0.0', modelPath: 'smoke', schemaVersion: 2,
  capabilities: {
    headControl: true, bodyControl: true, eyeBlink: true, eyeSmile: true, gazeControl: true,
    mouthOpen: true, mouthSmile: true, browControl: true, blush: false, tear: false, sweat: false, breath: true,
  },
  parameterMap: {
    eyeOpen: { targets: ['ParamEyeLOpen', 'ParamEyeROpen'], mode: 'set', scale: 1, min: 0, max: 1.2 },
    eyeBlinkL: { target: 'ParamEyeLOpen', mode: 'subtract', scale: 1, min: 0, max: 1.2 },
    eyeBlinkR: { target: 'ParamEyeROpen', mode: 'subtract', scale: 1, min: 0, max: 1.2 },
    gazeX: { target: 'ParamEyeBallX', mode: 'set', scale: 1, min: -1, max: 1 },
    headX: { target: 'ParamAngleX', mode: 'set', scale: 30, min: -30, max: 30 },
    mouthSmile: { target: 'ParamMouthForm', mode: 'set', scale: 1, min: -1, max: 1 },
    mouthOpen: { target: 'ParamMouthOpenY', mode: 'set', scale: 1, min: 0, max: 1 },
    breath: { target: 'ParamBreath', mode: 'set', scale: 1, min: 0, max: 1 },
  },
  idleConfig: { breath: [0.5, 1.0], eyeOpen: [0.9, 1.0] },
  neutralParams: { ParamEyeLOpen: 1, ParamEyeROpen: 1, ParamBreath: 0.5 },
  parameterSmoothing: {},
};

// validateProfileObject
const v = global.Soullink.validateProfile(profile);
assert.ok(v.ok, 'validateProfile failed: ' + JSON.stringify(v.errors));
console.log('validateProfile OK');

// createLayer + update → live2dParams
const layer = global.Soullink.createLayer({ profile, motionStyle: { seed: 7 } });
let t = 0;
for (let i = 0; i < 30; i++) { t += 1 / 60; layer.update(t, 1 / 60); }
const params = layer.getParams();
assert.ok(params && typeof params === 'object', 'getParams missing');
assert.ok(Object.keys(params).length > 0, 'live2dParams empty');
console.log('createLayer/update OK, live2dParams keys:', Object.keys(params).length);

// triggerIntent 表情时间线
layer.triggerIntent({ emotion: 'happy', intensity: 0.8, contextTags: [] }, t);
const snap = layer.update(t + 0.2, 0.2);
console.log('after intent state:', snap.state, '| dominant:', snap.vad.dominantEmotion);

// bridge 映射
const intent = global.Soullink.mapEmotionToIntent('joy', 8);
assert.strictEqual(intent.emotion, 'excited', 'joy@0.8 should map to excited, got ' + intent.emotion);
const intent2 = global.Soullink.mapEmotionToIntent('boredom', 6);
assert.strictEqual(intent2.emotion, 'tired');
const intent3 = global.Soullink.mapEmotionToIntent('curiosity', 4);
assert.strictEqual(intent3.emotion, 'curious');
console.log('bridge OK: joy@8→excited, boredom@6→tired, curiosity@4→curious');

// audio-level analyzer（无真实 analyser → null 兼容）
assert.strictEqual(global.Soullink.createAudioLevelAnalyzer(null), null);
console.log('SMOKE_OK');

