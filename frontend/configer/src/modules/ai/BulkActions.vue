<script setup lang="ts">
import { API } from '@/api/endpoints';
import { formatApiError } from '@/api/errors';
import { cfgApi } from '@/api/ipc';
import FieldFold from '@/components/config/FieldFold.vue';
import { useProvidersStore } from '@/stores/providers';
import { useUiStore } from '@/stores/ui';
import type { LoadModelsResult } from './providerOps';

// 批量操作（旧 _aiBulkActions）：会直接写配置，收进「高级操作」折叠。
const store = useProvidersStore();
const ui = useUiStore();

async function pullPendingProviders(): Promise<void> {
  const pending = (store.providers || []).filter((p) => !(p.models && p.models.length));
  if (!pending.length) {
    ui.showBanner('info', '所有服务商都已经有模型了');
    return;
  }
  let ok = 0;
  let fail = 0;
  for (const provider of pending) {
    try {
      const result = await cfgApi<LoadModelsResult>('POST', API.providerLoadModels(provider.name));
      if ((result.models || []).length) ok += 1;
      else fail += 1;
    } catch {
      fail += 1;
    }
  }
  await store.refreshProviders();
  ui.showBanner(
    fail ? 'error' : 'success',
    `拉取完成：成功 ${ok} 个，失败 ${fail} 个（保存后生效）`,
  );
}

async function fillContextLengths(): Promise<void> {
  const all = store.providers || [];
  if (!all.length) {
    ui.showBanner('info', '还没有服务商');
    return;
  }
  let ok = 0;
  let fail = 0;
  for (const provider of all) {
    try {
      await cfgApi('POST', API.providerLoadModels(provider.name));
      ok += 1;
    } catch {
      fail += 1;
    }
  }
  await store.refreshProviders();
  const filled = (store.providers || []).reduce(
    (total, provider) => total + Object.keys(provider.model_context_lengths || {}).length,
    0,
  );
  ui.showBanner(
    fail ? 'error' : 'success',
    `上下文长度已补全：成功 ${ok} 个服务商，失败 ${fail} 个；共 ${filled} 个模型有明确长度（保存后生效）`,
  );
}

async function refreshModelCatalog(): Promise<void> {
  try {
    const result = await cfgApi<{ status?: string; providers?: number; matched?: number; error?: string }>(
      'POST',
      API.modelsDevRefresh,
    );
    await store.refreshProviders();
    ui.showBanner(
      result.status === 'ok' ? 'success' : 'error',
      `模型目录已更新：${result.providers || 0} 个服务商，识别出 ${result.matched || 0} 个`
        + (result.error ? `；错误：${result.error}` : ''),
    );
  } catch (err) {
    ui.showBanner('error', `更新目录失败: ${formatApiError(err)}`);
  }
}
</script>

<template>
  <FieldFold title="高级操作" state-key="ai-bulk">
    <p class="form-hint">
      这几个动作会直接写配置，通常只在新增服务商后做一次。日常改服务商请用上面的「编辑」。
    </p>
    <div class="toolbar">
      <button class="btn btn-secondary" type="button" @click="pullPendingProviders">
        拉取没加载模型的服务商
      </button>
      <button class="btn btn-secondary" type="button" @click="fillContextLengths">补全上下文长度</button>
      <button class="btn btn-ghost" type="button" @click="refreshModelCatalog">更新模型目录</button>
    </div>
  </FieldFold>
</template>
