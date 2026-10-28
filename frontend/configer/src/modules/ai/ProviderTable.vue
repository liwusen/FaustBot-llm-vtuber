<script setup lang="ts">
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import type { ProviderConfig } from '@/config/types';
import ProviderModal from './ProviderModal.vue';
import { keyStatus } from './providerOps';

// 服务商表（旧 _aiProvidersTable + _aiDeleteProvider）。
const store = useProvidersStore();
const ui = useUiStore();

function editProvider(provider: ProviderConfig): void {
  ui.openModal(ProviderModal, { provider }, `编辑服务商 · ${provider.name}`);
}

function modelCountText(provider: ProviderConfig): string {
  const count = (provider.models || []).length;
  return count ? `${count} 个` : '未拉取';
}

function deleteProvider(provider: ProviderConfig): void {
  // 删除确认文案与旧实现逐字一致。
  if (!window.confirm(`删除服务商「${provider.name}」？它的模型与相关设置会一起从待保存改动里移除。`)) return;
  store.removeProvider(provider.name);
  ui.showBanner('success', `已删除服务商 ${provider.name}（点窗口顶部「保存」写入配置）`);
}
</script>

<template>
  <table class="simple-table ai-table">
    <thead>
      <tr>
        <th>服务商</th>
        <th>接口地址</th>
        <th title="只显示有没有配，不显示具体值">密钥</th>
        <th>模型</th>
        <th>操作</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="provider in store.providers" :key="provider.name">
        <td class="cell-primary">
          <div class="ai-provider-cell">
            <span class="ai-provider-name">{{ provider.name }}</span>
            <span :class="provider.catalog_provider_id ? 'tag-chip' : 'tag-chip tag-chip-muted'">
              {{ provider.catalog_provider_id ? `目录 ${provider.catalog_provider_id}` : '目录未识别' }}
            </span>
          </div>
        </td>
        <td>
          <span class="ai-url" :title="provider.base_url || ''">{{ provider.base_url || '—' }}</span>
        </td>
        <td>
          <span :class="provider.key ? 'ai-flag ok' : 'ai-flag warn'">{{ keyStatus(provider) }}</span>
        </td>
        <td>
          <span :class="{ 'ai-muted': !(provider.models || []).length }">{{ modelCountText(provider) }}</span>
        </td>
        <td class="ai-ops-cell">
          <div class="toolbar compact">
            <button class="btn btn-ghost" type="button" @click="editProvider(provider)">编辑</button>
            <button class="btn btn-ghost btn-danger" type="button" @click="deleteProvider(provider)">
              删除
            </button>
          </div>
        </td>
      </tr>
    </tbody>
  </table>
</template>
