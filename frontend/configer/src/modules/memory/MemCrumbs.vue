// 面包屑：根「记忆」+ 当前目录各层（选中文件时含文件）；过长折叠为「…」同级选择器。
// 右键「复制路径」。

<script setup lang="ts">
import { computed } from 'vue';
import { kbParentPath } from '@/memory/kbUtils';
import { useMemoryStore } from '@/stores/memory';
import { openSiblingPickerFlow } from './actions';
import { openCopyPathMenu } from './menus';
import type { CrumbSegment } from '@/memory/paths';

const store = useMemoryStore();
const segs = computed<CrumbSegment[]>(() => store.crumbs);
const target = computed(() => (segs.value.length ? segs.value[segs.value.length - 1].path : '/'));

function goDir(path: string): void {
  store.selectDir(path);
}

function goSeg(seg: CrumbSegment): void {
  if (seg.kind === 'file') store.selectFile(seg.path);
  else goDir(seg.path);
}

function openSiblings(): void {
  openSiblingPickerFlow(segs.value[1] ? kbParentPath(segs.value[1].path) : '/');
}

function onContext(event: MouseEvent): void {
  event.preventDefault();
  openCopyPathMenu(event.clientX, event.clientY, target.value || '/');
}
</script>

<template>
  <div class="mem-crumbs" :title="target" @contextmenu="onContext">
    <button class="mem-crumb" type="button" @click="goDir('/')">记忆</button>
    <template v-if="segs.length > 4">
      <span class="mem-crumb-sep">/</span>
      <button
        class="mem-crumb"
        type="button"
        :title="segs[0].path"
        @click="goSeg(segs[0])"
      >
        {{ segs[0].label }}
      </button>
      <span class="mem-crumb-sep">/</span>
      <button
        class="mem-crumb mem-crumb-more"
        type="button"
        title="选择同级目录"
        @click="openSiblings"
      >
        …
      </button>
      <template v-for="seg in segs.slice(-2)" :key="seg.path">
        <span class="mem-crumb-sep">/</span>
        <button
          class="mem-crumb"
          :class="{ 'mem-crumb-file': seg.kind === 'file' }"
          type="button"
          :title="seg.path"
          @click="goSeg(seg)"
        >
          {{ seg.label }}
        </button>
      </template>
    </template>
    <template v-else>
      <template v-for="seg in segs" :key="seg.path">
        <span class="mem-crumb-sep">/</span>
        <button
          class="mem-crumb"
          :class="{ 'mem-crumb-file': seg.kind === 'file' }"
          type="button"
          :title="seg.path"
          @click="goSeg(seg)"
        >
          {{ seg.label }}
        </button>
      </template>
    </template>
  </div>
</template>
