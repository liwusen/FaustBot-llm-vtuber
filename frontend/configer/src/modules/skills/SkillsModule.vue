<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import InfoCard from '@/components/ui/InfoCard.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SimpleTableCard from '@/components/ui/SimpleTableCard.vue';
import TagListCard from '@/components/ui/TagListCard.vue';
import EmptyState from '@/components/ui/EmptyState.vue';
import { getFaustbotRoot, openFileDialog, openPath } from '@/api/ipc';
import { useConfigStore } from '@/stores/config';
import { useModulesStore } from '@/stores/modules';
import { useSkillsStore } from '@/stores/skills';
import { useUiStore } from '@/stores/ui';
import SkillMdModal from './SkillMdModal.vue';
import SkillSlugInstallModal from './SkillSlugInstallModal.vue';
import SkillZipInstallModal from './SkillZipInstallModal.vue';
import {
  normalizeAgentInput,
  normalizeSlug,
  resolveAgentName,
  skillBasicRows,
  skillDirPath,
  skillFileRows,
  skillMetaRows,
  skillStatusText,
  skillTags,
} from './skillUtils';

defineOptions({ name: 'module-skills' });

const config = useConfigStore();
const modules = useModulesStore();
const store = useSkillsStore();
const ui = useUiStore();

const runtimeAgent = computed(() => String(config.runtime.current_agent || ''));
const configAgent = computed(() => String(config.publicConfig.AGENT_NAME || ''));

// 旧实现：输入框初值 = skillsAgent || runtime.current_agent || AGENT_NAME。
const agentInput = ref(store.skillsAgent || runtimeAgent.value || configAgent.value);

function currentAgentName(): string {
  return resolveAgentName(
    agentInput.value,
    store.skillsAgent,
    runtimeAgent.value,
    configAgent.value,
  );
}

function refresh(): void {
  store.skillsAgent = normalizeAgentInput(agentInput.value);
  modules.refreshCurrent();
}

function installBySlug(): void {
  const agentName = currentAgentName();
  if (!agentName) {
    ui.showBanner('error', '请先填写 Agent 名称。');
    return;
  }
  ui.openModal(SkillSlugInstallModal, { agentName }, '安装 Skill (Slug)');
}

async function installFromZip(): Promise<void> {
  const agentName = currentAgentName();
  if (!agentName) {
    ui.showBanner('error', '请先填写 Agent 名称。');
    return;
  }
  const zipPath = await openFileDialog({
    title: '选择 Skill ZIP 文件',
    filters: [{ name: 'ZIP', extensions: ['zip'] }],
  });
  if (!zipPath) return;
  ui.openModal(SkillZipInstallModal, { agentName, zipPath }, '从 ZIP 安装 Skill');
}

async function openSkillDir(): Promise<void> {
  const agentName = normalizeAgentInput(agentInput.value) || store.skillsAgent;
  if (!agentName) {
    ui.showBanner('error', '请先填写 Agent 名称。');
    return;
  }
  const root = await getFaustbotRoot();
  const basePath = skillDirPath(root, agentName, store.selectedSkillSlug);
  await openPath(basePath);
}

async function selectSkill(slug: string): Promise<void> {
  store.selectedSkillSlug = slug;
  await store.loadSkills();
}

async function editSkill(slug: string): Promise<void> {
  store.selectedSkillSlug = slug;
  await store.loadSkills();
  const detail = store.skillDetail;
  if (!detail) {
    // 详情读取失败时不能用空内容打开编辑器，否则保存会把真实 SKILL.md 覆盖成空串。
    ui.showBanner('error', '加载技能详情失败，无法编辑 SKILL.md。');
    return;
  }
  ui.openModal(
    SkillMdModal,
    {
      slug,
      content: String(detail.skill_md || ''),
      agentName: store.skillsAgent,
    },
    `编辑 SKILL.md - ${slug}`,
  );
}

async function deleteSkill(slug: string): Promise<void> {
  if (!window.confirm(`确定删除 Skill ${normalizeSlug(slug)} ?`)) return;
  await store.deleteSkill(slug);
  modules.refreshCurrent();
}

onMounted(() => {
  void store.loadSkills();
});
</script>

<template>
  <SectionCard title="技能操作">
    <div class="toolbar">
      <input v-model="agentInput" class="input" placeholder="Agent 名称" />
      <button class="btn btn-ghost" type="button" @click="refresh">刷新</button>
      <button class="btn btn-primary" type="button" @click="installBySlug">安装(Slug)</button>
      <button class="btn btn-ghost" type="button" @click="installFromZip">从 ZIP 安装</button>
      <button class="btn btn-ghost" type="button" @click="openSkillDir">打开目录</button>
    </div>
  </SectionCard>

  <article class="card full-span">
    <h3 class="card-title">技能列表</h3>
    <table class="simple-table">
      <thead>
        <tr>
          <th>技能标识</th>
          <th>版本</th>
          <th>状态</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="skill in store.skills"
          :key="String(skill.slug || '')"
          :class="store.selectedSkillSlug === String(skill.slug || '') ? 'selected' : ''"
          @click="selectSkill(String(skill.slug || ''))"
        >
          <td class="cell-primary">{{ skill.slug }}</td>
          <td>{{ skill.version || '-' }}</td>
          <td>{{ skillStatusText(skill) }}</td>
          <td>
            <div class="toolbar compact" @click.stop>
              <button
                class="btn btn-primary"
                type="button"
                @click="editSkill(String(skill.slug || ''))"
              >
                编辑
              </button>
              <button class="btn btn-ghost" type="button" @click="deleteSkill(String(skill.slug || ''))">
                删除
              </button>
            </div>
          </td>
        </tr>
        <tr v-if="!store.skills.length">
          <td class="table-empty" colspan="4">当前没有已安装技能。</td>
        </tr>
      </tbody>
    </table>
  </article>

  <SectionCard v-if="!store.skillDetail" title="技能详情">
    <EmptyState text="请选择技能查看详情。" />
  </SectionCard>

  <template v-else>
    <InfoCard title="技能基本信息" :rows="skillBasicRows(store.skillDetail)" />
    <InfoCard title="相关信息" :rows="skillMetaRows(store.skillDetail)" />
    <TagListCard title="Meta 标签" :tags="skillTags(store.skillDetail)" />
    <SimpleTableCard
      title="Skill 文件清单"
      :columns="['路径', '类型']"
      :rows="skillFileRows(store.skillDetail.files)"
    />
  </template>
</template>
