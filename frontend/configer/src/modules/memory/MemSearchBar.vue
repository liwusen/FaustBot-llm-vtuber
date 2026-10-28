// 搜索栏：草稿 → 点「搜索」生效；高级面板（标签逻辑/排序/顺序/日期）。
// 输入只更新草稿，不重绘（避免焦点丢失）；排序/顺序变更立即重排当前结果。

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { parseTags, tagsKey } from '@/memory/search';
import type { MemFilter } from '@/memory/types';
import { useMemoryStore } from '@/stores/memory';

const store = useMemoryStore();

const draft = computed<MemFilter>(() => store.searchDraft);

const query = ref(draft.value.query);
const tagsText = ref((draft.value.tags || []).join(', '));
const tagLogic = ref(draft.value.tagLogic || 'AND');
const dateFrom = ref(draft.value.dateFrom || '');
const dateTo = ref(draft.value.dateTo || '');
const sortBy = ref(draft.value.sortBy || 'relevance');
const sortOrder = ref(draft.value.sortOrder || 'desc');

function patchDraft(patch: Partial<MemFilter>): void {
  store.searchDraft = { ...store.searchDraft, ...patch };
}

// 已生效条件被外部改写（清除/可视化点击/结果集切换）时同步表单
watch(
  draft,
  (next) => {
    if (query.value !== next.query) query.value = next.query;
    const joined = (next.tags || []).join(', ');
    if (tagsKey(parseTags(tagsText.value)) !== tagsKey(next.tags)) tagsText.value = joined;
    if (tagLogic.value !== next.tagLogic) tagLogic.value = next.tagLogic;
    if (dateFrom.value !== next.dateFrom) dateFrom.value = next.dateFrom;
    if (dateTo.value !== next.dateTo) dateTo.value = next.dateTo;
    if (sortBy.value !== next.sortBy) sortBy.value = next.sortBy;
    if (sortOrder.value !== next.sortOrder) sortOrder.value = next.sortOrder;
  },
  { deep: true },
);

function formValues(): MemFilter {
  return {
    query: String(query.value || '').trim(),
    tags: parseTags(tagsText.value),
    tagLogic: String(tagLogic.value || 'AND'),
    dateFrom: String(dateFrom.value || ''),
    dateTo: String(dateTo.value || ''),
    sortBy: String(sortBy.value || 'relevance'),
    sortOrder: String(sortOrder.value || 'desc'),
  };
}

function onQueryInput(): void {
  patchDraft({ query: String(query.value || '') });
}

function onTagsInput(): void {
  patchDraft({ tags: parseTags(tagsText.value) });
}

function onBasicChange(): void {
  patchDraft({ tagLogic: String(tagLogic.value || 'AND') });
}

function onDateChange(): void {
  patchDraft({ dateFrom: String(dateFrom.value || ''), dateTo: String(dateTo.value || '') });
}

function onSortChange(): void {
  patchDraft({ sortBy: String(sortBy.value || 'relevance'), sortOrder: String(sortOrder.value || 'desc') });
  store.applySort(String(sortBy.value || 'relevance'), String(sortOrder.value || 'desc'));
}

function runSearch(): void {
  void store.applySearch(formValues());
}
</script>

<template>
  <div class="mem-search">
    <div class="mem-search-basic">
      <input
        id="memSearchQuery"
        v-model="query"
        class="input"
        placeholder="关键词（文件名/描述/正文）"
        @input="onQueryInput"
        @keydown.enter="runSearch"
      />
      <input
        id="memSearchTags"
        v-model="tagsText"
        class="input"
        placeholder="标签，逗号分隔"
        @input="onTagsInput"
        @keydown.enter="runSearch"
      />
      <button id="memSearchGo" class="btn btn-go" type="button" @click="runSearch">搜索</button>
      <button id="memSearchAdv" class="btn btn-quiet" type="button" @click="store.toggleSearchAdvanced()">
        <span id="memSearchAdvLabel" class="mem-search-advlabel">高级 {{ store.searchAdvancedOpen ? '\u25B4' : '\u25BE' }}</span>
        <span
          id="memSearchAdvCount"
          class="mem-search-advcount"
          :hidden="!store.advancedCount"
        >{{ store.advancedCount ? String(store.advancedCount) : '' }}</span>
      </button>
      <span id="memSearchHint" class="mem-search-hint" :hidden="!store.searchDirty">{{ store.searchDirty ? '条件已修改，点「搜索」生效' : '' }}</span>
      <button
        v-if="store.searchResults !== null"
        id="memSearchClose"
        class="btn btn-exit"
        type="button"
        @click="store.clearFilter()"
      >
        关闭搜索
      </button>
    </div>
    <div id="memSearchAdvanced" class="mem-search-grid" :hidden="!store.searchAdvancedOpen">
      <label class="mem-field">
        标签逻辑
        <select id="memSearchTagLogic" v-model="tagLogic" class="select" @change="onBasicChange">
          <option value="AND">同时满足</option>
          <option value="OR">任一满足</option>
        </select>
      </label>
      <label class="mem-field">
        排序
        <select id="memSearchSortBy" v-model="sortBy" class="select" @change="onSortChange">
          <option value="relevance">相关度</option>
          <option value="updated_at">更新时间</option>
        </select>
      </label>
      <label class="mem-field">
        顺序
        <select id="memSearchSortOrder" v-model="sortOrder" class="select" @change="onSortChange">
          <option value="desc">降序</option>
          <option value="asc">升序</option>
        </select>
      </label>
      <div class="mem-field">
        更新日期
        <div class="mem-search-dates">
          <input id="memSearchDateFrom" v-model="dateFrom" type="date" @change="onDateChange" />
          <span class="mem-search-tilde">~</span>
          <input id="memSearchDateTo" v-model="dateTo" type="date" @change="onDateChange" />
        </div>
      </div>
    </div>
  </div>
</template>
