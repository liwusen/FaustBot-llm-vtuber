import { describe, expect, it } from 'vitest';
import { LIVE2D_KEYS } from '@/config/constants';
import {
  cloneImagesConfig,
  filterModelsByType,
  formatProfileStatus,
  normalizeModelPath,
  normalizeModelType,
  pickModelConfigKey,
  pickModelFieldKeys,
  resolveModelDir,
  splitAdvancedKeys,
  summarizeImagesConfig,
} from './filters';

function configWith(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const config: Record<string, unknown> = {};
  for (const key of LIVE2D_KEYS) config[key] = '';
  config.MODEL_TYPE = 'live2d';
  config.LIVE2D_MODEL_SCALE = 1;
  return { ...config, ...overrides };
}

describe('模型类型字段过滤（pickModelFieldKeys）', () => {
  it('live2d：保留 LIVE2D_KEYS 顺序，只隐藏 IMAGE_MODEL_CONFIG', () => {
    const keys = pickModelFieldKeys(configWith({ IMAGE_MODEL_CONFIG: { baseImages: [] } }));
    expect(keys.privateKeys).toEqual([]);
    expect(keys.publicKeys).toEqual(LIVE2D_KEYS.filter((key) => key !== 'IMAGE_MODEL_CONFIG'));
  });

  it('vrm：隐藏 Live2D 路径 / X / Y 与 Images 配置', () => {
    const keys = pickModelFieldKeys(configWith({
      MODEL_TYPE: 'VRM',
      IMAGE_MODEL_CONFIG: { baseImages: [] },
      LIVE2D_MODEL_X: 0.5,
      LIVE2D_MODEL_Y: 0.8,
    }));
    expect(keys.publicKeys).toContain('MODEL_TYPE');
    expect(keys.publicKeys).toContain('VRM_MODEL_PATH');
    expect(keys.publicKeys).toContain('LIVE2D_MODEL_SCALE');
    expect(keys.publicKeys).not.toContain('LIVE2D_MODEL_PATH');
    expect(keys.publicKeys).not.toContain('LIVE2D_MODEL_X');
    expect(keys.publicKeys).not.toContain('LIVE2D_MODEL_Y');
    expect(keys.publicKeys).not.toContain('IMAGE_MODEL_CONFIG');
  });

  it('images：隐藏 Live2D/VRM 路径与 Images 原始配置', () => {
    const keys = pickModelFieldKeys(configWith({
      MODEL_TYPE: 'images',
      IMAGE_MODEL_CONFIG: { baseImages: [] },
    }));
    expect(keys.publicKeys).toContain('MODEL_TYPE');
    expect(keys.publicKeys).not.toContain('LIVE2D_MODEL_PATH');
    expect(keys.publicKeys).not.toContain('VRM_MODEL_PATH');
    expect(keys.publicKeys).not.toContain('IMAGE_MODEL_CONFIG');
  });

  it('images：与旧实现一致，仍保留 X / Y 与其它通用键', () => {
    const keys = pickModelFieldKeys(configWith({
      MODEL_TYPE: 'images',
      LIVE2D_MODEL_X: 0.5,
      LIVE2D_MODEL_Y: 0.5,
    }));
    expect(keys.publicKeys).toContain('LIVE2D_MODEL_X');
    expect(keys.publicKeys).toContain('LIVE2D_MODEL_Y');
    expect(keys.publicKeys).toContain('LIVE2D_MOUSE_TRACKING_STRENGTH');
    expect(keys.publicKeys).toContain('PTT_MODE');
  });

  it('只渲染配置里真实存在的键，顺序沿用常量表', () => {
    const keys = pickModelFieldKeys({
      MODEL_TYPE: 'live2d',
      LIVE2D_MODEL_PATH: '2D/Hiyori/Hiyori.model3.json',
    });
    expect(keys.publicKeys).toEqual(['MODEL_TYPE', 'LIVE2D_MODEL_PATH']);
  });

  it('未知类型按 live2d 规则过滤（与旧实现相同）', () => {
    expect(normalizeModelType(undefined)).toBe('live2d');
    expect(normalizeModelType('VRM')).toBe('vrm');
    expect(normalizeModelType('foo')).toBe('foo');
    const keys = pickModelFieldKeys({
      MODEL_TYPE: 'foo',
      LIVE2D_MODEL_PATH: '2D/a/a.model3.json',
      VRM_MODEL_PATH: 'a.vrm',
      IMAGE_MODEL_CONFIG: {},
    });
    expect(keys.publicKeys).toEqual(['MODEL_TYPE', 'LIVE2D_MODEL_PATH', 'VRM_MODEL_PATH']);
  });
});

describe('基础 / 高级配置拆分（splitAdvancedKeys）', () => {
  it('模型模块的四个高级键按常量顺序归入高级区', () => {
    const { basic, advanced } = splitAdvancedKeys(LIVE2D_KEYS);
    expect(advanced).toEqual([
      'TEXT_CHAT_BAR_Y_FACTOR',
      'FRONTEND_QUICK_CONTROLLER_X_OFFSET',
      'FRONTEND_CLICK_THROUGH',
      'FRONTEND_DEFAULT_TTS_LANG',
    ]);
    expect(basic).toContain('MODEL_TYPE');
    expect(basic).toContain('LIVE2D_MODEL_SCALE');
    expect(basic).toContain('PTT_MODE');
  });
});

