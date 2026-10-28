import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { mount } from '@vue/test-utils';
import type { FrontendApi } from '@/api/types';
import { useConfigStore } from '@/stores/config';
import ModelModule from './ModelModule.vue';
import ImagesModelEditor from './ImagesModelEditor.vue';
import SoullinkProfileEditor from './SoullinkProfileEditor.vue';

function installApi(publicOverrides: Record<string, unknown> = {}): void {
  const publicConfig = {
    MODEL_TYPE: 'live2d',
    LIVE2D_MODEL_PATH: '2D/Hiyori/Hiyori.model3.json',
    VRM_MODEL_PATH: 'VRM/Alicia.vrm',
    IMAGE_MODEL_CONFIG: { baseImages: [], emotions: [], tapImages: [], mouthShapes: [] },
    LIVE2D_MODEL_SCALE: 1,
    LIVE2D_MODEL_X: 0.5,
    LIVE2D_MODEL_Y: 0.5,
    LIVE2D_MOUSE_TRACKING_STRENGTH: 0.4,
    AUTO_IMAGE_ATTACH_ENABLED: false,
    TEXT_CHAT_BAR_Y_FACTOR: 0,
    FRONTEND_QUICK_CONTROLLER_X_OFFSET: 0,
    FRONTEND_CLICK_THROUGH: false,
    FRONTEND_DEFAULT_TTS_LANG: 'zh',
    PTT_MODE: false,
    ...publicOverrides,
  };
  const api = {
    backendBaseUrl: 'http://127.0.0.1:13900',
    configRequest: vi.fn(async (method: string, path: string) => {
      if (path === '/faust/admin/config') return { public: publicConfig, private: {} };
      if (path === '/faust/admin/live2d/models') {
        return {
          items: [
            { type: 'live2d', label: 'Hiyori', path: '2D/Hiyori/Hiyori.model3.json' },
            { type: 'vrm', label: 'Alicia', path: 'VRM/Alicia.vrm' },
          ],
        };
      }
      return { ok: true };
    }),
    ensureModelProfile: vi.fn(async () => ({
      ok: true,
      cached: false,
      profile: { a: 1 },
      coverage: { mappedKeyCount: 1, facsKeyCount: 2 },
    })),
    saveModelProfile: vi.fn(async () => ({ ok: true })),
  } as unknown as FrontendApi;
  window.api = api;
}

async function makeConfig(overrides: Record<string, unknown> = {}): Promise<void> {
  installApi(overrides);
  setActivePinia(createPinia());
  await useConfigStore().loadConfig();
}

describe('ModelModule smoke', () => {
  beforeEach(async () => {
    await makeConfig();
  });

  it('renders field cards and the model cards', async () => {
    const wrapper = mount(ModelModule);
    await vi.waitFor(() => expect(wrapper.text()).toContain('Hiyori'));
    expect(wrapper.text()).toContain('模型类型');
    expect(wrapper.text()).toContain('当前模型类型: LIVE2D');
    expect(wrapper.text()).toContain('可用模型');
    expect(wrapper.findAll('.field-card').length).toBe(13);
    expect(wrapper.text()).toContain('高级配置');
  });

  it('switches the model type through the switch card', async () => {
    const wrapper = mount(ModelModule);
    const config = useConfigStore();
    const button = wrapper.findAll('button').find((item) => item.text() === '切换到 VRM');
    expect(button).toBeTruthy();
    await button!.trigger('click');
    expect(config.publicConfig.MODEL_TYPE).toBe('vrm');
    expect(config.dirtyPublic.get('MODEL_TYPE')).toBe('vrm');
  });

  it('renders the images summary and opens the images editor', async () => {
    const wrapper = mount(ModelModule);
    const config = useConfigStore();
    config.updateValue('public', 'MODEL_TYPE', 'images');
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain('默认图 0 张 | 情绪 0 组 | Tap 图 0 张 | 嘴型图 0 张');
    expect(wrapper.find('.advanced-divider').exists()).toBe(true);
  });
});

describe('ImagesModelEditor smoke', () => {
  beforeEach(async () => {
    await makeConfig({
      IMAGE_MODEL_CONFIG: {
        baseImages: ['a.png'],
        emotions: [{ name: 'happy', images: ['b.png'] }],
        tapImages: [],
        mouthShapes: [{ path: 'c.png', openness: 0.5 }],
        scale: 1.2,
        motionDurationMs: 1500,
        tapDurationMs: 400,
      },
    });
  });

  it('renders all sections and saves through the store', async () => {
    const wrapper = mount(ImagesModelEditor);
    expect(wrapper.text()).toContain('通用设置');
    expect(wrapper.text()).toContain('默认图片');
    expect(wrapper.text()).toContain('情绪变体');
    expect(wrapper.text()).toContain('嘴型图片');
    expect(wrapper.findAll('.empty-state').length).toBe(1);

    const save = wrapper.findAll('button').find((item) => item.text() === '保存 Images 模型');
    await save!.trigger('click');
    const config = useConfigStore();
    expect(config.publicConfig.MODEL_TYPE).toBe('images');
    expect((config.publicConfig.IMAGE_MODEL_CONFIG as { scale: number }).scale).toBe(1.2);
  });

  it('adds a base image row and writes it back on save', async () => {
    const wrapper = mount(ImagesModelEditor);
    const sections = wrapper.findAll('.card.full-span');
    const defaultSection = sections.find(
      (item) => item.find('.card-title').exists() && item.find('.card-title').text() === '默认图片',
    );
    const addButton = defaultSection!.findAll('button').find((item) => item.text() === '添加');
    await addButton!.trigger('click');
    const save = wrapper.findAll('button').find((item) => item.text() === '保存 Images 模型');
    await save!.trigger('click');
    const config = useConfigStore();
    expect((config.publicConfig.IMAGE_MODEL_CONFIG as { baseImages: string[] }).baseImages).toEqual([
      'a.png',
      '',
    ]);
  });
});

describe('SoullinkProfileEditor smoke', () => {
  beforeEach(async () => {
    await makeConfig();
  });

  it('shows coverage status and regenerates the profile', async () => {
    const wrapper = mount(SoullinkProfileEditor, {
      props: {
        modelDir: 'Hiyori',
        initialProfile: { a: 1 },
        initialStatus: '（已生成） | 标准参数覆盖率: 1/2',
      },
    });
    expect(wrapper.text()).toContain('标准参数覆盖率: 1/2');
    const regenerate = wrapper.findAll('button').find((item) => item.text() === '重新生成');
    await regenerate!.trigger('click');
    await vi.waitFor(() => expect(wrapper.text()).toContain('已重新生成（启发式），保存后重载生效'));
  });
});
