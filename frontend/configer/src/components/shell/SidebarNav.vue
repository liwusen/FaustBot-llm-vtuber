<script setup lang="ts">
import { nextTick } from 'vue';
import { useModulesStore } from '@/stores/modules';

const modules = useModulesStore();

async function onNavigate(moduleId: string): Promise<void> {
  if (!modules.navigate(moduleId)) return;
  await nextTick();
  const root = document.getElementById('cardsRoot');
  if (root) root.scrollTop = 0;
}
</script>

<template>
  <aside class="sidebar">
    <div class="brand">
      <span class="brand-eyebrow">FaustBot / Configer</span>
      <h1>配置</h1>
      <p>模型、语音与记忆的本地设置</p>
    </div>
    <nav id="moduleNav" class="module-nav" aria-label="配置模块">
      <button
        v-for="mod in modules.allModules"
        :key="mod.id"
        type="button"
        class="nav-btn"
        :class="{ active: modules.activeModule === mod.id }"
        :data-module="mod.id"
        @click="onNavigate(mod.id)"
      >
        <span>{{ mod.title }}</span>
        <small>{{ mod.nav || mod.desc }}</small>
      </button>
    </nav>
  </aside>
</template>
