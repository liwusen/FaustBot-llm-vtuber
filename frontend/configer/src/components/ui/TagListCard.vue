<script setup lang="ts">
import { humanizeLabel } from '@/utils/format';

defineProps<{
  title: string;
  tags: string[];
}>();
</script>

<template>
  <article class="card">
    <h3 class="card-title">{{ humanizeLabel(title) }}</h3>
    <div class="tag-list">
      <div v-if="!Array.isArray(tags) || !tags.length" class="empty-state">无</div>
      <template v-else>
        <span v-for="(tag, index) in tags" :key="index" class="tag-chip">{{ String(tag) }}</span>
      </template>
    </div>
  </article>
</template>
