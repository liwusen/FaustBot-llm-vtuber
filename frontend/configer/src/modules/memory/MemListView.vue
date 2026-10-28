// 列表视图：搜索栏 + 工具栏 + 结果表。
// 工具栏动作失败统一提示「<动作>失败: …」（旧 memListRun）。

<script setup lang="ts">
import { computed } from 'vue';
import { useUiStore } from '@/stores/ui';
import { useMemoryStore } from '@/stores/memory';
import MemSearchBar from './MemSearchBar.vue';
import MemResultTable from './MemResultTable.vue';
import { newFileFlow, newFolderFlow } from './actions';

const store = useMemoryStore();
const ui = useUiStore();

const deletable = computed(() => {
  const sel = store.selection;
  return (
    (sel.kind === 'file' || sel.kind === 'dir') &&
    Boolean(sel.path) &&
    sel.path !== '/'
  );
});

const selectedPath = computed(() => {
  const sel = store.selection;
  return sel.kind === 'file' || sel.kind === 'dir' ? sel.path : '';
});

function run(label: string, fn: () => unknown): void {
  try {
    const result = fn();
    if (result && typeof (result as Promise<unknown>).catch === 'function') {
      (result as Promise<unknown>).catch((err: unknown) => {
        console.error(`[memory] ${label}失败`, err);
        ui.showBanner('error', `${label}失败: ${String((err as Error)?.message || err)}`);
      });
    }
  } catch (err) {
    console.error(`[memory] ${label}失败`, err);
    ui.showBanner('error', `${label}失败: ${String((err as Error)?.message || err)}`);
  }
}
</script>

<template>
  <div>
    <MemSearchBar />
    <div class="mem-list-bar">
      <button class="btn btn-quiet" type="button" @click="run('刷新', () => store.reload())">刷新</button>
      <button class="btn btn-quiet" type="button" @click="run('新建文件', () => newFileFlow(store.currentDir))">新建文件</button>
      <button class="btn btn-quiet" type="button" @click="run('新建文件夹', () => newFolderFlow(store.currentDir))">新建文件夹</button>
      <button
        class="btn btn-danger"
        type="button"
        :disabled="!deletable"
        @click="run('删除', () => store.deletePath(selectedPath))"
      >
        删除
      </button>
      <button class="btn btn-quiet" type="button" @click="run('导入外部文件', () => store.importExternal())">导入外部文件</button>
    </div>
    <MemResultTable />
  </div>
</template>
