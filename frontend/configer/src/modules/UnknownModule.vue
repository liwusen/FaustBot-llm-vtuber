<script setup lang="ts">
defineOptions({ name: 'module-unknown' });
defineProps<{ moduleId?: string }>();
</script>

<template>
  <article class="card full-span">
    <h3 class="card-title">数据</h3>
    <div class="empty-state">该模块没有可渲染的页面。</div>
  </article>
</template>
