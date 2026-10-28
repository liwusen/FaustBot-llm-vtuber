// 详情栏：目录统计 / 文件元数据+标签+关联实体+图片 / 实体详情+关联文件。
// 异步请求带 token，选中项变化或组件卸载后丢弃过期响应，不写 DOM。

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import {
  detailDash,
  detailFmtTime,
  detailMsg,
  fileExtraKvRows,
  imageDataUrl,
  isImageMeta,
  subtreeStats,
  truncateEntityDesc,
} from '@/memory/detail';
import { normalizeKbPath } from '@/memory/kbUtils';
import { graphTypeColor } from '@/memory/graphCanvas';
import { useMemoryStore } from '@/stores/memory';
import { openImagePreview } from './dialogs';
import type { EntityChild, EntityDetail } from '@/stores/memory';

interface KvRow {
  label: string;
  value: string;
}

const store = useMemoryStore();

let alive = true;
onBeforeUnmount(() => {
  alive = false;
});

const selection = computed(() => store.selection);
const detail = computed(() => store.detail);

const entity = ref<EntityDetail | null>(null);
const entityLoading = ref(false);
const entityError = ref('');
const entities = ref<EntityChild[]>([]);
const entitiesLoading = ref(false);
const entitiesError = ref('');
const imageSrc = ref('');
const imageLoading = ref(false);
const imageError = ref('');
const imageEmpty = ref(false);

const newTag = ref('');
const tagError = ref('');
const extrasOpen = ref(false);

const heading = computed(() => {
  const sel = store.selection;
  if (sel.kind === 'file') {
    const node = store.findNode(sel.path);
    return String((node && node.name) || sel.path.split('/').pop() || sel.path || '');
  }
  if (sel.kind === 'dir') {
    const node = store.findNode(sel.path);
    return String((node && node.name) || sel.path.split('/').pop() || '/');
  }
  return '';
});

const tags = computed<string[]>(() => {
  const meta = detail.value ? detail.value.meta : null;
  const path = selection.value.kind === 'file' ? selection.value.path : '';
  if (meta && Array.isArray(meta.tags)) return (meta.tags as unknown[]).map(String);
  const node = store.findNode(path);
  return node && Array.isArray(node.tags) ? node.tags.map(String) : [];
});

const fileRows = computed<KvRow[]>(() => {
  const meta = (detail.value ? detail.value.meta : {}) || {};
  const path = selection.value.kind === 'file' ? selection.value.path : '';
  const rows: KvRow[] = [
    { label: '路径', value: path },
    { label: '更新时间', value: detailFmtTime(meta.updated_at) },
    { label: '创建者', value: detailDash(meta.declared_by) },
    { label: '索引块', value: detailDash(meta.chunk_count) },
    { label: '权重', value: detailDash(meta.score_patch) },
    { label: '索引状态', value: meta.indexed ? '已索引' : '未索引' },
  ];
  if (meta.content_type) rows.push({ label: '类型', value: String(meta.content_type) });
  if (meta.description) rows.push({ label: '描述', value: String(meta.description) });
  return rows;
});

const extraRows = computed<KvRow[]>(() =>
  fileExtraKvRows(detail.value ? detail.value.meta : {}).map((row) => ({
    label: row.label,
    value: detailDash(row.value),
  })),
);

const dirRows = computed<KvRow[]>(() => {
  if (selection.value.kind !== 'dir') return [];
  const path = normalizeKbPath(selection.value.path);
  const node = store.findNode(path);
  const stats = subtreeStats(node);
  const rows: KvRow[] = [
    { label: '路径', value: path },
    { label: '子目录', value: String(stats.dirs) },
    { label: '文件', value: String(stats.files) },
    { label: '索引块', value: String(stats.chunks) },
    { label: '最近更新', value: detailFmtTime(stats.updatedAt) },
  ];
  const description = node ? String(node.description || '') : '';
  if (description) rows.push({ label: '描述', value: description });
  return rows;
});

const entityRows = computed<KvRow[]>(() => {
  const current = entity.value;
  if (!current) return [];
  const rows: KvRow[] = [
    { label: '类型', value: String(current.entity_type || 'custom') },
    { label: '关系数', value: detailDash(current.relations_count) },
    { label: '创建时间', value: current.created_at ? detailFmtTime(current.created_at) : '—' },
  ];
  if (current.description) rows.push({ label: '描述', value: String(current.description) });
  return rows;
});

const entityProps = computed<KvRow[]>(() =>
  Object.entries((entity.value && entity.value.properties) || {}).map(([label, value]) => ({
    label,
    value: detailDash(value),
  })),
);

const linkedFiles = computed<string[]>(() => {
  const current = entity.value;
  return current && Array.isArray(current.linked_files)
    ? (current.linked_files as string[]).map(String)
    : [];
});

const isImage = computed(() => {
  if (selection.value.kind !== 'file') return false;
  return isImageMeta(detail.value ? detail.value.meta : {}, selection.value.path);
});

