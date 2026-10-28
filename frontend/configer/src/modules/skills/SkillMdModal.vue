<script setup lang="ts">
import { ref } from 'vue';
import CodeMirrorHost from '@/components/ui/CodeMirrorHost.vue';
import { useModulesStore } from '@/stores/modules';
import { useSkillsStore } from '@/stores/skills';
import { useUiStore } from '@/stores/ui';

const props = defineProps<{ slug: string; content: string; agentName: string }>();

const store = useSkillsStore();
const ui = useUiStore();
const modules = useModulesStore();

const content = ref(props.content);

async function save(): Promise<void> {
  const agentName = String(props.agentName || store.skillsAgent || '').trim();
  await store.saveSkillMd(props.slug, agentName, content.value);
  ui.showBanner('success', `SKILL.md 已保存: ${props.slug}`);
  ui.closeModal();
  modules.refreshCurrent();
}
</script>

<template>
  <div class="card-help">Skill: {{ slug }} | Agent: {{ agentName || '-' }}</div>
  <div style="height: 360px; border: 1px solid var(--line); border-radius: 10px; overflow: hidden">
    <CodeMirrorHost v-model="content" language="markdown" :read-only="false" height="360px" />
  </div>
  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="save">保存 SKILL.md</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">关闭</button>
  </div>
</template>
