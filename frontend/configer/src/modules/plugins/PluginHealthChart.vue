<script setup lang="ts">
// 插件列表行首的健康环：按旧 mountPluginChart 用 SVG renderer 绘制；
// 卸载/重渲染时 dispose，避免列表刷新后残留旧实例。
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import type { ECharts, EChartsOption } from 'echarts';
import { PLUGIN_HEALTH_RENDERER, pluginHealthChartOption, type PluginInfo } from './pluginPageOps';

const props = defineProps<{ plugin: PluginInfo }>();

const host = ref<HTMLElement | null>(null);
let chart: ECharts | null = null;
let observer: ResizeObserver | null = null;
let disposed = false;

function applyOption(): void {
  if (!chart) return;
  if (typeof chart.isDisposed === 'function' && chart.isDisposed()) {
    chart = null;
    return;
  }
  chart.setOption(pluginHealthChartOption(props.plugin) as EChartsOption, true);
}

function resize(): void {
  if (!chart) return;
  if (typeof chart.isDisposed === 'function' && chart.isDisposed()) return;
  chart.resize();
}

function initChart(): void {
  if (disposed || chart || !host.value) return;
  const echartsLib = window.echarts;
  if (!echartsLib) return;
  try {
    chart = echartsLib.init(host.value, null, { renderer: PLUGIN_HEALTH_RENDERER });
  } catch {
    chart = null;
    return;
  }
  applyOption();
  if (typeof ResizeObserver === 'function') {
    observer = new ResizeObserver(() => resize());
    observer.observe(host.value);
  }
  window.addEventListener('resize', resize);
}

onMounted(() => {
  void nextTick(initChart);
});

watch(
  () => props.plugin,
  () => {
    if (!chart) initChart();
    applyOption();
  },
  { deep: true },
);

onBeforeUnmount(() => {
  disposed = true;
  if (observer) {
    observer.disconnect();
    observer = null;
  }
  window.removeEventListener('resize', resize);
  if (chart) {
    if (!(typeof chart.isDisposed === 'function' && chart.isDisposed())) chart.dispose();
    chart = null;
  }
});
</script>

<template>
  <div class="plugin-chart-badge">
    <div ref="host" style="width: 100%; height: 100%"></div>
  </div>
</template>
