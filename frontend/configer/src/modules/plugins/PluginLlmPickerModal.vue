<script setup lang="ts">
// 插件配置的 LLM 模型选择弹窗：数据源与 AI 服务商页的 Models 表同源
// （useProvidersStore.ensureProviders + allModelEntries）。
import { computed, onMounted, ref } from 'vue';
import type { ModelEntry } from '@/config/types';
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import { humanizeLabel } from '@/utils/format';

const props = defineProps<{
  fieldKey: string;
  fieldLabel?: string;
  currentValue?: string;
  onPick?: (spec: string, entry: ModelEntry) => void;
}>();

const providers = useProvidersStore();
const ui = useUiStore();

const selected = ref(String(props.currentValue || ''));
const PAGE_SIZE = 10;

const entries = computed<ModelEntry[]>(() => providers.allModelEntries);
const tableTitle = computed(() => `模型列表 - ${props.fieldLabel || props.fieldKey}`);
const state = computed(() => ui.tableState(tableTitle.value));
const searchText = ref(state.value.query);
const radioName = computed(() => `plugin-llm-pick-${props.fieldKey}`);

const filteredRows = computed(() => {
  const query = state.value.query;
  if (!query) return entries.value;
  return entries.value.filter((entry) => entry.model.toLowerCase().includes(query));
});

const totalPages = computed(() => Math.max(1, Math.ceil(filteredRows.value.length / PAGE_SIZE)));

const currentPage = computed(() => {
  const page = Number(state.value.page) || 1;
  return Math.min(Math.max(1, page), totalPages.value);
});

const pageRows = computed(() => {
  const start = (currentPage.value - 1) * PAGE_SIZE;
  return filteredRows.value.slice(start, start + PAGE_SIZE);
});

const pageInfo = computed(
  () => `第 ${currentPage.value} / ${totalPages.value} 页 · 共 ${filteredRows.value.length} 项`,
);

const emptyMessage = computed(() => (state.value.query ? '无匹配结果' : '无数据'));

function onSearch(event: Event): void {
  const raw = (event.target as HTMLInputElement).value;
  searchText.value = raw;
  state.value.query = raw.trim().toLowerCase();
  state.value.page = 1;
}

function prevPage(): void {
  if (currentPage.value > 1) state.value.page = currentPage.value - 1;
}

function nextPage(): void {
  if (currentPage.value < totalPages.value) state.value.page = currentPage.value + 1;
}

function contextText(entry: ModelEntry): string {
  const provider = providers.findProvider(entry.provider);
  const length = provider?.model_context_lengths
    ? provider.model_context_lengths[entry.model]
    : undefined;
  return length ? String(length) : '默认';
}

function pick(entry: ModelEntry): void {
  selected.value = entry.spec;
  props.onPick?.(entry.spec, entry);
}

onMounted(async () => {
  if (providers.providers.length) return;
  try {
    await providers.ensureProviders();
  } catch (err) {
    console.warn('load providers for llm picker failed', err);
  }
});
</script>

<template>
  <p class="card-help">
    选中即写入该配置项（保存插件配置并重载后生效）。存储值是模型标识符，形如 provider::model。
  </p>
  <div v-if="!entries.length" class="empty-state">
    暂无可用模型：请先到「AI 服务商」页面添加 Provider 并加载模型列表。
  </div>
  <article v-else class="card full-span">
    <input
      class="input table-search"
      :placeholder="`搜索${humanizeLabel('模型')}...`"
      :value="searchText"
      @input="onSearch"
    />
    <h3 class="card-title">{{ tableTitle }}</h3>
    <table class="simple-table">
      <thead>
        <tr>
          <th>Provider</th>
          <th>模型</th>
          <th>上下文长度</th>
          <th>选择</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!pageRows.length">
          <td class="table-empty" colspan="4">{{ emptyMessage }}</td>
        </tr>
        <tr v-for="entry in pageRows" :key="entry.spec">
          <td class="cell-primary">{{ entry.provider }}</td>
          <td>{{ entry.model }}</td>
          <td>{{ contextText(entry) }}</td>
          <td>
            <input
              class="provider-pick"
              type="radio"
              :name="radioName"
              :checked="selected === entry.spec"
              @change="pick(entry)"
            />
          </td>
        </tr>
      </tbody>
    </table>
    <div class="toolbar table-pager">
      <button class="btn btn-ghost" type="button" :disabled="currentPage <= 1" @click="prevPage">
        上一页
      </button>
      <span class="card-help">{{ pageInfo }}</span>
      <button
        class="btn btn-ghost"
        type="button"
        :disabled="currentPage >= totalPages"
        @click="nextPage"
      >
        下一页
      </button>
    </div>
  </article>
</template>
