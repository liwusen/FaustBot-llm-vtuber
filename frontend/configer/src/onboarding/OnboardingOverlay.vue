<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import type { CSSProperties } from 'vue';
import { API } from '@/api/endpoints';
import { cfgApi } from '@/api/ipc';
import { CONFIG_ONBOARDING_STEPS, type OnboardingStep } from './steps';

const emit = defineEmits<{ complete: [] }>();

const active = ref(false);
const steps = ref<OnboardingStep[]>([]);
const index = ref(-1);
const highlightRect = ref<{ left: number; top: number; width: number; height: number } | null>(null);
const bubbleStyle = ref<CSSProperties>({});
const bubbleWithTarget = ref(false);
const bodyText = ref('');

let repositionTimer: number | null = null;
let autoStartTimer: number | null = null;

const step = computed<OnboardingStep | null>(() => steps.value[index.value] || null);
const hasAction = computed(() => typeof step.value?.action === 'function');
const isLast = computed(() => index.value >= steps.value.length - 1);

const highlightStyle = computed<CSSProperties>(() => {
  const r = highlightRect.value;
  if (!r) return { display: 'none' };
  return {
    display: 'block',
    left: `${r.left}px`,
    top: `${r.top}px`,
    width: `${r.width}px`,
    height: `${r.height}px`,
  };
});

function computeBubblePlacement(
  rect: DOMRect,
  vw: number,
  vh: number,
  opts: { bubbleWidth?: number; bubbleHeight?: number; gap?: number } = {},
): { side: 'top' | 'bottom'; left: number; top: number } {
  const bw = opts.bubbleWidth || 340;
  const bh = opts.bubbleHeight || 150;
  const gap = opts.gap || 14;
  const side = rect.bottom + bh + gap > vh && rect.top - bh - gap >= 0 ? 'top' : 'bottom';
  const left = Math.max(8, Math.min(rect.left + rect.width / 2 - bw / 2, vw - bw - 8));
  const top = side === 'bottom' ? rect.bottom + gap : Math.max(8, rect.top - bh - gap);
  return { side, left, top };
}

function positionOnTarget(): void {
  if (!active.value) return;
  const current = step.value;
  const target = current?.target ? document.querySelector(current.target) : null;
  if (!target) {
    highlightRect.value = null;
    bubbleWithTarget.value = false;
    bubbleStyle.value = {
      left: '50%',
      top: '38%',
      transform: 'translate(-50%, -50%)',
    };
    return;
  }
  const rect = target.getBoundingClientRect();
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const offscreen = rect.bottom < 0 || rect.top > vh || rect.right < 0 || rect.left > vw;
  if (offscreen) {
    target.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    if (repositionTimer !== null) window.clearTimeout(repositionTimer);
    repositionTimer = window.setTimeout(() => {
      if (!active.value) return;
      positionOnTarget();
    }, 360);
    return;
  }
  highlightRect.value = { left: rect.left, top: rect.top, width: rect.width, height: rect.height };
  const placement = computeBubblePlacement(rect, vw, vh);
  bubbleWithTarget.value = true;
  bubbleStyle.value = {
    left: `${placement.left}px`,
    top: `${placement.top}px`,
    transform: 'none',
  };
}

function renderStep(): void {
  const current = step.value;
  if (!current) return;
  bodyText.value = current.body || '';
  highlightRect.value = null;
  void nextTick(() => positionOnTarget());
}

function start(stepDefs: OnboardingStep[] = CONFIG_ONBOARDING_STEPS): void {
  if (!Array.isArray(stepDefs) || stepDefs.length === 0) return;
  steps.value = stepDefs;
  index.value = 0;
  active.value = true;
  renderStep();
}

function stop(): void {
  active.value = false;
  index.value = -1;
  highlightRect.value = null;
  if (repositionTimer !== null) {
    window.clearTimeout(repositionTimer);
    repositionTimer = null;
  }
}

function next(): void {
  if (!active.value || index.value < 0) return;
  if (isLast.value) {
    stop();
    emit('complete');
    void saveOnboardingFlag(true);
    return;
  }
  index.value += 1;
  renderStep();
}

