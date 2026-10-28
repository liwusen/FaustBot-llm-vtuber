// 可视化卡片（通栏）：词云 / 时间线 / Treemap。
// 只在选中目录时展开；数据源始终是当前目录整棵子树（忽略检索条件）。
// 图表重建条件：目录/选中态、tab、当前目录、树数据版本任一变化。

<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import {
  buildTimelineData,
  buildTreemapData,
  chartHasSeriesModel,
  cloudEmptyText,
  cloudEntries,
  cloudOption,
  cloudPickWord,
  timelineOption,
  timelinePickKey,
  treemapOption,
  treemapPickDir,
} from '@/memory/visuals';
import type { TagFreqEntry } from '@/memory/visuals';
import { MEM_VIZ_TABS, useMemoryStore } from '@/stores/memory';
import MemChartHost from './MemChartHost.vue';

interface VizState {
  kind: string;
  empty: string | null;
  option: unknown;
  entries: TagFreqEntry[];
}

const store = useMemoryStore();
const cloudDegraded = ref(false);
const chartRef = ref<InstanceType<typeof MemChartHost> | null>(null);

const dirSelected = computed(() => store.selection.kind === 'dir');
const files = computed(() => store.vizFiles);
const noFiles = computed(() => dirSelected.value && !files.value.length);

const chartKey = computed(
  () => `${dirSelected.value ? 'dir' : 'other'}|${store.vizTab}|${store.currentDir}|${store.treeVersion}`,
);

const viz = computed<VizState>(() => {
  const tab = store.vizTab;
  if (tab === 'cloud') {
    const entries = cloudEntries(files.value);
    const empty = cloudEmptyText(files.value, entries);
    return { kind: 'cloud', empty, option: empty ? null : cloudOption(entries), entries };
  }
  if (tab === 'timeline') {
    const built = buildTimelineData(files.value);
    if (!built.ok) return { kind: 'timeline', empty: built.empty, option: null, entries: [] };
    return { kind: 'timeline', empty: null, option: timelineOption(built.data), entries: [] };
  }
  const built = buildTreemapData(store.vizScopedTree);
  if (!built.ok) return { kind: 'treemap', empty: built.empty, option: null, entries: [] };
  return {
    kind: 'treemap',
    empty: null,
    option: treemapOption(built.data.datums, built.data.degraded),
    entries: [],
  };
});

watch(chartKey, () => {
  cloudDegraded.value = false;
});

/** 词云降级为 chip 列表时的点击（与图表点击同一联动）。 */
function onPickTag(name: string): void {
  store.toggleTag(name);
}

function onReady(chart: unknown): void {
  if (viz.value.kind !== 'cloud') {
    cloudDegraded.value = false;
    return;
  }
  cloudDegraded.value = !chartHasSeriesModel(chart, 'wordCloud');
}

function onPick(params: unknown): void {
  const kind = viz.value.kind;
  if (kind === 'cloud') {
    const word = cloudPickWord(params);
    if (word) store.toggleTag(word);
    return;
  }
  if (kind === 'timeline') {
    const key = timelinePickKey(params);
    if (key) void store.applyCondition({ dateFrom: key, dateTo: key }, { view: 'list' });
    return;
  }
  if (kind === 'treemap') {
    const path = treemapPickDir(params);
    if (path) store.selectDir(path);
  }
}
</script>

<template>
  <div class="mem-viz-card" :class="{ 'mem-viz-off': !dirSelected }">
    <template v-if="!dirSelected">
      <div class="mem-viz-hint">可视化不可用，请选择文件夹</div>
    </template>
    <template v-else>
      <div class="mem-viz-head">
        <span class="mem-viz-title">可视化</span>
        <div class="seg mem-viz-tabs">
          <button
            v-for="tab in MEM_VIZ_TABS"
            :key="tab.id"
            class="seg-item"
            :class="{ active: store.vizTab === tab.id }"
            type="button"
            :data-viz="tab.id"
            @click="store.setVizTab(tab.id)"
          >
            {{ tab.label }}
          </button>
        </div>
      </div>
      <div class="mem-viz-body">
        <div v-if="noFiles" class="mem-empty">当前范围内没有可统计的文件</div>
        <template v-else>
          <div v-if="viz.empty" class="mem-empty">{{ viz.empty }}</div>
          <template v-else-if="viz.option">
            <MemChartHost
              v-if="!(viz.kind === 'cloud' && cloudDegraded)"
              :key="chartKey"
              ref="chartRef"
              :option="viz.option"
              @ready="onReady"
              @pick="onPick"
            />
            <div v-else class="mem-chips">
              <button
                v-for="entry in viz.entries"
                :key="entry.name"
                class="mem-chip"
                type="button"
                @click="onPickTag(entry.name)"
              >
                {{ entry.name }} ({{ entry.value }})
              </button>
            </div>
          </template>
        </template>
      </div>
    </template>
  </div>
</template>
