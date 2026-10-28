<script setup lang="ts">
import { onMounted } from 'vue';
import EmptyState from '@/components/ui/EmptyState.vue';
import InfoCard from '@/components/ui/InfoCard.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SimpleTableCard from '@/components/ui/SimpleTableCard.vue';
import { useMcpStore } from '@/stores/mcp';
import { useModulesStore } from '@/stores/modules';
import { useUiStore } from '@/stores/ui';
import McpEditorModal from './McpEditorModal.vue';
import McpLogsModal from './McpLogsModal.vue';
import {
  mcpBasicRows,
  mcpListLine,
  mcpServerId,
  mcpStatusColor,
  mcpToolRows,
  type McpPayload,
  type McpServerRecord,
} from './mcpUtils';

defineOptions({ name: 'module-mcp' });

const store = useMcpStore();
const modules = useModulesStore();
const ui = useUiStore();

function addServer(): void {
  ui.openModal(
    McpEditorModal,
    {
      initial: null,
      onSubmit: async (payload: McpPayload) => {
        await store.saveServer(payload);
        store.selectedMcpId = payload.server_id;
        modules.refreshCurrent();
      },
    },
    '新建 MCP Server',
  );
}

function editServer(item: McpServerRecord): void {
  const id = mcpServerId(item);
  store.selectedMcpId = id;
  ui.openModal(
    McpEditorModal,
    {
      initial: item,
      onSubmit: async (payload: McpPayload) => {
        await store.saveServer(payload);
        modules.refreshCurrent();
      },
    },
    `编辑 MCP Server: ${id}`,
  );
}

async function toggleServer(item: McpServerRecord): Promise<void> {
  await store.toggleServer(item);
  modules.refreshCurrent();
}

async function showLogs(item: McpServerRecord): Promise<void> {
  const id = mcpServerId(item);
  const fresh = await store.fetchServerWithLog(id, item);
  ui.openModal(McpLogsModal, { logTail: fresh.log_tail }, `MCP 日志: ${id}`);
}

async function deleteServer(item: McpServerRecord): Promise<void> {
  const id = mcpServerId(item);
  if (!window.confirm(`确定删除 MCP server ${id} ?`)) return;
  await store.deleteServer(id);
  if (store.selectedMcpId === id) store.selectedMcpId = '';
  modules.refreshCurrent();
}

onMounted(() => {
  void store.loadServers();
});
</script>

<template>
  <SectionCard title="MCP 操作">
    <div class="toolbar">
      <button class="btn btn-ghost" type="button" @click="modules.refreshCurrent()">刷新</button>
      <button class="btn btn-primary" type="button" @click="addServer">添加 Server</button>
    </div>
  </SectionCard>

  <SectionCard title="服务器列表">
    <div class="list-box">
      <div
        v-for="item in store.mcpServers"
        :key="mcpServerId(item)"
        class="list-row clickable"
        :class="{ selected: store.selectedMcpId === mcpServerId(item) }"
        @click="store.selectedMcpId = mcpServerId(item)"
      >
        <div>
          <span
            style="display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; vertical-align: middle"
            :style="{ background: mcpStatusColor(item) }"
          ></span>
          <span class="mono">{{ mcpListLine(item) }}</span>
        </div>
        <div class="toolbar compact" @click.stop>
          <button class="btn btn-ghost" type="button" @click="toggleServer(item)">
            {{ item.enabled ? '停用' : '启用' }}
          </button>
          <button class="btn btn-ghost" type="button" @click="editServer(item)">编辑</button>
          <button class="btn btn-ghost" type="button" @click="showLogs(item)">查看日志</button>
          <button class="btn btn-ghost" type="button" @click="deleteServer(item)">删除</button>
        </div>
      </div>
    </div>
  </SectionCard>

  <SectionCard v-if="!store.selectedServer" title="服务器详情">
    <EmptyState text="请选择 MCP 服务器。" />
  </SectionCard>

  <template v-else>
    <InfoCard title="基本信息" :rows="mcpBasicRows(store.selectedServer)" />
    <SimpleTableCard
      title="工具列表"
      :columns="['名称', '标题', '描述']"
      :rows="mcpToolRows(store.selectedServer)"
    />
  </template>
</template>
