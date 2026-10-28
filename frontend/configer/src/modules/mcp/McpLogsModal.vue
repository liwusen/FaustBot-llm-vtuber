<script setup lang="ts">
import { computed } from 'vue';
import { mcpLogText } from './mcpUtils';

const props = defineProps<{ logTail: unknown }>();

const text = computed(() => mcpLogText(props.logTail));
</script>

<template>
  <textarea
    class="textarea"
    readonly
    style="min-height: 360px"
    :value="text"
  ></textarea>
</template>
