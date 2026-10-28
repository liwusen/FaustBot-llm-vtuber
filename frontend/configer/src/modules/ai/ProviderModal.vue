<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { API } from '@/api/endpoints';
import { formatApiError } from '@/api/errors';
import { cfgApi } from '@/api/ipc';
import FieldFold from '@/components/config/FieldFold.vue';
import {
  API_FORMAT_LABELS,
  API_FORMAT_OPTIONS,
  FIELD_OPTION_LABELS,
  FIELD_OPTIONS,
} from '@/config/constants';
import type { ProviderConfig } from '@/config/types';
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import AiFormField from './AiFormField.vue';
import {
  asAiProvider,
  applyLoadModelsResult,
  applyProviderForm,
  catalogResultText,
  catalogSummary,
  cloneProviders,
  newProviderRecord,
  type AiCatalogEntry,
  type LoadModelsResult,
  type ProviderForm,
} from './providerOps';

// 服务商添加/编辑弹窗（旧 openProviderModal）。
// 「保存到配置」只改待保存状态；拉取模型 / 添加模型 / 目录识别会立即写后端。
const props = defineProps<{
  provider?: ProviderConfig | null;
}>();

const store = useProvidersStore();
const ui = useUiStore();

const isEdit = computed(() => Boolean(props.provider));
const existing = computed(() => asAiProvider(props.provider));

const name = ref(props.provider?.name || '');
const baseUrl = ref(props.provider?.base_url || '');
const keyInput = ref(props.provider?.key ? '********' : '');
const thinkingType = ref(props.provider?.thinking_type || 'qwen');
const apiFormat = ref(props.provider?.api_format || 'auto');
const streamUsage = ref(
  props.provider?.stream_usage === true ? 'on' : props.provider?.stream_usage === false ? 'off' : 'auto',
);
const opencodeGo = ref(Boolean(existing.value?.opencode_go));
const catalogPin = ref(props.provider?.catalog_provider_id || '');
const modelInput = ref('');

const formatBaseUrls = reactive<Record<string, string>>({});
for (const option of API_FORMAT_OPTIONS) {
  if (option.value === 'auto') continue;
  formatBaseUrls[option.value] = existing.value?.format_base_urls?.[option.value] || '';
}

const catalogText = ref(catalogSummary(existing.value, store.providerCatalog));

const thinkingOptions = FIELD_OPTIONS.THINKING_TYPE as string[];
const thinkingLabels = FIELD_OPTION_LABELS.THINKING_TYPE as unknown as Record<string, string>;
const formatOptions = API_FORMAT_OPTIONS.filter((option) => option.value !== 'auto');

const keyPlaceholder = computed(() => (
  props.provider && props.provider.key ? '已设置，留空表示不修改' : '粘贴服务商提供的密钥'
));
const opencodeGoText = computed(() => (opencodeGo.value ? '已启用' : '已禁用'));

const currentName = computed(() => (
  isEdit.value ? props.provider?.name || '' : name.value.trim()
));

const modelCountText = computed(() => {
  const provider = store.findProvider(currentName.value);
  const count = provider && provider.models ? provider.models.length : 0;
  return count
    ? `已加载 ${count} 个模型，回到页面就能在模型表里挑主模型与子模型。`
    : '还没有模型。点「从服务商拉取模型」自动读取，或手动输入模型名添加。';
});

function formatLabel(value: string): string {
  return (API_FORMAT_LABELS as unknown as Record<string, string>)[value] || value;
}

function readForm(): ProviderForm {
  const formatBaseUrlValues: Record<string, string> = {};
  for (const [format, value] of Object.entries(formatBaseUrls)) {
    const text = String(value || '').trim();
    if (text) formatBaseUrlValues[format] = text;
  }
  const rawKey = keyInput.value && keyInput.value !== '********' ? keyInput.value : '';
  return {
    base_url: String(baseUrl.value || '').trim(),
    key: rawKey || null,
    thinking_type: thinkingType.value,
    opencode_go: opencodeGo.value,
    api_format: apiFormat.value,
    stream_usage: streamUsage.value === 'auto' ? null : streamUsage.value === 'on',
    format_base_urls: formatBaseUrlValues,
    catalog_provider_id: String(catalogPin.value || '').trim() || null,
  };
}

/**
 * 把当前表单提交到后端（目录识别 / 拉取模型都要求服务商已存在于后端）。
 * 与旧 persistForm 一致：这一步只同步到后端，不标记「待保存」。
 */
