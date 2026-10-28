<script setup lang="ts">
import { computed, onErrorCaptured, ref, watch } from 'vue';
import { PERSISTENT_COMPONENT_NAMES, useModulesStore } from '@/stores/modules';
import { resolveModule } from '@/modules/registry';

const modules = useModulesStore();
const errorText = ref('');

const resolution = computed(() => resolveModule(modules.activeModule));

const moduleKey = computed(() => {
  const id = modules.activeModule;
  // 持久模块的缓存 key 不含 epoch；epoch 改的是 KeepAlive 本身（见模板），
  // 这样旧实例会被真正 unmount，而不是留在缓存里等 LRU 淘汰。
  if (modules.isPersistent(id)) return id;
  return `${id}:${modules.nonce}`;
});

watch(
  () => moduleKey.value,
  () => {
    errorText.value = '';
  },
);

onErrorCaptured((err) => {
  console.error('[module] render failed', err);
  errorText.value = `模块加载失败: ${String((err as { message?: unknown })?.message || err)}`;
  return false;
});
</script>

<template>
  <div class="module-container">
    <div v-if="errorText" class="empty-state">{{ errorText }}</div>
    <KeepAlive
      v-else
      :key="`keepalive:${modules.reloadEpoch}`"
      :include="[...PERSISTENT_COMPONENT_NAMES]"
      :max="3"
    >
      <component
        :is="resolution.component"
        :key="moduleKey"
        v-bind="resolution.props"
      />
    </KeepAlive>
  </div>
</template>
