<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue';
import { useModulesStore } from '@/stores/modules';
import { useSkillsStore } from '@/stores/skills';
import { useUiStore } from '@/stores/ui';
import { normalizeSlug } from './skillUtils';

const props = defineProps<{ agentName: string }>();

const store = useSkillsStore();
const ui = useUiStore();
const modules = useModulesStore();

const slug = ref('');
const overwrite = ref(false);
const slugInput = ref<HTMLInputElement | null>(null);

onMounted(() => {
  void nextTick(() => slugInput.value?.focus());
});

async function install(): Promise<void> {
  const value = normalizeSlug(slug.value);
  if (!value) {
    ui.showBanner('error', '请输入 Skill slug。');
    return;
  }
  ui.closeModal();
  await store.installSlug(value, props.agentName, overwrite.value);
  store.skillsAgent = props.agentName;
  ui.showBanner('success', `Skill ${value} 已安装。`);
  modules.refreshCurrent();
}
</script>

<template>
  <input ref="slugInput" v-model="slug" class="input" placeholder="输入 Skill slug" />
  <label style="display: flex; align-items: center; gap: 8px; font-size: 13px">
    <input v-model="overwrite" type="checkbox" />
    若已存在则覆盖安装
  </label>
  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="install">安装</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">取消</button>
  </div>
</template>
