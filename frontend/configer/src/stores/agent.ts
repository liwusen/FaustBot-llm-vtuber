import { ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { cfgApi, getFaustbotRoot, openPath } from '@/api/ipc';
import { useConfigStore } from '@/stores/config';
import { buildCardImportBody, CARD_IMPORT_TIMEOUT_MS } from '@/modules/agent/cardImport';
import type { CardImportResult, CardPreview } from '@/modules/agent/cardImport';
import { agentDirPath, buildAgentFilesPayload } from '@/modules/agent/agentFiles';
import type { AgentDetail, AgentListItem } from '@/modules/agent/agentFiles';

/**
 * 角色页状态，对应旧 state.agents / state.selectedAgent / state.agentDetail。
 * 放在 store 里，切页（组件重建）后选中项与详情不丢。
 */
export const useAgentStore = defineStore('configer-agent', () => {
  const agents = ref<AgentListItem[]>([]);
  const selectedAgent = ref('');
  const agentDetail = ref<AgentDetail | null>(null);

  async function loadDetail(name: string): Promise<void> {
    const data = await cfgApi<{ detail?: AgentDetail }>('GET', API.agent(name));
    agentDetail.value = data?.detail || null;
  }

  /** 旧 ensureModuleData('agent')：列表 + 选中项详情。 */
  async function refresh(): Promise<void> {
    const data = await cfgApi<{ items?: AgentListItem[] }>('GET', API.agents);
    agents.value = Array.isArray(data?.items) ? data.items : [];
    if (!selectedAgent.value && agents.value.length) {
      selectedAgent.value = String(
        agents.value.find((item) => item.is_current)?.name || agents.value[0].name || '',
      );
    }
    if (selectedAgent.value) await loadDetail(selectedAgent.value);
    else agentDetail.value = null;
  }

  async function selectAgent(name: string): Promise<void> {
    selectedAgent.value = String(name || '');
    await refresh();
  }

  async function createAgent(name: string, templateAgent = ''): Promise<void> {
    const payload: Record<string, unknown> = { agent_name: name };
    if (templateAgent) payload.template_agent = templateAgent;
    await cfgApi('POST', API.agents, payload);
    await refresh();
  }

  /**
   * 切换当前角色：后端 switch 会写 AGENT_NAME，前端再把这个值同步进配置 store 并保存，
   * 随后由 saveConfig 重新读取配置 / 运行时并刷新全部模块（等同旧 reloadAll）。
   */
  async function switchCurrent(name: string): Promise<void> {
    await cfgApi('POST', API.agentSwitch, { agent_name: name });
    const config = useConfigStore();
    config.updateValue('public', 'AGENT_NAME', name);
    if (config.isDirty('public', 'AGENT_NAME')) {
      await config.saveConfig();
    } else {
      // 已经是同名角色：没有 dirty 可保存，直接以磁盘值为准重读。
      await config.loadConfig();
      await config.loadRuntimeSummary();
    }
  }

  async function deleteAgent(name: string): Promise<void> {
    await cfgApi('DELETE', API.agent(name));
    selectedAgent.value = '';
    await refresh();
  }

  async function deleteCheckpoint(name: string): Promise<void> {
    await cfgApi('DELETE', API.agentCheckpoint(name));
  }

  /** 旧「保存全部文件」：四个固定文件一起 PUT。 */
  async function saveFiles(name: string, values: Record<string, string>): Promise<void> {
    await cfgApi('PUT', API.agentFiles(name), buildAgentFilesPayload(values));
    await refresh();
  }

  async function parseCard(cardPath: string): Promise<CardPreview> {
    return cfgApi<CardPreview>('POST', API.agentParseCard, { card_path: cardPath });
  }

  /** 世界书逐条嵌入可能耗时数十秒，沿用旧实现的 10 分钟超时。 */
  async function importCard(cardPath: string, agentName: string): Promise<CardImportResult> {
    return cfgApi<CardImportResult>(
      'POST',
      API.agentImportCard,
      buildCardImportBody(cardPath, agentName),
      null,
      { timeoutMs: CARD_IMPORT_TIMEOUT_MS },
    );
  }

  async function openDirectory(name: string): Promise<void> {
    const root = await getFaustbotRoot();
    await openPath(agentDirPath(root, name));
  }

  return {
    agents,
    selectedAgent,
    agentDetail,
    refresh,
    selectAgent,
    loadDetail,
    createAgent,
    switchCurrent,
    deleteAgent,
    deleteCheckpoint,
    saveFiles,
    parseCard,
    importCard,
    openDirectory,
  };
});
