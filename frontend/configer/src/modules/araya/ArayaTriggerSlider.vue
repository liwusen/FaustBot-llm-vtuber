<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue';

const props = withDefaults(
  defineProps<{
    /** 触发回调；返回字符串时直接作为提示文案（旧实现约定）。 */
    trigger: () => Promise<unknown>;
    idleHint?: string;
  }>(),
  { idleHint: '向右拖动触发 Araya' },
);

const wrap = ref<HTMLDivElement | null>(null);
const canvas = ref<HTMLCanvasElement | null>(null);
const hint = ref(props.idleHint);

const MIN_W = 320;
const H = 46;
const pad = 10;
const knobR = 14;

let dpr = 1;
let W = MIN_W;
let x = 0;
let v = 0;
let target = 0;
let dragging = false;
let draggingOffset = 0;
let armed = false;
let triggered = false;
let raf = 0;
let resetTimer: ReturnType<typeof setTimeout> | null = null;
let resizeObserver: ResizeObserver | null = null;

function currentDpr(): number {
  return window.devicePixelRatio || 1;
}

const min = (): number => pad + knobR;
const max = (): number => W - pad - knobR;
const range = (): number => max() - min();
const threshold = (): number => range() * 0.82;

// 画布铺满卡片宽度（创建时可能还没进 DOM → 先按最小宽度，ResizeObserver 再校正）
function measuredWidth(): number {
  return Math.max(MIN_W, Math.floor(wrap.value?.clientWidth || 0));
}

function setupCanvas(): void {
  const el = canvas.value;
  if (!el) return;
  dpr = currentDpr();
  W = measuredWidth();
  el.width = Math.floor(W * dpr);
  el.height = Math.floor(H * dpr);
  el.style.width = `${W}px`;
  el.style.height = `${H}px`;
}

function roundRect(
  ctx: CanvasRenderingContext2D,
  rx: number,
  ry: number,
  rw: number,
  rh: number,
  rr: number,
): void {
  const r = Math.min(rr, rw / 2, rh / 2);
  ctx.beginPath();
  ctx.moveTo(rx + r, ry);
  ctx.arcTo(rx + rw, ry, rx + rw, ry + rh, r);
  ctx.arcTo(rx + rw, ry + rh, rx, ry + rh, r);
  ctx.arcTo(rx, ry + rh, rx, ry, r);
  ctx.arcTo(rx, ry, rx + rw, ry, r);
  ctx.closePath();
}

function draw(): void {
  const el = canvas.value;
  if (!el) return;
  const ctx = el.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const cy = H / 2;
  const trackY = cy - 12;
  const trackH = 24;
  const trackX = pad;
  const trackW = W - pad * 2;

  roundRect(ctx, trackX, trackY, trackW, trackH, 12);
  ctx.fillStyle = '#ecf2fb';
  ctx.fill();
  ctx.strokeStyle = '#cfd9e8';
  ctx.lineWidth = 1;
  ctx.stroke();

  const clampedX = Math.max(-28, Math.min(range(), x));
  const knobX = min() + clampedX;
  const progressW = Math.max(0, Math.min(trackW, knobX - trackX));

  if (progressW > 2) {
    const grad = ctx.createLinearGradient(trackX, 0, trackX + progressW, 0);
    grad.addColorStop(0, '#6f96ff');
    grad.addColorStop(1, '#3f6be8');
    roundRect(ctx, trackX, trackY, progressW, trackH, 12);
    ctx.fillStyle = grad;
    ctx.fill();
  }

  const tX = min() + threshold();
  ctx.beginPath();
  ctx.moveTo(tX, trackY + 4);
  ctx.lineTo(tX, trackY + trackH - 4);
  ctx.strokeStyle = 'rgba(63,107,232,0.45)';
  ctx.lineWidth = 2;
  ctx.stroke();

  if (clampedX < 0) {
    ctx.beginPath();
    ctx.moveTo(min(), cy);
    ctx.quadraticCurveTo(min() + clampedX * 0.45, cy - 8, knobX, cy);
    ctx.strokeStyle = 'rgba(229,68,71,0.45)';
    ctx.lineWidth = 3;
    ctx.stroke();
  }

  ctx.beginPath();
  ctx.arc(knobX, cy, knobR, 0, Math.PI * 2);
  ctx.fillStyle = triggered ? '#2c9158' : '#ffffff';
  ctx.fill();
  ctx.strokeStyle = triggered ? '#2c9158' : '#3f6be8';
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(knobX - 4, cy - 5);
  ctx.lineTo(knobX + 3, cy);
  ctx.lineTo(knobX - 4, cy + 5);
  ctx.strokeStyle = triggered ? '#ffffff' : '#3f6be8';
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.stroke();
}

