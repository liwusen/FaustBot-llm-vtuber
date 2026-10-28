// 分帧渲染：大列表/树按帧分批显示，等价旧 libs/configer/dom-utils.js 的 appendChunked。
// 停用（KeepAlive deactivated）时暂停 rAF，激活后从断点继续；卸载取消。

import { onBeforeUnmount, ref, watch } from 'vue';
import type { Ref } from 'vue';

export interface ProgressiveOptions {
  /** 每帧最多显示多少项（首批同步显示同样数量）。 */
  chunkSize?: number;
  /** 返回 false 时一次性显示全部（旧列表在总数 ≤ 200 时同步渲染）。 */
  progressive?: () => boolean;
  /** 模块可见性；false 时暂停。 */
  active?: Ref<boolean>;
}

export interface ProgressiveResult<T> {
  visible: Ref<T[]>;
  restart: () => void;
}

export function useProgressiveRows<T>(
  source: () => readonly T[],
  options: ProgressiveOptions = {},
): ProgressiveResult<T> {
  const chunkSize = options.chunkSize || 100;
  const visible = ref([]) as Ref<T[]>;
  let frame: number | null = null;
  let index = 0;

  function cancel(): void {
    if (frame !== null) {
      if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame);
      frame = null;
    }
  }

  function schedule(): void {
    if (typeof requestAnimationFrame !== 'function') {
      visible.value = source().slice();
      index = source().length;
      return;
    }
    frame = requestAnimationFrame(step);
  }

  function step(): void {
    frame = null;
    if (options.active && !options.active.value) return;
    const list = source();
    index = Math.min(index + chunkSize, list.length);
    visible.value = list.slice(0, index);
    if (index < list.length) schedule();
  }

  function restart(): void {
    cancel();
    const list = source();
    if (!list.length) {
      index = 0;
      visible.value = [];
      return;
    }
    const progressive = options.progressive ? options.progressive() : true;
    if (!progressive || list.length <= chunkSize) {
      index = list.length;
      visible.value = list.slice();
      return;
    }
    index = chunkSize;
    visible.value = list.slice(0, index);
    if (!(options.active && !options.active.value)) schedule();
  }

  watch(source, restart, { immediate: true });

  if (options.active) {
    watch(options.active, (value) => {
      if (!value) cancel();
      else if (index < source().length && frame === null) schedule();
    });
  }

  onBeforeUnmount(cancel);

  return { visible, restart };
}
