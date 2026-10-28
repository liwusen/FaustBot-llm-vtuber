// 记忆页操作流：带守卫的「新建文件 / 新建文件夹」与同级目录选择器。

import { defaultNewFilePath, defaultNewFolderPath } from '@/memory/editor';
import { siblingDirs } from '@/memory/paths';
import { openPathPrompt, openSiblingPicker } from '@/modules/memory/dialogs';
import { useMemoryStore } from '@/stores/memory';

export function newFileFlow(dir: string): void {
  const store = useMemoryStore();
  if (store.guardEditor('新建文件', () => newFileFlow(dir))) return;
  openPathPrompt('新建文件', defaultNewFilePath(dir), (target) => store.newFile(dir, target));
}

export function newFolderFlow(dir: string): void {
  const store = useMemoryStore();
  openPathPrompt('新建文件夹', defaultNewFolderPath(dir), (target) => store.newFolder(dir, target));
}

export function openSiblingPickerFlow(baseDir: string): void {
  const store = useMemoryStore();
  openSiblingPicker(siblingDirs(store.kbTree, baseDir), (dir) => store.selectDir(dir));
}
