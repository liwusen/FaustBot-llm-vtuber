<script setup lang="ts">
import { onBeforeUnmount, onMounted } from 'vue';
import { useUiStore } from '@/stores/ui';

const ui = useUiStore();

function onKeydown(event: KeyboardEvent): void {
  if (event.key !== 'Escape') return;
  if (!ui.modalOpen) return;
  ui.closeModal();
}

function onOverlayClick(event: MouseEvent): void {
  if (event.target === event.currentTarget) ui.closeModal();
}

onMounted(() => {
  window.addEventListener('keydown', onKeydown);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeydown);
  ui.closeModal();
});
</script>

<template>
  <div
    v-if="ui.modalOpen"
    id="cfgModalOverlay"
    class="cfg-modal-overlay"
    @click="onOverlayClick"
  >
    <div class="cfg-modal">
      <div class="cfg-modal-head">
        <h3 id="cfgModalTitle" class="cfg-modal-title">{{ ui.modalTitle }}</h3>
        <button class="btn btn-ghost" type="button" @click="ui.closeModal()">关闭</button>
      </div>
      <div id="cfgModalBody" class="cfg-modal-body">
        <component :is="ui.modalComponent" v-if="ui.modalComponent" v-bind="ui.modalProps" />
      </div>
    </div>
  </div>
</template>
