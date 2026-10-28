<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue';
import { formatApiError } from '@/api/errors';
import FieldFold from '@/components/config/FieldFold.vue';
import FieldGroup from '@/components/config/FieldGroup.vue';
import FieldRow from '@/components/config/FieldRow.vue';
import {
  AI_GROUPS,
  AI_GROUPS_TYPED,
  AI_PRIVATE_KEYS,
  AI_PUBLIC_KEYS,
} from '@/config/constants';
import type { AiGroupSpec, ConfigScope } from '@/config/types';
import { useConfigStore } from '@/stores/config';
import { useModulesStore } from '@/stores/modules';
import { useProvidersStore } from '@/stores/providers';
import ProvidersGroup from './ProvidersGroup.vue';

// AI 服务商页：分组布局（组目录 + 组卡片 + 字段行）。
// 分组与行顺序来自 constants.ts 的 AI_GROUPS（那也是本页的渲染白名单）；
// 服务商/模型的数据操作与弹窗在 providerWizard 对应的 Vue 组件里。
// 所有本地改动只写 store，随窗口顶部「保存」统一提交；页内重渲染不重新拉后端。
defineOptions({ name: 'module-ai' });

interface RowView {
  scope: ConfigScope;
  key: string;
  readonly: boolean;
}

interface GroupView {
  spec: AiGroupSpec;
  normal: RowView[];
  advanced: RowView[];
  meta?: string;
  foldCount: number;
  forceOpen: boolean;
}

const config = useConfigStore();
const providers = useProvidersStore();
const modules = useModulesStore();

const groups = AI_GROUPS_TYPED;

// 白名单里没被任何分组覆盖的键：只告警，不静默丢弃（另有静态测试守护）。
const coveredKeys = new Set<string>();
for (const spec of AI_GROUPS as unknown as AiGroupSpec[]) {
  for (const row of spec.keys || []) coveredKeys.add(row.key);
}
const missingKeys = [...AI_PUBLIC_KEYS, ...AI_PRIVATE_KEYS].filter((key) => !coveredKeys.has(key));
if (missingKeys.length) {
  console.warn('[ai] 这些 AI 配置键没有分组，界面上不会出现:', missingKeys);
}

const loading = ref(true);
const loadError = ref('');
const activeChip = ref('');

onMounted(async () => {
  try {
    // 只在首次进入时读后端；切走再回来复用 store，避免冲掉未保存的服务商/模型草稿。
    await providers.ensureProviders();
  } catch (err) {
    loadError.value = `模块加载失败: ${formatApiError(err)}`;
  } finally {
    loading.value = false;
  }
});

function buildRows(spec: AiGroupSpec): { normal: RowView[]; advanced: RowView[] } {
  const normal: RowView[] = [];
  const advanced: RowView[] = [];
  for (const rowSpec of spec.keys || []) {
    const scope: ConfigScope = rowSpec.scope || 'public';
    const bucket = scope === 'private' ? config.privateConfig : config.publicConfig;
    if (!(rowSpec.key in bucket)) continue; // 该键在当前配置里不存在
    if (rowSpec.gated && !config.publicConfig[rowSpec.gated]) continue;
    const row: RowView = {
      scope,
      key: rowSpec.key,
      readonly: spec.id === 'agent' || rowSpec.readonly === true,
    };
    if (rowSpec.advanced) advanced.push(row);
    else normal.push(row);
  }
  return { normal, advanced };
}

const groupViews = computed<GroupView[]>(() => {
  const views: GroupView[] = [];
  for (const spec of groups) {
    if (spec.custom === 'providers') continue;
    const { normal, advanced } = buildRows(spec);
    if (!normal.length && !advanced.length) continue; // 整组都没有可显示的行
    const dirtyCount = (spec.keys || [])
      .filter((row) => config.isDirty(row.scope || 'public', row.key)).length;
    views.push({
      spec,
      normal,
      advanced,
      meta: dirtyCount ? `${dirtyCount} 项已修改` : undefined,
      foldCount: normal.length + (advanced.length ? 1 : 0),
      forceOpen: advanced.some((row) => config.isDirty(row.scope, row.key)),
    });
  }
  return views;
});

const viewsById = computed(() => new Map(groupViews.value.map((view) => [view.spec.id, view])));

function viewOf(spec: AiGroupSpec): GroupView | undefined {
  return viewsById.value.get(spec.id);
}

function jumpTo(groupId: string): void {
  const target = document.getElementById(`group-${groupId}`);
  if (!target) return;
  target.scrollIntoView({ block: 'start', behavior: 'smooth' });
  activeChip.value = groupId;
}

function goToAgent(): void {
  if (!modules.navigate('agent')) return;
  void nextTick(() => {
    const root = document.getElementById('cardsRoot');
    if (root) root.scrollTop = 0;
  });
}
</script>

<template>
  <template v-if="loading"></template>

  <template v-else-if="loadError">
    <section class="module-section card full-span">
      <h3 class="section-title">错误</h3>
      <div class="module-section-content">
        <div class="empty-state">{{ loadError }}</div>
      </div>
    </section>
  </template>

  <template v-else-if="!groups.length">
    <section class="module-section card full-span">
      <h3 class="section-title">提示</h3>
      <div class="module-section-content">
        <div class="empty-state">分组配置没有加载（AI_GROUPS 缺失）。</div>
      </div>
    </section>
  </template>

  <template v-else>
    <nav class="ai-dir" aria-label="AI 服务商页分组">
      <button
        v-for="spec in groups"
        :key="spec.id"
        class="ai-dir-chip"
        :class="{ active: activeChip === spec.id }"
        type="button"
        @click="jumpTo(spec.id)"
      >{{ spec.title }}</button>
    </nav>

    <template v-for="spec in groups" :key="spec.id">
      <ProvidersGroup v-if="spec.custom === 'providers'" :spec="spec" />

      <FieldGroup
        v-else-if="viewOf(spec)"
        :id="spec.id"
        :title="spec.title"
        :desc="spec.desc"
        :meta="viewOf(spec)?.meta"
        :foot="spec.footer"
        :folded="Boolean(spec.folded)"
        :fold-key="`ai-group-${spec.id}`"
        :fold-label="`展开（${viewOf(spec)?.foldCount ?? 0} 项）`"
      >
        <FieldRow
          v-for="row in (viewOf(spec)?.normal ?? [])"
          :key="`${row.scope}:${row.key}`"
          :scope="row.scope"
          :field-key="row.key"
          :readonly="row.readonly"
        />

        <FieldFold
          v-if="(viewOf(spec)?.advanced.length ?? 0) > 0"
          :title="`高级（${viewOf(spec)?.advanced.length ?? 0} 项）`"
          :state-key="`ai-adv-${spec.id}`"
          :force-open="Boolean(viewOf(spec)?.forceOpen)"
        >
          <FieldRow
            v-for="row in (viewOf(spec)?.advanced ?? [])"
            :key="`${row.scope}:${row.key}`"
            :scope="row.scope"
            :field-key="row.key"
            :readonly="row.readonly"
          />
        </FieldFold>

        <template v-if="spec.id === 'agent'" #actions>
          <button class="btn btn-secondary" type="button" @click="goToAgent">
            前往「角色」模块
          </button>
        </template>
      </FieldGroup>
    </template>
  </template>
</template>
