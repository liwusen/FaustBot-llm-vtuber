<script setup lang="ts">
import { computed } from 'vue';

const props = defineProps<{
  modelValue: boolean;
  label?: string;
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void;
}>();

const text = computed(() => props.label ?? (props.modelValue ? '已启用' : '已禁用'));

function onChange(event: Event): void {
  if (props.disabled) return;
  emit('update:modelValue', Boolean((event.target as HTMLInputElement).checked));
}
</script>

<template>
  <div class="switch-row" :class="{ 'switch-row-disabled': disabled }">
    <span class="switch-text">{{ text }}</span>
    <label class="switch" :class="{ 'switch-disabled': disabled }">
      <input
        type="checkbox"
        :checked="modelValue"
        :disabled="disabled"
        @change="onChange"
      />
      <span class="switch-slider"></span>
    </label>
  </div>
</template>
