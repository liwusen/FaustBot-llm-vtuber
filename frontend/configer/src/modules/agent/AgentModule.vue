<script setup lang="ts">
import { computed, onMounted } from 'vue';
import EmptyState from '@/components/ui/EmptyState.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import { formatApiError } from '@/api/errors';
import { openFileDialog } from '@/api/ipc';
import { useAgentStore } from '@/stores/agent';
import { useUiStore } from '@/stores/ui';
import AgentCreateModal from './AgentCreateModal.vue';
import AgentFilesModal from './AgentFilesModal.vue';
import CardImportModal from './CardImportModal.vue';
import { agentName, agentStatusText } from './agentFiles';
import type { AgentListItem } from './agentFiles';

defineOptions({ name: 'module-agent' });

const store = useAgentStore();
const ui = useUiStore();

const detailFiles = computed(() => store.agentDetail?.files || null);
const agentNames = computed(() => store.agents.map((item) => agentName(item)).filter(Boolean));

async function load(): Promise<void> {
  try {
    await store.refresh();
  } catch (err) {
    ui.showBanner('error', `加载 Agent 列表失败: ${formatApiError(err)}`);
  }
}

function refreshAll(): void {
  void load();
}

function openCreate(): void {
  ui.openModal(AgentCreateModal, { agents: agentNames.value }, '新建 Agent');
}

async function importFromCard(): Promise<void> {
  const cardPath = await openFileDialog({
    title: '选择角色卡（SillyTavern PNG / JSON）',
    filters: [{ name: '角色卡', extensions: ['png', 'json'] }],
  });
  if (!cardPath) return;
  try {
    const preview = await store.parseCard(cardPath);
    ui.openModal(
      CardImportModal,
      { preview, cardPath, existingNames: agentNames.value },
      '从角色卡导入',
    );
  } catch (err) {
    ui.showBanner('error', `角色卡解析失败：${formatApiError(err)}`);
  }
}

async function switchCurrent(): Promise<void> {
  const name = store.selectedAgent;
  if (!name) return;
  try {
    await store.switchCurrent(name);
    await store.refresh();
    ui.showBanner('success', `已切换 Agent: ${name}`);
  } catch (err) {
    ui.showBanner('error', `切换失败: ${formatApiError(err)}`);
  }
}

async function deleteSelected(): Promise<void> {
  const name = store.selectedAgent;
  if (!name) return;
  if (!window.confirm(`确定删除 ${name} ?`)) return;
  try {
    await store.deleteAgent(name);
  } catch (err) {
    ui.showBanner('error', `删除失败: ${formatApiError(err)}`);
  }
}

async function deleteCheckpoint(): Promise<void> {
  const name = store.selectedAgent;
  if (!name) return;
  if (!window.confirm(`确定删除 ${name} 的 checkpoint?`)) return;
  try {
    await store.deleteCheckpoint(name);
    ui.showBanner('success', 'Checkpoint 已删除。');
  } catch (err) {
    ui.showBanner('error', `删除 Checkpoint 失败: ${formatApiError(err)}`);
  }
}

function openFilesModal(name: string): void {
  ui.openModal(
    AgentFilesModal,
    { agentName: name, files: store.agentDetail?.files || {} },
    `Agent 文件编辑 - ${name}`,
  );
}

async function selectAgent(item: AgentListItem): Promise<void> {
  try {
    await store.selectAgent(agentName(item));
  } catch (err) {
    ui.showBanner('error', `加载 Agent 详情失败: ${formatApiError(err)}`);
  }
}

async function editFiles(item: AgentListItem): Promise<void> {
  const name = agentName(item);
  try {
    await store.selectAgent(name);
    openFilesModal(name);
  } catch (err) {
    ui.showBanner('error', `加载 Agent 文件失败: ${formatApiError(err)}`);
  }
}

function editCurrentFiles(): void {
  if (!store.selectedAgent) return;
  openFilesModal(store.selectedAgent);
}

async function openCurrentDir(): Promise<void> {
  if (!store.selectedAgent) return;
  try {
    await store.openDirectory(store.selectedAgent);
  } catch (err) {
    ui.showBanner('error', `打开目录失败: ${formatApiError(err)}`);
  }
}

onMounted(load);
</script>

<template>
  <SectionCard title="Agent 操作">
    <div class="toolbar">
      <button class="btn btn-ghost" type="button" @click="refreshAll">刷新</button>
      <button class="btn btn-ghost" type="button" @click="openCreate">新建</button>
      <button class="btn btn-secondary" type="button" @click="importFromCard">从角色卡导入</button>
      <button class="btn btn-secondary" type="button" @click="switchCurrent">切换为当前</button>
      <button class="btn btn-ghost" type="button" @click="deleteSelected">删除</button>
      <button class="btn btn-ghost" type="button" @click="deleteCheckpoint">删除 Checkpoint</button>
    </div>
  </SectionCard>

  <article class="card full-span">
    <h3 class="card-title">Agent 列表</h3>
    <table class="simple-table">
      <thead>
        <tr>
          <th>名称</th>
          <th>状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="item in store.agents"
          :key="agentName(item)"
          class="clickable"
          :class="{ selected: store.selectedAgent === agentName(item) }"
          @click="selectAgent(item)"
        >
          <td>
            <span class="mono">{{ agentName(item) }}</span>
          </td>
          <td>{{ agentStatusText(item) }}</td>
          <td>
            <div class="toolbar compact" @click.stop>
              <button class="btn btn-ghost" type="button" @click="editFiles(item)">编辑文件</button>
            </div>
          </td>
        </tr>
        <tr v-if="!store.agents.length">
          <td class="table-empty" colspan="3">无数据</td>
        </tr>
      </tbody>
    </table>
  </article>

  <SectionCard v-if="!detailFiles" title="Agent 文件">
    <EmptyState text="请选择 Agent 后编辑文件。" />
  </SectionCard>

  <SectionCard v-else title="Agent 文件操作">
    <div class="toolbar">
      <button class="btn btn-primary" type="button" @click="editCurrentFiles">
        编辑 Agent 文件
      </button>
      <button class="btn btn-ghost" type="button" @click="openCurrentDir">打开 Agent 目录</button>
    </div>
  </SectionCard>
</template>
