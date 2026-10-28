// 详情栏图片预览大图。

<script setup lang="ts">
defineProps<{ src: string }>();
</script>

<template>
  <img class="mem-image-full" alt="图片预览" :src="src" />
</template>
