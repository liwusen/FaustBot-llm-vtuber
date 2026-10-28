<script setup lang="ts">
import { onMounted } from 'vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import { useModulesStore } from '@/stores/modules';
import { useTriggersStore } from '@/stores/triggers';
import { useUiStore } from '@/stores/ui';
import TriggerEditorModal from './TriggerEditorModal.vue';
import { buildTriggerUpdatePayload, type TriggerPayload, type TriggerRecord } from './payload';

defineOptions({ name: 'module-triggers' });

const modules = useModulesStore();
const store = useTriggersStore();
const ui = useUiStore();

function openEditor(initial: TriggerRecord | null, title: string, submit: (payload: TriggerPayload) => Promise<void>): void {
  ui.openModal(
    TriggerEditorModal,
    {
      initial,
      onSubmit: async (payload: TriggerPayload) => {
        await submit(payload);
      },
    },
    title,
  );
}

function createTrigger(): void {
  openEditor(null, '新建 Trigger', async (payload) => {
    await store.createTrigger(payload);
    modules.refreshCurrent();
  });
}

function editSelected(): void {
  if (!store.selectedTriggerId) return;
  const source = store.findTrigger(store.selectedTriggerId);
  if (!source) return;
  const base = buildTriggerUpdatePayload(source);
  openEditor(base, `编辑 Trigger: ${String(source.id || '')}`, async (payload) => {
    await store.updateTrigger(store.selectedTriggerId, payload);
    modules.refreshCurrent();
  });
}

async function deleteSelected(): Promise<void> {
  if (!store.selectedTriggerId) return;
  if (!window.confirm(`确定删除 Trigger ${store.selectedTriggerId} ?`)) return;
  await store.deleteTrigger(store.selectedTriggerId);
  store.selectedTriggerId = '';
  modules.refreshCurrent();
}

function editRow(trigger: TriggerRecord): void {
  const tid = String(trigger.id || '');
  store.selectedTriggerId = tid;
  const source = store.findTrigger(tid);
  if (!source) return;
  const base = buildTriggerUpdatePayload(source);
  openEditor(base, `编辑 Trigger: ${tid}`, async (payload) => {
    await store.updateTrigger(tid, payload);
    modules.refreshCurrent();
  });
}

async function deleteRow(trigger: TriggerRecord): Promise<void> {
  const tid = String(trigger.id || '');
  if (!window.confirm(`确定删除 Trigger ${tid} ?`)) return;
  await store.deleteTrigger(tid);
  if (store.selectedTriggerId === tid) store.selectedTriggerId = '';
  modules.refreshCurrent();
}

function refresh(): void {
  modules.refreshCurrent();
}

onMounted(() => {
  void store.loadTriggers();
});
</script>

<template>
  <SectionCard title="触发器操作">
    <div class="toolbar">
      <button class="btn btn-ghost" type="button" @click="refresh">刷新</button>
      <button class="btn btn-primary" type="button" @click="createTrigger">新建</button>
      <button class="btn btn-ghost" type="button" @click="editSelected">编辑</button>
      <button class="btn btn-ghost" type="button" @click="deleteSelected">删除</button>
    </div>
  </SectionCard>

  <article class="card full-span">
    <h3 class="card-title">触发器列表</h3>
    <table class="simple-table">
      <thead>
        <tr>
          <th>触发器 ID</th>
          <th>类型</th>
          <th>运行方式</th>
          <th>有效期</th>
          <th>说明</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="trigger in store.triggers"
          :key="String(trigger.id || '')"
          :class="store.selectedTriggerId === String(trigger.id || '') ? 'selected' : ''"
          @click="store.selectedTriggerId = String(trigger.id || '')"
        >
          <td class="cell-primary">{{ trigger.id }}</td>
          <td>{{ trigger.type || '-' }}</td>
          <td>{{ trigger.run_background ? '后台' : '前台' }}</td>
          <td>{{ trigger.lifespan == null ? '未设置' : String(trigger.lifespan) }}</td>
          <td>{{ trigger.recall_description || '-' }}</td>
          <td>
            <div class="toolbar compact" @click.stop>
              <button class="btn btn-ghost" type="button" @click="editRow(trigger)">编辑</button>
              <button class="btn btn-ghost" type="button" @click="deleteRow(trigger)">删除</button>
            </div>
          </td>
        </tr>
        <tr v-if="!store.triggers.length">
          <td class="table-empty" colspan="6">当前没有 Trigger。 </td>
        </tr>
      </tbody>
    </table>
  </article>
</template>
