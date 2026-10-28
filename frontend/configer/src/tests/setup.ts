import { vi } from 'vitest';

// jsdom 没有实现 ResizeObserver，ECharts/CodeMirror 组件在单测中会用到。
if (!('ResizeObserver' in globalThis)) {
  class ResizeObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  (globalThis as unknown as { ResizeObserver: typeof ResizeObserverStub }).ResizeObserver =
    ResizeObserverStub;
}

const windowRecord = window as unknown as Record<string, unknown>;
if (typeof windowRecord.matchMedia !== 'function') {
  windowRecord.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}
