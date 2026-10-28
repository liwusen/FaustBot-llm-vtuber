<script setup lang="ts">
import { computed } from 'vue';
import ConfigField from '@/components/config/ConfigField.vue';
import { getFieldMeta } from '@/config/meta';
import { useConfigStore } from '@/stores/config';
import type { ConfigScope } from '@/config/types';

const props = defineProps<{
  scope: ConfigScope;
  fieldKey: string;
  rerender?: () => void;
}>();

const config = useConfigStore();
const meta = computed(() => getFieldMeta(props.fieldKey));
const dirty = computed(() => config.isDirty(props.scope, props.fieldKey));
</script>

<template>
  <article class="card full-span field-card" :class="{ dirty }">
    <div class="field-card-head">
      <div class="field-card-title-wrap">
        <h3 class="card-title">{{ meta.label }}</h3>
        <div class="card-key">{{ fieldKey }}</div>
      </div>
    </div>
    <p class="card-help">{{ meta.help || '' }}</p>
    <ConfigField :scope="scope" :field-key="fieldKey" :rerender="rerender" />
  </article>
</template>
