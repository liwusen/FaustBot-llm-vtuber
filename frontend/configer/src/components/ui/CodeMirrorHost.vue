<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import type { EditorView } from '@codemirror/view';

const props = withDefaults(
  defineProps<{
    modelValue: string;
    language?: string;
    readOnly?: boolean;
    height?: string | number;
  }>(),
  { language: 'markdown', readOnly: false, height: '100%' },
);

const emit = defineEmits<{
  (event: 'update:modelValue', value: string): void;
}>();

const host = ref<HTMLElement | null>(null);
const view = shallowRef<EditorView | null>(null);

const cssHeight = computed(() => {
  if (typeof props.height === 'number') return `${props.height}px`;
  return props.height || '100%';
});

function onChange(next: string): void {
  emit('update:modelValue', next);
}

function create(initial?: string): void {
  if (!host.value) return;
  const factory = window.createCodeMirrorEditor;
  if (typeof factory !== 'function') {
    // 与旧 codemirror-editor.js 的降级文案一致。
    host.value.textContent = 'CodeMirror 加载失败，请刷新页面重试。';
    return;
  }
  const created = factory(host.value, initial ?? props.modelValue, {
    language: props.language,
    readOnly: props.readOnly,
    onChange,
  });
  view.value = created;
  void nextTick(measure);
}

function destroy(): void {
  const current = view.value;
  view.value = null;
  if (current) current.destroy();
}

onMounted(() => {
  create();
});

onBeforeUnmount(() => {
  destroy();
});

watch(
  () => props.modelValue,
  (next) => {
    const current = view.value;
    if (!current) return;
    if (current.state.doc.toString() === next) return;
    current.dispatch({
      changes: { from: 0, to: current.state.doc.length, insert: next },
    });
  },
);

watch(
  [() => props.language, () => props.readOnly],
  () => {
    const currentValue = view.value?.state.doc.toString() ?? props.modelValue;
    destroy();
    create(currentValue);
  },
);

/** 隐藏 tab 显示后，外部可调用 measure() 让 CodeMirror 重新测量布局。 */
function measure(): void {
  const current = view.value;
  if (!current) return;
  current.requestMeasure();
}

function getValue(): string {
  return view.value?.state.doc.toString() ?? props.modelValue;
}

function setValue(next: string): void {
  const current = view.value;
  if (!current) return;
  if (current.state.doc.toString() === next) return;
  current.dispatch({
    changes: { from: 0, to: current.state.doc.length, insert: next },
  });
}

function focus(): void {
  view.value?.focus();
}

function isReady(): boolean {
  return Boolean(view.value);
}

defineExpose({ getValue, setValue, measure, focus, isReady, view });
</script>

<template>
  <div ref="host" class="cm-host" :style="{ height: cssHeight }"></div>
</template>
