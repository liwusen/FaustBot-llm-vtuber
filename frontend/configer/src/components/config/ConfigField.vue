<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useConfigStore } from '@/stores/config';
import {
  FIELD_OPTIONS,
  FIELD_OPTION_LABELS,
  SECRET_KEYS,
  TEXTAREA_KEYS,
} from '@/config/constants';
import { getNumberFieldSpec } from '@/config/fieldSpecs';
import { isMaskedSecret, normalizeNumberInput } from '@/utils/format';
import { openFileDialog } from '@/api/ipc';
import SliderField from '@/components/ui/SliderField.vue';
import SwitchField from '@/components/ui/SwitchField.vue';
import type { ConfigScope } from '@/config/types';

const props = defineProps<{
  scope: ConfigScope;
  fieldKey: string;
  rerender?: () => void;
  afterChange?: () => void;
}>();

const config = useConfigStore();

// 与旧 field-render.js 相同的复习文本：TTS 分块滑块实时预览。
const TTS_SAMPLE_TEXT =
  '你好呀。今天天气真不错，我们一起去公园散步，顺便聊聊最近的生活吧！据说周末会有短暂降雨，出门记得带伞哦。LSTM networks are widely used for sequence modeling tasks in modern deep learning systems.';

const currentValue = computed<unknown>(
  () => (props.scope === 'private' ? config.privateConfig : config.publicConfig)[props.fieldKey],
);

// 旧实现只在构建控件时读取一次 value：控件的分支与数字步长不会随每次输入改变。
const renderedValue = ref<unknown>(currentValue.value);

const fieldOptions = computed<string[] | null>(() => {
  const raw = (FIELD_OPTIONS as unknown as Record<string, string[] | undefined>)[props.fieldKey];
  return Array.isArray(raw) && raw.length ? raw : null;
});

const optionLabels = computed<Record<string, string> | null>(() => {
  const raw = (FIELD_OPTION_LABELS as unknown as Record<
    string,
    Record<string, string> | undefined
  >)[props.fieldKey];
  return raw || null;
});

const numberSpec = computed(() => getNumberFieldSpec(props.fieldKey, renderedValue.value));

type ControlKind =
  | 'boolean'
  | 'select'
  | 'textarea'
  | 'secret'
  | 'range'
  | 'number'
  | 'ttsFile'
  | 'text';

const kind = computed<ControlKind>(() => {
  const value = renderedValue.value;
  if (typeof value === 'boolean') return 'boolean';
  if (fieldOptions.value) return 'select';
  if (TEXTAREA_KEYS.has(props.fieldKey)) return 'textarea';
  if (SECRET_KEYS.has(props.fieldKey)) return 'secret';
  if (typeof value === 'number') {
    return numberSpec.value.type === 'range' ? 'range' : 'number';
  }
  if (props.fieldKey === 'TTS_REFER_WAV_PATH') return 'ttsFile';
  return 'text';
});

const checked = computed(() => Boolean(currentValue.value));

const textValue = computed(() => {
  const value = currentValue.value;
  return value === null || value === undefined ? '' : String(value);
});

const secretValue = computed(() => {
  const dirty = config.dirtyValue(props.scope, props.fieldKey);
  return typeof dirty === 'string' ? dirty : '';
});

const secretPlaceholder = ref(
  isMaskedSecret(currentValue.value) ? '已设置，保持不变' : '请输入密钥',
);

// 旧实现在构建时读 value 判断是否已设置；配置异步到位时补一次判断。
watch(currentValue, (value) => {
  if (isMaskedSecret(value)) secretPlaceholder.value = '已设置，保持不变';
});

const numberDraft = ref(String(currentValue.value ?? 0));

const rangeModel = computed<number | null>(() =>
  typeof currentValue.value === 'number' ? currentValue.value : null,
);

const ttsPreview = computed<string[] | undefined>(() => {
  if (props.fieldKey !== 'TTS_CHUNK_IDEAL_TOKENS') return undefined;
  const splitter = window.TtsSplitter;
  if (!splitter || typeof splitter.demoSplit !== 'function') return undefined;
  const tokens = Math.round(Number(currentValue.value) || 30);
  return splitter.demoSplit(TTS_SAMPLE_TEXT, tokens);
});

