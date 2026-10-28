// 分块渲染：大列表按帧分批 append，避免一次性同步创建大量 DOM 卡顿。
// 与旧 libs/configer/dom-utils.js 的 appendChunked 行为一致。

export interface AppendChunkedOptions<T> {
  /** 每帧最多追加多少项，默认 50。 */
  chunkSize?: number;
  /** 每批追加完成后回调（参数为已处理数量与总数）。 */
  onProgress?: (index: number, total: number) => void;
  /** 全部追加完成后回调；空列表会同步回调。 */
  onDone?: (items: readonly T[]) => void;
}

export interface ChunkedHandle {
  cancel(): void;
}

function scheduleFrame(callback: () => void): { id: number; kind: 'raf' | 'timeout' } {
  if (typeof requestAnimationFrame === 'function') {
    return { id: requestAnimationFrame(callback), kind: 'raf' };
  }
  return {
    id: setTimeout(callback, 0) as unknown as number,
    kind: 'timeout',
  };
}

function cancelFrame(handle: { id: number; kind: 'raf' | 'timeout' } | null): void {
  if (!handle) return;
  if (handle.kind === 'raf') {
    if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(handle.id);
    return;
  }
  clearTimeout(handle.id);
}

export function appendChunked<T>(
  container: HTMLElement,
  items: readonly T[],
  renderFn: (item: T, index: number) => Node | null | undefined,
  options: AppendChunkedOptions<T> = {},
): ChunkedHandle {
  const chunkSize = options.chunkSize || 50;
  const list = Array.isArray(items) ? items : [];
  let index = 0;
  let cancelled = false;
  let frame: { id: number; kind: 'raf' | 'timeout' } | null = null;

  function nextChunk(): void {
    frame = null;
    if (cancelled) return;
    const end = Math.min(index + chunkSize, list.length);
    for (; index < end; index++) {
      const node = renderFn(list[index], index);
      if (node) container.appendChild(node);
    }
    if (options.onProgress) options.onProgress(index, list.length);
    if (index < list.length) {
      frame = scheduleFrame(nextChunk);
    } else if (options.onDone) {
      options.onDone(list);
    }
  }

  if (list.length === 0) {
    if (options.onDone) options.onDone(list);
  } else {
    nextChunk();
  }

  return {
    cancel(): void {
      cancelled = true;
      cancelFrame(frame);
      frame = null;
    },
  };
}

/** 等待下一帧；环境没有 rAF 时退化为 setTimeout(0)。 */
export function nextFrame(): Promise<void> {
  return new Promise((resolve) => {
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => resolve());
      return;
    }
    setTimeout(resolve, 0);
  });
}
