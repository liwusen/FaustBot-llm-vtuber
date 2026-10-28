// 记忆页导航相关纯函数：当前目录、面包屑、祖先展开链、图片识别、兄弟目录。

import { findKbNodeByPath, kbParentPath, normalizeKbPath } from '@/memory/kbUtils';
import type { KbNode, MemSelection } from '@/memory/types';

export function isImagePath(path: unknown): boolean {
  return /\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(String(path || ''));
}

/** 选中项所属的当前目录（实体 → 根）。 */
export function currentDirOf(selection: MemSelection | null | undefined): string {
  const sel = selection || { kind: 'dir', path: '/' };
  if (sel.kind === 'dir') return normalizeKbPath(sel.path || '/');
  if (sel.kind === 'file') return kbParentPath(sel.path);
  return '/';
}

/** 检索范围：根目录不传 scope。 */
export function scopeOfDir(dir: string): string | null {
  return dir === '/' ? null : dir;
}

export function inScope(path: unknown, dir: string): boolean {
  if (dir === '/') return true;
  const norm = normalizeKbPath(path);
  return norm === dir || norm.startsWith(`${dir}/`);
}

export interface CrumbSegment {
  label: string;
  path: string;
  kind: 'dir' | 'file';
}

/** 面包屑：当前目录各层 +（选中文件时）文件自身。 */
export function breadcrumbSegments(selection: MemSelection | null | undefined): CrumbSegment[] {
  const sel = selection || { kind: 'dir', path: '/' };
  const dir = sel.kind === 'file' ? kbParentPath(sel.path) : currentDirOf(sel);
  const segments: CrumbSegment[] = [];
  if (dir && dir !== '/') {
    let acc = '';
    for (const part of dir.slice(1).split('/')) {
      acc += `/${part}`;
      segments.push({ label: part, path: acc, kind: 'dir' });
    }
  }
  if (sel.kind === 'file') {
    segments.push({ label: sel.path.split('/').pop() || sel.path, path: sel.path, kind: 'file' });
  }
  return segments;
}

/** 选中路径的祖先目录链（仅渲染时临时展开，不写 localStorage）。 */
export function revealedAncestors(selection: MemSelection | null | undefined): Set<string> {
  const sel = selection;
  const path = sel && (sel.kind === 'dir' || sel.kind === 'file')
    ? normalizeKbPath(sel.path || '')
    : '';
  const out = new Set<string>();
  if (!path || path === '/') return out;
  const segments = path.split('/').filter(Boolean);
  let acc = '';
  for (let i = 0; i < segments.length - 1; i++) {
    acc += `/${segments[i]}`;
    out.add(acc);
  }
  return out;
}

/** 兄弟目录选择器的候选（父目录的直接子目录）。 */
export function siblingDirs(tree: KbNode | null, dirPath: string): string[] {
  const parent = normalizeKbPath(dirPath);
  const node = findKbNodeByPath(tree, parent === '' ? '/' : parent);
  return (node && node.children ? node.children : [])
    .filter((child) => child.type === 'dir')
    .map((child) => normalizeKbPath(child.path));
}
