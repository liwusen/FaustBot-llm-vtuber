<script setup lang="ts">
import { API } from '@/api/endpoints';
import { cfgApi } from '@/api/ipc';
import { useConfigStore } from '@/stores/config';
import { useUiStore } from '@/stores/ui';

const props = defineProps<{ host: string; key: string }>();
const config = useConfigStore();
const ui = useUiStore();

async function confirmAndSave(): Promise<void> {
  try {
    ui.setBusy(true);
    await cfgApi('POST', API.config, {
      public: {
        FAUSTBOT_CLOUD_BASE_URL: props.host,
        TTS_MODE: 'faustbot-cloud',
        ASR_MODE: 'faustbot-cloud',
      },
      private: {
        FAUSTBOT_CLOUD_SERVICE_KEY: props.key,
      },
    });
    try {
      await cfgApi('POST', API.configReload, { reset_dialog: false, no_initial_chat: true });
    } catch {
      // 与旧实现一致：reload 失败不阻断已写入的配置。
    }
    try {
      await config.reloadAll();
    } catch (err) {
      console.warn('reloadAll failed after deeplink config', err);
    }
    ui.showBanner('success', 'FaustBot Cloud 已配置。');
    ui.closeModal();
  } catch (err) {
    console.error('Failed to apply FaustBot Cloud config', err);
    ui.showBanner('error', `配置保存失败: ${String(err)}`);
  } finally {
    ui.setBusy(false);
  }
}
</script>

<template>
  <div class="card-help">
    系统检测到来自 FaustBot Cloud 的配置请求。确认后将把云地址和服务密钥写入配置，并将 TTS/ASR 模式切换为 FaustBot-cloud。
  </div>
  <label>FaustBot Cloud 地址</label>
  <input class="input" :value="host" readonly />
  <label>Service Key</label>
  <input class="input" :value="key" readonly />
  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="confirmAndSave">确认并保存</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">取消</button>
  </div>
</template>
