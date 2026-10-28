// 记忆模块：三栏（左树 / 列表·图谱 / 详情）+ 底部通栏可视化卡片。
// KeepAlive 依赖 defineOptions({ name: 'module-memory' })。
// 离开守卫覆盖：切换目录 / 文件 / 视图 / 打开编辑器（各动作内部）+ 离开记忆页（此处注册）。

<script setup lang="ts">
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import { useModulesStore } from '@/stores/modules';
import { MEM_VIEWS, useMemoryStore } from '@/stores/memory';
import type { MemView } from '@/memory/types';
import MemCrumbs from './MemCrumbs.vue';
import MemTree from './MemTree.vue';
import MemListView from './MemListView.vue';
import MemDetail from './MemDetail.vue';
import MemEditor from './MemEditor.vue';
import MemGraph from './MemGraph.vue';
import MemVizCard from './MemVizCard.vue';
import MemContextMenu from './MemContextMenu.vue';
import MemColumnDropdown from './MemColumnDropdown.vue';
import { guardEditor } from './editorGuard';

defineOptions({ name: 'module-memory' });

const store = useMemoryStore();
const modules = useModulesStore();
const loadError = ref('');
let disposeLeaveGuard: (() => void) | null = null;

function switchView(id: MemView): void {
  if (store.view === id) return;
  if (store.guardEditor('切换视图', () => store.setView(id))) return;
  store.setView(id);
}

onMounted(async () => {
  store.setEditorGuard(guardEditor);
  disposeLeaveGuard = modules.registerLeaveGuard((next) => {
    if (next === 'memory') return true;
    return !store.guardEditor('离开记忆页', () => {
      modules.switchModule(next);
    });
  });
  try {
    await store.reload();
    loadError.value = '';
  } catch (err) {
    console.error('[memory] 模块加载失败', err);
    loadError.value = `模块加载失败: ${String((err as Error)?.message || err)}`;
  }
});

onActivated(() => {
  store.moduleVisible = true;
});

onDeactivated(() => {
  store.moduleVisible = false;
});

onBeforeUnmount(() => {
  if (disposeLeaveGuard) {
    disposeLeaveGuard();
    disposeLeaveGuard = null;
  }
  store.setEditorGuard(null);
  store.moduleVisible = false;
  // 与旧 teardown 一致：组件销毁时丢弃编辑器活实例
  store.closeEditor();
});
</script>

<template>
  <SectionCard title="记忆">
    <div v-if="loadError" class="empty-state">{{ loadError }}</div>
    <div v-else class="mem-shell-wrap">
      <div class="mem-shell">
        <MemTree />
        <div class="mem-main">
          <div class="mem-top">
            <MemCrumbs />
            <div class="mem-views">
              <div class="seg">
                <button
                  v-for="item in MEM_VIEWS"
                  :key="item.id"
                  class="seg-item"
                  :class="{ active: store.view === item.id }"
                  type="button"
                  :data-view="item.id"
                  @click="switchView(item.id)"
                >
                  {{ item.label }}
                </button>
              </div>
            </div>
          </div>
          <div class="mem-views-host">
            <div id="memViewList" class="mem-view" :hidden="store.view !== 'list'">
              <template v-if="store.view === 'list'">
                <MemEditor v-if="store.editor" />
                <MemListView v-else />
              </template>
            </div>
            <div id="memViewGraph" class="mem-view" :hidden="store.view !== 'graph'">
              <MemGraph v-if="store.view === 'graph'" />
            </div>
          </div>
        </div>
        <MemDetail />
        <MemVizCard />
      </div>
    </div>
    <MemContextMenu />
    <MemColumnDropdown />
  </SectionCard>
</template>
