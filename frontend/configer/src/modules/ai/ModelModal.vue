<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import FieldFold from '@/components/config/FieldFold.vue';
import { API_FORMAT_OPTIONS, SERVER_TOOL_OPTIONS } from '@/config/constants';
import type { ModelEntry } from '@/config/types';
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import AiFormField from './AiFormField.vue';
import {
  applyModelSettings,
  asAiProvider,
  cloneProviders,
  deleteModelFromProvider,
  formatContext,
  formatDecision,
  formatText,
  parseContextLength,
  removeModelSelection,
  renameModelSelection,
  thinkingText,
} from './providerOps';

// 模型设置弹窗（旧 openModelModal）：重命名会迁移所有按模型名索引的配置。
const props = defineProps<{
  entry: ModelEntry;
}>();

const store = useProvidersStore();
const ui = useUiStore();

interface ToolRow {
  value: string;
  label: string;
  help: string;
  formats: string[];
  checked: boolean;
  disabled: boolean;
}

const provider = computed(() => store.findProvider(props.entry.provider));
const decision = computed(() => formatDecision(provider.value, props.entry.model, store.resolvedFormats));
const resolved = computed(() => (store.resolvedFormats[props.entry.provider] || {})[props.entry.model] || {});

const nameInput = ref(props.entry.model);
const configuredContext = computed(() => provider.value?.model_context_lengths?.[props.entry.model]);
const configuredMaxOutput = computed(() => provider.value?.model_max_output_tokens?.[props.entry.model]);
const contextInput = ref(
  configuredContext.value === undefined || configuredContext.value === null
    ? ''
    : String(configuredContext.value),
);
const maxOutputInput = ref(configuredMaxOutput.value ? String(configuredMaxOutput.value) : '');
const apiFormat = ref(decision.value.source === 'manual' ? decision.value.format : 'auto');
const thinkingMode = ref(asAiProvider(provider.value)?.model_thinking_modes?.[props.entry.model] || 'auto');

const enabledTools = new Set(provider.value?.model_server_tools?.[props.entry.model] || []);
const tools = reactive<ToolRow[]>(
  SERVER_TOOL_OPTIONS.map((tool) => ({
    value: tool.value,
    label: tool.label,
    help: tool.help,
    formats: tool.formats,
    checked: enabledTools.has(tool.value),
    disabled: false,
  })),
);
const toolsNote = ref('由服务商在服务端执行，不会出现在本地工具列表里。');

function syncTools(): void {
  const effective = apiFormat.value === 'auto' ? decision.value.format : apiFormat.value;
  let blocked = 0;
  for (const item of tools) {
    const supported = !effective || effective === 'auto' || item.formats.includes(effective);
    item.disabled = !supported;
    if (!supported && item.checked) item.checked = false;
    if (!supported) blocked += 1;
  }
  toolsNote.value = blocked
    ? `当前接口格式（${formatText(effective, '')}）不支持服务端工具，换个格式才会生效。`
    : '由服务商在服务端执行，不会出现在本地工具列表里。';
}

syncTools();
watch(apiFormat, syncTools);

const infoText = computed(() => {
  const exact = provider.value?.model_context_lengths?.[props.entry.model] ?? null;
  return `当前格式：${formatText(decision.value.format, decision.value.source)}`
    + `｜思考：${thinkingText(resolved.value.thinking)}`
    + `｜上下文：${exact ? `${formatContext(exact)}（${exact} token）` : '未配置，按默认 128K'}`
    + `｜服务商：${props.entry.provider}`;
});

const thinkingOptions = [
  { value: 'auto', label: '自动（按服务商 / 模型目录推断）' },
  { value: 'effort', label: '思维等级（thinking level）' },
  { value: 'budget', label: '思维预算（thinking budget）' },
  { value: 'toggle', label: '思考开关（toggle）' },
  { value: 'none', label: '不发送思考参数' },
];