async function persistForm(): Promise<string | null> {
  const targetName = isEdit.value ? props.provider?.name || '' : name.value.trim();
  const form = readForm();
  if (!targetName || !form.base_url) {
    ui.showBanner('error', '请先填写名称与接口地址');
    return null;
  }
  const next = cloneProviders(store.providers);
  let target = next.find((p) => p.name === targetName);
  if (!target) {
    target = newProviderRecord(targetName, form);
    next.push(target);
  } else {
    applyProviderForm(target, form);
  }
  store.replaceProviders(next);
  await cfgApi('POST', API.config, {
    providers: cloneProviders(next),
    main_model: store.mainModel,
    subagent_models: [...(store.subagentModels || [])],
  });
  return targetName;
}

async function loadModels(): Promise<void> {
  try {
    const targetName = await persistForm();
    if (!targetName) return;
    const result = await cfgApi<LoadModelsResult>('POST', API.providerLoadModels(targetName));
    const next = cloneProviders(store.providers);
    applyLoadModelsResult(next, targetName, result);
    store.setProviders(next);
    if (result.catalog) catalogText.value = catalogResultText(result.catalog);
    const routed = (result.catalog && result.catalog.routed) || 0;
    ui.showBanner(
      'success',
      `拉取到 ${(result.models || []).length} 个模型`
        + (routed ? `，${routed} 个已按模型目录确定接口格式` : '')
        + '（回到页面点「保存」写入配置）',
    );
  } catch (err) {
    ui.showBanner('error', `拉取模型失败: ${formatApiError(err)}`);
  }
}

function addModel(): void {
  const modelName = String(modelInput.value || '').trim();
  if (!modelName) {
    ui.showBanner('error', '请输入模型名');
    return;
  }
  const form = readForm();
  const providerName = isEdit.value ? props.provider?.name || '' : name.value.trim();
  const next = cloneProviders(store.providers);
  let target = next.find((p) => p.name === providerName);
  if (!target) {
    if (!providerName || !form.base_url) {
      ui.showBanner('error', '先填写名称与接口地址');
      return;
    }
    target = newProviderRecord(providerName, form);
    next.push(target);
  }
  if (!target.models) target.models = [];
  if (target.models.includes(modelName)) {
    ui.showBanner('error', '这个模型已经加过了');
    return;
  }
  target.models.push(modelName);
  store.setProviders(next);
  modelInput.value = '';
  ui.showBanner('success', `已添加模型 ${modelName}（保存后生效）`);
}

async function rematchCatalog(): Promise<void> {
  try {
    const targetName = await persistForm();
    if (!targetName) return;
    const response = await cfgApi<{ result?: AiCatalogEntry }>(
      'POST',
      API.providerCatalogMatch(targetName),
      { provider_id: String(catalogPin.value || '').trim() || null },
    );
    await store.refreshProvider(targetName);
    const result = response.result || {};
    catalogText.value = catalogResultText(result);
    ui.showBanner(
      'success',
      result.provider_id
        ? `已识别为目录条目「${result.provider_id}」（${result.routed || 0} 个模型有格式结论）`
        : '没找到对应的目录条目：可以直接填目录条目名，或在模型设置里手动指定接口格式',
    );
  } catch (err) {
    ui.showBanner('error', `识别失败: ${formatApiError(err)}`);
  }
}

async function refreshCatalog(): Promise<void> {
  try {
    const result = await cfgApi<{ providers?: number; matched?: number }>('POST', API.modelsDevRefresh);
    await store.refreshCatalogEntries();
    catalogText.value = catalogSummary(
      asAiProvider(store.findProvider(currentName.value)),
      store.providerCatalog,
    );
    ui.showBanner(
      'success',
      `模型目录已更新：${result.providers || 0} 个服务商，识别出 ${result.matched || 0} 个（保存后生效）`,
    );
  } catch (err) {
    ui.showBanner('error', `更新目录失败: ${formatApiError(err)}`);
  }
}

function saveToConfig(): void {
  const targetName = name.value.trim();
  const form = readForm();
  if (!targetName || !form.base_url) {
    ui.showBanner('error', '名称与接口地址必填');
    return;
  }
  const next = cloneProviders(store.providers);
  if (isEdit.value) {
    const target = next.find((p) => p.name === targetName);
    if (target) applyProviderForm(target, form);
  } else {
    if (next.some((p) => p.name === targetName)) {
      ui.showBanner('error', '这个服务商已经存在');
      return;
    }
    next.push(newProviderRecord(targetName, form));
  }
  store.setProviders(next);
  ui.closeModal();
  ui.showBanner('success', `服务商已${isEdit.value ? '更新' : '添加'}（点窗口顶部「保存」写入配置）`);
}
</script>

