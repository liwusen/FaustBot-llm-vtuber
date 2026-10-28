<script setup lang="ts">
import { computed } from 'vue';
import { formatScalar, humanizeLabel } from '@/utils/format';

defineOptions({ name: 'DataView' });

const props = defineProps<{ value: unknown }>();

function isObjectValue(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object';
}

const isPrimitive = computed(() => !isObjectValue(props.value));
const isArrayValue = computed(() => Array.isArray(props.value));
const arrayItems = computed<unknown[]>(() => (Array.isArray(props.value) ? props.value : []));
const objectEntries = computed<Array<[string, unknown]>>(() => {
  if (!isObjectValue(props.value) || Array.isArray(props.value)) return [];
  return Object.entries(props.value);
});

function isComplex(value: unknown): boolean {
  return value !== null && typeof value === 'object';
}

function detailsSummary(value: unknown): string {
  return Array.isArray(value) ? `数组 (${value.length})` : '展开详情';
}
</script>

<template>
  <span v-if="isPrimitive" class="kv-inline-value">{{ formatScalar(value) }}</span>
  <div v-else-if="isArrayValue" class="kv-stack">
    <div v-if="!arrayItems.length" class="empty-state">空数组</div>
    <div v-for="(item, index) in arrayItems" :key="index" class="kv-row">
      <div class="kv-key">第 {{ index + 1 }} 项</div>
      <div class="kv-value">
        <DataView :value="item" />
      </div>
    </div>
  </div>
  <table v-else class="simple-table simple-table-compact">
    <tbody>
      <tr v-if="!objectEntries.length">
        <td class="table-empty" colspan="2">空对象</td>
      </tr>
      <tr v-for="entry in objectEntries" :key="entry[0]">
        <td class="cell-label">{{ humanizeLabel(entry[0]) }}</td>
        <td class="cell-value">
          <details v-if="isComplex(entry[1])" class="kv-details">
            <summary class="kv-summary">{{ detailsSummary(entry[1]) }}</summary>
            <DataView :value="entry[1]" />
          </details>
          <div v-else class="kv-inline-value">{{ formatScalar(entry[1], entry[0]) }}</div>
        </td>
      </tr>
    </tbody>
  </table>
</template>
