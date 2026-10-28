<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from 'vue';
import SidebarNav from '@/components/shell/SidebarNav.vue';
import TopBar from '@/components/shell/TopBar.vue';
import BannerBar from '@/components/shell/BannerBar.vue';
import ModuleHost from '@/components/shell/ModuleHost.vue';
import ModalHost from '@/components/ui/ModalHost.vue';
import DeeplinkModal from '@/components/ui/DeeplinkModal.vue';
import OnboardingOverlay from '@/onboarding/OnboardingOverlay.vue';
import { loadPluginAssets } from '@/plugin/loadAssets';
import { useConfigStore } from '@/stores/config';
import { useModulesStore } from '@/stores/modules';
import { useUiStore } from '@/stores/ui';
import { onConfigDeeplink } from '@/deeplink';

const config = useConfigStore();
const modules = useModulesStore();
const ui = useUiStore();

const ready = ref(false);
const onboarding = ref<InstanceType<typeof OnboardingOverlay> | null>(null);
let unsubscribeDeeplink: (() => void) | null = null;

function handleDeeplink(payload: unknown): void {
  if (!payload || typeof payload !== 'object') return;
  const data = payload as { host?: unknown; key?: unknown };
  const host = String(data.host || '').trim();
  const key = String(data.key || '').trim();
  if (!host || !key) return;
  ui.openModal(DeeplinkModal, { host, key }, '收到 FaustBot Cloud 配置');
}

onMounted(async () => {
  unsubscribeDeeplink = onConfigDeeplink(handleDeeplink);
  await loadPluginAssets();
  await config.reloadAll();
  ready.value = true;
  await nextTick();
  onboarding.value?.maybeAutoStart();
});

onBeforeUnmount(() => {
  unsubscribeDeeplink?.();
});
</script>

<template>
  <div class="app-shell">
    <SidebarNav />
    <main class="main-panel">
      <TopBar />
      <BannerBar />
      <section class="content-head">
        <h2 id="moduleTitle">{{ modules.currentModule?.title || '配置' }}</h2>
        <p id="moduleDesc">{{ modules.currentModule?.desc || '在此模块中管理配置项。' }}</p>
      </section>
      <section id="cardsRoot" class="cards-root">
        <article v-if="!ready" id="bootPlaceholder" class="card full-span">
          <h3 class="card-title">配置中心启动中</h3>
          <p class="card-help">正在连接后端并加载配置，请稍候。</p>
        </article>
        <ModuleHost v-else />
      </section>
    </main>
  </div>
  <ModalHost />
  <OnboardingOverlay ref="onboarding" />
</template>
