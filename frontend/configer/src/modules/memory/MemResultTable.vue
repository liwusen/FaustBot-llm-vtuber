// 结果表 + 状态行 + 空态 + 分页（200 条/页）；表头点击只重排当前结果集。
// 「更新刻度尺」为内联 SVG：横轴新鲜度、高度/颜色规模档位。

<script setup lang="ts">
import { computed, ref, toRef } from 'vue';
import {
  MEM_LIST_SORT_KEYS,
  MEM_SCALE_LEVELS,
  formatRelative,
  formatScore,
  nextSort,
  parseMemoryDate,
  scaleLevel,
  scaleX,
} from '@/memory/sort';
import { isImagePath } from '@/memory/paths';
import type { MemRow } from '@/memory/types';
import { useMemoryStore } from '@/stores/memory';
import { openColumnDropdown, openContextMenu } from './menus';
import { useProgressiveRows } from './useProgressive';

interface UpdatedCell {
  x: string;
  y: string;
  height: string;
  color: string;
  empty: boolean;
  title: string;
  relative: string;
}

interface DisplayRow {
  key: string;
  row: MemRow;
  isDir: boolean;
  selected: boolean;
  glyph: string;
  tags: string[];
  updated: UpdatedCell | null;
  size: string;
  indexed: string;
  scorePatch: string;
  declaredBy: string;
  contentType: string;
}

const store = useMemoryStore();
const dots = ref<HTMLButtonElement | null>(null);

const { visible } = useProgressiveRows<MemRow>(() => store.pageRows, {
  chunkSize: 100,
  progressive: () => store.sortedRows.length > 200,
  active: toRef(store, 'moduleVisible'),
});

function updatedCell(row: MemRow): UpdatedCell | null {
  if (row.type === 'dir') return null;
  const date = parseMemoryDate(row.updated_at);
  if (!date) return null;
  const spec = MEM_SCALE_LEVELS[scaleLevel(row.chunk_count)];
  const count = Number(row.chunk_count || 0);
  const empty = !row.indexed || count === 0;
  return {
    x: scaleX(date).toFixed(2),
    y: String(11 - spec.height),
    height: String(spec.height),
    color: spec.color,
    empty,
    title: `${String(row.updated_at || '').slice(0, 10)} · ${count} 块`,
    relative: formatRelative(date),
  };
}

function decorate(row: MemRow): DisplayRow {
  const isDir = row.type === 'dir';
  const selected = store.selection.kind === 'dir'
    ? isDir && store.selection.path === row.path
    : !isDir && store.selection.kind === 'file' && store.selection.path === row.path;
  return {
    key: `${row.type}:${row.path}`,
    row,
    isDir,
    selected,
    glyph: isDir ? '\u25B8' : isImagePath(row.path) ? '\u25A4' : '\u25AA',
    tags: !isDir && Array.isArray(row.tags) ? row.tags : [],
    updated: updatedCell(row),
    size: isDir ? '—' : String(Number(row.chunk_count || 0)),
    indexed: isDir ? '—' : row.indexed ? '已索引' : '未索引',
    scorePatch: isDir ? '—' : String(Number(row.score_patch || 0)),
    declaredBy: isDir ? '—' : String(row.declared_by || '') || '—',
    contentType: isDir ? '—' : String(row.content_type || '') || '—',
  };
}

const displayRows = computed<DisplayRow[]>(() => visible.value.map(decorate));

const statusMode = computed<'none' | 'searching' | 'results' | 'filtered'>(() => {
  if (store.searching) return 'searching';
  if (store.searchResults === null) return 'none';
  return store.filterActive ? 'filtered' : 'results';
});

const hitText = computed(() => {
  const count = store.searchResults ? store.searchResults.length : 0;
  return count >= 200
    ? `命中 \u2265${count} 条（仅显示前 200 条，请收紧条件）`
    : `命中 ${count} 条`;
});

const resultChip = computed(
  () => `${store.resultLabel || '结果'} (${store.searchResults ? store.searchResults.length : 0})`,
);

const emptyMode = computed<'searching' | 'browse' | 'no-match'>(() => {
  if (store.searching) return 'searching';
  if (!store.filterActive && store.searchResults === null) return 'browse';
  return 'no-match';
});

const emptyScope = computed(() => {
  const dir = store.currentDir;
  return dir === '/' ? '全库' : `当前目录（${dir} 子树）`;
});

function headerLabel(label: string, key: string | null): string {
  if (!key) return label;
  if (store.sort.by !== key) return label;
  return `${label}${store.sort.order === 'asc' ? ' \u2197' : ' \u2198'}`;
}

function sortHeader(key: string | null): void {
  if (!key) return;
  store.setSort(nextSort(store.sort, key));
}

function onRowClick(item: DisplayRow): void {
  if (item.isDir) store.selectDir(item.row.path);
  else store.selectFile(item.row.path, { keepResults: true });
}

function onRowDblClick(item: DisplayRow): void {
  if (item.isDir) store.selectDir(item.row.path);
  else void store.openEditor(item.row.path, { keepResults: true });
}

function onRowContext(item: DisplayRow, event: MouseEvent): void {
  event.preventDefault();
  openContextMenu(event.clientX, event.clientY, {
    type: item.isDir ? 'dir' : 'file',
    path: item.row.path,
    name: item.row.name,
  });
}

function onTagClick(tag: string, event: MouseEvent): void {
  event.stopPropagation();
  store.toggleTag(tag);
}

function openDots(): void {
  openColumnDropdown(dots.value);
}

function focusSearch(): void {
  const input = document.getElementById('memSearchQuery') as HTMLInputElement | null;
  if (input && typeof input.focus === 'function') input.focus();
}
</script>

