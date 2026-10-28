<script setup lang="ts">
import { computed } from 'vue';
import FieldCard from '@/components/config/FieldCard.vue';
import EmptyState from '@/components/ui/EmptyState.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import { toggleLogPanel } from '@/api/ipc';
import { useConfigStore } from '@/stores/config';
import { useModulesStore } from '@/stores/modules';
import { useUiStore } from '@/stores/ui';
import { bucketModuleFields, pickUnclassifiedFields } from './advancedFields';

defineOptions({ name: 'module-advanced' });

const config = useConfigStore();
const modules = useModulesStore();
const ui = useUiStore();

const fields = computed(() => bucketModuleFields(
  pickUnclassifiedFields(config.publicConfig, config.privateConfig),
));

const empty = computed(
  () => !fields.value.basicPublic.length
    && !fields.value.basicPrivate.length
    && fields.value.advancedCount === 0,
);

// 与旧 renderConfigModule 一致：展开状态按模块记住，重渲染不会自己合上。
const advancedOpen = computed(() => ui.foldState('advanced:advanced') === true);

function toggleAdvanced(): void {
  ui.setFoldState('advanced:advanced', !advancedOpen.value);
}

// 旧 makeFieldCard 的 rerender 就是 renderModule()：随值联动的控件改完重建模块。
function rerender(): void {
  modules.refreshCurrent();
}

async function onToggleLogPanel(): Promise<void> {
  try {
    await toggleLogPanel();
  } catch (err) {
    console.error('toggleLogPanel failed', err);
  }
}
</script>

<template>
  <SectionCard v-if="empty" title="提示">
    <EmptyState text="当前模块暂无可编辑字段。" />
  </SectionCard>

  <template v-else>
    <FieldCard
      v-for="key in fields.basicPublic"
      :key="`public:${key}`"
      scope="public"
      :field-key="key"
      :rerender="rerender"
    />
    <FieldCard
      v-for="key in fields.basicPrivate"
      :key="`private:${key}`"
      scope="private"
      :field-key="key"
      :rerender="rerender"
    />

    <template v-if="fields.advancedCount">
      <div class="advanced-divider" :class="{ open: advancedOpen }" @click="toggleAdvanced">
        <span class="arrow">▶</span> 高级配置
        <span class="badge-adv">{{ fields.advancedCount }} 项</span>
      </div>
      <div class="advanced-body" :class="{ open: advancedOpen }">
        <FieldCard
          v-for="key in fields.advancedPublic"
          :key="`advanced-public:${key}`"
          scope="public"
          :field-key="key"
          :rerender="rerender"
        />
        <FieldCard
          v-for="key in fields.advancedPrivate"
          :key="`advanced-private:${key}`"
          scope="private"
          :field-key="key"
          :rerender="rerender"
        />
      </div>
    </template>

    <article class="card">
      <h3 class="card-title">日志面板</h3>
      <p class="card-help">打开/切换主窗口的日志浮动面板，实时查看后端日志。</p>
      <button class="btn btn-primary" type="button" @click="onToggleLogPanel">
        打开日志面板
      </button>
    </article>
  </template>
</template>
