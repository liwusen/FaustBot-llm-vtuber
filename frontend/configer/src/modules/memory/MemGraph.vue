// 图谱视图：深度 1-3、缩放/适应、实体搜索、BFS 展开、图例、双击打开关联文件。
// 单击节点 = 选中实体 + BFS；双击节点 = 打开关联文件（path: 前缀直接跳文件）。
// 切走（KeepAlive deactivated）暂停 rAF，卸载销毁 canvas 与全部监听。

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { GraphCanvas, graphTypeColor } from '@/memory/graphCanvas';
import { normalizeKbPath } from '@/memory/kbUtils';
import type { GraphEdge, GraphNode } from '@/memory/types';
import { useMemoryStore } from '@/stores/memory';
import type { GraphEntityItem } from '@/stores/memory';
import { useUiStore } from '@/stores/ui';

const store = useMemoryStore();
const ui = useUiStore();

const wrap = ref<HTMLElement | null>(null);
const query = ref('');
const depth = ref(3);
const status = ref('加载中…');
const legendTypes = ref<string[]>([]);
const results = ref<GraphEntityItem[]>([]);
const resultsVisible = ref(false);

let canvas: GraphCanvas | null = null;
let loadSeq = 0;
let bfsSeq = 0;
let alive = true;

const legendItems = computed(() => legendTypes.value.map((type) => ({ type, color: graphTypeColor(type) })));

function setStatus(text: string): void {
  status.value = text;
}

function makeNode(
  id: string,
  name: string | undefined,
  entityType: string | undefined,
  description?: string,
): GraphNode {
  return {
    id,
    name,
    entity_type: entityType,
    type: 'entity',
    description,
    x: 0,
    y: 0,
    vx: 0,
    vy: 0,
  };
}

function renderLegend(nodes: GraphNode[]): void {
  const types: string[] = [];
  for (const node of nodes || []) {
    const type = String(node.entity_type || node.type || 'custom');
    if (!types.includes(type)) types.push(type);
  }
  legendTypes.value = types;
}

function ensureCanvas(): GraphCanvas | null {
  if (canvas) return canvas;
  if (!wrap.value) return null;
  canvas = new GraphCanvas(wrap.value, {
    onNodeClick: (node) => {
      if (!canvas) return;
      canvas._selectedNode = node;
      canvas.render();
      store.selectEntity(node.id);
      void bfs(node.id, String(node.name || node.id), String(node.entity_type || 'custom'));
    },
    onNodeDblClick: (node) => {
      void openLinkedFile(node.id);
    },
  });
  return canvas;
}

function mountGraph(nodes: GraphNode[], edges: GraphEdge[]): void {
  const instance = ensureCanvas();
  if (!instance) return;
  instance.clearExpanded();
  instance.setData(nodes, edges);
  renderLegend(nodes);
  const sel = store.selection;
  if (sel.kind === 'entity' && sel.entityId) instance.focusNode(sel.entityId);
  instance.fitToScreen();
}

async function load(): Promise<void> {
  if (!wrap.value) return;
  const seq = ++loadSeq;
  setStatus('请求数据中…');
  const sel = store.selection;
  let nodes: GraphNode[] = [];
  let edges: GraphEdge[] = [];
  try {
    if (sel.kind === 'entity' && sel.entityId) {
      const expanded = await store.expandGraph(sel.entityId, depth.value);
      const head = await store.loadEntityHead(sel.entityId);
      nodes.push(
        makeNode(
          sel.entityId,
          String(head.name || sel.entityId),
          String(head.entity_type || 'custom'),
          head.description,
        ),
      );
      const seen = new Set<string>([sel.entityId]);
      for (const item of expanded.items) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        nodes.push(
          makeNode(item.id, item.name, String(item.entity_type || item.type), item.description),
        );
      }
      for (const edge of expanded.edges) {
        edges.push({ source: edge.source, target: edge.target, type: edge.type, key: store.graphEdgeKey(edge) });
      }
    } else {
      const centerPath = sel.kind === 'file' ? sel.path : store.currentDir;
      const centerId = `path:${centerPath}`;
      const entityChildren = await store.loadEntityChildren(centerPath);
      const seen = new Set<string>();
      for (const entity of entityChildren) {
        if (seen.has(entity.id)) continue;
        seen.add(entity.id);
        nodes.push(
          makeNode(entity.id, entity.name, String(entity.entity_type), entity.description),
        );
      }
      const expanded = await store.expandGraph(centerId, depth.value);
      const seenCenter = new Set<string>([centerId]);
      const centerName = centerPath === '/' ? '/' : centerPath.split('/').pop() || centerPath;
      nodes.unshift(
        makeNode(centerId, centerName, sel.kind === 'file' ? 'file' : 'dir'),
      );
      for (const item of expanded.items) {
        if (seenCenter.has(item.id)) continue;
        seenCenter.add(item.id);
        nodes.push(
          makeNode(item.id, item.name, String(item.entity_type || item.type), item.description),
        );
      }
      for (const edge of expanded.edges) {
        edges.push({ source: edge.source, target: edge.target, type: edge.type, key: store.graphEdgeKey(edge) });
      }
    }
  } catch (err) {
    console.error('[memory] 图谱加载失败', err);
    if (seq === loadSeq) setStatus(`加载失败: ${String((err as Error)?.message || err)}`);
    return;
  }
  if (seq !== loadSeq || !alive) return;
  await nextTick();
  if (seq !== loadSeq || !alive) return;
  mountGraph(nodes, edges);
  const truncated = nodes.length > 500 ? ' (超 500 已截断)' : '';
  setStatus(
    `实体: ${Math.min(nodes.length, 500)}${truncated} | 关系: ${edges.length} | 深度: ${depth.value}`,
  );
}

