<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(
  defineProps<{
    title?: string;
    fullSpan?: boolean;
    cardClass?: string;
  }>(),
  { title: '', fullSpan: true, cardClass: '' },
);

const classes = computed(() => [
  'module-section',
  'card',
  props.fullSpan ? 'full-span' : '',
  props.cardClass,
].filter(Boolean));
</script>

<template>
  <section :class="classes">
    <h3 v-if="title" class="section-title">{{ title }}</h3>
    <div v-if="$slots.actions" class="toolbar">
      <slot name="actions" />
    </div>
    <div class="module-section-content">
      <slot />
    </div>
  </section>
</template>
