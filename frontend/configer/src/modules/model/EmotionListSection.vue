<script setup lang="ts">
import { ref } from 'vue';
import ImagesListSection from './ImagesListSection.vue';
import type { EmotionEntry, MouthShapeEntry } from './filters';

/**
 * Images 模型弹窗的「情绪变体」分组编辑。
 * 结构与文案与旧实现一致：每组一个 `.card`，组内名称行 + 「该情绪图片」列表。
 */
const props = defineProps<{
  modelValue: EmotionEntry[];
}>();

const emit = defineEmits<{
  (event: 'update:modelValue', value: EmotionEntry[]): void;
}>();

interface Row {
  id: number;
  name: string;
  images: string[];
}

let seq = 0;

function buildRows(): Row[] {
  const items = Array.isArray(props.modelValue) ? props.modelValue : [];
  return items.map((item) => ({
    id: seq++,
    name: String(item?.name || ''),
    images: Array.isArray(item?.images) ? [...item.images] : [],
  }));
}

const rows = ref<Row[]>(buildRows());

function emitUpdate(): void {
  emit(
    'update:modelValue',
    rows.value.map((row) => ({ name: row.name, images: row.images })),
  );
}

function addEmotion(): void {
  rows.value.push({ id: seq++, name: '', images: [] });
  emitUpdate();
}

function removeEmotion(id: number): void {
  rows.value = rows.value.filter((row) => row.id !== id);
  emitUpdate();
}

function onNameInput(row: Row, event: Event): void {
  row.name = (event.target as HTMLInputElement).value;
  emitUpdate();
}

function onImagesChange(row: Row, next: string[] | MouthShapeEntry[]): void {
  row.images = next as string[];
  emitUpdate();
}
</script>

<template>
  <div class="card full-span">
    <h3 class="card-title">情绪变体</h3>
    <p class="form-hint">
      按情绪分组配置的立绘。每组填写情绪名称（如 happy、angry）与对应图片；当 Agent
      表达该情绪时会切换到本组图片，持续时间由下方“情绪持续”控制。
    </p>
    <button class="btn btn-secondary" type="button" @click="addEmotion">添加情绪分组</button>
    <div style="display: flex; flex-direction: column; gap: 10px">
      <div v-if="!rows.length" class="empty-state">暂无情绪分组</div>
      <div v-for="row in rows" :key="row.id" class="card">
        <div class="toolbar">
          <input
            class="input"
            :value="row.name"
            placeholder="情绪名称，如 happy"
            @input="onNameInput(row, $event)"
          />
          <button class="btn btn-ghost" type="button" @click="removeEmotion(row.id)">
            删除分组
          </button>
        </div>
        <ImagesListSection
          title="该情绪图片"
          hint="该情绪触发时显示的图片，可配置多张。"
          :model-value="row.images"
          @update:model-value="onImagesChange(row, $event)"
        />
      </div>
    </div>
  </div>
</template>
