import { describe, expect, it } from 'vitest';
import {
  computeDirty,
  defaultNewFilePath,
  defaultNewFolderPath,
  editorMetaText,
  editorStatusText,
  isEditorDirty,
  isValidEditorPath,
} from '@/memory/editor';
import type { MemEditorState } from '@/memory/types';

function makeEditor(partial: Partial<MemEditorState> = {}): MemEditorState {
  return {
    path: '/notes/a.md',
    original: 'hello',
    content: 'hello',
    dirty: false,
    isNew: false,
    index: true,
    meta: {},
    ...partial,
  };
}

describe('编辑器脏状态', () => {
  it('内容等于原文不脏，不等脏', () => {
    const editor = makeEditor();
    computeDirty(editor, 'hello');
    expect(editor.dirty).toBe(false);
    computeDirty(editor, 'hello world');
    expect(editor.dirty).toBe(true);
    computeDirty(editor, 'hello');
    expect(editor.dirty).toBe(false);
    expect(isEditorDirty(null)).toBe(false);
    expect(isEditorDirty(editor)).toBe(false);
  });

  it('状态文案：未保存 / 未创建 / 已保存', () => {
    expect(editorStatusText(null)).toBe('');
    expect(editorStatusText(makeEditor())).toBe('已保存');
    expect(editorStatusText(makeEditor({ dirty: true }))).toBe('未保存');
    expect(editorStatusText(makeEditor({ isNew: true }))).toBe('未创建');
  });

  it('元数据行：更新 / 分块 / 索引 / 未创建', () => {
    const editor = makeEditor({
      meta: { updated_at: '2026-09-10', chunk_count: 3, indexed: true },
    });
    expect(editorMetaText(editor)).toBe('更新 2026-09-10 · 分块 3 · 索引 已加入');
    expect(editorMetaText(makeEditor({ meta: { indexed: false } }))).toBe('索引 未加入');
    expect(editorMetaText(makeEditor({ isNew: true }))).toBe('未创建');
    expect(editorMetaText(null)).toBe('');
  });

  it('路径校验拒绝空值与根', () => {
    expect(isValidEditorPath('/a.md')).toBe(true);
    expect(isValidEditorPath('a.md')).toBe(true);
    expect(isValidEditorPath('/')).toBe(false);
    expect(isValidEditorPath('')).toBe(false);
  });

  it('新建默认路径：根目录不带前导空段', () => {
    expect(defaultNewFilePath('/')).toBe('/new.md');
    expect(defaultNewFilePath('/notes')).toBe('/notes/new.md');
    expect(defaultNewFolderPath('/')).toBe('/new-folder');
    expect(defaultNewFolderPath('/notes/deep')).toBe('/notes/deep/new-folder');
  });
});
