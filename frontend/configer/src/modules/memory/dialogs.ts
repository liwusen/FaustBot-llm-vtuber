// 记忆模块弹窗：统一走 useUiStore().openModal，不改共享 ModalHost。

import { useUiStore } from '@/stores/ui';
import MemUnsavedModal from '@/modules/memory/MemUnsavedModal.vue';
import MemPromptModal from '@/modules/memory/MemPromptModal.vue';
import MemSiblingPicker from '@/modules/memory/MemSiblingPicker.vue';
import MemImagePreview from '@/modules/memory/MemImagePreview.vue';

export interface UnsavedDialogOptions {
  reason: string;
  path: string;
  onSaveAndGo: () => void | Promise<void>;
  onDiscard: () => void;
}

/** 「未保存的改动」：保存并继续 / 放弃修改 / 取消。 */
export function openUnsavedDialog(options: UnsavedDialogOptions): void {
  useUiStore().openModal(
    MemUnsavedModal,
    options as unknown as Record<string, unknown>,
    '未保存的改动',
  );
}

/** 新建文件/文件夹的路径输入；确认后回调已归一化的路径。 */
export function openPathPrompt(
  title: string,
  value: string,
  onConfirm: (value: string) => void | Promise<void>,
): void {
  useUiStore().openModal(MemPromptModal, { value, onConfirm }, title);
}

/** 面包屑「…」：同级目录选择器。 */
export function openSiblingPicker(dirs: string[], onPick: (dir: string) => void): void {
  useUiStore().openModal(MemSiblingPicker, { dirs, onPick }, '跳转目录');
}

/** 详情栏图片预览。 */
export function openImagePreview(src: string): void {
  useUiStore().openModal(MemImagePreview, { src }, '图片预览');
}
