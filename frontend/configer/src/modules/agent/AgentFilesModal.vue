<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue';
import CodeMirrorHost from '@/components/ui/CodeMirrorHost.vue';
import { AGENT_FILES } from '@/config/constants';
import { formatApiError } from '@/api/errors';
import { useAgentStore } from '@/stores/agent';
import { useUiStore } from '@/stores/ui';
import { agentFileLanguage, agentFileValues } from './agentFiles';
import type { AgentFileEntry } from './agentFiles';

const props = defineProps<{
  agentName: string;
  files?: Record<string, AgentFileEntry | string> | null;
}>();

const store = useAgentStore();
const ui = useUiStore();

const initial = agentFileValues(props.files || {});
const contents = reactive<Record<string, string>>({});
for (const filename of AGENT_FILES) contents[filename] = initial[filename].content;

const activeTab = ref(AGENT_FILES[0]);
const editorRefs = new Map<string, { measure?: () => void }>();

const paneStyle = {
  height: 'min(65vh, 600px)',
  border: '1px solid var(--line)',
  borderRadius: '10px',
  overflow: 'hidden',
};

const activeReadonly = computed(
  () => initial[activeTab.value]?.readonly === true,
);
const readonlyHint = '（模板文件，不可编辑 — 修改请更新 agents_template/faust/ 源文件）';

function setEditor(filename: string, el: unknown): void {
  if (el) editorRefs.set(filename, el as { measure?: () => void });
  else editorRefs.delete(filename);
}

function switchTab(filename: string): void {
  if (activeTab.value === filename) return;
  activeTab.value = filename;
}

// 隐藏的 pane 刚显示：让 CodeMirror 重新测量布局，否则光标/滚动高度会错乱。
watch(activeTab, async (next) => {
  await nextTick();
  editorRefs.get(next)?.measure?.();
});

async function saveAll(): Promise<void> {
  const values: Record<string, string> = {};
  for (const filename of AGENT_FILES) values[filename] = contents[filename] ?? '';
  try {
    await store.saveFiles(props.agentName, values);
    ui.showBanner('success', `Agent 文件已保存: ${props.agentName}`);
  } catch (err) {
    ui.showBanner('error', `保存失败: ${formatApiError(err)}`);
  }
}

async function openDirectory(): Promise<void> {
  try {
    await store.openDirectory(props.agentName);
  } catch (err) {
    ui.showBanner('error', `打开目录失败: ${formatApiError(err)}`);
  }
}

function close(): void {
  ui.closeModal();
}
</script>

<template>
  <div class="toolbar">
    <button class="btn btn-ghost" type="button" @click="openDirectory">打开 Agent 目录</button>
    <button class="btn btn-primary" type="button" @click="saveAll">保存全部文件</button>
    <button class="btn btn-ghost" type="button" @click="close">关闭</button>
  </div>

  <div class="editor-tab-bar">
    <button
      v-for="filename in AGENT_FILES"
      :key="filename"
      type="button"
      class="editor-tab-btn"
      :class="{ active: activeTab === filename, readonly: initial[filename].readonly }"
      @click="switchTab(filename)"
    >
      {{ filename }}
    </button>
  </div>

  <div class="editor-area">
    <div
      v-for="filename in AGENT_FILES"
      :key="filename"
      v-show="activeTab === filename"
      class="editor-pane"
      :style="paneStyle"
    >
      <CodeMirrorHost
        :ref="(el) => setEditor(filename, el)"
        v-model="contents[filename]"
        :language="agentFileLanguage(filename)"
        :readonly="initial[filename].readonly"
        height="100%"
      />
    </div>
  </div>

  <small v-if="activeReadonly" class="card-help">{{ readonlyHint }}</small>
</template>
