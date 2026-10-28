<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import FieldCard from '@/components/config/FieldCard.vue';
import { API } from '@/api/endpoints';
import { cfgApi, ensureModelProfile } from '@/api/ipc';
import { useConfigStore } from '@/stores/config';
import { useUiStore } from '@/stores/ui';
import ImagesModelEditor from './ImagesModelEditor.vue';
import SoullinkProfileEditor from './SoullinkProfileEditor.vue';
import {
  MODEL_TYPE_OPTIONS,
  filterModelsByType,
  formatProfileStatus,
  normalizeModelPath,
  normalizeModelType,
  pickModelConfigKey,
  pickModelFieldKeys,
  resolveModelDir,
  splitAdvancedKeys,
  summarizeImagesConfig,
} from './filters';
import type { ModelListEntry } from './filters';

defineOptions({ name: 'module-live2d' });

const config = useConfigStore();
const ui = useUiStore();

const modelType = computed(() => normalizeModelType(config.publicConfig.MODEL_TYPE));

const publicKeys = computed(() => pickModelFieldKeys(config.publicConfig).publicKeys);
const basicPublic = computed(() => splitAdvancedKeys(publicKeys.value).basic);
const advancedPublic = computed(() => splitAdvancedKeys(publicKeys.value).advanced);
const advancedCount = computed(() => advancedPublic.value.length);
const totalFieldCount = computed(() => publicKeys.value.length);

const advancedOpen = computed(() => ui.foldState('advanced:live2d') === true);

function toggleAdvanced(): void {
  ui.setFoldState('advanced:live2d', !advancedOpen.value);
}

const models = ref<ModelListEntry[]>([]);
const modelsLoaded = ref(false);
const loadError = ref('');

const imagesSummary = computed(() => summarizeImagesConfig(config.publicConfig.IMAGE_MODEL_CONFIG));
const filteredModels = computed(() => filterModelsByType(models.value, modelType.value));
const switchTargets = computed(() => MODEL_TYPE_OPTIONS.filter((type) => type !== modelType.value));

function errorText(err: unknown): string {
  const message = (err as { message?: unknown })?.message;
  return String(message || err);
}

onMounted(async () => {
  try {
    const data = await cfgApi<{ items?: ModelListEntry[] }>('GET', API.live2dModels);
    models.value = Array.isArray(data?.items) ? data.items : [];
  } catch (err) {
    loadError.value = errorText(err);
  } finally {
    modelsLoaded.value = true;
  }
});

async function switchModelType(target: string): Promise<void> {
  config.updateValue('public', 'MODEL_TYPE', target);
  await config.saveConfig();
}

async function useModel(item: ModelListEntry): Promise<void> {
  config.updateValue('public', pickModelConfigKey(modelType.value), String(item.path || ''));
  config.updateValue('public', 'MODEL_TYPE', modelType.value);
  await config.saveConfig();
}

function openImagesEditor(): void {
  ui.openModal(ImagesModelEditor, {}, '编辑 Images 模型');
}

async function openSoullinkProfile(item: ModelListEntry): Promise<void> {
  const rawPath = normalizeModelPath(item.path);
  const modelDir = resolveModelDir(rawPath);
  if (!modelDir) {
    ui.showBanner('error', `无法从模型路径解析模型目录: ${rawPath}`);
    return;
  }
  if (!window.api || typeof window.api.ensureModelProfile !== 'function') {
    ui.showBanner('error', '当前环境不支持 soullink profile（缺少 window.api.ensureModelProfile）');
    return;
  }
  try {
    const result = await ensureModelProfile(modelDir);
    if (!result || !result.ok) throw new Error((result && result.error) || '未知错误');
    const label = String(item.label || modelDir);
    ui.openModal(
      SoullinkProfileEditor,
      {
        modelDir,
        initialProfile: (result.profile || {}) as Record<string, unknown>,
        initialStatus: formatProfileStatus(result),
      },
      `参数映射 - ${label}（soullink.profile.json）`,
    );
  } catch (err) {
    ui.showBanner('error', `获取 soullink profile 失败：${errorText(err)}`);
  }
}
</script>

<template>
  <section v-if="loadError" class="module-section card full-span">
    <h3 class="section-title">错误</h3>
    <div class="module-section-content">
      <div class="empty-state">模块加载失败: {{ loadError }}</div>
    </div>
  </section>

  <section v-else-if="!totalFieldCount" class="module-section card full-span">
    <h3 class="section-title">提示</h3>
    <div class="module-section-content">
      <div class="empty-state">当前模块暂无可编辑字段。</div>
    </div>
  </section>

  <template v-else>
    <FieldCard
      v-for="key in basicPublic"
      :key="`public-${key}`"
      scope="public"
      :field-key="key"
    />

    <template v-if="advancedCount">
      <div class="advanced-divider" :class="{ open: advancedOpen }" @click="toggleAdvanced">
        <span class="arrow">▶</span> 高级配置
        <span class="badge-adv">{{ advancedCount }} 项</span>
      </div>
      <div class="advanced-body" :class="{ open: advancedOpen }">
        <FieldCard
          v-for="key in advancedPublic"
          :key="`advanced-${key}`"
          scope="public"
          :field-key="key"
        />
      </div>
    </template>

    <article class="card full-span">
      <h3 class="card-title">模型类型</h3>
      <div class="list-row">
        <span>当前模型类型: {{ modelType.toUpperCase() }}</span>
        <button
          v-for="target in switchTargets"
          :key="target"
          class="btn btn-ghost"
          type="button"
          @click="switchModelType(target)"
        >
          切换到 {{ target.toUpperCase() }}
        </button>
      </div>
    </article>

    <article v-if="modelsLoaded || modelType === 'images'" class="card full-span">
      <h3 class="card-title">可用模型</h3>
      <div class="list-box">
        <template v-if="modelType === 'images'">
          <div class="card-help">{{ imagesSummary }}</div>
          <div class="toolbar">
            <button class="btn btn-primary" type="button" @click="openImagesEditor">
              编辑 Images 模型
            </button>
          </div>
        </template>
        <template v-else>
          <div v-if="!filteredModels.length" class="empty-state">
            暂无 {{ modelType.toUpperCase() }} 模型，可先点击右上角 Reload 或手动放置模型文件。
          </div>
          <div v-for="item in filteredModels" :key="item.path" class="list-row">
            <span>{{ item.label || '-' }} | {{ item.path }}</span>
            <button class="btn btn-ghost" type="button" @click="useModel(item)">使用</button>
            <button
              v-if="modelType === 'live2d'"
              class="btn btn-ghost"
              type="button"
              @click="openSoullinkProfile(item)"
            >
              参数映射
            </button>
          </div>
        </template>
      </div>
    </article>
  </template>
</template>
