// 记忆可视化（echarts）的 option / 数据转换：逐条移植 modules/memory/visuals.js。
// 实例的 attach/dispose 由组件负责（MemChartHost.vue），本文件保持纯函数，便于单测。

import type { KbNode, MemRow } from '@/memory/types';

const BLUE = '#3f6be8';
const BLUE_MID = '#6f90ee';
const BLUE_SOFT = '#b9cbf7';
const BLUE_PALE = '#e6edf7';
const GRAY = '#647086';
const WHITE = '#ffffff';
const BLUE_RAMP = [BLUE_PALE, BLUE_SOFT, BLUE_MID, BLUE];
const CLOUD_COLORS = [BLUE, BLUE_MID, BLUE_MID, GRAY];

export const MEM_VIZ_EMPTY_ECHARTS = '图表组件未加载（echarts 不可达）';
export const MEM_VIZ_EMPTY_DATA = '暂无数据';
export const MEM_VIZ_EMPTY_DATED = '暂无更新时间数据';
export const MEM_VIZ_EMPTY_TAGS = '暂无标签数据';

export const CLOUD_MAX_WORDS = 60;
export const TREEMAP_NODE_LIMIT = 2000;
export const TREEMAP_LEAF_DEPTH = 2;
export const CALENDAR_DAYS = 365;

const DAY_MS = 24 * 60 * 60 * 1000;

export type ChartOption = Record<string, unknown>;

function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

export function utcDayKey(date: Date): string {
  return `${date.getUTCFullYear()}-${pad2(date.getUTCMonth() + 1)}-${pad2(date.getUTCDate())}`;
}

