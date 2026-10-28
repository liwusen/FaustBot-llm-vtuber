<script setup lang="ts">
import { ref, watch } from 'vue';
import { useUiStore } from '@/stores/ui';

const props = defineProps<{
  title: string;
  stateKey: string;
  forceOpen?: boolean;
  defaultOpen?: boolean;
}>();

const ui = useUiStore();

function resolveOpen(): boolean {
  if (props.forceOpen) return true;
  const stored = ui.foldState(props.stateKey);
  return stored === undefined ? Boolean(props.defaultOpen) : stored;
}

const open = ref(resolveOpen());

watch(
  () => props.forceOpen,
  (value) => {
    if (value) open.value = true;
  },
);

watch(
  () => props.stateKey,
  () => {
    open.value = resolveOpen();
  },
);

function toggle(): void {
  open.value = !open.value;
  ui.setFoldState(props.stateKey, open.value);
}
</script>

<template>
  <div class="field-fold">
    <button
      class="field-fold-btn"
      type="button"
      :aria-expanded="open ? 'true' : 'false'"
      @click="toggle"
    >
      <span class="field-fold-arrow">▸</span>
      <span>{{ title }}</span>
    </button>
    <div class="field-fold-body" :class="{ open }">
      <slot />
    </div>
  </div>
</template>
