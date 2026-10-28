// 内联编辑器纯逻辑：脏状态、状态文案、元数据行、新文件默认路径。

import { normalizeKbPath } from '@/memory/kbUtils';
import type { MemEditorState } from '@/memory/types';

export function isEditorDirty(editor: MemEditorState | null | undefined): boolean {
  return Boolean(editor && editor.dirty);
}

/** 恢复/重算脏状态（内容与原始内容比较）。 */
export function computeDirty(editor: MemEditorState, content: string): MemEditorState {
  editor.content = content;
  editor.dirty = content !== editor.original;
  return editor;
}

/** 状态文案：未保存 / 未创建 / 已保存。 */
export function editorStatusText(editor: MemEditorState | null | undefined): string {
  if (!editor) return '';
  if (editor.dirty) return '未保存';
  return editor.isNew ? '未创建' : '已保存';
}

/** 元数据行：更新 / 分块 / 索引 / 未创建，以 ` · ` 连接。 */
export function editorMetaText(editor: MemEditorState | null | undefined): string {
  if (!editor) return '';
  const meta = editor.meta || {};
  const parts: string[] = [];
  if (meta.updated_at) parts.push(`更新 ${String(meta.updated_at)}`);
  if (meta.chunk_count !== undefined && meta.chunk_count !== null) {
    parts.push(`分块 ${String(meta.chunk_count)}`);
  }
  if (meta.indexed !== undefined && meta.indexed !== null) {
    parts.push(`索引 ${meta.indexed ? '已加入' : '未加入'}`);
  }
  if (editor.isNew) parts.push('未创建');
  return parts.join(' · ');
}

export function isValidEditorPath(path: unknown): boolean {
  const normalized = normalizeKbPath(path);
  return Boolean(normalized) && normalized !== '/';
}

/** 新建文件默认路径：`<dir>/new.md`（根目录不带前导空段）。 */
export function defaultNewFilePath(dir: string): string {
  const base = dir === '/' ? '' : `${dir.slice(1)}/`;
  return normalizeKbPath(`${base}new.md`);
}

/** 新建文件夹默认路径：`<dir>/new-folder`。 */
export function defaultNewFolderPath(dir: string): string {
  const base = dir === '/' ? '' : `${dir.slice(1)}/`;
  return normalizeKbPath(`${base}new-folder`);
}
