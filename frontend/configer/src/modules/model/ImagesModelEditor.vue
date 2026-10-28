<script setup lang="ts">
import { ref } from 'vue';
import { useConfigStore } from '@/stores/config';
import { useUiStore } from '@/stores/ui';
import EmotionListSection from './EmotionListSection.vue';
import ImagesListSection from './ImagesListSection.vue';
import { cloneImagesConfig } from './filters';
import type { EmotionEntry, ImagesModelConfig, MouthShapeEntry } from './filters';

/**
 * Images 模型编辑弹窗（旧 renderConfigModule 内联实现）。
 * 弹窗内容顺序：通用设置 → 默认图片 → 情绪变体 → Tap 图片 → 嘴型图片 → 操作。
 */
const config = useConfigStore();
const ui = useUiStore();

const draft = ref<ImagesModelConfig>(
  cloneImagesConfig(config.publicConfig.IMAGE_MODEL_CONFIG),
);

const scaleText = ref(String(draft.value.scale || 1.0));
const motionText = ref(String(draft.value.motionDurationMs || 3000));
const tapText = ref(String(draft.value.tapDurationMs || 700));

function onScaleInput(event: Event): void {
  const raw = (event.target as HTMLInputElement).value;
  scaleText.value = raw;
  draft.value.scale = Number(raw || 1.0);
}

function onMotionInput(event: Event): void {
  const raw = (event.target as HTMLInputElement).value;
  motionText.value = raw;
  draft.value.motionDurationMs = Number(raw || 3000);
}

function onTapInput(event: Event): void {
  const raw = (event.target as HTMLInputElement).value;
  tapText.value = raw;
  draft.value.tapDurationMs = Number(raw || 700);
}

function onBaseImages(next: string[] | MouthShapeEntry[]): void {
  draft.value.baseImages = next as string[];
}

function onTapImages(next: string[] | MouthShapeEntry[]): void {
  draft.value.tapImages = next as string[];
}

function onMouthShapes(next: string[] | MouthShapeEntry[]): void {
  draft.value.mouthShapes = next as MouthShapeEntry[];
}

function onEmotions(next: EmotionEntry[]): void {
  draft.value.emotions = next;
}

async function saveImages(): Promise<void> {
  // 传纯对象给 store/IPC，避免把 Vue 响应式 Proxy 带进结构化克隆。
  config.updateValue('public', 'IMAGE_MODEL_CONFIG', JSON.parse(JSON.stringify(draft.value)));
  config.updateValue('public', 'MODEL_TYPE', 'images');
  ui.closeModal();
  await config.saveConfig();
}
</script>

<template>
  <article class="card full-span">
    <h3 class="card-title">通用设置</h3>
    <div class="form-grid">
      <div class="form-field">
        <label class="form-field-label">图片缩放</label>
        <p class="form-hint">整体显示缩放倍数（0.1~4），1 为原始尺寸，用于适配窗口大小。</p>
        <div class="form-field-control">
          <input
            class="number"
            type="number"
            step="0.05"
            min="0.1"
            max="4"
            :value="scaleText"
            @input="onScaleInput"
          />
        </div>
      </div>
      <div class="form-field">
        <label class="form-field-label">情绪持续 (ms)</label>
        <p class="form-hint">切换到情绪立绘后保持的毫秒数，到时后回到默认图片。</p>
        <div class="form-field-control">
          <input class="number" type="number" :value="motionText" @input="onMotionInput" />
        </div>
      </div>
      <div class="form-field">
        <label class="form-field-label">Tap 持续 (ms)</label>
        <p class="form-hint">点击后显示 Tap 图片的毫秒数，到时后恢复。</p>
        <div class="form-field-control">
          <input class="number" type="number" :value="tapText" @input="onTapInput" />
        </div>
      </div>
    </div>
  </article>

  <ImagesListSection
    title="默认图片"
    hint="角色的常态/待机图片；若配置多张会随机或轮换显示，作为无特殊情绪时的默认立绘。"
    :model-value="draft.baseImages || []"
    @update:model-value="onBaseImages"
  />

  <EmotionListSection :model-value="draft.emotions || []" @update:model-value="onEmotions" />

  <ImagesListSection
    title="Tap 图片"
    hint="被点击（Tap）时短暂切换显示的图片，用于点击反馈；持续时间由下方“Tap 持续”控制。"
    :model-value="draft.tapImages || []"
    @update:model-value="onTapImages"
  />

  <ImagesListSection
    title="嘴型图片"
    hint="口型同步用图片，每张对应一个张嘴程度 openness（0=闭合，1=最大张开）；说话时按音量匹配显示。"
    mouth
    :model-value="draft.mouthShapes || []"
    @update:model-value="onMouthShapes"
  />

  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="saveImages">保存 Images 模型</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">关闭</button>
  </div>
</template>
