<script setup lang="ts">
// 弹窗里的通用字段块：标题 + 一句说明 + 控件（与旧 providerWizard.js 的 field() 一致）。
defineProps<{
  label: string;
  hint?: string;
}>();
</script>

<template>
  <div class="form-field">
    <label class="form-field-label">{{ label }}</label>
    <p v-if="hint" class="form-hint">{{ hint }}</p>
    <div class="form-field-control">
      <slot />
    </div>
  </div>
</template>
