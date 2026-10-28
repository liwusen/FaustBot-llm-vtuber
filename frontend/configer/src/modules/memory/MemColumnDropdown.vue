// 表头「⋯」列选择下拉：勾选即时生效，下拉保持打开；点击外部关闭。

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue';
import { MEM_COLUMN_DEFS, useMemoryStore } from '@/stores/memory';
import { closeDropdown, dropdown } from './menus';

const store = useMemoryStore();
const menuEl = ref<HTMLElement | null>(null);
const left = ref(0);
let alive = true;

function onDocumentClick(event: MouseEvent): void {
  const target = event.target as Node | null;
  if (!target) return;
  if (menuEl.value && menuEl.value.contains(target)) return;
  if (dropdown.anchorEl && dropdown.anchorEl.contains(target)) return;
  closeDropdown();
}

function bindOutside(): void {
  window.setTimeout(() => {
    if (alive) document.addEventListener('click', onDocumentClick);
  }, 0);
}

watch(
  () => dropdown.open,
  async (open) => {
    if (open) {
      await nextTick();
      const width = menuEl.value ? menuEl.value.offsetWidth : 0;
      left.value = Math.max(8, Math.round(dropdown.anchorRight - width));
      bindOutside();
    } else {
      document.removeEventListener('click', onDocumentClick);
    }
  },
);

onBeforeUnmount(() => {
  alive = false;
  document.removeEventListener('click', onDocumentClick);
});

function toggle(id: string, event: Event): void {
  const input = event.target as HTMLInputElement;
  store.toggleColumn(id, input.checked);
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="dropdown.open"
      ref="menuEl"
      class="mem-dropdown"
      :style="{ left: `${left}px`, top: `${dropdown.anchorTop}px` }"
    >
      <div class="mem-dropdown-title">{{ dropdown.title }}</div>
      <div class="mem-menu-box">
        <label class="list-row">
          <input type="checkbox" checked disabled />
          <span>名称</span>
        </label>
        <label v-for="def in MEM_COLUMN_DEFS" :key="def.id" class="list-row">
          <input
            type="checkbox"
            :checked="store.columns.includes(def.id)"
            @change="toggle(def.id, $event)"
          />
          <span>{{ def.label }}</span>
        </label>
      </div>
    </div>
  </Teleport>
</template>
