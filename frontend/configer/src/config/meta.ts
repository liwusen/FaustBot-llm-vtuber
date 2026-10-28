import { META } from './constants';
import type { FieldMeta } from './types';

/** 与旧 field-render.js 的 getMeta 一致：缺失时回落到键名、空说明。 */
export function getFieldMeta(fieldKey: string): FieldMeta {
  return META[fieldKey] || { label: fieldKey, help: '' };
}
