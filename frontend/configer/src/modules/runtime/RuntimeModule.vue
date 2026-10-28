<script setup lang="ts">
import { onMounted } from 'vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import { API } from '@/api/endpoints';
import { cfgApi } from '@/api/ipc';
import { useConfigStore } from '@/stores/config';
import { useModulesStore } from '@/stores/modules';
import { useUiStore } from '@/stores/ui';
import { useRuntimeServicesStore } from './runtimeStore';

defineOptions({ name: 'module-runtime' });

const config = useConfigStore();
const modules = useModulesStore();
const ui = useUiStore();
const store = useRuntimeServicesStore();

async function rebuildAgentRuntime(): Promise<void> {
  await cfgApi('POST', API.runtimeReloadAgent, {});
  await config.reloadAll();
  ui.showBanner('success', 'Agent Runtime 已重建。');
}

async function reloadRuntime(): Promise<void> {
  await cfgApi('POST', API.runtimeReloadAll, {});
  await config.reloadAll();
  ui.showBanner('success', '运行时已重载。');
}

function refreshList(): void {
  modules.refreshCurrent();
}

function selectService(key: string): void {
  store.selectedService = key;
  modules.refreshCurrent();
}

async function startService(key: string): Promise<void> {
  await store.startService(key);
  modules.refreshCurrent();
}

async function stopService(key: string): Promise<void> {
  await store.stopService(key);
  modules.refreshCurrent();
}

async function restartService(key: string): Promise<void> {
  await store.restartService(key);
  modules.refreshCurrent();
}

onMounted(() => {
  void store.loadServices();
});
</script>

<template>
  <SectionCard title="运行时控制">
    <div class="toolbar">
      <button class="btn btn-secondary" type="button" @click="rebuildAgentRuntime">
        重建 Agent Runtime
      </button>
      <button class="btn btn-primary" type="button" @click="reloadRuntime">
        重载配置并重建 Runtime
      </button>
      <button class="btn btn-ghost" type="button" @click="refreshList">
        刷新服务列表
      </button>
    </div>
  </SectionCard>

  <article class="card full-span">
    <h3 class="card-title">服务列表</h3>
    <table class="simple-table">
      <thead>
        <tr>
          <th>服务</th>
          <th>名称</th>
          <th>状态</th>
          <th>端口</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="svc in store.services"
          :key="String(svc.key || '')"
          :class="store.selectedService === String(svc.key || '') ? 'selected' : ''"
          @click="selectService(String(svc.key || ''))"
        >
          <td class="cell-primary">{{ svc.key }}</td>
          <td>{{ svc.name || '-' }}</td>
          <td>{{ svc.is_running ? '运行中' : '未运行' }}</td>
          <td>{{ svc.port ? String(svc.port) : '-' }}</td>
          <td>
            <div class="toolbar compact" @click.stop>
              <button class="btn btn-ghost" type="button" @click="selectService(String(svc.key || ''))">
                查看
              </button>
              <button class="btn btn-ghost" type="button" @click="startService(String(svc.key || ''))">
                启动
              </button>
              <button class="btn btn-ghost" type="button" @click="stopService(String(svc.key || ''))">
                停止
              </button>
              <button class="btn btn-ghost" type="button" @click="restartService(String(svc.key || ''))">
                重启
              </button>
            </div>
          </td>
        </tr>
        <tr v-if="!store.services.length">
          <td class="table-empty" colspan="5">当前没有服务信息。</td>
        </tr>
      </tbody>
    </table>
  </article>

  <SectionCard title="服务日志">
    <textarea
      class="textarea code-area code-area-lg"
      readonly
      :value="String(store.serviceDetail?.log_tail || '暂无日志')"
    ></textarea>
  </SectionCard>
</template>
