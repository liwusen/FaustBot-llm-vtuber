<script setup lang="ts">
import { ref } from 'vue';
import { useModulesStore } from '@/stores/modules';
import { useSkillsStore } from '@/stores/skills';
import { useUiStore } from '@/stores/ui';

const props = defineProps<{ agentName: string; zipPath: string }>();

const store = useSkillsStore();
const ui = useUiStore();
const modules = useModulesStore();

const overwrite = ref(false);

async function install(): Promise<void> {
  ui.closeModal();
  await store.installZip(props.zipPath, props.agentName, overwrite.value);
  store.skillsAgent = props.agentName;
  ui.showBanner('success', 'Skill ZIP 安装完成。');
  modules.refreshCurrent();
}
</script>

<template>
  <div class="card-help">{{ zipPath }}</div>
  <label style="display: flex; align-items: center; gap: 8px; font-size: 13px">
    <input v-model="overwrite" type="checkbox" />
    若已存在则覆盖安装
  </label>
  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="install">安装</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">取消</button>
  </div>
</template>
