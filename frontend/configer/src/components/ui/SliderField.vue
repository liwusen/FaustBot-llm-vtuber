<script setup lang="ts">
import { computed } from 'vue';
import { normalizeNumberInput } from '@/utils/format';

const props = withDefaults(
  defineProps<{
    modelValue: number | null;
    min: number;
    max: number;
    step: number;
    unit?: string;
    /** TTS 分块预览等分段结果；传入时渲染 .tts-split-preview（空数组也会渲染空容器）。 */
    preview?: string[];
  }>(),
  { unit: '' },
);

const emit = defineEmits<{
  (event: 'update:modelValue', value: number | null): void;
}>();

const numericValue = computed(() => {
  const value = Number(props.modelValue);
  return Number.isFinite(value) ? value : 0;
});

const displayValue = computed(
  () => `${numericValue.value.toFixed(props.step < 1 ? 2 : 0)}${props.unit || ''}`,
);

const fill = computed(() => {
  const span = props.max - props.min || 1;
  const ratio = ((numericValue.value - props.min) / span) * 100;
  return `${Math.max(0, Math.min(100, ratio))}%`;
});

const sliderStyle = computed<Record<string, string>>(() => ({ '--range-fill': fill.value }));

function onInput(event: Event): void {
  const raw = (event.target as HTMLInputElement).value;
  const fallback = props.modelValue === null ? props.min : props.modelValue;
  emit('update:modelValue', normalizeNumberInput(raw, fallback));
}
</script>

<template>
  <div class="slider-field">
    <div v-if="preview !== undefined" class="tts-split-preview">
      <span
        v-for="(segment, index) in preview"
        :key="index"
        class="tts-split-seg"
        :class="{ alt: index % 2 === 1 }"
      >
        {{ segment }}
      </span>
    </div>
    <input
      class="range-input"
      type="range"
      :min="min"
      :max="max"
      :step="step"
      :value="modelValue === null ? '' : modelValue"
      :style="sliderStyle"
      @input="onInput"
    />
    <div class="slider-meter">
      <strong class="slider-value">{{ displayValue }}</strong>
      <span class="slider-hint">{{ min }} - {{ max }}{{ unit }}</span>
    </div>
  </div>
</template>
