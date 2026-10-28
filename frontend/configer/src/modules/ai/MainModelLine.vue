<script setup lang="ts">
import { computed } from 'vue';
import { useProvidersStore } from '@/stores/providers';
import {
  asAiProvider,
  formatContext,
  formatDecision,
  formatText,
  splitModelSpec,
} from './providerOps';

// 「对话模型服务商」组头右侧的当前主模型摘要（旧 _fillMainModelLine）。
const store = useProvidersStore();

const spec = computed(() => store.mainModel || '');
const parts = computed(() => splitModelSpec(spec.value));
const provider = computed(() => (parts.value.provider ? store.findProvider(parts.value.provider) : undefined));
const decision = computed(() => formatDecision(provider.value, parts.value.model, store.resolvedFormats));

const contextTokens = computed<number | null>(() => {
  const current = asAiProvider(provider.value);
  if (!current || !current.model_context_lengths) return null;
  const value = current.model_context_lengths[parts.value.model];
  return value === undefined ? null : value;
});

const serverTools = computed<string[]>(() => {
  const current = asAiProvider(provider.value);
  if (!current || !current.model_server_tools) return [];
  return current.model_server_tools[parts.value.model] || [];
});
</script>

<template>
  <div class="ai-main-model">
    <span v-if="!spec" class="ai-main-model-none">还没有选择主模型</span>
    <template v-else>
      <span class="ai-main-model-label">主模型</span>
      <span class="ai-main-model-name">{{ parts.provider }} · {{ parts.model }}</span>
      <span class="format-badge">{{ formatText(decision.format, decision.source) }}</span>
      <span class="ai-main-model-meta">上下文 {{ formatContext(contextTokens) }}</span>
      <span v-if="serverTools.length" class="tag-chip">服务端联网搜索</span>
    </template>
  </div>
</template>