export function utcKeyToMs(key: unknown): number {
  const parts = String(key || '').split('-');
  if (parts.length !== 3) return NaN;
  const ms = Date.UTC(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  return Number.isNaN(ms) ? NaN : ms;
}

/** `2026-09-10T03:00:00Z` / 任意可解析值 → UTC 日期键，否则空串。 */
export function parseUtcDayKey(value: unknown): string {
  const raw = String(value == null ? '' : value).trim();
  if (!raw) return '';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return '';
  return utcDayKey(date);
}

/** 与旧 `textContent → innerHTML` 等价的最小转义（& < >）。 */
export function escapeHtml(value: unknown): string {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function chunkCountOf(file: Partial<MemRow> | null | undefined): number {
  const count = Number((file || {}).chunk_count);
  return Number.isFinite(count) && count > 0 ? count : 0;
}

export interface TagFreqEntry {
  name: string;
  value: number;
}

// ── cloud ──

/** 标签词频：同一文件内重复标签只计一次，按次数降序、名称 zh-Hans-CN 升序。 */
export function collectTagFreq(files: readonly MemRow[]): TagFreqEntry[] {
  const counts = new Map<string, number>();
  for (const file of files) {
    const tags = Array.isArray((file || {}).tags) ? (file as MemRow).tags : [];
    const seen = new Set<string>();
    for (const raw of tags) {
      const tag = String(raw == null ? '' : raw).trim();
      if (!tag || seen.has(tag)) continue;
      seen.add(tag);
      counts.set(tag, (counts.get(tag) || 0) + 1);
    }
  }
  const entries: TagFreqEntry[] = [];
  for (const [name, value] of counts) entries.push({ name, value });
  entries.sort(
    (a, b) => b.value - a.value || a.name.localeCompare(b.name, 'zh-Hans-CN'),
  );
  return entries;
}

/** 词云数据：前 60 个高频标签。 */
export function cloudEntries(files: readonly MemRow[]): TagFreqEntry[] {
  return collectTagFreq(files).slice(0, CLOUD_MAX_WORDS);
}

export function cloudOption(entries: readonly TagFreqEntry[]): ChartOption {
  return {
    animation: false,
    tooltip: { show: false },
    series: [
      {
        type: 'wordCloud',
        shape: 'circle',
        left: 'center',
        top: 'center',
        width: '96%',
        height: '96%',
        sizeRange: [12, 44],
        rotationRange: [0, 0],
        gridSize: 8,
        drawOutOfBound: false,
        layoutAnimation: false,
        textStyle: { fontWeight: 'normal' },
        data: entries.map((entry, index) => ({
          name: entry.name,
          value: entry.value,
          textStyle: { color: CLOUD_COLORS[index % CLOUD_COLORS.length] },
        })),
      },
    ],
  };
}

/** 词云数据源：无文件 → 「暂无数据」，有文件无标签 → 「暂无标签数据」。 */
export function cloudEmptyText(files: readonly MemRow[], entries: readonly TagFreqEntry[]): string | null {
  if (!files.length) return MEM_VIZ_EMPTY_DATA;
  if (!entries.length) return MEM_VIZ_EMPTY_TAGS;
  return null;
}

// ── timeline ──

export interface TimelineData {
  startKey: string;
  endKey: string;
  heat: Array<[string, number]>;
  cumFiles: Array<[string, number]>;
  cumChunks: Array<[string, number]>;
  maxDaily: number;
}

export type TimelineBuild =
  | { ok: true; data: TimelineData }
  | { ok: false; empty: string };

/** 365 天日历热力 + 累计文件/chunk 折线数据；无文件 → 暂无数据，无日期 → 暂无更新时间数据。 */
export function buildTimelineData(files: readonly MemRow[], now: Date = new Date()): TimelineBuild {
  if (!files.length) return { ok: false, empty: MEM_VIZ_EMPTY_DATA };
  const endMs = utcKeyToMs(utcDayKey(now));
  const startMs = endMs - (CALENDAR_DAYS - 1) * DAY_MS;
  const startKey = utcDayKey(new Date(startMs));
  const endKey = utcDayKey(new Date(endMs));
  const daily = new Map<string, { count: number; chunks: number }>();
  for (const file of files) {
    const key = parseUtcDayKey(file && file.updated_at);
    if (!key) continue;
    const ms = utcKeyToMs(key);
    if (Number.isNaN(ms) || ms < startMs || ms > endMs) continue;
    const bucket = daily.get(key) || { count: 0, chunks: 0 };
    bucket.count += 1;
    bucket.chunks += chunkCountOf(file);
    daily.set(key, bucket);
  }
  if (!daily.size) return { ok: false, empty: MEM_VIZ_EMPTY_DATED };
  const heat: Array<[string, number]> = [];
  const cumFiles: Array<[string, number]> = [];
  const cumChunks: Array<[string, number]> = [];
  let totalFiles = 0;
  let totalChunks = 0;
  let maxDaily = 1;
  for (let i = 0; i < CALENDAR_DAYS; i++) {
    const key = utcDayKey(new Date(startMs + i * DAY_MS));
    const bucket = daily.get(key);
    if (bucket) {
      totalFiles += bucket.count;
      totalChunks += bucket.chunks;
      if (bucket.count > maxDaily) maxDaily = bucket.count;
    }
    heat.push([key, bucket ? bucket.count : 0]);
    cumFiles.push([key, totalFiles]);
    cumChunks.push([key, totalChunks]);
  }
  return { ok: true, data: { startKey, endKey, heat, cumFiles, cumChunks, maxDaily } };
}

export function timelineOption(data: TimelineData): ChartOption {
  const { startKey, endKey, heat, cumFiles, cumChunks, maxDaily } = data;
  return {
    animation: false,
    tooltip: {
      trigger: 'item',
      borderColor: BLUE_SOFT,
      textStyle: { color: GRAY, fontSize: 12 },
      formatter(params: {
        value: unknown;
        seriesType?: string;
        seriesName?: string;
      }): string {
        const value = params.value;
        if (params.seriesType === 'heatmap') {
          const day = Array.isArray(value) ? value[0] : '';
          const count = Array.isArray(value) ? value[1] : 0;
          return `${escapeHtml(day)} 更新 ${Number(count) || 0} 个文件`;
        }
        const day = new Date((Array.isArray(value) ? value[0] : value) as string | number);
        const label = Number.isNaN(day.getTime()) ? '' : utcDayKey(day);
        return `${escapeHtml(label)} ${escapeHtml(params.seriesName)}：${Number(Array.isArray(value) ? value[1] : 0) || 0}`;
      },
    },
    calendar: {
      top: 28,
      left: 56,
      right: 28,
      bottom: '48%',
      range: [startKey, endKey],
      cellSize: ['auto', 13],
      splitLine: { show: false },
      itemStyle: { color: BLUE_PALE, borderColor: WHITE, borderWidth: 1 },
      dayLabel: { firstDay: 1, nameMap: 'ZH', color: GRAY, fontSize: 10 },
      monthLabel: { nameMap: 'ZH', color: GRAY, fontSize: 11 },
      yearLabel: { show: false },
    },
    visualMap: {
      type: 'continuous',
      min: 0,
      max: maxDaily,
      calculable: false,
      orient: 'horizontal',
      left: 'center',
      bottom: '40%',
      itemWidth: 12,
      itemHeight: 150,
      borderColor: BLUE_SOFT,
      indicatorStyle: { color: BLUE, borderColor: WHITE },
      textStyle: { color: GRAY, fontSize: 11 },
      inRange: { color: BLUE_RAMP },
    },
    grid: { left: 62, right: 62, top: '66%', height: '24%' },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: BLUE_SOFT } },
      axisLabel: { color: GRAY, fontSize: 11 },
      splitLine: { show: false },
    },
    yAxis: [
      {
        type: 'value',
        name: '文件数',
        nameTextStyle: { color: GRAY, fontSize: 11 },
        axisLine: { show: false },
        axisLabel: { color: GRAY, fontSize: 11 },
        splitLine: { lineStyle: { color: BLUE_PALE } },
      },
      {
        type: 'value',
        name: 'chunks',
        nameTextStyle: { color: GRAY, fontSize: 11 },
        axisLine: { show: false },
        axisLabel: { color: GRAY, fontSize: 11 },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '当日更新',
        type: 'heatmap',
        coordinateSystem: 'calendar',
        animation: false,
        data: heat,
        itemStyle: { borderColor: WHITE, borderWidth: 1 },
      },
      {
        name: '累计文件数',
        type: 'line',
        yAxisIndex: 0,
        animation: false,
        showSymbol: false,
        // 曲线只作参考：不参与命中，否则点日历格会被折线截走（seriesType 变 line，取不到日期）
        silent: true,
        data: cumFiles,
        lineStyle: { color: BLUE, width: 2 },
        itemStyle: { color: BLUE },
        areaStyle: { color: BLUE, opacity: 0.08 },
      },
      {
        name: '累计 chunk',
        type: 'line',
        yAxisIndex: 1,
        animation: false,
        showSymbol: false,
        silent: true,
        data: cumChunks,
        lineStyle: { color: GRAY, width: 2 },
        itemStyle: { color: GRAY },
      },
    ],
  };
}

