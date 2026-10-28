// ECharts 宿主：attach / resize / dispose 全在本组件内，点击回调透传给父组件。
// 共享 EChart.vue 目前没有 click 事件与实例暴露，记忆可视化需要点击联动，
// 因此在本模块内维护实例（不修改共享组件）。

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { ECharts, EChartsOption } from 'echarts';
import { MEM_VIZ_EMPTY_ECHARTS } from '@/memory/visuals';
import { useMemoryStore } from '@/stores/memory';

const props = defineProps<{ option: unknown }>();

const emit = defineEmits<{
  (event: 'ready', chart: unknown): void;
  (event: 'pick', params: unknown): void;
}>();

const store = useMemoryStore();
const host = ref<HTMLElement | null>(null);
const unavailable = ref(false);
let chart: ECharts | null = null;
let observer: ResizeObserver | null = null;

function apply(): void {
  if (!chart || props.option === null || props.option === undefined) return;
  chart.setOption(props.option as EChartsOption, true);
}

function resize(): void {
  if (chart && !chart.isDisposed()) chart.resize();
}

function init(): void {
  if (chart || !host.value) return;
  const echartsLib = window.echarts;
  if (!echartsLib) {
    unavailable.value = true;
    return;
  }
  try {
    chart = echartsLib.init(host.value);
  } catch {
    unavailable.value = true;
    return;
  }
  apply();
  chart.on('click', (params: unknown) => emit('pick', params));
  if (typeof ResizeObserver === 'function') {
    observer = new ResizeObserver(() => resize());
    observer.observe(host.value);
  }
  emit('ready', chart);
}

onMounted(() => {
  void nextTick(() => {
    init();
    resize();
  });
});

watch(
  () => props.option,
  () => {
    if (!chart) init();
    apply();
  },
  { deep: true },
);

// KeepAlive 重新激活后图表容器尺寸可能变化
watch(
  () => store.moduleVisible,
  (visible) => {
    if (visible) void nextTick(resize);
  },
);

onBeforeUnmount(() => {
  if (observer) {
    observer.disconnect();
    observer = null;
  }
  if (chart) {
    if (!chart.isDisposed()) chart.dispose();
    chart = null;
  }
});

function getChart(): unknown {
  return chart;
}

defineExpose({ getChart, resize });
</script>

<template>
  <div v-if="unavailable" class="mem-empty">{{ MEM_VIZ_EMPTY_ECHARTS }}</div>
  <div v-else class="mem-viz">
    <div ref="host" class="mem-viz-canvas"></div>
  </div>
</template>
