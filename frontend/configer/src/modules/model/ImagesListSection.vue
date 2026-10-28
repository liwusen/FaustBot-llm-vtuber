<script setup lang="ts">
import { ref } from 'vue';
import { openFileDialog } from '@/api/ipc';
import type { MouthShapeEntry } from './filters';

/**
 * Images 模型弹窗里的一个图片列表区块（默认图 / Tap 图 / 嘴型图 / 情绪图复用）。
 * 文案、按钮顺序、占位符与旧 buildListSection 一致。
 */
const props = withDefaults(
  defineProps<{
    title: string;
    hint?: string;
    modelValue: string[] | MouthShapeEntry[];
    mouth?: boolean;
  }>(),
  { hint: '', mouth: false },
);

const emit = defineEmits<{
  (event: 'update:modelValue', value: string[] | MouthShapeEntry[]): void;
}>();

interface Row {
  id: number;
  path: string;
  openness: string;
}

let seq = 0;

function buildRows(): Row[] {
  const items = Array.isArray(props.modelValue) ? props.modelValue : [];
  return items.map((item) => {
    if (props.mouth) {
      const shape = (item || {}) as MouthShapeEntry;
      return {
        id: seq++,
        path: String(shape.path || ''),
        openness: String(shape.openness ?? 0),
      };
    }
    return { id: seq++, path: String(item ?? ''), openness: '' };
  });
}

const rows = ref<Row[]>(buildRows());

function emitUpdate(): void {
  if (props.mouth) {
    emit(
      'update:modelValue',
      rows.value.map((row) => ({ path: row.path, openness: Number(row.openness || 0) })),
    );
    return;
  }
  emit('update:modelValue', rows.value.map((row) => row.path));
}

function add(): void {
  rows.value.push({ id: seq++, path: '', openness: props.mouth ? '0' : '' });
  emitUpdate();
}

function remove(id: number): void {
  rows.value = rows.value.filter((row) => row.id !== id);
  emitUpdate();
}

function onPathInput(row: Row, event: Event): void {
  row.path = (event.target as HTMLInputElement).value;
  emitUpdate();
}

function onOpennessInput(row: Row, event: Event): void {
  row.openness = (event.target as HTMLInputElement).value;
  emitUpdate();
}

async function pickImage(row: Row): Promise<void> {
  try {
    const filePath = await openFileDialog({
      title: '选择图片',
      filters: [{ name: '图片', extensions: ['png', 'jpg', 'jpeg', 'webp', 'gif', 'bmp'] }],
    });
    if (!filePath) return;
    row.path = filePath;
    emitUpdate();
  } catch (err) {
    console.error('选择图片失败', err);
  }
}
</script>

<template>
  <div class="card full-span">
    <h3 class="card-title">{{ title }}</h3>
    <p v-if="hint" class="form-hint">{{ hint }}</p>
    <button class="btn btn-secondary" type="button" @click="add">添加</button>
    <div style="display: flex; flex-direction: column; gap: 8px">
      <div v-if="!rows.length" class="empty-state">暂无项目</div>
      <div
        v-for="row in rows"
        :key="row.id"
        class="toolbar"
        style="align-items: center"
      >
        <input
          class="input"
          :value="row.path"
          placeholder="图片路径（支持任意磁盘位置的绝对路径）"
          @input="onPathInput(row, $event)"
        />
        <input
          v-if="mouth"
          class="number"
          type="number"
          min="0"
          max="1"
          step="0.05"
          title="嘴巴张开程度 0~1"
          :value="row.openness"
          @input="onOpennessInput(row, $event)"
        />
        <button class="btn btn-ghost" type="button" @click="pickImage(row)">选择图片</button>
        <button class="btn btn-ghost" type="button" @click="remove(row.id)">删除</button>
      </div>
    </div>
  </div>
</template>
