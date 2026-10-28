// 知识库路径工具：逐条移植 frontend/libs/configer/kb-utils.js（行为、排序、兜底完全一致）。

import type { KbNode } from '@/memory/types';

/** 反斜杠归一、补前导 `/`、压缩重复 `/`、去掉尾部 `/`；空值统一为 `/`。 */
export function normalizeKbPath(raw: unknown): string {
  const input = String(raw || '').replace(/\\/g, '/').trim();
  if (!input || input === '/') return '/';
  const withLeading = input.startsWith('/') ? input : `/${input}`;
  const compact = withLeading.replace(/\/{2,}/g, '/');
  return compact.endsWith('/') ? compact.slice(0, -1) : compact;
}

/** 父目录；根目录的父目录仍是 `/`。 */
export function kbParentPath(path: unknown): string {
  const normalized = normalizeKbPath(path);
  if (normalized === '/') return '/';
  const index = normalized.lastIndexOf('/');
  return index <= 0 ? '/' : normalized.slice(0, index);
}

/** 深度优先按路径查节点；找不到返回 null。 */
export function findKbNodeByPath<T extends { path?: string; children?: T[] }>(
  root: T | null | undefined,
  path: unknown,
): T | null {
  if (!root || typeof root !== 'object') return null;
  const target = normalizeKbPath(path);
  const stack: T[] = [root];
  while (stack.length) {
    const current = stack.pop() as T;
    const currentPath = normalizeKbPath(current.path || '/');
    if (currentPath === target) return current;
    for (const child of current.children || []) stack.push(child);
  }
  return null;
}

/** `getKbChildren` 的行：目录 / 文件 / 实体三种。 */
export interface KbChildRow {
  id?: string;
  path?: string;
  name: string;
  type: string;
  entity_type?: string;
  description?: string;
}

/**
 * 读取某目录的直接子项：目录 → 文件 → 实体（同级按名称 localeCompare）。
 * 目标是文件或不存在时返回空数组。
 */
export function getKbChildren(
  root: KbNode | null | undefined,
  dirPath: unknown,
): KbChildRow[] {
  const dirNode = findKbNodeByPath(root, dirPath);
  if (!dirNode || String(dirNode.type || 'dir') === 'file') return [];
  const rows: KbChildRow[] = (dirNode.children || []).map((child) => {
    if (child.type === 'entity') {
      return {
        id: String(child.id || ''),
        name: String(child.name || ''),
        type: 'entity',
        entity_type: String(child.entity_type || 'custom'),
        description: String(child.description || ''),
      };
    }
    return {
      path: normalizeKbPath(child.path || '/'),
      type: String(child.type || 'dir'),
      name: String(child.name || child.path || '') || '/',
    };
  });
  rows.sort((a, b) => {
    if (a.type !== b.type) {
      if (a.type === 'dir') return -1;
      if (b.type === 'dir') return 1;
      if (a.type === 'file') return -1;
      if (b.type === 'file') return 1;
      return 0;
    }
    return a.name.localeCompare(b.name);
  });
  return rows;
}