watch(
  () => props.fieldKey,
  () => {
    renderedValue.value = currentValue.value;
    numberDraft.value = String(currentValue.value ?? 0);
    secretPlaceholder.value = isMaskedSecret(currentValue.value)
      ? '已设置，保持不变'
      : '请输入密钥';
  },
);

function onToggle(next: boolean): void {
  config.updateValue(props.scope, props.fieldKey, next);
  props.afterChange?.();
  props.rerender?.();
}

function onSelect(event: Event): void {
  const value = (event.target as HTMLSelectElement).value;
  config.updateValue(props.scope, props.fieldKey, value);
  props.afterChange?.();
  props.rerender?.();
}

function onTextareaInput(event: Event): void {
  const value = (event.target as HTMLTextAreaElement).value;
  config.updateValue(props.scope, props.fieldKey, value);
  props.afterChange?.();
}

function onSecretInput(event: Event): void {
  const value = (event.target as HTMLInputElement).value;
  config.updateValue(props.scope, props.fieldKey, value);
  // 旧实现只有输入非空才就地标记 dirty；空值等价于“未修改该密钥”。
  if (value.length > 0) props.afterChange?.();
}

function onRangeInput(next: number | null): void {
  config.updateValue(props.scope, props.fieldKey, next);
  props.afterChange?.();
}

function onNumberInput(event: Event): void {
  const raw = (event.target as HTMLInputElement).value;
  numberDraft.value = raw;
  const fallback = typeof renderedValue.value === 'number' ? renderedValue.value : 0;
  config.updateValue(props.scope, props.fieldKey, normalizeNumberInput(raw, fallback));
  props.afterChange?.();
}

function onTextInput(event: Event): void {
  const value = (event.target as HTMLInputElement).value;
  config.updateValue(props.scope, props.fieldKey, value);
  props.afterChange?.();
}

async function pickReferWav(): Promise<void> {
  try {
    const filePath = await openFileDialog({
      title: '选择 TTS 参考音频',
      filters: [
        { name: '音频文件', extensions: ['wav', 'mp3', 'flac', 'm4a', 'ogg'] },
        { name: '所有文件', extensions: ['*'] },
      ],
    });
    if (!filePath) return;
    config.updateValue(props.scope, props.fieldKey, filePath);
    props.afterChange?.();
  } catch (err) {
    console.error('选择 TTS 参考音频失败', err);
  }
}
</script>

<template>
  <div class="field-wrap">
    <SwitchField
      v-if="kind === 'boolean'"
      :model-value="checked"
      @update:model-value="onToggle"
    />
    <select
      v-else-if="kind === 'select'"
      class="select"
      :value="currentValue === null || currentValue === undefined ? '' : String(currentValue)"
      @change="onSelect"
    >
      <option v-for="option in fieldOptions" :key="option" :value="option">
        {{ optionLabels?.[option] || option }}
      </option>
    </select>
    <textarea
      v-else-if="kind === 'textarea'"
      class="textarea"
      :name="fieldKey"
      :value="textValue"
      @input="onTextareaInput"
    ></textarea>
    <input
      v-else-if="kind === 'secret'"
      class="input"
      type="password"
      :name="fieldKey"
      :value="secretValue"
      :placeholder="secretPlaceholder"
      autocomplete="off"
      @input="onSecretInput"
    />
    <SliderField
      v-else-if="kind === 'range'"
      :model-value="rangeModel"
      :min="numberSpec.min ?? 0"
      :max="numberSpec.max ?? 1"
      :step="numberSpec.step ?? 0.01"
      :unit="numberSpec.unit || ''"
      :preview="ttsPreview"
      @update:model-value="onRangeInput"
    />
    <input
      v-else-if="kind === 'number'"
      class="number"
      type="number"
      :name="fieldKey"
      :value="numberDraft"
      :step="String(numberSpec.step ?? 1)"
      @input="onNumberInput"
    />
    <div v-else-if="kind === 'ttsFile'" class="toolbar">
      <input
        class="input"
        type="text"
        :name="fieldKey"
        :value="textValue"
        placeholder="选择或输入参考音频文件路径"
        @input="onTextInput"
      />
      <button class="btn btn-secondary" type="button" @click="pickReferWav">选择文件</button>
    </div>
    <input
      v-else
      class="input"
      type="text"
      :name="fieldKey"
      :value="textValue"
      @input="onTextInput"
    />
  </div>
</template>
