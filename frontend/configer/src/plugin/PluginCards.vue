<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { usePluginsStore } from '@/stores/plugins';
import type { PluginCardSpec } from './types';

const props = defineProps<{ moduleId: string }>();
const plugins = usePluginsStore();
const hosts = new Map<string, HTMLElement>();
const rendered = new Set<string>();

const cards = computed(() => plugins.cards.filter((c) => c.moduleId === props.moduleId));

function cardKey(card: PluginCardSpec, index: number): string {
  return card._key || `${card.moduleId || ''}:${card.title || ''}:${index}`;
}

function setHost(key: string, el: unknown): void {
  if (el instanceof HTMLElement) hosts.set(key, el);
  else hosts.delete(key);
}

/** 逐张调用旧式 card.render；content/html 由模板回退渲染。 */
async function renderCards(): Promise<void> {
  await nextTick();
  const list = cards.value;
  for (let index = 0; index < list.length; index += 1) {
    const card = list[index];
    const key = cardKey(card, index);
    const host = hosts.get(key);
    if (!host || typeof card.render !== 'function') continue;
    // 卡片是原地渲染的，同一挂载实例里只调用一次，避免重复绑定事件。
    if (rendered.has(key)) continue;
    rendered.add(key);
    try {
      await card.render(host);
    } catch (err) {
      console.error('[plugin] card render failed', err);
      host.textContent = `模块加载失败: ${String((err as { message?: unknown })?.message || err)}`;
    }
  }
}

onMounted(renderCards);
watch(cards, () => {
  void renderCards();
}, { flush: 'post' });

onBeforeUnmount(() => {
  for (const host of hosts.values()) host.innerHTML = '';
  hosts.clear();
  rendered.clear();
});
</script>

<template>
  <section v-for="(card, index) in cards" :key="cardKey(card, index)" class="module-section card full-span">
    <h3 class="section-title">{{ card.title || '插件卡片' }}</h3>
    <div class="module-section-content">
      <div v-if="typeof card.render === 'function'" :ref="(el) => setHost(cardKey(card, index), el)" />
      <div v-else class="card-content">{{ card.content || card.html || '' }}</div>
    </div>
  </section>
</template>
