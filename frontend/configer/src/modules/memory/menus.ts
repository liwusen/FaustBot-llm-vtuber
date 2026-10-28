// 记忆页浮层状态：树/列表/面包屑右键菜单 + 表头列选择下拉。
// 状态是模块级单例（同一时间只有一个记忆页实例）。

import { reactive } from 'vue';
import { kbParentPath } from '@/memory/kbUtils';
import { useMemoryStore } from '@/stores/memory';
import { newFileFlow, newFolderFlow } from '@/modules/memory/actions';

export interface ContextMenuItem {
  label: string;
  disabled?: boolean;
  danger?: boolean;
  action?: () => void | Promise<void>;
}

/** null 表示分隔线。 */
export type ContextMenuEntry = ContextMenuItem | null;

export const contextMenu = reactive<{
  open: boolean;
  x: number;
  y: number;
  items: ContextMenuEntry[];
}>({ open: false, x: 0, y: 0, items: [] });

export const dropdown = reactive<{
  open: boolean;
  anchorRight: number;
  anchorTop: number;
  title: string;
  anchorEl: HTMLElement | null;
}>({ open: false, anchorRight: 0, anchorTop: 0, title: '', anchorEl: null });

export function closeContextMenu(): void {
  contextMenu.open = false;
  contextMenu.items = [];
}

export function closeDropdown(): void {
  dropdown.open = false;
  dropdown.anchorEl = null;
}

export function openContextMenu(
  x: number,
  y: number,
  node: { type: 'dir' | 'file'; path: string; name: string },
): void {
  const store = useMemoryStore();
  const parentDir = node.type === 'dir' ? node.path : kbParentPath(node.path);
  const items: ContextMenuEntry[] = [];
  if (node.type === 'file') {
    items.push({ label: '编辑', action: () => store.openEditor(node.path) });
    items.push({ label: '新建文件', action: () => newFileFlow(parentDir) });
    items.push({ label: '新建文件夹', action: () => newFolderFlow(parentDir) });
  } else {
    items.push({ label: '新建文件', action: () => newFileFlow(node.path) });
    items.push({ label: '新建文件夹', action: () => newFolderFlow(node.path) });
  }
  items.push(null);
  items.push({ label: '重命名', action: () => store.renameNode(node) });
  if (node.type === 'file') {
    items.push({ label: '复制', action: () => store.setClipboard('copy', node) });
    items.push({ label: '剪切', action: () => store.setClipboard('cut', node) });
  }
  items.push({
    label: '粘贴',
    disabled: !store.clipboard,
    action: () => store.paste(parentDir),
  });
  items.push(null);
  items.push({ label: '复制路径', action: () => store.copyPath(node.path) });
  items.push({
    label: node.type === 'file' ? '删除文件' : '删除目录',
    danger: true,
    action: () => store.deletePath(node.path),
  });

  contextMenu.items = items;
  contextMenu.x = x;
  contextMenu.y = y;
  contextMenu.open = true;
}

/** 面包屑右键：只提供「复制路径」。 */
export function openCopyPathMenu(x: number, y: number, path: string): void {
  const store = useMemoryStore();
  contextMenu.items = [
    {
      label: '复制路径',
      action: () => store.copyPath(path),
    },
  ];
  contextMenu.x = x;
  contextMenu.y = y;
  contextMenu.open = true;
}

export function openColumnDropdown(anchor: HTMLElement | null): void {
  if (!anchor) return;
  const rect = anchor.getBoundingClientRect();
  dropdown.anchorRight = rect.right;
  dropdown.anchorTop = rect.bottom + 4;
  dropdown.title = '选择显示列';
  dropdown.anchorEl = anchor;
  dropdown.open = true;
}

export async function runContextAction(item: ContextMenuItem): Promise<void> {
  closeContextMenu();
  if (item.disabled || !item.action) return;
  await item.action();
}