function skip(): void {
  stop();
}

function dismiss(): void {
  stop();
  emit('complete');
  void saveOnboardingFlag(true);
}

function onTry(): void {
  const current = step.value;
  if (!current) return;
  if (typeof current.action === 'function') {
    try {
      current.action();
    } catch (err) {
      console.warn('onboarding action failed', err);
    }
  }
  if (current.actionHint) bodyText.value = current.actionHint;
}

function onOverlayClick(event: MouseEvent): void {
  if (event.target !== event.currentTarget) return;
  if (hasAction.value) return;
  next();
}

function onKeyDown(event: KeyboardEvent): void {
  if (!active.value) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    skip();
  } else if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault();
    next();
  }
}

function onResize(): void {
  if (!active.value) return;
  positionOnTarget();
}

async function saveOnboardingFlag(done: boolean): Promise<void> {
  try {
    const data = await cfgApi<{ settings?: Record<string, unknown> }>('GET', API.uiSetting);
    const settings = (data && data.settings) || {};
    await cfgApi('POST', API.uiSetting, {
      ...settings,
      widgets: (settings && settings.widgets) || {},
      onboarding: { done: !!done },
    });
  } catch (err) {
    console.warn('saveConfigOnboardingFlag failed', err);
  }
}

function maybeAutoStart(): void {
  try {
    void cfgApi<{ settings?: { onboarding?: { done?: boolean } } }>('GET', API.uiSetting)
      .then((data) => {
        const done = !!data?.settings?.onboarding?.done;
        if (done) return;
        autoStartTimer = window.setTimeout(() => {
          start(CONFIG_ONBOARDING_STEPS);
        }, 800);
      })
      .catch(() => {
        // 读不到 ui-setting 时不自动播放（与旧实现一致）。
      });
  } catch (err) {
    // window.api 缺失时只警告一次，不让 mounted 钩子产生未处理异常。
    console.warn('maybeStartConfigOnboarding failed', err);
  }
}

onMounted(() => {
  window.addEventListener('keydown', onKeyDown);
  window.addEventListener('resize', onResize);
  window.configAppUI = window.configAppUI || {};
  window.configAppUI.startConfigOnboarding = () => start(CONFIG_ONBOARDING_STEPS);
});

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKeyDown);
  window.removeEventListener('resize', onResize);
  if (repositionTimer !== null) window.clearTimeout(repositionTimer);
  if (autoStartTimer !== null) window.clearTimeout(autoStartTimer);
  if (window.configAppUI?.startConfigOnboarding) {
    delete window.configAppUI.startConfigOnboarding;
  }
});

defineExpose({ start, stop, maybeAutoStart });
</script>

<template>
  <div v-if="active" class="onboarding-root">
    <div
      class="onboarding-overlay"
      :class="{ passive: hasAction }"
      @click="onOverlayClick"
    />
    <div class="onboarding-highlight" :style="highlightStyle" />
    <div
      class="onboarding-bubble"
      :class="{ 'with-target': bubbleWithTarget }"
      :style="bubbleStyle"
    >
      <div class="onboarding-bubble-title">{{ step?.title }}</div>
      <div class="onboarding-bubble-body">{{ bodyText }}</div>
      <div v-if="hasAction" class="onboarding-bubble-try">
        <button type="button" class="onboarding-btn-try" @click="onTry">
          {{ step?.actionLabel || '让我试试' }}
        </button>
      </div>
      <div class="onboarding-bubble-actions">
        <button type="button" class="onboarding-btn-skip" @click="skip">跳过</button>
        <button type="button" class="onboarding-btn-dismiss" @click="dismiss">不再提示</button>
        <span class="onboarding-bubble-dots">
          <span
            v-for="(_, dotIndex) in steps"
            :key="dotIndex"
            class="onboarding-dot"
            :class="{ active: dotIndex === index }"
          />
        </span>
        <span class="onboarding-progress">{{ index + 1 }} / {{ steps.length }}</span>
        <button type="button" class="onboarding-btn-next" @click="next">
          {{ isLast ? '完成' : '下一步' }}
        </button>
      </div>
    </div>
  </div>
</template>
