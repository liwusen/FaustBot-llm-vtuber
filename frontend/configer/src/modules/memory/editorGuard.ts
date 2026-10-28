// 编辑器离开守卫：脏状态弹「保存并继续 / 放弃修改 / 取消」。
// 返回 true = 已拦截（用户正在确认）；false = 放行（必要时已关闭干净的编辑器）。

import { useUiStore } from '@/stores/ui';
import { useMemoryStore } from '@/stores/memory';
import { openUnsavedDialog } from '@/modules/memory/dialogs';

export function guardEditor(reason: string, proceed: () => void | Promise<void>): boolean {
  const store = useMemoryStore();
  const editor = store.editor;
  if (!editor) return false;
  if (!editor.dirty) {
    store.closeEditor();
    return false;
  }
  openUnsavedDialog({
    reason,
    path: String(editor.path || ''),
    onSaveAndGo: async () => {
      const ok = await store.saveEditor();
      if (!ok) return;
      useUiStore().closeModal();
      store.closeEditor();
      await proceed();
    },
    onDiscard: () => {
      useUiStore().closeModal();
      store.closeEditor();
      void proceed();
    },
  });
  return true;
}
