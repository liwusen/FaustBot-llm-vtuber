<script setup lang="ts">
import { computed } from 'vue';
import ConfigField from '@/components/config/ConfigField.vue';
import { getFieldMeta } from '@/config/meta';
import { useConfigStore } from '@/stores/config';
import { useUiStore } from '@/stores/ui';
import { isMaskedSecret } from '@/utils/format';
import type { ConfigScope } from '@/config/types';

const props = defineProps<{
  scope: ConfigScope;
  fieldKey: string;
  rerender?: () => void;
  readonly?: boolean;
}>();

const config = useConfigStore();
const ui = useUiStore();

const meta = computed(() => getFieldMeta(props.fieldKey));
const dirty = computed(() => config.isDirty(props.scope, props.fieldKey));
const currentValue = computed<unknown>(
  () => (props.scope === 'private' ? config.privateConfig : config.publicConfig)[props.fieldKey],
);
const masked = computed(() => isMaskedSecret(currentValue.value));
const staticText = computed(() => {
  const value = currentValue.value;
  return value === null || value === undefined || value === '' ? '-' : String(value);
});

async function copyKey(): Promise<void> {
  try {
    await navigator.clipboard.writeText(props.fieldKey);
    ui.showBanner('info', `已复制键名：${props.fieldKey}`);
  } catch {
    ui.showBanner('error', `复制键名失败：${props.fieldKey}`);
  }
}

function undo(): void {
  config.revertValue(props.scope, props.fieldKey);
  props.rerender?.();
}
</script>

<template>
  <div class="field-row" :class="{ dirty }">
    <div class="field-row-info">
      <div class="field-row-title">
        <span class="field-row-label">{{ meta.label || fieldKey }}</span>
        <button
          class="key-copy"
          type="button"
          :title="fieldKey"
          :aria-label="`复制配置键名 ${fieldKey}`"
          @click="copyKey"
        >
          ⧉
        </button>
        <span v-if="masked" class="row-flag ok">已设置</span>
        <span v-if="dirty" class="row-flag dirty">已修改</span>
      </div>
      <p v-if="meta.help" class="field-row-help">{{ meta.help }}</p>
    </div>
    <div class="field-row-control">
      <template v-if="readonly">
        <span class="field-row-static">{{ staticText }}</span>
      </template>
      <template v-else>
        <ConfigField :scope="scope" :field-key="fieldKey" :rerender="rerender" />
        <button v-show="dirty" class="btn btn-ghost row-undo" type="button" @click="undo">
          撤销
        </button>
      </template>
    </div>
  </div>
</template>
