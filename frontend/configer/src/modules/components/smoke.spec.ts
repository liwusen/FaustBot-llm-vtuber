// 组件页挂载冒烟：GPU / ASR / FunASR / TTS / Minecraft 卡片与操作按钮。
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { flushPromises, mount } from '@vue/test-utils';
import type { FrontendApi } from '@/api/types';
import ComponentsModule from './ComponentsModule.vue';

const STATUS = {
  gpu: {
    has_nvidia: true,
    gpus: [{ name: 'RTX 4090', cuda_version: '12.8', driver_version: '570.1' }],
  },
  components: {
    funasr: { installed: true, version: '1.2.0', torch_version: '2.4.0', torch_variant: 'cu128' },
    tts: { installed: true, path: 'tts-hub/GPT-SoVITS-v2pro', variant: 'nvidia50' },
    minecraft_bridge: { is_running: true },
  },
  services: {
    asr: { is_running: true, port: 10095 },
    tts: { is_running: false, port: 5000 },
    minecraft: { status: 'starting', is_running: false, port: 25566 },
  },
};

function installApi(): void {
  window.api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string) => {
      if (path === '/faust/components/status') return STATUS;
      return {};
    }),
  } as unknown as FrontendApi;
}

describe('Components module smoke', () => {
  let pinia: ReturnType<typeof createPinia>;

  beforeEach(() => {
    pinia = createPinia();
    setActivePinia(pinia);
    installApi();
  });

  it('renders GPU, ASR, FunASR, TTS and Minecraft cards', async () => {
    const wrapper = mount(ComponentsModule, { global: { plugins: [pinia] } });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('GPU 信息');
    expect(text).toContain('RTX 4090');
    expect(text).toContain('CUDA 版本');
    expect(text).toContain('ASR 服务');
    expect(text).toContain('运行中 端口 10095');
    expect(text).toContain('FunASR / Torch 依赖');
    expect(text).toContain('已安装 (funasr 1.2.0)');
    expect(text).toContain('2.4.0 (cu128)');
    expect(text).toContain('TTS GPU 类型');
    expect(text).toContain('nvidia50');
    expect(text).toContain('Minecraft 操作桥');
    expect(text).toContain('启动中 端口 25566');
    expect(text).toContain('后端启动时自动运行，无需下载');
    expect(text).toContain('终止');

    expect(wrapper.findAll('button').map((button) => button.text())).toEqual(
      expect.arrayContaining(['启动服务', '停止服务', '日志', '安装/重装', '重新下载']),
    );
  });

  it('submits funasr install with the detected variant', async () => {
    const wrapper = mount(ComponentsModule, { global: { plugins: [pinia] } });
    await flushPromises();
    const api = window.api as unknown as { configRequest: ReturnType<typeof vi.fn> };

    const install = wrapper.findAll('button').find((button) => button.text() === '安装/重装');
    await install?.trigger('click');
    await flushPromises();

    const call = api.configRequest.mock.calls.find(
      ([method, path]) => method === 'POST' && path === '/faust/components/install',
    );
    expect(call?.[2]).toEqual({
      component: 'funasr',
      torch_variant: 'cu128',
      use_aliyun_mirror: false,
    });
  });
});