<template>
  <AiFormField label="名称" hint="自定义的名字，模型会记成「名称::模型名」；保存后不可改名。">
    <input v-model="name" class="input" type="text" placeholder="例如 deepseek" :disabled="isEdit" />
  </AiFormField>

  <AiFormField label="接口地址" hint="服务商给的 API 地址，通常以 /v1 结尾。">
    <input v-model="baseUrl" class="input" type="text" placeholder="例如 https://api.deepseek.com/v1" />
  </AiFormField>

  <AiFormField label="密钥" hint="只存在本地配置文件里，不会写进公共配置。">
    <input
      v-model="keyInput"
      class="input"
      type="password"
      :placeholder="keyPlaceholder"
      autocomplete="off"
    />
  </AiFormField>

  <h4 class="modal-section-title">模型</h4>
  <p class="form-hint">{{ modelCountText }}</p>
  <div class="toolbar">
    <button class="btn btn-secondary" type="button" @click="loadModels">从服务商拉取模型</button>
  </div>
  <div class="toolbar modal-inline">
    <input v-model="modelInput" class="input" type="text" placeholder="模型名，例如 deepseek-chat" />
    <button class="btn btn-ghost" type="button" @click="addModel">添加</button>
  </div>

  <FieldFold title="高级设置" state-key="provider-advanced">
    <AiFormField
      label="思考参数格式"
      hint="OpenAI Chat 格式下决定思考参数怎么写；Responses / Anthropic / Gemini 只按「思考强度」换算，选「不使用」可强制关闭思考。"
    >
      <select v-model="thinkingType" class="select">
        <option v-for="option in thinkingOptions" :key="option" :value="option">
          {{ thinkingLabels[option] || option }}
        </option>
      </select>
    </AiFormField>

    <AiFormField
      label="默认接口格式"
      hint="自动识别时按模型目录与接口地址判断；同一个服务商的不同模型可以用不同格式。"
    >
      <select v-model="apiFormat" class="select">
        <option v-for="option in API_FORMAT_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
    </AiFormField>

    <AiFormField label="用量统计" hint="关掉后不再读取 token 用量，自动压缩也不会被触发。">
      <select v-model="streamUsage" class="select">
        <option value="auto">自动（按接口格式决定）</option>
        <option value="on">统计（能触发自动压缩）</option>
        <option value="off">不统计（网关不支持用量字段时）</option>
      </select>
    </AiFormField>

    <AiFormField
      label="OpenCode Go 专用会话头"
      hint="仅 OpenCode Go 订阅端点需要：开启后每个请求自动附带 x-opencode-session 会话头。"
    >
      <div class="switch-row">
        <span class="switch-text">{{ opencodeGoText }}</span>
        <label class="switch">
          <input v-model="opencodeGo" type="checkbox" />
          <span class="switch-slider"></span>
        </label>
      </div>
    </AiFormField>

    <div class="form-field">
      <label class="form-field-label">模型目录</label>
      <p class="form-hint">{{ catalogText }}</p>
      <div class="form-field-control">
        <div class="toolbar modal-inline">
          <input v-model="catalogPin" class="input" type="text" placeholder="目录条目名（留空表示自动识别）" />
          <button class="btn btn-secondary" type="button" @click="rematchCatalog">自动识别服务商</button>
          <button class="btn btn-ghost" type="button" @click="refreshCatalog">更新模型目录</button>
        </div>
      </div>
      <p class="form-hint">
        模型目录用来判断每个模型该用哪种接口格式，也能补全上下文长度；识别要求服务商已保存且已拉取模型。
      </p>
    </div>

    <div class="form-field">
      <label class="form-field-label">各格式接口地址</label>
      <p class="form-hint">
        只有同一服务商的多种格式走不同路径时才需要填。留空时：地址与目录一致就用目录里的地址，否则用上面的接口地址。
      </p>
      <div class="format-base-rows">
        <AiFormField v-for="option in formatOptions" :key="option.value" :label="formatLabel(option.value)">
          <input
            v-model="formatBaseUrls[option.value]"
            class="input"
            type="text"
            placeholder="留空 = 用模型目录或上面填的接口地址"
          />
        </AiFormField>
      </div>
    </div>
  </FieldFold>

  <p class="form-hint">
    「保存到配置」只是把改动放进待保存；要真正写入配置，请点窗口顶部的「保存」。拉取模型与目录识别会立即保存这个服务商。
  </p>
  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="saveToConfig">保存到配置</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">关闭</button>
  </div>
</template>
