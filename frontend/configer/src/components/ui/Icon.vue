<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    name: string;
    size?: number;
    className?: string;
  }>(),
  { size: 16 },
);

const markup = computed(() => {
  if (!props.name) return '';
  const icons = typeof window === 'undefined' ? undefined : window.FaustIcons;
  if (!icons || typeof icons.svg !== 'function') return '';
  if (Array.isArray(icons.names) && !icons.names.includes(props.name)) return '';
  try {
    return icons.svg(props.name, { size: props.size, className: props.className });
  } catch {
    return '';
  }
});
</script>

<template>
  <span v-if="markup" v-html="markup"></span>
</template>