function tick(): void {
  raf = 0;
  // 页面重建后画布已脱离文档：动画自动停
  if (!canvas.value?.isConnected) return;
  if (!dragging) {
    const force = (target - x) * 0.18;
    v = v * 0.78 + force;
    x += v;
    if (Math.abs(target - x) < 0.03 && Math.abs(v) < 0.03) {
      x = target;
      v = 0;
    }
  }
  draw();
  if (!dragging && (Math.abs(target - x) >= 0.03 || Math.abs(v) >= 0.03)) startLoop();
}

// 只在需要动画时跑 rAF（拖动中由 pointermove 直接重绘），静止时零开销
function startLoop(): void {
  if (!raf && canvas.value?.isConnected) raf = window.requestAnimationFrame(tick);
}

function stopLoop(): void {
  if (raf) window.cancelAnimationFrame(raf);
  raf = 0;
}

function setTarget(value: number): void {
  target = value;
  startLoop();
}

function pointX(evt: PointerEvent): number {
  const el = canvas.value;
  if (!el) return 0;
  const rect = el.getBoundingClientRect();
  return evt.clientX - rect.left;
}

function updateByPointer(px: number): void {
  const dx = px - min() - draggingOffset;
  x = dx < 0 ? dx * 0.35 : Math.min(range() + 8, dx);
  armed = x >= threshold();
  if (!triggered) hint.value = armed ? '松手触发 Araya' : props.idleHint;
}

async function releaseHandle(): Promise<void> {
  dragging = false;
  if (armed && !triggered) {
    triggered = true;
    setTarget(range());
    hint.value = '触发中...';
    draw();
    try {
      // trigger 可以用字符串说明「为什么没触发」，直接当作提示文案
      const message = await props.trigger();
      hint.value = typeof message === 'string' && message ? message : '触发成功';
    } catch {
      hint.value = '触发失败，请重试';
    }
    if (resetTimer) clearTimeout(resetTimer);
    resetTimer = setTimeout(() => {
      resetTimer = null;
      triggered = false;
      armed = false;
      setTarget(0);
      hint.value = props.idleHint;
    }, 800);
    return;
  }
  armed = false;
  setTarget(0);
  hint.value = props.idleHint;
}

function onPointerDown(evt: PointerEvent): void {
  const el = canvas.value;
  if (!el || triggered) return;
  el.setPointerCapture(evt.pointerId);
  dragging = true;
  v = 0;
  target = x;
  stopLoop();
  const px = pointX(evt);
  draggingOffset = px - (min() + x);
}

function onPointerMove(evt: PointerEvent): void {
  if (!dragging || triggered) return;
  updateByPointer(pointX(evt));
  draw();
}

async function onPointerUp(evt: PointerEvent): Promise<void> {
  if (!dragging) return;
  const el = canvas.value;
  if (el && el.hasPointerCapture(evt.pointerId)) el.releasePointerCapture(evt.pointerId);
  await releaseHandle();
}

async function onLostCapture(): Promise<void> {
  if (dragging) await releaseHandle();
}

watch(
  () => props.idleHint,
  (next) => {
    if (!dragging && !triggered) hint.value = next;
  },
);

onMounted(() => {
  setupCanvas();
  draw();
  if (window.ResizeObserver) {
    resizeObserver = new ResizeObserver(() => {
      if (!canvas.value?.isConnected) {
        resizeObserver?.disconnect();
        return;
      }
      if (measuredWidth() === W) return;
      setupCanvas();
      x = Math.max(-28, Math.min(range(), x));
      draw();
    });
    if (wrap.value) resizeObserver.observe(wrap.value);
  }
});

onBeforeUnmount(() => {
  stopLoop();
  resizeObserver?.disconnect();
  resizeObserver = null;
  if (resetTimer) clearTimeout(resetTimer);
  resetTimer = null;
});
</script>

<template>
  <div ref="wrap" class="araya-trigger-wrap">
    <canvas
      ref="canvas"
      class="araya-trigger-canvas"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="onPointerUp"
      @lostpointercapture="onLostCapture"
    ></canvas>
    <div class="araya-trigger-hint">{{ hint }}</div>
  </div>
</template>