function typeColorOf(type: unknown): string {
  return graphTypeColor(type);
}

function sameSelection(snapshot: { kind: string; path?: string; entityId?: string }): boolean {
  const sel = store.selection;
  if (sel.kind !== snapshot.kind) return false;
  if (snapshot.kind === 'entity') {
    return (sel as { entityId: string }).entityId === snapshot.entityId;
  }
  return (sel as { path: string }).path === snapshot.path;
}

function validFileToken(seq: number, path: string): boolean {
  return (
    alive &&
    seq === store.detailSeq &&
    store.selection.kind === 'file' &&
    store.selection.path === path
  );
}

function resetLocal(): void {
  entity.value = null;
  entityLoading.value = false;
  entityError.value = '';
  entities.value = [];
  entitiesLoading.value = false;
  entitiesError.value = '';
  imageSrc.value = '';
  imageLoading.value = false;
  imageError.value = '';
  imageEmpty.value = false;
  extrasOpen.value = false;
  newTag.value = '';
  tagError.value = '';
}

async function loadEntities(path: string, seq: number): Promise<void> {
  entitiesLoading.value = true;
  entitiesError.value = '';
  entities.value = [];
  try {
    const items = await store.loadEntityChildren(path);
    if (!validFileToken(seq, path)) return;
    entities.value = items;
  } catch (err) {
    if (!validFileToken(seq, path)) return;
    console.error('[memory] 加载关联实体失败', path, err);
    entitiesError.value = detailMsg(err);
  } finally {
    if (validFileToken(seq, path)) entitiesLoading.value = false;
  }
}

async function loadImage(path: string, seq: number): Promise<void> {
  imageLoading.value = true;
  imageError.value = '';
  imageEmpty.value = false;
  imageSrc.value = '';
  try {
    const resp = await store.loadAttachment(path);
    if (!validFileToken(seq, path)) return;
    const src = imageDataUrl(resp);
    if (!src) {
      imageEmpty.value = true;
      return;
    }
    imageSrc.value = src;
  } catch (err) {
    if (!validFileToken(seq, path)) return;
    console.error('[memory] 加载图片失败', path, err);
    imageError.value = detailMsg(err);
  } finally {
    if (validFileToken(seq, path)) imageLoading.value = false;
  }
}

async function loadForSelection(): Promise<void> {
  resetLocal();
  const snapshot = store.selection;
  const seq = store.detailSeq;
  if (snapshot.kind === 'entity' && snapshot.entityId) {
    entityLoading.value = true;
    try {
      const payload = await store.loadEntityDetail(snapshot.entityId);
      if (!alive || seq !== store.detailSeq || !sameSelection(snapshot)) return;
      entity.value = payload;
    } catch (err) {
      if (!alive || seq !== store.detailSeq || !sameSelection(snapshot)) return;
      console.error('[memory] 加载实体详情失败', snapshot.entityId, err);
      entityError.value = detailMsg(err);
    } finally {
      if (alive && seq === store.detailSeq && sameSelection(snapshot)) entityLoading.value = false;
    }
    return;
  }
  if (snapshot.kind === 'file') {
    const path = snapshot.path;
    if (!store.detail) return;
    if (store.detail.error) return;
    void loadEntities(path, seq);
    if (isImageMeta(store.detail.meta || {}, path)) void loadImage(path, seq);
  }
}

watch(
  [() => store.selection, () => store.detailSeq, () => store.detail],
  () => {
    void loadForSelection();
  },
  { immediate: true },
);

async function writeTags(nextTags: string[]): Promise<void> {
  if (store.selection.kind !== 'file') return;
  tagError.value = '';
  const message = await store.writeTags(store.selection.path, nextTags);
  if (!alive) return;
  if (message) tagError.value = message;
}

function removeTag(tag: string): void {
  void writeTags(tags.value.filter((item) => item !== tag));
}

async function addTag(): Promise<void> {
  const value = newTag.value.trim();
  if (!value) {
    tagError.value = '请输入标签名';
    return;
  }
  if (tags.value.includes(value)) return;
  const next = tags.value.concat([value]);
  await writeTags(next);
  if (!tagError.value) newTag.value = '';
}

function openEntity(item: EntityChild): void {
  if (!item.id) return;
  store.setView('graph', false);
  store.selectEntity(item.id);
}

function previewImage(): void {
  if (imageSrc.value) openImagePreview(imageSrc.value);
}
</script>

