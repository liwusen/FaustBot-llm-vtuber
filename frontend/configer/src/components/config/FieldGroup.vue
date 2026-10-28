<script setup lang="ts">
import { computed, useSlots } from 'vue';
import FieldFold from '@/components/config/FieldFold.vue';

const props = withDefaults(
  defineProps<{
    id?: string;
    title: string;
    desc?: string;
    meta?: unknown;
    folded?: boolean;
    foldKey?: string;
    foldLabel?: string;
    foot?: string;
  }>(),
  { id: '', desc: '', folded: false, foldKey: '', foldLabel: '展开', foot: '' },
);

const slots = useSlots();

const groupId = computed(() => (props.id ? `group-${props.id}` : undefined));
const foldStateKey = computed(() => props.foldKey || props.id || '');
const hasMeta = computed(
  () => Boolean(slots.meta) || (props.meta !== undefined && props.meta !== null && props.meta !== ''),
);
</script>

<template>
  <article :id="groupId" class="card full-span field-group">
    <div class="field-group-head">
      <div class="field-group-heading">
        <h3 class="card-title">{{ title }}</h3>
        <p v-if="desc" class="card-help">{{ desc }}</p>
      </div>
      <div v-if="hasMeta" class="field-group-meta">
        <slot name="meta">{{ meta }}</slot>
      </div>
      <div v-if="$slots.actions" class="toolbar">
        <slot name="actions" />
      </div>
    </div>
    <FieldFold v-if="folded" :title="foldLabel || '展开'" :state-key="foldStateKey">
      <slot />
      <p v-if="foot" class="field-group-foot">{{ foot }}</p>
    </FieldFold>
    <template v-else>
      <slot />
      <p v-if="foot" class="field-group-foot">{{ foot }}</p>
    </template>
  </article>
</template>
