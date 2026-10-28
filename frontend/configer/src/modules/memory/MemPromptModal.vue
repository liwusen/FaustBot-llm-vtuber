// 新建文件/文件夹的路径输入弹窗。

<script setup lang="ts">
import { ref } from 'vue';
import { normalizeKbPath } from '@/memory/kbUtils';
import { useUiStore } from '@/stores/ui';

const props = defineProps<{
  value: string;
  onConfirm: (value: string) => void | Promise<void>;
}>();

const ui = useUiStore();
const input = ref(props.value);

function confirm(): void {
  const value = String(input.value || '').trim();
  if (!value) {
    ui.showBanner('error', '请输入路径');
    return;
  }
  ui.closeModal();
  void props.onConfirm(normalizeKbPath(value));
}
</script>

<template>
  <input v-model="input" class="input" @keydown.enter.prevent="confirm" />
  <div class="toolbar">
    <button class="btn btn-go" type="button" @click="confirm">创建</button>
    <button class="btn btn-quiet" type="button" @click="ui.closeModal()">取消</button>
  </div>
</template>
