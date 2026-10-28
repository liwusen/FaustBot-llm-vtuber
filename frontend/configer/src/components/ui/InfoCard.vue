<script setup lang="ts">
import { formatScalar, humanizeLabel } from '@/utils/format';
import type { InfoRow } from '@/config/types';

defineProps<{
  title: string;
  rows: InfoRow[];
}>();
</script>

<template>
  <article class="card">
    <h3 class="card-title">{{ humanizeLabel(title) }}</h3>
    <table class="simple-table info-table">
      <tbody>
        <tr v-for="(row, index) in rows" :key="`${row.label}-${index}`">
          <td class="cell-label">{{ humanizeLabel(row.label) }}</td>
          <td class="cell-value">{{ formatScalar(row.value, row.label) }}</td>
        </tr>
      </tbody>
    </table>
  </article>
</template>
