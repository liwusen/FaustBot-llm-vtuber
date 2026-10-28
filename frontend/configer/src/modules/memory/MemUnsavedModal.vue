// 未保存改动的三选一弹窗（保存并继续 / 放弃修改 / 取消）。
// 取消只关闭弹窗：调用方的守卫已返回 true 拦截原操作。

<script setup lang="ts">
import { useUiStore } from '@/stores/ui';

const props = defineProps<{
  reason: string;
  path: string;
  onSaveAndGo: () => void | Promise<void>;
  onDiscard: () => void;
}>();

const ui = useUiStore();

function saveAndGo(): void {
  void props.onSaveAndGo();
}

function discard(): void {
  props.onDiscard();
}
</script>

<template>
  <div class="list-box">
    <div>{{ props.reason }} 前需要处理未保存的改动：</div>
    <div class="card-help mono">{{ props.path }}</div>
  </div>
  <div class="toolbar">
    <button class="btn btn-go" type="button" @click="saveAndGo">保存并继续</button>
    <button class="btn btn-danger" type="button" @click="discard">放弃修改</button>
    <button class="btn btn-quiet" type="button" @click="ui.closeModal()">取消</button>
  </div>
</template>