function saveToConfig(): void {
  const newName = String(nameInput.value || '').trim();
  if (!newName) {
    ui.showBanner('error', '模型名不能为空');
    return;
  }
  const next = cloneProviders(store.providers);
  const applied = applyModelSettings(next, props.entry.provider, props.entry.model, {
    modelName: newName,
    contextLength: parseContextLength(contextInput.value),
    maxOutputTokens: parseContextLength(maxOutputInput.value),
    apiFormat: apiFormat.value,
    thinkingMode: thinkingMode.value,
    serverTools: tools.filter((item) => item.checked).map((item) => item.value),
  });
  if (!applied) {
    ui.closeModal();
    return;
  }
  const selection = renameModelSelection(
    { mainModel: store.mainModel, subagentModels: store.subagentModels },
    props.entry.provider,
    props.entry.model,
    newName,
  );
  store.setProviders(next);
  store.setMainModel(selection.mainModel);
  store.setSubagentModels(selection.subagentModels);
  ui.closeModal();
  ui.showBanner('success', '模型已保存（点窗口顶部「保存」写入配置）');
}

function deleteModel(): void {
  const next = cloneProviders(store.providers);
  deleteModelFromProvider(next, props.entry.provider, props.entry.model);
  const selection = removeModelSelection(
    { mainModel: store.mainModel, subagentModels: store.subagentModels },
    props.entry.provider,
    props.entry.model,
  );
  store.setProviders(next);
  store.setMainModel(selection.mainModel);
  store.setSubagentModels(selection.subagentModels);
  ui.closeModal();
  ui.showBanner('success', '模型已删除（点窗口顶部「保存」写入配置）');
}
</script>

<template>
  <p class="form-hint">{{ infoText }}</p>

  <AiFormField label="模型名" hint="要与服务商文档里的模型 ID 完全一致。">
    <input v-model="nameInput" class="input" type="text" />
  </AiFormField>

  <AiFormField
    label="上下文长度（Token）"
    hint="这个模型最多能记住多少 token。留空按默认 128K；拉取模型时会尽量自动填。"
  >
    <input
      v-model="contextInput"
      class="input"
      type="number"
      min="1"
      step="1"
      placeholder="留空按默认 128K"
    />
  </AiFormField>

  <AiFormField label="最大输出" hint="单次回答最多生成多少 token。留空按服务商返回的默认值。">
    <input
      v-model="maxOutputInput"
      class="input"
      type="number"
      min="1"
      placeholder="留空按服务商返回的默认值"
    />
  </AiFormField>

  <FieldFold title="高级设置" :state-key="`model-advanced-${entry.spec}`">
    <AiFormField label="接口格式" hint="只在自动识别不对时才需要改；保存并应用后生效。">
      <select v-model="apiFormat" class="select">
        <option v-for="option in API_FORMAT_OPTIONS" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
    </AiFormField>

    <AiFormField
      label="思考格式"
      hint="自动时先看服务商 /models，再看模型目录，最后按模型名推断；推断不对时在这里强制指定。"
    >
      <select v-model="thinkingMode" class="select">
        <option v-for="option in thinkingOptions" :key="option.value" :value="option.value">
          {{ option.label }}
        </option>
      </select>
    </AiFormField>

    <AiFormField
      label="服务端工具"
      hint="服务商自带的联网搜索：由服务商检索后直接作答，不占用本地工具。"
    >
      <div class="field-wrap">
        <label
          v-for="tool in tools"
          :key="tool.value"
          class="switch-row tool-row"
          :class="{ disabled: tool.disabled }"
        >
          <input v-model="tool.checked" type="checkbox" :disabled="tool.disabled" :title="tool.help" />
          <span class="switch-text">{{ tool.label }}</span>
        </label>
        <p class="form-hint">{{ toolsNote }}</p>
      </div>
    </AiFormField>
  </FieldFold>

  <p class="form-hint">改完点「保存到配置」，再点窗口顶部的「保存」才会写入配置文件。</p>
  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="saveToConfig">保存到配置</button>
    <button class="btn btn-ghost" type="button" @click="deleteModel">删除模型</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">关闭</button>
  </div>
</template>
