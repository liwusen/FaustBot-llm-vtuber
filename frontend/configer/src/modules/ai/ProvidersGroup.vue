<script setup lang="ts">
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import FieldGroup from '@/components/config/FieldGroup.vue';
import type { AiGroupSpec } from '@/config/types';
import BulkActions from './BulkActions.vue';
import MainModelLine from './MainModelLine.vue';
import ModelTable from './ModelTable.vue';
import ProviderModal from './ProviderModal.vue';
import ProviderTable from './ProviderTable.vue';

// 「对话模型服务商」自定义分组（旧 _aiProvidersGroup）：服务商表 + 模型表 + 批量操作。
defineProps<{
  spec: AiGroupSpec;
}>();

const store = useProvidersStore();
const ui = useUiStore();

function addProvider(): void {
  ui.openModal(ProviderModal, { provider: null }, '添加服务商');
}
</script>

<template>
  <FieldGroup :id="spec.id" :title="spec.title" :desc="spec.desc">
    <template #meta>
      <MainModelLine />
    </template>

    <div v-if="!store.providers.length" class="ai-empty">
      <p class="ai-empty-text">
        还没有服务商。填三项就能开始用：名称、接口地址、密钥；模型可以一键拉取。
      </p>
      <button class="btn btn-primary" type="button" @click="addProvider">添加服务商</button>
    </div>

    <template v-else>
      <div class="ai-subhead">
        <span class="ai-subhead-title">服务商</span>
        <span class="ai-subhead-count">{{ store.providers.length }} 个</span>
        <button class="btn btn-primary ai-subhead-btn" type="button" @click="addProvider">
          添加服务商
        </button>
      </div>
      <ProviderTable />
      <ModelTable />
      <BulkActions />
    </template>
  </FieldGroup>
</template>
