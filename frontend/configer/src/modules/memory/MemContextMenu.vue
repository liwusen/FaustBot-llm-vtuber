// 右键菜单（树节点 / 列表行 / 面包屑复制路径）：Teleport 到 body，点击外部关闭。

<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue';
import { closeContextMenu, contextMenu, runContextAction } from './menus';

let alive = true;

function onDocumentClick(event: MouseEvent): void {
  const target = event.target as Element | null;
  if (target && typeof target.closest === 'function' && target.closest('.mem-context-menu')) return;
  closeContextMenu();
}

watch(
  () => contextMenu.open,
  (open) => {
    if (open) {
      window.setTimeout(() => {
        if (alive) document.addEventListener('click', onDocumentClick);
      }, 0);
    } else {
      document.removeEventListener('click', onDocumentClick);
    }
  },
);

onBeforeUnmount(() => {
  alive = false;
  document.removeEventListener('click', onDocumentClick);
});
</script>

<template>
  <Teleport to="body">
    <div
      v-if="contextMenu.open"
      class="context-menu mem-context-menu"
      :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
    >
      <template v-for="(item, index) in contextMenu.items" :key="index">
        <div v-if="!item" class="mem-menu-sep"></div>
        <div
          v-else
          class="context-menu-item"
          :class="{ disabled: item.disabled, danger: item.danger }"
          @click="runContextAction(item)"
        >
          {{ item.label }}
        </div>
      </template>
    </div>
  </Teleport>
</template>
