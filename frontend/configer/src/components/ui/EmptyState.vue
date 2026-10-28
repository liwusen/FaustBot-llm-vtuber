<script setup lang="ts">
defineProps<{ text: string }>();
</script>

<template>
  <div class="empty-state">{{ text }}</div>
</template>