async function bfs(centerId: string, centerName: string, centerType: string): Promise<void> {
  if (!wrap.value) return;
  const seq = ++bfsSeq;
  setStatus(`BFS 展开 ${centerName}…`);
  try {
    const expanded = await store.expandGraph(centerId, depth.value);
    if (!alive || seq !== bfsSeq) return;
    const nodes: GraphNode[] = [makeNode(centerId, centerName, centerType || 'entity')];
    const seen = new Set<string>([centerId]);
    for (const item of expanded.items) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      nodes.push(
        makeNode(item.id, item.name, String(item.entity_type || item.type), item.description),
      );
    }
    const edges: GraphEdge[] = [];
    const edgeKeys = new Set<string>();
    for (const edge of expanded.edges) {
      const key = store.graphEdgeKey(edge);
      if (edgeKeys.has(key)) continue;
      edgeKeys.add(key);
      edges.push({ source: edge.source, target: edge.target, type: edge.type, key });
    }
    const instance = ensureCanvas();
    if (!instance) return;
    instance.clearExpanded();
    instance.setData(nodes, edges);
    instance.focusNode(centerId);
    renderLegend(nodes);
    setStatus(`实体: ${Math.min(nodes.length, 500)} | 关系: ${edges.length} (深度: ${depth.value})`);
  } catch (err) {
    console.error('[memory] BFS 展开失败', centerId, err);
    if (alive && seq === bfsSeq) setStatus(`BFS 失败: ${String((err as Error)?.message || err)}`);
  }
}

async function runSearch(): Promise<void> {
  const text = String(query.value || '').trim();
  if (!text) return;
  setStatus('搜索中…');
  resultsVisible.value = false;
  results.value = [];
  try {
    const items = await store.searchEntities(text, 20);
    if (!alive) return;
    if (!items.length) {
      setStatus('未找到匹配实体');
      return;
    }
    if (canvas) canvas.highlightIds(items.map((item) => item.id));
    results.value = items;
    resultsVisible.value = true;
    setStatus(`命中 ${items.length} 个实体`);
  } catch (err) {
    console.error('[memory] 实体搜索失败', err);
    if (alive) setStatus(`搜索失败: ${String((err as Error)?.message || err)}`);
  }
}

function pickResult(item: GraphEntityItem): void {
  store.selectEntity(item.id);
  void bfs(item.id, String(item.name || item.id), String(item.entity_type || 'entity'));
}

async function openLinkedFile(entityId: string): Promise<void> {
  const id = String(entityId || '');
  if (!id) return;
  if (id.startsWith('path:')) {
    const path = normalizeKbPath(id.slice('path:'.length) || '/');
    if (path && path !== '/') {
      store.setView('list', false);
      store.selectFile(path);
    }
    return;
  }
  const detail = await store.loadEntityHead(id);
  if (!alive) return;
  const files = Array.isArray(detail.linked_files) ? detail.linked_files : [];
  if (!files.length) {
    ui.showBanner('info', '该实体没有关联文件。');
    return;
  }
  store.setView('list', false);
  store.selectFile(files[0]);
}

function zoom(factor: number): void {
  if (canvas) canvas.zoomBy(factor);
}

function fit(): void {
  if (canvas) canvas.fitToScreen();
}

onMounted(() => {
  void nextTick(() => {
    void load();
  });
});

watch(
  () => store.moduleVisible,
  (visible) => {
    if (!canvas) return;
    if (visible) canvas.resume();
    else canvas.pause();
  },
);

onBeforeUnmount(() => {
  alive = false;
  loadSeq += 1;
  bfsSeq += 1;
  if (canvas) {
    canvas.destroy();
    canvas = null;
  }
});
</script>

<template>
  <div class="graph-toolbar">
    <input v-model="query" class="input mem-graph-search" placeholder="搜索实体" @keydown.enter="runSearch" />
    <button class="btn btn-quiet" type="button" @click="runSearch">搜索</button>
    <span class="card-help">{{ status }}</span>
    <span> | </span>
    <button class="btn btn-quiet" type="button" @click="zoom(1.2)">放大</button>
    <button class="btn btn-quiet" type="button" @click="zoom(1 / 1.2)">缩小</button>
    <button class="btn btn-quiet" type="button" @click="fit">适应</button>
    <span class="card-help">深度: {{ depth }}</span>
    <input v-model.number="depth" type="range" min="1" max="3" @change="load" />
  </div>
  <div ref="wrap" class="graph-canvas-wrap"></div>
  <div class="graph-legend">
    <template v-if="legendItems.length">
      <span class="card-help">图例:</span>
      <span v-for="item in legendItems" :key="item.type" class="graph-legend-item">
        <span class="graph-legend-dot" :style="{ background: item.color }"></span>
        {{ item.type }}
      </span>
    </template>
  </div>
  <div v-show="resultsVisible" class="list-box">
    <div
      v-for="item in results"
      :key="item.id"
      class="list-row clickable"
      @click="pickResult(item)"
    >
      <span class="graph-legend-dot" :style="{ background: graphTypeColor(item.entity_type || item.type) }"></span>
      <span class="mono">[{{ item.entity_type || 'entity' }}] {{ item.name }}</span>
      <span v-if="item.description" class="card-help">{{ String(item.description).slice(0, 80) }}</span>
    </div>
  </div>
</template>
