// 高级模块的字段挑选规则：逐行对照旧 field-render.js 的 pickModuleFields（非专用模块分支）
// 与 renderConfigModule 的高级折叠分组。

import {
  ADVANCED_KEYS,
  AI_PRIVATE_KEYS,
  AI_PUBLIC_KEYS,
  LIVE2D_KEYS,
  SPEECH_PRIVATE_KEYS,
  SPEECH_PUBLIC_KEYS,
} from '@/config/constants';
import type { ConfigDict } from '@/config/types';

/** 由具体模块自己渲染、不再出现在「未归类」里的公共键（与旧实现一致）。 */
export const MODULE_MANAGED_PUBLIC_KEYS = ['MC_OPERATOR_URL', 'MC_EVENT_TRIGGER_ENABLED', 'mcp_servers'];

export interface UnclassifiedFields {
  publicKeys: string[];
  privateKeys: string[];
}

export interface ModuleFieldBuckets extends UnclassifiedFields {
  basicPublic: string[];
  basicPrivate: string[];
  advancedPublic: string[];
  advancedPrivate: string[];
  advancedCount: number;
}

/** 旧 pickModuleFields 的通用分支：按 localeCompare 排序并剔除已归类的键。 */
export function pickUnclassifiedFields(
  publicConfig: ConfigDict,
  privateConfig: ConfigDict,
): UnclassifiedFields {
  const publicKeys = Object.keys(publicConfig || {}).sort((a, b) => a.localeCompare(b));
  const privateKeys = Object.keys(privateConfig || {}).sort((a, b) => a.localeCompare(b));
  const usedPublic = new Set<string>([
    ...AI_PUBLIC_KEYS,
    ...LIVE2D_KEYS,
    ...SPEECH_PUBLIC_KEYS,
    ...MODULE_MANAGED_PUBLIC_KEYS,
  ]);
  const usedPrivate = new Set<string>([...AI_PRIVATE_KEYS, ...SPEECH_PRIVATE_KEYS]);
  return {
    publicKeys: publicKeys.filter((key) => !usedPublic.has(key)),
    privateKeys: privateKeys.filter((key) => !usedPrivate.has(key)),
  };
}

/** 旧 renderConfigModule 的基础/高级分组：高级键进折叠区，顺序保持原样。 */
export function bucketModuleFields(fields: UnclassifiedFields): ModuleFieldBuckets {
  const basicPublic: string[] = [];
  const advancedPublic: string[] = [];
  for (const key of fields.publicKeys) {
    if (ADVANCED_KEYS.has(key)) advancedPublic.push(key);
    else basicPublic.push(key);
  }
  const basicPrivate: string[] = [];
  const advancedPrivate: string[] = [];
  for (const key of fields.privateKeys) {
    if (ADVANCED_KEYS.has(key)) advancedPrivate.push(key);
    else basicPrivate.push(key);
  }
  return {
    publicKeys: fields.publicKeys,
    privateKeys: fields.privateKeys,
    basicPublic,
    basicPrivate,
    advancedPublic,
    advancedPrivate,
    advancedCount: advancedPublic.length + advancedPrivate.length,
  };
}
