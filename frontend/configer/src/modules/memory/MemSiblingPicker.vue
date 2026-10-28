// 面包屑「…」同级目录选择器。

<script setup lang="ts">
import { useUiStore } from '@/stores/ui';

const props = defineProps<{
  dirs: string[];
  onPick: (dir: string) => void;
}>();

const ui = useUiStore();

function pick(dir: string): void {
  ui.closeModal();
  props.onPick(dir);
}
</script>

<template>
  <div class="list-box" style="max-height: 320px">
    <div v-if="!props.dirs.length" class="mem-empty">没有同级目录</div>
    <div
      v-for="dir in props.dirs"
      :key="dir"
      class="list-row clickable"
      @click="pick(dir)"
    >
      <span class="mono">{{ dir }}</span>
    </div>
  </div>
  <div class="toolbar">
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">关闭</button>
  </div>
</template>
