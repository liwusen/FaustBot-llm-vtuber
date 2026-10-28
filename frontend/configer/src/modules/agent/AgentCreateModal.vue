<script setup lang="ts">
import { computed, ref } from 'vue';
import { formatApiError } from '@/api/errors';
import { useAgentStore } from '@/stores/agent';
import { useUiStore } from '@/stores/ui';
import { CARD_IMPORT_NAME_HELP, cardImportNameState } from './cardImport';

const props = withDefaults(defineProps<{ agents?: string[] }>(), { agents: () => [] });

const store = useAgentStore();
const ui = useUiStore();

const name = ref('');
const templateAgent = ref('');
const busy = ref(false);
const errorText = ref('');

const nameState = computed(() => cardImportNameState(name.value, props.agents));
const canSubmit = computed(() => nameState.value.ok && !busy.value);
const validationMessage = computed(() => (name.value.trim() ? nameState.value.message : ''));

const templateOptions = computed(() => [
  { value: '', label: '默认模板（agents_template/faust）' },
  ...props.agents.map((item) => ({ value: item, label: `复制 ${item} 的文件` })),
]);

async function submit(): Promise<void> {
  const state = nameState.value;
  if (!state.ok) {
    errorText.value = state.message;
    return;
  }
  const target = name.value.trim();
  busy.value = true;
  errorText.value = '';
  try {
    await store.createAgent(target, templateAgent.value);
    ui.closeModal();
    ui.showBanner('success', `已创建 Agent: ${target}`);
  } catch (err) {
    errorText.value = `创建失败：${formatApiError(err)}`;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div>
    <label class="kv-key">Agent 名称</label>
    <input
      v-model="name"
      class="input"
      type="text"
      placeholder="Agent 目录名"
      :disabled="busy"
    />
    <p class="card-help">{{ CARD_IMPORT_NAME_HELP }}</p>
  </div>

  <label class="kv-key">复制模板</label>
  <select v-model="templateAgent" class="select" :disabled="busy">
    <option v-for="option in templateOptions" :key="option.value" :value="option.value">
      {{ option.label }}
    </option>
  </select>

  <p v-if="errorText || validationMessage" class="card-help">
    {{ errorText || validationMessage }}
  </p>

  <div class="toolbar">
    <button class="btn btn-primary" type="button" :disabled="!canSubmit" @click="submit">创建</button>
    <button class="btn btn-ghost" type="button" :disabled="busy" @click="ui.closeModal()">取消</button>
  </div>
</template>