// ── treemap ──

export function dirOf(path: unknown): string {
  const text = String(path || '');
  const idx = text.lastIndexOf('/');
  if (idx <= 0) return '/';
  return text.slice(0, idx);
}

function isDirNode(node: KbNode | null | undefined): boolean {
  return String((node || {}).type || '') === 'dir';
}

function countTreeNodes(node: KbNode | null | undefined, cap: number): number {
  if (!node || typeof node !== 'object') return 0;
  let total = 1;
  for (const child of Array.isArray(node.children) ? node.children : []) {
    if (!isDirNode(child) && String(child.type || '') !== 'file') continue;
    total += countTreeNodes(child, cap);
    if (total > cap) return total;
  }
  return total;
}

// Leaf tile fill ramps blue → light with depth; containers are drawn by echarts
// with itemStyle.borderColor (the gap colour), so their labels must stay
// readable on that light background.
function depthColor(depth: number): string {
  if (depth <= 1) return BLUE;
  if (depth === 2) return BLUE_MID;
  if (depth === 3) return BLUE_SOFT;
  return BLUE_PALE;
}

function depthTextColor(depth: number): string {
  return depth <= 2 ? WHITE : GRAY;
}

export interface TreemapDatum {
  name: string;
  value?: number;
  _memPath?: string;
  _memIsDir?: boolean;
  itemStyle?: { color: string };
  label?: { color: string };
  upperLabel?: { color: string };
  children?: TreemapDatum[];
}

interface Counter {
  count: number;
}

// node -> new datum (never mutates the input tree).
function buildTreemapNode(
  node: KbNode,
  depth: number,
  maxDepth: number,
  counter: Counter,
): TreemapDatum | null {
  if (counter.count > TREEMAP_NODE_LIMIT) return null;
  counter.count += 1;
  const dir = isDirNode(node);
  const path = String(node.path || '/');
  const datum: TreemapDatum = {
    name: String(node.name || path),
    _memPath: path,
    _memIsDir: dir,
  };
  if (!dir) {
    datum.value = Math.max(chunkCountOf(node as unknown as Partial<MemRow>), 1);
    datum.itemStyle = { color: depthColor(depth) };
    datum.label = { color: depthTextColor(depth) };
    return datum;
  }
  const children: TreemapDatum[] = [];
  let sum = 0;
  if (depth < maxDepth) {
    for (const child of Array.isArray(node.children) ? node.children : []) {
      const type = String((child || {}).type || '');
      if (type !== 'dir' && type !== 'file') continue;
      const built = buildTreemapNode(child, depth + 1, maxDepth, counter);
      if (!built) break;
      children.push(built);
      sum += built.value || 0;
    }
  }
  datum.value = Math.max(sum, 1);
  if (children.length) datum.children = children;
  // Nodes shallower than leafDepth render as containers (filled with the gap
  // colour), so their title band must be dark; deeper nodes render as coloured
  // leaves.
  const container = children.length > 0 && depth < TREEMAP_LEAF_DEPTH;
  const textColor = container ? GRAY : depthTextColor(depth);
  datum.itemStyle = { color: depthColor(depth) };
  datum.label = { color: textColor };
  datum.upperLabel = { color: textColor };
  return datum;
}

