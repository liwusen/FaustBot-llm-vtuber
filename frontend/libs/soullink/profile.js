// profile 加载/校验/回退 — 优先主进程生成的 soullink.profile.json
// 关键保障：即使生成/校验失败，也使用标准参数映射回退，保证生命力（呼吸/眨眼/微动）始终可用
import { migrateProfile, validateModelProfile } from '@soullink-emotion/engine/internal';

const EMPTY_CAPABILITIES = Object.freeze({
  headControl: false,
  bodyControl: false,
  eyeBlink: false,
  eyeSmile: false,
  gazeControl: false,
  mouthOpen: false,
  mouthSmile: false,
  browControl: false,
  blush: false,
  tear: false,
  sweat: false,
  breath: false,
});

// 标准 Cubism 参数映射（参照 profile-generator 的 canonical reference；参数不存在时 core 忽略，无副作用）
export const STANDARD_FALLBACK_MAP = Object.freeze({
  eyeOpen: { targets: ['ParamEyeLOpen', 'ParamEyeROpen'], mode: 'set', scale: 1, min: 0, max: 1.2 },
  eyeBlinkL: { target: 'ParamEyeLOpen', mode: 'subtract', scale: 1, min: 0, max: 1.2 },
  eyeBlinkR: { target: 'ParamEyeROpen', mode: 'subtract', scale: 1, min: 0, max: 1.2 },
  eyeSquint: { targets: ['ParamEyeLOpen', 'ParamEyeROpen'], mode: 'subtract', scale: 0.22, min: 0, max: 1.2 },
  gazeX: { target: 'ParamEyeBallX', mode: 'set', scale: 1, min: -1, max: 1 },
  gazeY: { target: 'ParamEyeBallY', mode: 'set', scale: 1, min: -1, max: 1 },
  headX: { target: 'ParamAngleX', mode: 'set', scale: 30, min: -30, max: 30 },
  headY: { target: 'ParamAngleY', mode: 'set', scale: 30, min: -30, max: 30 },
  headZ: { target: 'ParamAngleZ', mode: 'set', scale: 30, min: -30, max: 30 },
  bodyX: { target: 'ParamBodyAngleX', mode: 'set', scale: 12, min: -12, max: 12 },
  bodyY: { target: 'ParamBodyAngleY', mode: 'set', scale: 12, min: -12, max: 12 },
  bodyZ: { target: 'ParamBodyAngleZ', mode: 'set', scale: 12, min: -12, max: 12 },
  mouthSmile: { target: 'ParamMouthForm', mode: 'set', scale: 1, min: -1, max: 1 },
  mouthFrown: { target: 'ParamMouthForm', mode: 'subtract', scale: 1, min: -1, max: 1 },
  mouthOpen: { target: 'ParamMouthOpenY', mode: 'set', scale: 1, min: 0, max: 1 },
  browInnerUp: { targets: ['ParamBrowLY', 'ParamBrowRY'], mode: 'set', scale: 1, min: -1, max: 1 },
  browOuterUp: { targets: ['ParamBrowLAngle', 'ParamBrowRAngle'], mode: 'set', scale: 0.9, min: -1, max: 1 },
  browDown: { targets: ['ParamBrowLForm', 'ParamBrowRForm'], mode: 'set', scale: -0.85, min: -1, max: 1 },
  blush: { target: 'ParamCheek', mode: 'set', scale: 1, min: 0, max: 1 },
  breath: { target: 'ParamBreath', mode: 'set', scale: 1, min: 0, max: 1 },
});

// 为标准模型补齐缺失的标准键映射（参数不存在的模型由 core 忽略）
export function withStandardFallbackMappings(profile) {
  const map = { ...((profile && profile.parameterMap) || {}) };
  let added = 0;
  for (const [key, rule] of Object.entries(STANDARD_FALLBACK_MAP)) {
    if (map[key] === undefined) {
      map[key] = rule;
      added += 1;
    }
  }
  return { profile: { ...(profile || {}), parameterMap: map }, added };
}

// 通过 IPC 触发主进程生成/读取（Electron）；浏览器环境返回 null
export async function fetchProfileViaIpc(modelDir) {
  if (!window.api || typeof window.api.ensureModelProfile !== 'function') {
    console.warn('[soullink] window.api.ensureModelProfile 不可用（preload 未更新？）');
    return null;
  }
  const startedAt = Date.now();
  console.info('[soullink:ipc] ensureModelProfile 请求 modelDir=' + String(modelDir));
  try {
    const result = await window.api.ensureModelProfile(String(modelDir || ''));
    const cost = Date.now() - startedAt;
    if (result && result.ok && result.profile) {
      console.info('[soullink:ipc] 成功 (' + cost + 'ms): cached=' + !!result.cached + ' parameterMap keys=' + Object.keys(result.profile.parameterMap || {}).length);
      return result.profile;
    }
    console.warn('[soullink:ipc] 失败 (' + cost + 'ms):', result);
    return null;
  } catch (e) {
    console.warn('[soullink:ipc] 异常 (' + (Date.now() - startedAt) + 'ms):', e);
    return null;
  }
}

// 校验（含 schema 迁移）；返回 { ok, profile?, errors?, warnings? }
export function validateProfileObject(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, errors: ['profile 必须是 JSON 对象'] };
  }
  try {
    const migrated = migrateProfile(raw);
    const result = validateModelProfile(migrated.profile);
    if (result.ok) {
      return { ok: true, profile: result.profile, warnings: result.warnings || [] };
    }
    return { ok: false, errors: result.errors || ['profile 校验失败'] };
  } catch (e) {
    return { ok: false, errors: [String(e && e.message ? e.message : e)] };
  }
}

// 回退 profile：含标准参数映射，保证呼吸/眨眼/注视/头部/身体微动/口型在无生成 profile 时仍工作
export function deriveFallbackProfile(modelId) {
  const id = String(modelId || 'fallback');
  return {
    modelId: id,
    displayName: id,
    version: '1.0.0',
    modelPath: id,
    schemaVersion: 2,
    capabilities: { ...EMPTY_CAPABILITIES },
    parameterMap: { ...STANDARD_FALLBACK_MAP },
    idleConfig: {
      breath: [0.5, 1.0],
      eyeOpen: [0.9, 1.0],
      gazeX: [-0.15, 0.15],
      gazeY: [-0.1, 0.1],
      headX: [-3, 3],
      headY: [-3, 3],
      bodyX: [-1.5, 1.5],
      bodyY: [-1, 1],
    },
    neutralParams: {},
    parameterSmoothing: {},
  };
}
