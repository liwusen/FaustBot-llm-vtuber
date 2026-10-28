<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { formatApiError } from '@/api/errors';
import { useComponentsStore } from '@/stores/components';

const props = defineProps<{ serviceKey: string }>();

const store = useComponentsStore();
const text = ref('加载中...');

onMounted(async () => {
  try {
    text.value = await store.fetchServiceLog(props.serviceKey);
  } catch (err) {
    text.value = `获取日志失败: ${formatApiError(err)}`;
  }
});
</script>

<template>
  <textarea class="textarea service-log-area" readonly :value="text"></textarea>
</template>
