<script setup lang="ts">
import { useUiStore } from '@/stores/ui';

const ui = useUiStore();
</script>

<template>
  <section
    id="banner"
    class="banner"
    :class="[ui.bannerType, { hidden: !ui.bannerVisible }]"
    role="status"
    aria-live="polite"
  >{{ ui.bannerText }}</section>
</template>
