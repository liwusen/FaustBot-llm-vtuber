import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushPromises, mount } from '@vue/test-utils';
import PluginHealthChart from './PluginHealthChart.vue';

const chart = {
  setOption: vi.fn(),
  resize: vi.fn(),
  dispose: vi.fn(),
  isDisposed: vi.fn(() => false),
};
const init = vi.fn(
  (_dom: HTMLElement, _theme?: unknown, _opts?: { renderer?: string }) => chart,
);

beforeEach(() => {
  init.mockClear();
  chart.setOption.mockClear();
  chart.resize.mockClear();
  chart.dispose.mockClear();
  chart.isDisposed.mockClear();
  window.echarts = { init } as unknown as typeof import('echarts');
});

afterEach(() => {
  delete (window as { echarts?: typeof import('echarts') }).echarts;
});

describe('PluginHealthChart', () => {
  it('initializes an SVG renderer chart and disposes it on unmount', async () => {
    const wrapper = mount(PluginHealthChart, {
      props: {
        plugin: { id: 'pet', name: 'pet', enabled: true, health: { status: 'healthy' } },
      },
    });
    await flushPromises();

    expect(init).toHaveBeenCalledTimes(1);
    expect(init.mock.calls[0][2]).toEqual({ renderer: 'svg' });
    expect(chart.setOption).toHaveBeenCalledTimes(1);

    wrapper.unmount();
    expect(chart.dispose).toHaveBeenCalledTimes(1);
  });

  it('does not initialize when echarts is unavailable', async () => {
    delete (window as { echarts?: typeof import('echarts') }).echarts;
    const wrapper = mount(PluginHealthChart, {
      props: { plugin: { id: 'pet', name: 'pet' } },
    });
    await flushPromises();

    expect(init).not.toHaveBeenCalled();
    expect(wrapper.find('.plugin-chart-badge').exists()).toBe(true);
    wrapper.unmount();
  });
});