describe('Images 配置摘要与默认值', () => {
  it('按旧文案输出四类计数', () => {
    expect(summarizeImagesConfig({
      baseImages: ['a.png', 'b.png'],
      emotions: [{ name: 'happy', images: ['c.png'] }],
      tapImages: ['d.png'],
      mouthShapes: [{ path: 'e.png', openness: 0.5 }],
    })).toBe('默认图 2 张 | 情绪 1 组 | Tap 图 1 张 | 嘴型图 1 张');
  });

  it('配置缺失或字段非数组时按 0 计', () => {
    expect(summarizeImagesConfig(undefined)).toBe('默认图 0 张 | 情绪 0 组 | Tap 图 0 张 | 嘴型图 0 张');
    expect(summarizeImagesConfig({ baseImages: 'nope' })).toBe(
      '默认图 0 张 | 情绪 0 组 | Tap 图 0 张 | 嘴型图 0 张',
    );
  });

  it('缺失配置时回落到默认结构，且返回深拷贝', () => {
    const clone = cloneImagesConfig(undefined);
    expect(clone).toEqual({
      baseImages: [],
      emotions: [],
      tapImages: [],
      mouthShapes: [],
      motionDurationMs: 3000,
      tapDurationMs: 700,
    });
    clone.baseImages?.push('x.png');
    expect(cloneImagesConfig(undefined).baseImages).toEqual([]);
  });

  it('已有配置时完整保留原始字段', () => {
    const source = {
      baseImages: ['a.png'],
      emotions: [],
      tapImages: [],
      mouthShapes: [],
      scale: 1.5,
      motionDurationMs: 1200,
      tapDurationMs: 500,
      extra: 'kept',
    };
    const clone = cloneImagesConfig(source);
    expect(clone).toEqual(source);
    expect(clone).not.toBe(source);
  });
});

describe('模型路径与目录解析', () => {
  it('统一正斜杠', () => {
    expect(normalizeModelPath('2D\\Hiyori\\Hiyori.model3.json')).toBe('2D/Hiyori/Hiyori.model3.json');
    expect(normalizeModelPath(null)).toBe('');
  });

  it('取 2D 之后的目录名', () => {
    expect(resolveModelDir('2D/Hiyori/Hiyori.model3.json')).toBe('Hiyori');
    expect(resolveModelDir('2D/Hiyori')).toBe('Hiyori');
    expect(resolveModelDir('2D\\Hiyori\\Hiyori.model3.json')).toBe('Hiyori');
  });

  it('没有 2D 段时取第一段', () => {
    expect(resolveModelDir('Hiyori/Hiyori.model3.json')).toBe('Hiyori');
  });

  it('非法目录名 / 空路径返回空串', () => {
    expect(resolveModelDir('2D/中文模型/x.model3.json')).toBe('');
    expect(resolveModelDir('')).toBe('');
    expect(resolveModelDir(undefined)).toBe('');
    expect(resolveModelDir('2D')).toBe('');
  });

  it('使用按钮写入的配置键随模型类型变化', () => {
    expect(pickModelConfigKey('live2d')).toBe('LIVE2D_MODEL_PATH');
    expect(pickModelConfigKey('vrm')).toBe('VRM_MODEL_PATH');
    expect(pickModelConfigKey('images')).toBe('LIVE2D_MODEL_PATH');
  });
});

describe('可用模型列表（filterModelsByType）', () => {
  const models = [
    { type: 'live2d', label: 'Hiyori', path: '2D/Hiyori/Hiyori.model3.json' },
    { type: 'vrm', label: 'Alicia', path: 'VRM/Alicia.vrm' },
    { type: 'image-asset', label: 'Image Asset | idle', path: 'idle.png' },
  ];

  it('严格按 type 相等筛选', () => {
    expect(filterModelsByType(models, 'live2d')).toEqual([models[0]]);
    expect(filterModelsByType(models, 'vrm')).toEqual([models[1]]);
    expect(filterModelsByType(models, 'images')).toEqual([]);
    expect(filterModelsByType(models, 'image-asset')).toEqual([models[2]]);
  });

  it('未知类型与空列表都返回空数组', () => {
    expect(filterModelsByType(models, 'foo')).toEqual([]);
    expect(filterModelsByType([], 'live2d')).toEqual([]);
    expect(filterModelsByType(undefined as unknown as [], 'live2d')).toEqual([]);
  });
});

describe('soullink profile 覆盖率提示（formatProfileStatus）', () => {
  it('区分已生成 / 已缓存', () => {
    expect(formatProfileStatus({ cached: true })).toBe('（已缓存）');
    expect(formatProfileStatus({ cached: false })).toBe('（已生成）');
  });

  it('带覆盖率时追加标准参数覆盖率', () => {
    expect(formatProfileStatus({
      cached: false,
      coverage: { mappedKeyCount: 12, facsKeyCount: 20 },
    })).toBe('（已生成） | 标准参数覆盖率: 12/20');
  });

  it('覆盖率字段不完整时只显示缓存状态', () => {
    expect(formatProfileStatus({ cached: true, coverage: { mappedKeyCount: 3 } })).toBe('（已缓存）');
    expect(formatProfileStatus(null)).toBe('');
  });
});
