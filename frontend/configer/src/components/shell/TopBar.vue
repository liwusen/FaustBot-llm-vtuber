<script setup lang="ts">
import { useConfigStore } from '@/stores/config';
import { useUiStore } from '@/stores/ui';

const config = useConfigStore();
const ui = useUiStore();

function onSave(): void {
  void config.saveConfig();
}

function onApply(): void {
  void config.applyRuntime();
}

function onReload(): void {
  void config.reloadFromDisk();
}
</script>

<template>
  <header class="topbar">
    <div class="topbar-left">
      <span id="validBadge" class="badge badge-valid">配置有效</span>
      <span
        id="dirtyBadge"
        class="badge badge-dirty"
        :class="{ hidden: config.dirtyCount <= 0 }"
      >{{ config.dirtyCount }} 项配置已修改</span>
    </div>
    <div class="topbar-actions">
      <button id="reloadBtn" class="btn btn-ghost" type="button" :disabled="ui.busy" @click="onReload">
        重新加载
      </button>
      <button id="saveBtn" class="btn btn-primary" type="button" :disabled="ui.busy" @click="onSave">
        保存
      </button>
      <button id="applyBtn" class="btn btn-secondary" type="button" :disabled="ui.busy" @click="onApply">
        应用
      </button>
    </div>
  </header>
</template>
