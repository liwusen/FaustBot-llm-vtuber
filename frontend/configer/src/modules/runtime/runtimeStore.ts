import { ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { cfgApi } from '@/api/ipc';

export interface ServiceRecord {
  key?: string;
  name?: string;
  is_running?: boolean;
  port?: number | string;
  log_tail?: string;
  [key: string]: unknown;
}

/**
 * 运行时页的服务列表状态。放在 Pinia 里（而不是组件局部状态），
 * 这样模块重渲染 / 保存后重建都不会丢掉「当前查看的服务」。
 */
export const useRuntimeServicesStore = defineStore('configer-runtime-services', () => {
  const services = ref<ServiceRecord[]>([]);
  const selectedService = ref('');
  const serviceDetail = ref<ServiceRecord | null>(null);

  /** 旧 ensureModuleData('runtime')：列表带 include_log，再按选中项取详情。 */
  async function loadServices(): Promise<void> {
    try {
      const data = await cfgApi<{ items?: ServiceRecord[] }>(
        'GET',
        API.services,
        null,
        { include_log: true },
      );
      services.value = data.items || [];
      if (selectedService.value) {
        const detail = await cfgApi<{ item?: ServiceRecord }>(
          'GET',
          API.service(selectedService.value),
        );
        serviceDetail.value = detail.item || null;
      }
    } catch (err) {
      console.warn('runtime data fetch error', err);
    }
  }

  async function startService(key: string): Promise<void> {
    await cfgApi('POST', API.serviceStart(key), {});
  }

  async function stopService(key: string): Promise<void> {
    await cfgApi('POST', API.serviceStop(key), {});
  }

  async function restartService(key: string): Promise<void> {
    await cfgApi('POST', API.serviceRestart(key), {});
  }

  return {
    services,
    selectedService,
    serviceDetail,
    loadServices,
    startService,
    stopService,
    restartService,
  };
});
