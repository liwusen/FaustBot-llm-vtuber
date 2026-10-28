<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { formatApiError } from '@/api/errors';
import { useArayaStore } from '@/stores/araya';
import { useUiStore } from '@/stores/ui';
import { formatScalar, humanizeLabel } from '@/utils/format';
import ArayaTriggerSlider from './ArayaTriggerSlider.vue';
import {
  ARAYA_TRACE_EMPTY,
  ARAYA_TRACE_WAITING,
  arayaOverviewBadge,
  arayaOverviewHelp,
  arayaOverviewStats,
  arayaRunEmptyText,
  arayaRunRows,
  arayaTimelineRows,
  arayaToolCount,
  arayaTraceMeta,
  arayaTraceTitle,
} from './format';

defineOptions({ name: 'module-araya' });

const store = useArayaStore();
const ui = useUiStore();

const TRIGGER_BUSY_HINT = 'Araya 正在运行，请等这次跑完';

const enabledDraft = ref(true);
const idleDraft = ref(30);

watch(
  () => store.status,
  (next) => {
    if (!next) return;
    enabledDraft.value = next.enabled !== false;
    idleDraft.value = Number(next.idle_minutes || 30);
  },
  { immediate: true },
);

const badge = computed(() => arayaOverviewBadge(store.status, store.running));
const stats = computed(() => arayaOverviewStats(store.status, store.lastLog));
const overviewHelp = computed(() => arayaOverviewHelp(store.status));
const runRows = computed(() => arayaRunRows(store.lastLog));
const hasLog = computed(() => Boolean(store.lastLog && Object.keys(store.lastLog).length));
const runEmptyText = computed(() => arayaRunEmptyText(store.status));
const runOutput = computed(() => String(store.lastLog?.response || '（本次没有文本输出）'));

const timelineRows = computed(() => arayaTimelineRows(store.activeTrace?.messages));
const traceTitle = computed(() => arayaTraceTitle(Boolean(store.liveTrace)));
const traceMeta = computed(() => (
  store.traceError && !store.liveTrace
    ? ''
    : arayaTraceMeta(arayaToolCount(timelineRows.value), store.activeTrace?.started_at_iso)
));
const triggerHint = computed(() => (
  store.running ? TRIGGER_BUSY_HINT : '向右拖动触发 Araya'
));

const traceEmptyText = computed(() => {
  if (store.liveTrace) return ARAYA_TRACE_WAITING;
  if (store.traceError) return `读取轨迹失败: ${store.traceError}`;
  const messages = store.activeTrace?.messages;
  const hasMessages = Array.isArray(messages) && messages.length > 0;
  return hasMessages ? ARAYA_TRACE_WAITING : ARAYA_TRACE_EMPTY;
});

async function triggerRun(): Promise<string | void> {
  if (store.running) return TRIGGER_BUSY_HINT;
  return store.startRun();
}

async function saveSettings(): Promise<void> {
  try {
    await store.saveSettings(enabledDraft.value, idleDraft.value);
    ui.showBanner('success', 'Araya 设置已保存。');
  } catch (err) {
    ui.showBanner('error', `保存失败: ${formatApiError(err)}`);
  }
}

async function refreshStatus(): Promise<void> {
  await Promise.all([store.loadStatus(), store.loadTrace()]);
}

let activatedOnce = false;

onMounted(async () => {
  await Promise.all([store.loadStatus(), store.loadTrace()]);
});

onActivated(async () => {
  if (!activatedOnce) {
    activatedOnce = true;
    return;
  }
  if (!store.consumeStale()) return;
  await Promise.all([store.loadStatus(), store.loadTrace()]);
});

onBeforeUnmount(() => {
  store.closeStream();
});
</script>

