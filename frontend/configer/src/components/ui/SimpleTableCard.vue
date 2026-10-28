<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useUiStore } from '@/stores/ui';
import { formatScalar, humanizeLabel } from '@/utils/format';

const props = withDefaults(
  defineProps<{
    title: string;
    columns: string[];
    rows: unknown[][];
    pageSize?: number;
    searchKey?: number;
    emptyText?: string;
  }>(),
  { pageSize: 0, searchKey: -1, emptyText: '无数据' },
);

const ui = useUiStore();

// key = 卡片标题；组件重渲染后保留页码与搜索词（与旧 _tableUIState 一致）。
const state = computed(() => ui.tableState(props.title));
const searchText = ref(state.value.query);

watch(
  () => props.title,
  () => {
    searchText.value = state.value.query;
  },
);

const hasSearch = computed(() => Number(props.searchKey) >= 0);
const hasPager = computed(() => Number(props.pageSize) > 0);
const pageSize = computed(() => Math.max(0, Number(props.pageSize) || 0));

const allRows = computed<unknown[][]>(() => (Array.isArray(props.rows) ? props.rows : []));

const filteredRows = computed(() => {
  const query = state.value.query;
  if (!query) return allRows.value;
  const searchKey = Number(props.searchKey);
  return allRows.value.filter((row) => {
    const cell = row[searchKey];
    const text = cell === null || cell === undefined ? '' : String(cell);
    return text.toLowerCase().includes(query);
  });
});

const totalPages = computed(() => {
  if (!hasPager.value) return 1;
  return Math.max(1, Math.ceil(filteredRows.value.length / Math.max(1, pageSize.value)));
});

watch(totalPages, (next) => {
  if (state.value.page > next) state.value.page = next;
});

const currentPage = computed(() => {
  const page = Number(state.value.page) || 1;
  return Math.min(Math.max(1, page), totalPages.value);
});

const pageRows = computed(() => {
  if (!hasPager.value) return filteredRows.value;
  const start = (currentPage.value - 1) * pageSize.value;
  return filteredRows.value.slice(start, start + pageSize.value);
});

const pageInfo = computed(
  () => `第 ${currentPage.value} / ${totalPages.value} 页 · 共 ${filteredRows.value.length} 项`,
);

const emptyMessage = computed(() => (state.value.query ? '无匹配结果' : props.emptyText || '无数据'));

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
</script>

<template>
  <article class="card full-span">
    <input
      v-if="hasSearch"
      class="input table-search"
      :placeholder="`搜索${humanizeLabel(columns[searchKey])}...`"
      :value="searchText"
      @input="onSearch"
    />
    <h3 class="card-title">{{ humanizeLabel(title) }}</h3>
    <table class="simple-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ humanizeLabel(column) }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-if="!pageRows.length">
          <td class="table-empty" :colspan="columns.length">{{ emptyMessage }}</td>
        </tr>
        <tr v-for="(row, rowIndex) in pageRows" :key="rowIndex">
          <td
            v-for="(cell, cellIndex) in row"
            :key="cellIndex"
            :class="cellIndex === 0 ? 'cell-primary' : ''"
          >
            {{ formatScalar(cell, columns[cellIndex]) }}
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="hasPager" class="toolbar table-pager">
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
