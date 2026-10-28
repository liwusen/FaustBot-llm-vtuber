import { SCALE_PRESETS } from './constants';
import type { NumberFieldSpec } from './types';

/**
 * 与旧 field-render.js 的 getNumberFieldSpec 逐条一致：
 * 1. SCALE_PRESETS 里登记过的键直接用预设；
 * 2. 0~1 的数值默认用 0~1 的滑块；
 * 3. 其它数值用 number 输入，整数步长 1、小数步长 0.01。
 */
export function getNumberFieldSpec(fieldKey: string, value: unknown): NumberFieldSpec {
  const presets = SCALE_PRESETS as unknown as Record<string, NumberFieldSpec>;
  if (presets[fieldKey]) return presets[fieldKey];
  const numericValue = Number(value);
  if (Number.isFinite(numericValue) && numericValue >= 0 && numericValue <= 1) {
    return { type: 'range', min: 0, max: 1, step: 0.01, unit: '' };
  }
  return {
    type: 'number',
    step: Number.isInteger(value) ? 1 : 0.01,
    unit: '',
  };
}
