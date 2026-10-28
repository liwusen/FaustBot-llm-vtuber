import { ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { cfgApi } from '@/api/ipc';
import type { TriggerPayload, TriggerRecord } from '@/modules/triggers/payload';

/** 触发器列表与选中项；与旧 state.triggers / state.selectedTriggerId 对应。 */
export const useTriggersStore = defineStore('configer-triggers', () => {
  const triggers = ref<TriggerRecord[]>([]);
  const selectedTriggerId = ref('');

  async function loadTriggers(): Promise<void> {
    try {
      const data = await cfgApi<{ items?: TriggerRecord[] }>('GET', API.triggers);
      triggers.value = data.items || [];
    } catch (err) {
      console.warn('triggers data fetch error', err);
    }
  }

  function findTrigger(id: string): TriggerRecord | null {
    return triggers.value.find((item) => String(item.id) === String(id)) || null;
  }

  async function createTrigger(payload: TriggerPayload): Promise<void> {
    await cfgApi('POST', API.triggers, payload);
  }

  async function updateTrigger(id: string, payload: TriggerPayload): Promise<void> {
    await cfgApi('PUT', API.trigger(id), payload);
  }

  async function deleteTrigger(id: string): Promise<void> {
    await cfgApi('DELETE', API.trigger(id));
  }

  return {
    triggers,
    selectedTriggerId,
    loadTriggers,
    findTrigger,
    createTrigger,
    updateTrigger,
    deleteTrigger,
  };
});
