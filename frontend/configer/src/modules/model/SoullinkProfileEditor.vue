<script setup lang="ts">
import { onMounted, ref } from 'vue';
import CodeMirrorHost from '@/components/ui/CodeMirrorHost.vue';
import { ensureModelProfile, saveModelProfile } from '@/api/ipc';
import { useUiStore } from '@/stores/ui';

/**
 * soullink 参数映射编辑器（旧 openSoullinkProfileEditor）。
 * 纯 IPC：ensureModelProfile / saveModelProfile，不直接读配置。
 */
const props = defineProps<{
  modelDir: string;
  initialProfile: Record<string, unknown>;
  initialStatus: string;
}>();

const ui = useUiStore();
const editorHost = ref<InstanceType<typeof CodeMirrorHost> | null>(null);

const text = ref(JSON.stringify(props.initialProfile ?? {}, null, 2));
const status = ref(props.initialStatus || '');
const statusError = ref(false);
const editorReady = ref(false);

function setStatus(next: string, isError = false): void {
  status.value = next;
  statusError.value = isError;
}

function errorText(err: unknown): string {
  const message = (err as { message?: unknown })?.message;
  return String(message || err);
}

onMounted(() => {
  const factory = window.createCodeMirrorEditor;
  editorReady.value = typeof factory === 'function';
  if (!editorReady.value) {
    setStatus('CodeMirror 编辑器初始化失败：window.createCodeMirrorEditor 未定义', true);
  }
});

async function save(): Promise<void> {
  if (!editorReady.value || !editorHost.value?.isReady()) {
    setStatus('编辑器未就绪', true);
    return;
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(editorHost.value.getValue());
  } catch (err) {
    setStatus(`JSON 解析失败：${errorText(err)}`, true);
    return;
  }
  try {
    const result = await saveModelProfile(props.modelDir, parsed);
    if (!result || !result.ok) throw new Error((result && result.error) || '保存失败');
    setStatus('已保存，重载模型后生效');
  } catch (err) {
    setStatus(`保存失败：${errorText(err)}`, true);
  }
}

async function regenerate(): Promise<void> {
  try {
    const result = await ensureModelProfile(props.modelDir, true);
    if (!result || !result.ok) throw new Error((result && result.error) || '重新生成失败');
    text.value = JSON.stringify(result.profile || {}, null, 2);
    setStatus('已重新生成（启发式），保存后重载生效');
  } catch (err) {
    setStatus(`重新生成失败：${errorText(err)}`, true);
  }
}
</script>

<template>
  <div class="card-help" :style="{ color: statusError ? '#c62828' : 'inherit' }">
    {{ status }}
  </div>
  <div
    style="
      height: 420px;
      border: 1px solid rgba(0, 0, 0, 0.15);
      border-radius: 8px;
      overflow: hidden;
    "
  >
    <CodeMirrorHost ref="editorHost" v-model="text" language="json" height="420px" />
  </div>
  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="save">保存</button>
    <button class="btn btn-ghost" type="button" @click="regenerate">重新生成</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">关闭</button>
  </div>
</template>
