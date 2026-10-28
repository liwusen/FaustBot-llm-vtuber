<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { API } from '@/api/endpoints';
import { cfgApi } from '@/api/ipc';
import { useConfigStore } from '@/stores/config';
import { useUiStore } from '@/stores/ui';

/**
 * Edge TTS 语音选择器（旧 tts-voice.js 的 Vue 版本）。
 * 接口语义、筛选顺序与文案逐条保留；确认后写入 EDGE_TTS_VOICE 并关闭弹窗。
 */
interface EdgeTtsVoice {
  name: string;
  voice_id: string;
  language: string;
  gender: string;
  voice_personalities: string;
  content_categories?: string;
}

type ListState = 'loading' | 'ready' | 'empty' | 'error';

const config = useConfigStore();
const ui = useUiStore();

const searchQuery = ref('');
const language = ref('');
const gender = ref('');

const languages = ref<string[]>([]);
const genders = ref<string[]>([]);
const voices = ref<EdgeTtsVoice[]>([]);
const listState = ref<ListState>('loading');
const selected = ref<EdgeTtsVoice | null>(null);

async function loadFilters(): Promise<void> {
  try {
    const [languageData, genderData] = await Promise.all([
      cfgApi<{ languages?: string[] }>('GET', API.edgeTtsLanguages),
      cfgApi<{ genders?: string[] }>('GET', API.edgeTtsGenders),
    ]);
    languages.value = Array.isArray(languageData?.languages) ? languageData.languages : [];
    genders.value = Array.isArray(genderData?.genders) ? genderData.genders : [];
  } catch (err) {
    console.error('加载筛选器失败:', err);
  }
}

async function loadVoices(query = '', forceRefresh = false): Promise<void> {
  listState.value = 'loading';
  try {
    if (forceRefresh) {
      await cfgApi('POST', API.edgeTtsCacheRefresh, {});
    }
    const data = await cfgApi<{ voices?: EdgeTtsVoice[] }>(
      'GET',
      API.edgeTtsVoicesSearch,
      null,
      {
        q: query,
        language: language.value || null,
        gender: gender.value || null,
      },
    );
    voices.value = Array.isArray(data?.voices) ? data.voices : [];
    listState.value = voices.value.length ? 'ready' : 'empty';
  } catch (err) {
    console.error('加载语音列表失败:', err);
    voices.value = [];
    listState.value = 'error';
  }
}

function search(): void {
  void loadVoices(searchQuery.value);
}

function refresh(): void {
  void loadVoices('', true);
}

function select(voice: EdgeTtsVoice): void {
  selected.value = voice;
}

function confirmSelection(): void {
  const voice = selected.value;
  if (!voice) return;
  config.updateValue('public', 'EDGE_TTS_VOICE', voice.voice_id);
  ui.closeModal();
  ui.showBanner('success', `已选择语音: ${voice.name}`);
}

onMounted(async () => {
  await loadFilters();
  await loadVoices();
});
</script>

<template>
  <div class="edge-tts-voice-modal">
    <div class="search-bar">
      <input
        v-model="searchQuery"
        class="search-input"
        type="text"
        placeholder="搜索语音名称、ID或特征..."
      />
      <button class="btn btn-primary" type="button" @click="search">搜索</button>
      <button class="btn btn-secondary" type="button" @click="refresh">刷新</button>
    </div>

    <div class="filter-bar">
      <select v-model="language" class="filter-select">
        <option value="">所有语言</option>
        <option v-for="item in languages" :key="item" :value="item">{{ item }}</option>
      </select>
      <select v-model="gender" class="filter-select">
        <option value="">所有性别</option>
        <option v-for="item in genders" :key="item" :value="item">{{ item }}</option>
      </select>
    </div>

    <div class="voice-list">
      <div v-if="listState === 'loading'" class="voice-empty">加载中...</div>
      <div v-else-if="listState === 'error'" class="voice-empty is-error">加载失败</div>
      <div v-else-if="listState === 'empty'" class="voice-empty">未找到匹配的语音</div>
      <template v-else>
        <div
          v-for="voice in voices"
          :key="voice.voice_id"
          class="voice-item"
          :class="{ 'is-selected': selected?.voice_id === voice.voice_id }"
          @click="select(voice)"
        >
          <div class="voice-name">{{ voice.name }}</div>
          <div class="voice-meta">标识：{{ voice.voice_id }}</div>
          <div class="voice-meta">语言：{{ voice.language }} | 性别：{{ voice.gender }}</div>
          <div class="voice-meta dim">特征: {{ voice.voice_personalities }}</div>
        </div>
      </template>
    </div>

    <div class="selected-info">
      <template v-if="selected">
        <div><strong>已选择: {{ selected.name }}</strong></div>
        <div>语音标识：{{ selected.voice_id }}</div>
        <div>语言：{{ selected.language }} | 性别：{{ selected.gender }}</div>
        <div>特征: {{ selected.voice_personalities }}</div>
        <button type="button" @click="confirmSelection">确认选择</button>
      </template>
    </div>
  </div>
</template>
