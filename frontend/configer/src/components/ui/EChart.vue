<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { ECharts, EChartsOption } from 'echarts';

const props = withDefaults(
  defineProps<{
    option: unknown;
    height?: string | number;
  }>(),
  { height: '320px' },
);

const host = ref<HTMLElement | null>(null);
const unavailable = ref(false);
let chart: ECharts | null = null;
let observer: ResizeObserver | null = null;

const cssHeight = computed(() => {
  if (typeof props.height === 'number') return `${props.height}px`;
  return props.height || '320px';
});

function applyOption(): void {
  if (!chart || props.option === null || props.option === undefined) return;
  chart.setOption(props.option as EChartsOption, true);
}

function resize(): void {
  if (!chart) return;
  if (typeof chart.isDisposed === 'function' && chart.isDisposed()) return;
  chart.resize();
}

function onWindowResize(): void {
  resize();
}

function initChart(): void {
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
  applyOption();
  if (typeof ResizeObserver === 'function') {
    observer = new ResizeObserver(() => resize());
    observer.observe(host.value);
  }
  window.addEventListener('resize', onWindowResize);
}

onMounted(() => {
  void nextTick(() => {
    initChart();
    resize();
  });
});

watch(
  () => props.option,
  () => {
    if (!chart) initChart();
    applyOption();
  },
  { deep: true },
);

watch(cssHeight, () => {
  void nextTick(resize);
});

onBeforeUnmount(() => {
  if (observer) {
    observer.disconnect();
    observer = null;
  }
  window.removeEventListener('resize', onWindowResize);
  if (chart) {
    if (typeof chart.isDisposed === 'function' && chart.isDisposed()) {
      chart = null;
      return;
    }
    chart.dispose();
    chart = null;
  }
});

defineExpose({ resize });
</script>

<template>
  <div :style="{ height: cssHeight }">
    <div v-if="unavailable" class="empty-state">ECharts 未加载，无法显示图表。</div>
    <div v-else ref="host" style="width: 100%; height: 100%"></div>
  </div>
</template>
