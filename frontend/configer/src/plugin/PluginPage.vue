<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { usePluginsStore } from '@/stores/plugins';

const props = defineProps<{ moduleId: string }>();
const plugins = usePluginsStore();
const host = ref<HTMLElement | null>(null);
const errorText = ref('');

const page = computed(() => plugins.pages.find((p) => p.id === props.moduleId));

/**
 * 调用插件页自己的 render。容器始终由 Vue 持有，
 * 插件抛错只把内容区换成「模块加载失败: …」，不能冒泡到 App。
 */
async function renderPage(): Promise<void> {
  const container = host.value;
  if (!container) return;
  errorText.value = '';
  container.innerHTML = '';
  const render = page.value?.render;
  if (typeof render !== 'function') return;
  try {
    await render(container);
  } catch (err) {
    console.error('[plugin] page render failed', err);
    container.innerHTML = '';
    errorText.value = `模块加载失败: ${String((err as { message?: unknown })?.message || err)}`;
  }
}

onMounted(renderPage);

// 同一实例被复用到另一个模块时重新渲染（ModuleHost 通常换 key，但保持自洽）。
watch(() => props.moduleId, () => {
  void renderPage();
});

onBeforeUnmount(() => {
  if (host.value) host.value.innerHTML = '';
  errorText.value = '';
});
</script>

<template>
  <div v-show="!errorText" ref="host" class="plugin-page-host"></div>
  <div v-if="errorText" class="empty-state">{{ errorText }}</div>
</template>