<template>
  <div>
    <div v-if="statusMode !== 'none'" class="mem-list-status">
      <template v-if="statusMode === 'searching'">
        <span class="card-help">检索中…</span>
      </template>
      <template v-else-if="statusMode === 'results'">
        <span class="mem-chip-info">{{ resultChip }}</span>
        <button class="btn btn-quiet" type="button" @click="store.backToBrowse()">返回浏览</button>
      </template>
      <template v-else>
        <span class="card-help">{{ hitText }}</span>
        <span class="card-help">· 表头排序仅重排当前结果，取数顺序在「高级」里改</span>
      </template>
    </div>

    <div v-if="!store.sortedRows.length" class="mem-empty">
      <template v-if="emptyMode === 'searching'">
        <div>检索中…</div>
      </template>
      <template v-else-if="emptyMode === 'browse'">
        <div>{{ store.currentDir === '/' ? '记忆库为空' : '当前目录为空' }}</div>
      </template>
      <template v-else>
        <div>没有符合条件的结果</div>
        <div class="card-help">检索范围：{{ emptyScope }}；共有 {{ store.vizFiles.length }} 个候选文件</div>
        <div class="toolbar">
          <button class="btn btn-quiet" type="button" @click="store.clearFilter()">关闭搜索</button>
          <button class="btn btn-quiet" type="button" @click="focusSearch">回到搜索栏</button>
        </div>
      </template>
    </div>

    <template v-else>
      <div class="mem-list-scroll">
        <table class="mem-list-table">
          <thead>
            <tr>
              <th class="sortable" @click="sortHeader('name')">{{ headerLabel('名称', 'name') }}</th>
              <th
                v-if="store.hasScore"
                class="sortable"
                @click="sortHeader('score')"
              >
                {{ headerLabel('相关度', 'score') }}
              </th>
              <th
                v-for="col in store.columnsUsed"
                :key="col.id"
                :class="{ sortable: Boolean(MEM_LIST_SORT_KEYS[col.id]) }"
                @click="sortHeader(MEM_LIST_SORT_KEYS[col.id] || null)"
              >
                {{ headerLabel(col.label, MEM_LIST_SORT_KEYS[col.id] || null) }}
              </th>
              <th class="mem-col-dots">
                <button
                  ref="dots"
                  class="btn-icon mem-dots"
                  type="button"
                  title="选择显示列"
                  @click="openDots"
                >
                  ⋯
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in displayRows"
              :key="item.key"
              :class="{ selected: item.selected }"
              @click="onRowClick(item)"
              @dblclick="onRowDblClick(item)"
              @contextmenu="onRowContext(item, $event)"
            >
              <td class="mem-col-name">
                <span>{{ item.glyph }} {{ item.row.name || item.row.path || '' }}</span>
              </td>
              <td v-if="store.hasScore" class="mem-col-score mono">
                {{ item.isDir ? '—' : formatScore(item.row.score) }}
              </td>
              <td
                v-for="col in store.columnsUsed"
                :key="col.id"
                :class="col.id === 'size' ? 'mem-col-size mono' : col.id === 'updated' ? 'mem-col-updated' : col.id === 'score_patch' ? 'mono' : ''"
              >
                <template v-if="col.id === 'tags'">
                  <template v-if="item.tags.length">
                    <template v-for="(tag, index) in item.tags" :key="tag">
                      {{ index ? ' ' : '' }}<span class="mem-chip" @click="onTagClick(tag, $event)"><span>{{ tag }}</span></span>
                    </template>
                  </template>
                  <template v-else>—</template>
                </template>
                <template v-else-if="col.id === 'updated'">
                  <template v-if="item.updated">
                    <template v-if="item.updated.empty">
                      <span>
                        <svg class="mem-scale" viewBox="0 0 60 12" width="60" height="12" role="img">
                          <rect
                            :x="item.updated.x"
                            :y="item.updated.y"
                            width="3"
                            :height="item.updated.height"
                            rx="1"
                            fill="none"
                            :stroke="item.updated.color"
                            stroke-width="1"
                          >
                            <title>{{ item.updated.title }}</title>
                          </rect>
                        </svg>
                        <span class="mem-scale-empty">未索引</span>
                      </span>
                    </template>
                    <svg v-else class="mem-scale" viewBox="0 0 60 12" width="60" height="12" role="img">
                      <rect
                        :x="item.updated.x"
                        :y="item.updated.y"
                        width="3"
                        :height="item.updated.height"
                        rx="1"
                        :fill="item.updated.color"
                      >
                        <title>{{ item.updated.title }}</title>
                      </rect>
                    </svg>
                    <span class="card-help mono">{{ item.updated.relative }}</span>
                  </template>
                  <template v-else>—</template>
                </template>
                <template v-else-if="col.id === 'size'">{{ item.size }}</template>
                <template v-else-if="col.id === 'indexed'">{{ item.indexed }}</template>
                <template v-else-if="col.id === 'score_patch'">{{ item.scorePatch }}</template>
                <template v-else-if="col.id === 'declared_by'">{{ item.declaredBy }}</template>
                <template v-else-if="col.id === 'content_type'">{{ item.contentType }}</template>
                <template v-else>—</template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div v-if="store.totalPages > 1" class="toolbar table-pager">
        <button
          class="btn btn-ghost"
          type="button"
          :disabled="store.currentPage <= 0"
          @click="store.setPage(store.currentPage - 1)"
        >
          上一页
        </button>
        <span class="card-help">第 {{ store.currentPage + 1 }} / {{ store.totalPages }} 页</span>
        <button
          class="btn btn-ghost"
          type="button"
          :disabled="store.currentPage >= store.totalPages - 1"
          @click="store.setPage(store.currentPage + 1)"
        >
          下一页
        </button>
      </div>
    </template>
  </div>
</template>
