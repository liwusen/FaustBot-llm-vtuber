// 内联编辑器：CodeMirror 挂载在列表位置，标题/索引开关/脏状态/保存/退出。
// 编辑态下树、面包屑、详情栏、可视化卡片保持可见。

<script setup lang="ts">
import { computed } from 'vue';
import CodeMirrorHost from '@/components/ui/CodeMirrorHost.vue';
import { editorMetaText, editorStatusText } from '@/memory/editor';
import { guessEditorLanguage } from '@/shims/codeMirror';
import { useMemoryStore } from '@/stores/memory';

const store = useMemoryStore();

const editor = computed(() => store.editor);
const language = computed(() => (editor.value ? guessEditorLanguage(editor.value.path) : 'markdown'));
const statusText = computed(() => editorStatusText(editor.value));
const metaText = computed(() => editorMetaText(editor.value));

function exit(): void {
  if (store.guardEditor('退出编辑', exit)) return;
  store.closeEditor();
}

async function save(): Promise<void> {
  await store.saveEditor();
}

function onChange(value: string): void {
  store.updateEditorContent(value);
}

function toggleIndex(event: Event): void {
  store.toggleEditorIndex((event.target as HTMLInputElement).checked);
}
</script>

<template>
  <div v-if="editor">
    <div class="mem-editor-bar">
      <button class="btn btn-exit" type="button" @click="exit">退出编辑</button>
      <span class="mem-editor-name mono">{{ editor.path }}</span>
      <button class="btn btn-go" type="button" @click="save">保存</button>
      <div class="mem-editor-status" :class="{ dirty: editor.dirty }">{{ statusText }}</div>
    </div>
    <div class="mem-editor-meta">
      <label class="switch-text">
        <input type="checkbox" :checked="editor.index" @change="toggleIndex" />
        <span>保存后加入索引</span>
      </label>
      <span class="card-help mono">{{ metaText }}</span>
      <span class="card-help">标签在详情栏编辑</span>
    </div>
    <div class="mem-editor-box">
      <CodeMirrorHost
        :key="editor.path"
        :model-value="editor.content"
        :language="language"
        height="100%"
        @update:model-value="onChange"
      />
    </div>
  </div>
</template>
