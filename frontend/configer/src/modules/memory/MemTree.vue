// 左侧目录树：根行 + 递归行 + 待处理任务；展开状态持久化，选中祖先临时展开。
// 大列表按帧渲染（首批 50，其后每帧 50）。

<script setup lang="ts">
import { computed, nextTick, ref, toRef, watch } from 'vue';
import { isImagePath } from '@/memory/paths';
import { useMemoryStore } from '@/stores/memory';
import { openContextMenu } from './menus';
import type { TreeRow } from '@/stores/memory';
import { useProgressiveRows } from './useProgressive';

const store = useMemoryStore();
const rowsHost = ref<HTMLElement | null>(null);
const scrollKey = ref('');

const { visible } = useProgressiveRows<TreeRow>(() => store.treeRows, {
  chunkSize: 50,
  active: toRef(store, 'moduleVisible'),
});

const rootExpanded = computed(() => store.isExpanded('/'));
const hasRows = computed(() => store.treeRows.length > 0);

function glyph(row: TreeRow): string {
  if (row.isDir) return '\u25AA';
  return isImagePath(row.path) ? '\u25A4' : '\u25AA';
}

function onCaret(row: TreeRow, event: MouseEvent): void {
  event.stopPropagation();
  if (!row.isDir) return;
  store.toggleExpanded(row.path);
}

function onRootCaret(event: MouseEvent): void {
  event.stopPropagation();
  store.toggleExpanded('/');
}

function onRootClick(): void {
  if (!store.expanded.includes('/')) store.toggleExpanded('/');
  store.selectDir('/');
}

function onRowClick(row: TreeRow): void {
  if (row.isDir) {
    if (!store.expanded.includes(row.path)) store.toggleExpanded(row.path);
    store.selectDir(row.path);
  } else {
    store.selectFile(row.path);
  }
}

function onRowDblClick(row: TreeRow): void {
  if (!row.isDir) void store.openEditor(row.path);
}

function onRootContext(event: MouseEvent): void {
  event.preventDefault();
  openContextMenu(event.clientX, event.clientY, { type: 'dir', path: '/', name: '/' });
}

function onRowContext(row: TreeRow, event: MouseEvent): void {
  event.preventDefault();
  openContextMenu(event.clientX, event.clientY, {
    type: row.isDir ? 'dir' : 'file',
    path: row.path,
    name: row.name,
  });
}

// 选中行定位：仅在选中路径变化时滚动，避免每次重绘都把树拽回原位
watch(
  [() => store.selectedPath, visible],
  () => {
    const target = store.selectedPath;
    if (!target || scrollKey.value === target) return;
    scrollKey.value = target;
    void nextTick(() => {
      const node = rowsHost.value
        ? rowsHost.value.querySelector('.mem-tree-row.selected')
        : null;
      if (node && typeof node.scrollIntoView === 'function') {
        node.scrollIntoView({ block: 'nearest' });
      }
    });
  },
  { deep: false },
);
</script>

<template>
  <div class="mem-tree">
    <div class="mem-tree-body">
      <div
        v-if="store.kbTree"
        class="mem-tree-row"
        :class="{ selected: store.selectedPath === '/' }"
        @click="onRootClick"
        @contextmenu="onRootContext"
      >
        <span class="mem-tree-caret" @click="onRootCaret">{{ rootExpanded ? '\u25BE' : '\u25B8' }}</span>
        <span class="mem-tree-label mono">{{ '\u25AA' }} /</span>
      </div>
      <div v-if="!hasRows" class="mem-empty">记忆库为空</div>
      <div ref="rowsHost" class="mem-tree-rows">
        <div
          v-for="row in visible"
          :key="row.path"
          class="mem-tree-row"
          :class="{ selected: store.selectedPath === row.path }"
          :style="{ '--mem-depth': String(row.depth) }"
          :data-path="row.path"
          @click="onRowClick(row)"
          @dblclick="onRowDblClick(row)"
          @contextmenu="onRowContext(row, $event)"
        >
          <span class="mem-tree-caret" @click="onCaret(row, $event)">{{ row.isDir ? (store.isExpanded(row.path) ? '\u25BE' : '\u25B8') : '' }}</span>
          <span class="mem-tree-label mono">{{ glyph(row) }} {{ row.name }}</span>
          <span v-if="row.count" class="mem-tree-count">{{ row.count }}</span>
        </div>
      </div>
      <div v-if="store.kbTasks.length" class="mem-tree-tasks">
        <div class="mem-detail-title">待处理任务 ({{ store.kbTasks.length }})</div>
        <div v-for="(task, index) in store.kbTasks" :key="index" class="mem-tree-task">
          <span class="mem-task-status" :class="`mem-task-${task.status || 'waiting'}`">{{ task.status || 'waiting' }}</span>
          <span class="mono">{{ task.target || '-' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
