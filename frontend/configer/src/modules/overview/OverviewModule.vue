<script setup lang="ts">
import { computed, onActivated, onBeforeUnmount } from 'vue';
import Icon from '@/components/ui/Icon.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import {
  getFaustbotRoot,
  openFileDialog,
  openPath,
} from '@/api/ipc';
import { useConfigStore } from '@/stores/config';
import { useOverviewStore } from '@/stores/overview';

defineOptions({ name: 'module-overview' });

const config = useConfigStore();
const store = useOverviewStore();

const currentAgent = computed(
  () => String(store.runtime.current_agent || config.publicConfig.AGENT_NAME || '-'),
);
const model = computed(() => String(store.mainModel || config.publicConfig.CHAT_MODEL || '-'));
const ttsMode = computed(() => String(config.publicConfig.TTS_MODE || '-'));
const asrMode = computed(() => String(config.publicConfig.ASR_MODE || '-'));
const modelType = computed(() => String(config.publicConfig.MODEL_TYPE || 'live2d').toLowerCase());
const modelPath = computed(() => String(
  (modelType.value === 'vrm'
    ? config.publicConfig.VRM_MODEL_PATH
    : config.publicConfig.LIVE2D_MODEL_PATH) || '-',
));

const summaryRows = computed(() => [
  { label: '当前 Agent', value: currentAgent.value },
  { label: '主模型', value: model.value },
  { label: 'TTS 模式', value: ttsMode.value },
  { label: 'ASR 模式', value: asrMode.value },
  { label: '模型类型', value: modelType.value.toUpperCase() },
  { label: '模型路径', value: modelPath.value },
]);

const pluginsEnabled = computed(() => store.plugins.filter((item) => item.enabled).length);
const pluginsTotal = computed(() => store.plugins.length);
const servicesRunning = computed(() => store.services.filter((item) => item.is_running).length);
const servicesTotal = computed(() => store.services.length);

const statData = computed(() => [
  {
    label: '插件',
    icon: 'grid',
    value: `${pluginsEnabled.value}/${pluginsTotal.value}`,
    desc: '已启用 / 总数',
  },
  {
    label: '服务',
    icon: 'server',
    value: `${servicesRunning.value}/${servicesTotal.value}`,
    desc: '运行中 / 总数',
  },
  {
    label: '模型',
    icon: modelType.value === 'vrm' ? 'vr' : 'image',
    value: modelPath.value === '-' ? '未配置' : (modelPath.value.split('/').pop() ?? ''),
    desc: '当前载入',
  },
  { label: 'Agent', icon: 'user', value: currentAgent.value, desc: '当前角色' },
]);

const currentTag = computed(() => String(store.runtimeUpdate?.current_tag || '-'));

const available = computed(() => (
  store.updateResult.kind === 'available' ? store.updateResult : null
));
const manual = computed(() => (
  store.updateResult.kind === 'manual' ? store.updateResult : null
));
const errorText = computed(() => (
  store.updateResult.kind === 'error' ? store.updateResult.text : ''
));

async function openLiveConsole(): Promise<void> {
  if (window.api && typeof window.api.openLiveWindow === 'function') {
    await window.api.openLiveWindow();
  } else {
    window.open('live-window.html', '_blank', 'width=900,height=700');
  }
}

async function openDataDir(): Promise<void> {
  const root = await getFaustbotRoot();
  await openPath(root);
}

function startUpdate(): void {
  const target = available.value;
  if (!target) return;
  void store.applyUpdate(target.latestTag, target.assetName);
}

async function pickManualPackage(): Promise<void> {
  const filePath = await openFileDialog({
    title: '选择 Faust 更新包 (.zip)',
    filters: [{ name: 'Zip 更新包', extensions: ['zip'] }],
  });
  if (!filePath) return;
  await store.chooseManualPackage(filePath);
}

function startManualUpdate(): void {
  if (!manual.value) return;
  void store.applyManualUpdate();
}

// 概览是 KeepAlive 模块：SSE 下载随模块存活（切走不停），回到该页只重新同步概览数据。
// 更新结果 / 下载进度都存在 store 里，onActivated 不会重置它们。
onActivated(() => {
  void store.loadOverview();
});

onBeforeUnmount(() => {
  store.cancelDownload();
});
</script>