export interface TreemapBuild {
  datums: TreemapDatum[];
  degraded: boolean;
}

export type TreemapResult = { ok: true; data: TreemapBuild } | { ok: false; empty: string };

export function buildTreemapData(tree: KbNode | null | undefined): TreemapResult {
  if (!tree || typeof tree !== 'object') return { ok: false, empty: MEM_VIZ_EMPTY_DATA };
  const nodeTotal = countTreeNodes(tree, TREEMAP_NODE_LIMIT);
  const degraded = nodeTotal > TREEMAP_NODE_LIMIT;
  const rootDatum = buildTreemapNode(tree, 0, degraded ? 2 : 3, { count: 0 });
  const children = rootDatum && rootDatum.children ? rootDatum.children : [];
  // A dir root without renderable children (empty scope, or only entities) has
  // nothing to draw.
  if (!rootDatum || (isDirNode(tree) && !children.length)) {
    return { ok: false, empty: MEM_VIZ_EMPTY_DATA };
  }
  // The scope root itself is not drawn as a tile: the top level of the treemap
  // is its children, so leafDepth 2 exposes real files (and their dirs) and
  // every clickable tile has a path that onPickDir can use.
  const datums = children.length ? children : [rootDatum];
  return { ok: true, data: { datums, degraded } };
}

export function treemapOption(datums: readonly TreemapDatum[], degraded: boolean): ChartOption {
  return {
    animation: false,
    tooltip: {
      trigger: 'item',
      borderColor: BLUE_SOFT,
      textStyle: { color: GRAY, fontSize: 12 },
      formatter(params: { data?: TreemapDatum; name?: string; value?: unknown }): string {
        const datum = params.data || ({} as TreemapDatum);
        const lines = [escapeHtml(datum._memPath || params.name)];
        lines.push(`${datum._memIsDir ? '目录' : '文件'} · 权重 ${Number(params.value) || 0}`);
        return lines.join('<br/>');
      },
    },
    series: [
      {
        type: 'treemap',
        data: datums,
        animation: false,
        maxDepth: degraded ? 2 : 3,
        leafDepth: TREEMAP_LEAF_DEPTH,
        nodeClick: false,
        roam: false,
        breadcrumb: { show: false },
        width: '100%',
        height: '100%',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        label: { show: true, formatter: '{b}', fontSize: 12, overflow: 'truncate' },
        upperLabel: { show: true, height: 20, fontSize: 12, overflow: 'truncate' },
        itemStyle: { borderColor: BLUE_PALE, borderWidth: 1, gapWidth: 2 },
      },
    ],
  };
}

// ── 点击联动 ──

/** 词云点击：series 只给 name（或 data.name）。 */
export function cloudPickWord(params: unknown): string {
  const data = (params || {}) as { name?: unknown; data?: { name?: unknown } };
  return String(data.name || (data.data && data.data.name) || '').trim();
}

/** 时间线点击：heatmap 取日期数组首项，折线取时间戳。 */
export function timelinePickKey(params: unknown): string {
  const data = (params || {}) as { value?: unknown; seriesType?: string };
  const value = data.value;
  const ms = data.seriesType === 'heatmap'
    ? utcKeyToMs(Array.isArray(value) ? value[0] : value)
    : Number(Array.isArray(value) ? value[0] : value);
  if (!Number.isFinite(ms)) return '';
  return utcDayKey(new Date(ms));
}

/** Treemap 点击：目录取自身，文件取所在目录。 */
export function treemapPickDir(params: unknown): string {
  const data = (params || {}) as { data?: TreemapDatum };
  const datum = data.data || ({} as TreemapDatum);
  const path = String(datum._memPath || '');
  if (!path) return '';
  return datum._memIsDir ? path : dirOf(path);
}

/**
 * True only when echarts actually built a series model. 检查返回的 option 不够：
 * 未注册的 series 类型只留在 raw option 里，图表仍是空白。
 */
export function chartHasSeriesModel(chart: unknown, type: string): boolean {
  try {
    const instance = chart as {
      getModel?: () => { getSeriesByType?: (seriesType: string) => unknown[] } | null;
    };
    if (!instance || typeof instance.getModel !== 'function') return false;
    const model = instance.getModel();
    if (!model || typeof model.getSeriesByType !== 'function') return false;
    return model.getSeriesByType(type).length > 0;
  } catch {
    return false;
  }
}
