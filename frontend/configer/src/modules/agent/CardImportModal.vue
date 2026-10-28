<script setup lang="ts">
import { computed, ref } from 'vue';
import { formatApiError } from '@/api/errors';
import { useAgentStore } from '@/stores/agent';
import { useUiStore } from '@/stores/ui';
import {
  CARD_IMPORT_NAME_HELP,
  cardImportBanner,
  cardImportNameState,
  cardSummaryRows,
} from './cardImport';
import type { CardPreview } from './cardImport';

const props = withDefaults(
  defineProps<{
    preview: CardPreview;
    cardPath: string;
    existingNames?: string[];
  }>(),
  { existingNames: () => [] },
);

const store = useAgentStore();
const ui = useUiStore();

const name = ref(String(props.preview.suggested_name || ''));
const switchAfter = ref(false);
const busy = ref(false);
const statusOverride = ref('');

const rows = computed(() => cardSummaryRows(props.preview));
const validation = computed(() => cardImportNameState(name.value, props.existingNames));
const canImport = computed(() => validation.value.ok && !busy.value);
const statusText = computed(() => statusOverride.value || validation.value.message);

function onNameInput(): void {
  statusOverride.value = '';
}

async function doImport(): Promise<void> {
  if (!validation.value.ok) {
    statusOverride.value = validation.value.message;
    return;
  }
  busy.value = true;
  statusOverride.value = '正在解析卡片、写入角色文件与记忆库（世界书逐条嵌入，可能耗时数十秒）…';
  try {
    const result = await store.importCard(props.cardPath, name.value);
    ui.closeModal();
    const banner = cardImportBanner(result);
    ui.showBanner(banner.type, banner.text);
    const created = String(result.agent_name || '');
    if (switchAfter.value) {
      try {
        await store.switchCurrent(created);
        await store.refresh();
        // 旧实现切换后走 reloadAll()，会顺带隐藏刚才的导入 banner。
        ui.hideBanner();
      } catch (err) {
        ui.showBanner('error', `切换失败：${formatApiError(err)}`);
      }
    } else {
      store.selectedAgent = created;
      await store.refresh();
    }
  } catch (err) {
    busy.value = false;
    statusOverride.value = `导入失败：${formatApiError(err)}`;
  }
}
</script>

<template>
  <div>
    <div v-for="row in rows" :key="row.label" class="kv-row">
      <span class="kv-key">{{ row.label }}</span>
      <span class="kv-inline-value">{{ row.value }}</span>
    </div>
    <p v-if="preview.description_preview" class="card-help">
      设定摘要：{{ preview.description_preview }}
    </p>
    <p v-if="preview.first_mes_preview" class="card-help">
      开场白摘要：{{ preview.first_mes_preview }}
    </p>
  </div>

  <div>
    <label class="kv-key">Agent 目录名</label>
    <input
      v-model="name"
      class="input"
      type="text"
      placeholder="Agent 目录名"
      :disabled="busy"
      @input="onNameInput"
    />
    <p class="card-help">{{ CARD_IMPORT_NAME_HELP }}</p>
  </div>

  <label class="switch-row">
    <span class="card-help">创建后切换为当前角色（会重建运行时并重置当前对话）</span>
    <input v-model="switchAfter" type="checkbox" :disabled="busy" />
  </label>

  <p class="card-help">{{ statusText }}</p>

  <div class="toolbar">
    <button class="btn btn-primary" type="button" :disabled="!canImport" @click="doImport">
      导入
    </button>
    <button class="btn btn-ghost" type="button" :disabled="busy" @click="ui.closeModal()">
      取消
    </button>
  </div>
</template>
