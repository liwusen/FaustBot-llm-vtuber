import { defineComponent, h, KeepAlive, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';

describe('KeepAlive epoch remount semantics', () => {
  it('changing the KeepAlive key unmounts cached persistent instances', async () => {
    const events: string[] = [];
    const Child = defineComponent({
      name: 'module-overview',
      setup() {
        events.push('setup');
        return () => h('div', 'child');
      },
      mounted() {
        events.push('mounted');
      },
      beforeUnmount() {
        events.push('unmounted');
      },
    });

    const Host = defineComponent({
      setup() {
        const epoch = ref(0);
        return { epoch };
      },
      render() {
        return h(
          KeepAlive,
          { key: this.epoch, include: ['module-overview'], max: 3 },
          { default: () => h(Child) },
        );
      },
    });

    const wrapper = mount(Host);
    expect(events).toEqual(['setup', 'mounted']);

    (wrapper.vm as unknown as { epoch: number }).epoch += 1;
    await nextTick();

    expect(events).toEqual(['setup', 'mounted', 'unmounted', 'setup', 'mounted']);
    wrapper.unmount();
  });
});
