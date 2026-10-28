import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { cfgApi } from '@/api/ipc';
import {
  mcpServerId,
  type McpPayload,
  type McpServerRecord,
} from '@/modules/mcp/mcpUtils';

/** MCP 页状态：服务器列表与选中项，对应旧 state.mcpServers / selectedMcpId。 */
export const useMcpStore = defineStore('configer-mcp', () => {
  const mcpServers = ref<McpServerRecord[]>([]);
  const selectedMcpId = ref('');

  const selectedServer = computed<McpServerRecord | null>(() => (
    mcpServers.value.find((item) => mcpServerId(item) === selectedMcpId.value) || null
  ));

  /**
   * 旧 ensureModuleData('mcp')：列表（include_log=false），
   * 首次自动选中第一个；选中项消失时回落到第一个。
   */
  async function loadServers(): Promise<void> {
    try {
      const data = await cfgApi<{ items?: McpServerRecord[] }>(
        'GET',
        API.mcpServers,
        null,
        { include_log: 'false' },
      );
      mcpServers.value = data.items || [];
      if (!selectedMcpId.value && mcpServers.value.length) {
        selectedMcpId.value = mcpServerId(mcpServers.value[0]);
      }
      if (
        selectedMcpId.value
        && !mcpServers.value.find((item) => mcpServerId(item) === selectedMcpId.value)
      ) {
        selectedMcpId.value = mcpServers.value.length
          ? mcpServerId(mcpServers.value[0])
          : '';
      }
    } catch (err) {
      console.warn('mcp data fetch error', err);
      mcpServers.value = [];
    }
  }

  async function saveServer(payload: McpPayload): Promise<void> {
    await cfgApi('PUT', API.mcpServer(payload.server_id), payload);
  }

  /** 旧「启用/停用」：已启用 → stop，未启用 → start。 */
  async function toggleServer(item: McpServerRecord): Promise<void> {
    const id = mcpServerId(item);
    const path = item.enabled ? API.mcpStop(id) : API.mcpStart(id);
    await cfgApi('POST', path, {});
  }

  async function deleteServer(id: string): Promise<void> {
    await cfgApi('DELETE', API.mcpServer(id));
  }

  /** 旧「查看日志」：重新拉一次带日志的列表，取该 server 的最新记录。 */
  async function fetchServerWithLog(
    id: string,
    fallback: McpServerRecord,
  ): Promise<McpServerRecord> {
    const data = await cfgApi<{ items?: McpServerRecord[] }>(
      'GET',
      API.mcpServers,
      null,
      { include_log: 'true' },
    );
    const items = data.items || [];
    return items.find((item) => mcpServerId(item) === id) || fallback;
  }

  return {
    mcpServers,
    selectedMcpId,
    selectedServer,
    loadServers,
    saveServer,
    toggleServer,
    deleteServer,
    fetchServerWithLog,
  };
});