<template>
  <!-- 运行概览 -->
  <article class="card full-span">
    <div class="araya-card-head">
      <h3 class="card-title">运行概览</h3>
      <span :class="badge.className">{{ badge.text }}</span>
    </div>
    <div class="araya-stat-grid">
      <div v-for="cell in stats" :key="cell.label" class="araya-stat">
        <div class="araya-stat-value">{{ cell.value }}</div>
        <div class="araya-stat-label">{{ cell.label }}</div>
      </div>
    </div>
    <p class="card-help">{{ overviewHelp }}</p>
  </article>

  <!-- 自动维护设置 -->
  <article class="card full-span">
    <h3 class="card-title">自动维护设置</h3>
    <div class="toolbar">
      <label class="araya-check">
        <input v-model="enabledDraft" type="checkbox" />
        <span>启用自动维护</span>
      </label>
      <label class="araya-check">
        <input v-model.number="idleDraft" class="araya-number" type="number" min="1" step="1" />
        <span>空闲分钟</span>
      </label>
      <button class="btn btn-primary" type="button" @click="saveSettings">保存设置</button>
      <button class="btn btn-ghost" type="button" @click="refreshStatus">刷新状态</button>
    </div>
    <p v-if="store.status && store.status.enabled_by_config === false" class="card-help">
      配置文件里 ARAYA_ENABLED 为 false，这里的开关不会生效。
    </p>
  </article>

  <!-- 手动触发 -->
  <article class="card full-span">
    <h3 class="card-title">手动触发</h3>
    <ArayaTriggerSlider :trigger="triggerRun" :idle-hint="triggerHint" />
  </article>

  <!-- 最近一次运行 -->
  <article v-if="!hasLog" class="card full-span">
    <h3 class="card-title">最近一次运行</h3>
    <p class="card-help">{{ runEmptyText }}</p>
  </article>
  <article v-else class="card full-span">
    <h3 class="card-title">{{ humanizeLabel('最近一次运行') }}</h3>
    <table class="simple-table info-table">
      <tbody>
        <tr v-for="row in runRows" :key="row.label">
          <td class="cell-label">{{ humanizeLabel(row.label) }}</td>
          <td class="cell-value">{{ formatScalar(row.value, row.label) }}</td>
        </tr>
      </tbody>
    </table>
    <div class="araya-output">
      <div class="araya-output-label">Araya 输出</div>
      <div class="araya-output-text">{{ runOutput }}</div>
    </div>
  </article>

  <!-- 运行轨迹 -->
  <article class="card full-span">
    <div class="araya-card-head">
      <h3 class="card-title">{{ traceTitle }}</h3>
      <span class="araya-card-meta">{{ traceMeta }}</span>
    </div>
    <div class="araya-trace-host">
      <div v-if="timelineRows.length" class="araya-timeline">
        <div
          v-for="(row, index) in timelineRows"
          :key="index"
          class="araya-step"
          :class="`araya-step-${row.kind}`"
        >
          <span class="araya-step-dot"></span>
          <div class="araya-step-body">
            <template v-if="row.kind === 'assistant'">
              <div class="araya-step-label">Araya 输出</div>
              <div class="araya-step-text">{{ row.text }}</div>
            </template>
            <template v-else-if="row.kind === 'tool'">
              <details class="araya-tool" :open="!row.done">
                <summary>
                  <span class="araya-tool-caret">▸</span>
                  <span class="araya-tool-name">{{ row.name }}</span>
                  <span class="araya-tool-time">{{ row.time }}</span>
                </summary>
                <pre class="araya-tool-pre">{{ row.argsText }}</pre>
                <pre class="araya-tool-pre araya-tool-result">{{ row.resultText }}</pre>
              </details>
            </template>
            <template v-else>
              <div class="araya-step-label">{{ row.label }}</div>
              <pre class="araya-tool-pre">{{ row.resultText }}</pre>
            </template>
          </div>
        </div>
      </div>
      <div v-else class="empty-state">{{ traceEmptyText }}</div>
    </div>
  </article>
</template>
