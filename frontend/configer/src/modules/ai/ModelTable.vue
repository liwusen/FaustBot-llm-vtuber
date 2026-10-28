<script setup lang="ts">
import { computed, watchEffect } from 'vue';
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import type { ModelEntry, ProviderConfig } from '@/config/types';
import ModelModal from './ModelModal.vue';
import { AI_TABLE_PAGE_SIZE, formatContext, formatDecision, formatText } from './providerOps';

// 模型表：搜索 + 10 条/页分页；主模型单选 / 子模型多选（旧 _aiModelsBlock）。
const store = useProvidersStore();
const ui = useUiStore();

// 分页与搜索保存在 ui.tableUIState，页内重渲染/切回页面都不会丢。
const tableState = computed(() => ui.tableState('ai-models'));

const filtered = computed<ModelEntry[]>(() => {
  const query = String(tableState.value.query || '').trim().toLowerCase();
  const entries = store.allModelEntries;
  return query
    ? entries.filter((entry) => `${entry.provider} ${entry.model}`.toLowerCase().includes(query))
    : entries;
});

const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / AI_TABLE_PAGE_SIZE)));
const page = computed(() => Math.min(Math.max(tableState.value.page, 1), totalPages.value));

const pageRows = computed<ModelEntry[]>(() => {
  const start = (page.value - 1) * AI_TABLE_PAGE_SIZE;
  return filtered.value.slice(start, start + AI_TABLE_PAGE_SIZE);
});

const subagentSet = computed(() => new Set(store.subagentModels || []));

// 旧实现会在渲染时把越界页码夹回合法范围（state.aiTable.page）。
watchEffect(() => {
  if (tableState.value.page > totalPages.value) tableState.value.page = totalPages.value;
  if (tableState.value.page < 1) tableState.value.page = 1;
});

function onSearch(event: Event): void {
  tableState.value.query = (event.target as HTMLInputElement).value;
  tableState.value.page = 1;
}

function setMainModel(entry: ModelEntry): void {
  store.setMainModel(entry.spec);
}

function toggleSubagent(entry: ModelEntry, event: Event): void {
  store.toggleSubagentModel(entry.spec, (event.target as HTMLInputElement).checked);
}

function goPrev(): void {
  if (page.value <= 1) return;
  tableState.value.page = page.value - 1;
}

function goNext(): void {
  if (page.value >= totalPages.value) return;
  tableState.value.page = page.value + 1;
}

function providerOf(entry: ModelEntry): ProviderConfig | undefined {
  return store.findProvider(entry.provider);
}

function contextTokens(provider: ProviderConfig | undefined, model: string): number | null {
  if (!provider || !provider.model_context_lengths) return null;
  const value = provider.model_context_lengths[model];
  return value === undefined ? null : value;
}

function formatBadge(entry: ModelEntry): { text: string; error: boolean; title: string } {
  const decision = formatDecision(providerOf(entry), entry.model, store.resolvedFormats);
  return {
    text: formatText(decision.format, decision.source),
    error: decision.source === 'error',
    title: decision.source === 'manual'
      ? '在「设置」里手动指定'
      : '由模型目录、接口地址或模型类型自动判断；不对时可在「设置」里覆盖',
  };
}

function openSettings(entry: ModelEntry): void {
  ui.openModal(ModelModal, { entry }, `设置模型 · ${entry.spec}`);
}
</script>

<template>
  <div class="ai-models">
    <div class="ai-subhead">
      <span class="ai-subhead-title">模型</span>
      <input
        class="input ai-search"
        type="search"
        placeholder="搜索模型或服务商…"
        :value="tableState.query"
        @input="onSearch"
      />
    </div>

    <table class="simple-table ai-table">
      <thead>
        <tr>
          <th title="「服务商::模型名」就是它在配置里的写法">模型</th>
          <th title="由模型目录、接口地址或模型类型自动判断；不对时可在「设置」里覆盖">接口格式</th>
          <th title="这个模型最多能记住多少 token">上下文</th>
          <th class="ai-pick-cell" title="AI 对话与工具调用使用它，只能选一个">主模型</th>
          <th class="ai-pick-cell" title="允许作为子任务代理使用，可以多选">子模型</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!pageRows.length">
          <td class="table-empty" colspan="6">
            {{ tableState.query ? '没有匹配的模型' : '还没有模型：在服务商里点「编辑 › 从服务商拉取模型」' }}
          </td>
        </tr>
        <tr v-for="(entry, index) in pageRows" :key="`${entry.spec}:${index}`">
          <td class="cell-primary">
            <div class="ai-model-spec">
              <span class="ai-model-provider">{{ entry.provider }}</span>
              <span class="ai-model-sep">::</span>
              <span class="ai-model-name">{{ entry.model }}</span>
            </div>
          </td>
          <td>
            <span
              class="format-badge"
              :class="{ 'format-badge-error': formatBadge(entry).error }"
              :title="formatBadge(entry).title"
            >{{ formatBadge(entry).text }}</span>
          </td>
          <td>
            <span
              :class="{ 'ai-muted': !contextTokens(providerOf(entry), entry.model) }"
              :title="contextTokens(providerOf(entry), entry.model) ? `${contextTokens(providerOf(entry), entry.model)} token` : '未配置，后端按 128000 计算'"
            >{{ formatContext(contextTokens(providerOf(entry), entry.model)) }}</span>
          </td>
          <td class="ai-pick-cell">
            <input
              class="provider-pick"
              type="radio"
              name="ai-main-model"
              title="设为主模型"
              :aria-label="`把 ${entry.spec} 设为主模型`"
              :checked="store.mainModel === entry.spec"
              @change="setMainModel(entry)"
            />
          </td>
          <td class="ai-pick-cell">
            <input
              class="provider-pick"
              type="checkbox"
              title="允许作为子模型"
              :aria-label="`允许 ${entry.spec} 作为子模型`"
              :checked="subagentSet.has(entry.spec)"
              @change="toggleSubagent(entry, $event)"
            />
          </td>
          <td class="ai-ops-cell">
            <div class="toolbar compact">
              <button class="btn btn-ghost" type="button" @click="openSettings(entry)">设置</button>
            </div>
          </td>
        </tr>
      </tbody>
    </table>

    <div v-show="totalPages > 1" class="toolbar table-pager">
      <button class="btn btn-ghost" type="button" :disabled="page <= 1" @click="goPrev">上一页</button>
      <span class="card-help">第 {{ page }} / {{ totalPages }} 页 · 共 {{ filtered.length }} 个模型</span>
      <button class="btn btn-ghost" type="button" :disabled="page >= totalPages" @click="goNext">下一页</button>
    </div>
  </div>
</template>