<template>
  <div class="mem-detail">
    <template v-if="selection.kind === 'entity'">
      <div v-if="entityLoading" class="mem-empty">加载中…</div>
      <div v-else-if="entityError" class="mem-error">加载实体详情失败：{{ entityError }}</div>
      <div v-else-if="!entity" class="mem-error">实体不存在：{{ selection.entityId }}</div>
      <template v-else>
        <div class="mem-detail-title">{{ entity.name || selection.entityId }}</div>
        <div class="mem-detail-section">
          <div class="mem-detail-title">概览</div>
          <div class="mem-kv">
            <span class="mem-kv-key">名称</span>
            <span class="mem-kv-value"><span class="graph-legend-dot" :style="{ background: typeColorOf(entity.entity_type) }"></span> {{ entity.name || '(未命名)' }}</span>
          </div>
          <div v-for="row in entityRows" :key="row.label" class="mem-kv">
            <span class="mem-kv-key">{{ row.label }}</span>
            <span class="mem-kv-value">{{ row.value }}</span>
          </div>
          <template v-if="entityProps.length">
            <div class="mem-detail-title">属性</div>
            <div v-for="row in entityProps" :key="`prop-${row.label}`" class="mem-kv">
              <span class="mem-kv-key">{{ row.label }}</span>
              <span class="mem-kv-value">{{ row.value }}</span>
            </div>
          </template>
          <button
            v-if="linkedFiles.length"
            class="btn btn-quiet"
            type="button"
            @click="store.showLinkedFiles(linkedFiles)"
          >
            查看关联文件 {{ linkedFiles.length }} 个
          </button>
        </div>
      </template>
    </template>

    <template v-else-if="selection.kind === 'file'">
      <div class="mem-detail-title">{{ heading }}</div>
      <div v-if="!detail" class="mem-empty">加载中…</div>
      <div v-else-if="detail.error" class="mem-error">读取文件失败：{{ detail.error }}</div>
      <template v-else>
        <div class="mem-detail-section">
          <div class="mem-detail-title">概览</div>
          <div v-for="row in fileRows" :key="row.label" class="mem-kv">
            <span class="mem-kv-key">{{ row.label }}</span>
            <span class="mem-kv-value">{{ row.value }}</span>
          </div>
          <button
            class="btn btn-quiet"
            type="button"
            @click="extrasOpen = !extrasOpen"
          >
            {{ extrasOpen ? '收起' : '展开全部' }}
          </button>
          <div v-show="extrasOpen" class="mem-detail-section">
            <div v-for="row in extraRows" :key="`extra-${row.label}`" class="mem-kv">
              <span class="mem-kv-key">{{ row.label }}</span>
              <span class="mem-kv-value">{{ row.value }}</span>
            </div>
          </div>
        </div>

        <div class="mem-detail-section">
          <div class="mem-detail-title">标签</div>
          <div v-if="!tags.length" class="card-help">暂无标签</div>
          <div class="mem-tag-edit">
            <span v-for="tag in tags" :key="tag" class="mem-chip">
              <span>{{ tag }}</span>
              <button class="mem-chip-close" type="button" title="移除该标签" @click="removeTag(tag)">×</button>
            </span>
            <input v-model="newTag" class="input" placeholder="新标签" @keydown.enter.prevent="addTag" />
            <button class="btn btn-quiet" type="button" @click="addTag">添加</button>
          </div>
          <div v-show="tagError" class="mem-error">{{ tagError }}</div>
        </div>

        <div class="mem-detail-section">
          <div class="mem-detail-title">关联</div>
          <div v-if="entitiesLoading" class="card-help">加载关联实体…</div>
          <div v-else-if="entitiesError" class="mem-error">加载关联实体失败：{{ entitiesError }}</div>
          <div v-else-if="!entities.length" class="card-help">暂无关联实体</div>
          <template v-else>
            <div
              v-for="item in entities"
              :key="item.id"
              class="mem-entity-row"
              title="在图谱中查看该实体"
              @click="openEntity(item)"
            >
              <div>
                <span class="graph-legend-dot" :style="{ background: typeColorOf(item.entity_type || item.type) }"></span>
                <span class="mono">[{{ item.entity_type || item.type || 'custom' }}] {{ item.name || '(未命名)' }}</span>
              </div>
              <div v-if="item.description" class="card-help">{{ truncateEntityDesc(item.description) }}</div>
            </div>
          </template>
        </div>

        <div v-if="isImage" class="mem-detail-section">
          <div class="mem-detail-title">图片</div>
          <div v-if="imageLoading" class="card-help">加载图片…</div>
          <div v-else-if="imageError" class="mem-error">图片加载失败：{{ imageError }}</div>
          <div v-else-if="imageEmpty" class="mem-error">图片内容为空</div>
          <img
            v-else-if="imageSrc"
            class="mem-image"
            :alt="selection.path"
            :src="imageSrc"
            title="点击查看大图"
            @click="previewImage"
          />
        </div>
      </template>
    </template>

    <template v-else-if="selection.kind === 'dir'">
      <div class="mem-detail-title">{{ heading }}</div>
      <div class="mem-detail-section">
        <div class="mem-detail-title">概览</div>
        <div v-for="row in dirRows" :key="row.label" class="mem-kv">
          <span class="mem-kv-key">{{ row.label }}</span>
          <span class="mem-kv-value">{{ row.value }}</span>
        </div>
      </div>
    </template>

    <div v-else class="mem-empty">未选中节点</div>
  </div>
</template>
