import { ADVANCED_KEYS, LIVE2D_KEYS } from '@/config/constants';
import type { ConfigDict } from '@/config/types';

/**
 * 「模型」模块的纯逻辑：字段过滤、模型列表筛选、Images 配置摘要与路径解析。
 * 全部与旧 `libs/configer/field-render.js`（pickModuleFields / renderConfigModule /
 * openSoullinkProfileEditor）逐条对齐，便于单测覆盖。
 */

export type ModelType = 'live2d' | 'vrm' | 'images';

/** 模型类型切换按钮的顺序（旧实现同序）。 */
export const MODEL_TYPE_OPTIONS: ModelType[] = ['live2d', 'vrm', 'images'];

/** 与旧实现一致：缺省回落到 live2d；其它取值原样小写后参与显示与列表筛选。 */
export function normalizeModelType(value: unknown): string {
  return String(value || 'live2d').toLowerCase();
}

/** VRM 下隐藏的 Live2D 专属字段。 */
const VRM_HIDDEN_KEYS = new Set([
  'LIVE2D_MODEL_PATH',
  'LIVE2D_MODEL_X',
  'LIVE2D_MODEL_Y',
  'IMAGE_MODEL_CONFIG',
]);

/** Images 下隐藏的 2D/3D 路径与原始 JSON（由 Images 弹窗接管）。 */
const IMAGES_HIDDEN_KEYS = new Set([
  'LIVE2D_MODEL_PATH',
  'VRM_MODEL_PATH',
  'IMAGE_MODEL_CONFIG',
]);

export interface ModelFieldKeys {
  publicKeys: string[];
  privateKeys: string[];
}

/** 旧 pickModuleFields 的 live2d 分支：顺序沿用 LIVE2D_KEYS，只保留配置里真实存在的键。 */
export function pickModelFieldKeys(publicConfig: ConfigDict): ModelFieldKeys {
  const available = new Set(Object.keys(publicConfig || {}));
  const modelType = normalizeModelType(publicConfig?.MODEL_TYPE);
  if (modelType === 'vrm') {
    return {
      publicKeys: LIVE2D_KEYS.filter((key) => available.has(key) && !VRM_HIDDEN_KEYS.has(key)),
      privateKeys: [],
    };
  }
  if (modelType === 'images') {
    return {
      publicKeys: LIVE2D_KEYS.filter((key) => available.has(key) && !IMAGES_HIDDEN_KEYS.has(key)),
      privateKeys: [],
    };
  }
  return {
    publicKeys: LIVE2D_KEYS.filter((key) => available.has(key) && key !== 'IMAGE_MODEL_CONFIG'),
    privateKeys: [],
  };
}

/** 基础配置 / 高级配置拆分；顺序保持输入顺序。 */
export function splitAdvancedKeys(keys: string[]): { basic: string[]; advanced: string[] } {
  const basic: string[] = [];
  const advanced: string[] = [];
  for (const key of keys) {
    if (ADVANCED_KEYS.has(key)) advanced.push(key);
    else basic.push(key);
  }
  return { basic, advanced };
}

export interface MouthShapeEntry {
  path?: string;
  openness?: number;
}

export interface EmotionEntry {
  name?: string;
  images?: string[];
}

export interface ImagesModelConfig {
  baseImages?: string[];
  emotions?: EmotionEntry[];
  tapImages?: string[];
  mouthShapes?: MouthShapeEntry[];
  scale?: number;
  motionDurationMs?: number;
  tapDurationMs?: number;
  [key: string]: unknown;
}

/** 旧实现打开弹窗时的兜底默认值（仅在配置缺失时使用）。 */
export function imagesConfigDefaults(): ImagesModelConfig {
  return {
    baseImages: [],
    emotions: [],
    tapImages: [],
    mouthShapes: [],
    motionDurationMs: 3000,
    tapDurationMs: 700,
  };
}

/** 与旧实现相同的 JSON 深拷贝；配置缺失时回落到默认结构。 */
export function cloneImagesConfig(raw: unknown): ImagesModelConfig {
  const source = raw && typeof raw === 'object' ? raw : imagesConfigDefaults();
  return JSON.parse(JSON.stringify(source)) as ImagesModelConfig;
}

function arrayCount(value: unknown): number {
  return Array.isArray(value) ? value.length : 0;
}

/** 「可用模型」卡片里的 Images 摘要文案。 */
export function summarizeImagesConfig(raw: unknown): string {
  const cfg = (raw && typeof raw === 'object' ? raw : {}) as ImagesModelConfig;
  return `默认图 ${arrayCount(cfg.baseImages)} 张 | 情绪 ${arrayCount(cfg.emotions)} 组 | Tap 图 ${arrayCount(cfg.tapImages)} 张 | 嘴型图 ${arrayCount(cfg.mouthShapes)} 张`;
}

export interface ModelListEntry {
  type?: string;
  label?: string;
  path?: string;
}

/** 按当前模型类型筛选可用模型（旧实现用原始小写类型的严格相等）。 */
export function filterModelsByType(
  models: ModelListEntry[],
  modelType: string,
): ModelListEntry[] {
  const list = Array.isArray(models) ? models : [];
  return list.filter((item) => item.type === modelType);
}

/** 「使用」按钮写入的配置键。 */
export function pickModelConfigKey(modelType: string): string {
  return modelType === 'vrm' ? 'VRM_MODEL_PATH' : 'LIVE2D_MODEL_PATH';
}

/** 模型路径统一成正斜杠（旧实现相同）。 */
export function normalizeModelPath(modelPath: unknown): string {
  return String(modelPath ?? '').replace(/\\/g, '/');
}

/**
 * 从模型路径解析 soullink profile 的模型目录：
 * 取 `2D` 之后的目录名，没有 `2D` 段时取第一段；非法（非字母数字_-）返回空串。
 */
export function resolveModelDir(modelPath: unknown): string {
  const rawPath = normalizeModelPath(modelPath);
  const segments = rawPath.split('/').filter(Boolean);
  const index2d = segments.indexOf('2D');
  const modelDir = (index2d >= 0 ? segments[index2d + 1] : segments[0]) || '';
  return /^[A-Za-z0-9_-]+$/.test(modelDir) ? modelDir : '';
}

export interface ProfileResultLike {
  cached?: boolean;
  coverage?: { mappedKeyCount?: number; facsKeyCount?: number };
}

/** 参数映射弹窗顶部的覆盖率提示。 */
export function formatProfileStatus(result: ProfileResultLike | null | undefined): string {
  if (!result) return '';
  let text = result.cached ? '（已缓存）' : '（已生成）';
  const coverage = result.coverage;
  if (
    coverage
    && typeof coverage.mappedKeyCount === 'number'
    && typeof coverage.facsKeyCount === 'number'
  ) {
    text += ` | 标准参数覆盖率: ${coverage.mappedKeyCount}/${coverage.facsKeyCount}`;
  }
  return text;
}