<template>
  <article class="card full-span">
    <h3 class="card-title">运行概览</h3>
    <div class="info-grid">
      <div v-for="row in summaryRows" :key="row.label" class="info-item">
        <div class="info-key">{{ row.label }}</div>
        <div class="info-value">{{ row.value }}</div>
      </div>
    </div>
  </article>

  <SectionCard title="系统状态">
    <div class="stat-grid">
      <article v-for="item in statData" :key="item.label" class="card">
        <div class="stat-icon">
          <Icon :name="item.icon" :size="20" />
        </div>
        <div class="stat-value">{{ item.value }}</div>
        <div class="stat-label">{{ item.label }} · {{ item.desc }}</div>
      </article>
    </div>
  </SectionCard>

  <article class="card full-span">
    <h3 class="card-title">直播模式</h3>
    <p class="card-help">打开直播控制台，可以配置 B站弹幕监听、管理弹幕黑名单、查看实时弹幕和 Trigger 队列。</p>
    <button class="btn btn-primary" type="button" @click="openLiveConsole">
      打开直播控制台
    </button>
  </article>

  <article class="card full-span">
    <h3 class="card-title">版本更新</h3>
    <div style="margin-top: 8px; display: flex; align-items: center; gap: 8px">
      <input id="update-proxy-chk" v-model="store.proxyEnabled" type="checkbox" />
      <label for="update-proxy-chk">使用镜像加速下载 (gh-proxy.com)</label>
    </div>
    <p class="card-help">当前版本: {{ currentTag }}</p>
    <button
      class="btn btn-ghost"
      type="button"
      :disabled="store.checking"
      @click="store.checkUpdate()"
    >
      {{ store.checking ? '检查中...' : '检查更新' }}
    </button>
    <button
      class="btn btn-ghost"
      type="button"
      :disabled="store.manualBusy === 'verifying'"
      @click="pickManualPackage"
    >
      {{ store.manualBusy === 'verifying' ? '校验中...' : '手动选择更新包' }}
    </button>
    <div style="margin-top: 8px">
      <span v-if="errorText" style="color: var(--danger)">{{ errorText }}</span>
      <span v-else-if="store.updateResult.kind === 'latest'" style="color: var(--muted)">
        已是最新版本
      </span>
      <template v-else-if="available">
        <div style="color: var(--accent); font-weight: 600; margin-bottom: 6px">
          新版本可用: {{ available.latestTag }} ({{ available.latestVersion }})
        </div>
        <div
          style="font-size: 12px; margin-bottom: 8px; max-height: 80px; overflow: auto; background: var(--bg2); padding: 6px; border-radius: 4px"
        >{{ available.releaseBody }}</div>
        <div class="toolbar">
          <button
            class="btn btn-ghost"
            type="button"
            :disabled="store.updating"
            @click="startUpdate"
          >
            {{ store.updating ? '下载中...' : '开始更新' }}
          </button>
        </div>
        <div v-if="store.progress.visible" style="margin-top: 8px">
          <div class="progress-track">
            <div class="progress-fill" :style="{ width: `${store.progress.percent}%` }"></div>
          </div>
          <div class="progress-note">{{ store.progress.text }}</div>
        </div>
      </template>
      <template v-else-if="manual">
        <div style="color: var(--accent); font-weight: 600; margin-bottom: 6px">
          手动更新包已就绪: {{ manual.tag }} ({{ manual.version }})
        </div>
        <div style="font-size: 12px; color: var(--muted); margin-bottom: 8px">
          已跳过下载，将直接使用所选更新包进行更新。
        </div>
        <div class="toolbar">
          <button
            class="btn btn-ghost"
            type="button"
            :disabled="store.manualBusy === 'applying'"
            @click="startManualUpdate"
          >
            {{ store.manualBusy === 'applying' ? '准备中...' : '开始更新' }}
          </button>
        </div>
      </template>
      <div
        v-else-if="store.updateResult.kind === 'prepared'"
        style="color: var(--accent); font-weight: 600"
      >
        更新已准备就绪。请关闭所有窗口后重新启动 Faust 以完成更新。
      </div>
    </div>
  </article>

  <article class="card full-span">
    <h3 class="card-title">数据目录</h3>
    <p class="card-help">打开 FaustBot 用户数据目录（Agent 文件、日志、配置、插件等）。</p>
    <button class="btn btn-primary" type="button" @click="openDataDir">
      打开数据目录
    </button>
  </article>

  <article class="card full-span">
    <h3 class="card-title">最近错误日志</h3>
    <template v-if="store.recentErrors.length">
      <div
        v-for="(item, index) in store.recentErrors"
        :key="index"
        class="error-log-line"
      >{{ item.timestamp || '' }} [{{ item.name || '' }}] {{ item.message || '' }}</div>
      <p class="card-help">仅显示最近 {{ store.recentErrors.length }} 条 ERROR 日志。</p>
    </template>
    <p v-else class="card-help">暂无 ERROR 级别日志。</p>
  </article>
</template>
