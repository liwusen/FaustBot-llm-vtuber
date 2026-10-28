<script setup lang="ts">
import { computed } from 'vue';
import FieldCard from '@/components/config/FieldCard.vue';
import { useConfigStore } from '@/stores/config';
import { useUiStore } from '@/stores/ui';
import EdgeTtsVoicePicker from './EdgeTtsVoicePicker.vue';
import { pickSpeechFieldKeys, splitAdvancedKeys } from './filters';

defineOptions({ name: 'module-speech' });

const config = useConfigStore();
const ui = useUiStore();

const fields = computed(() => pickSpeechFieldKeys(config.publicConfig, config.privateConfig));
const basicPublic = computed(() => splitAdvancedKeys(fields.value.publicKeys).basic);
const basicPrivate = computed(() => splitAdvancedKeys(fields.value.privateKeys).basic);
const advancedPublic = computed(() => splitAdvancedKeys(fields.value.publicKeys).advanced);
const advancedPrivate = computed(() => splitAdvancedKeys(fields.value.privateKeys).advanced);
const advancedCount = computed(() => advancedPublic.value.length + advancedPrivate.value.length);
const totalFieldCount = computed(() => (
  fields.value.publicKeys.length + fields.value.privateKeys.length
));

const advancedOpen = computed(() => ui.foldState('advanced:speech') === true);

function toggleAdvanced(): void {
  ui.setFoldState('advanced:speech', !advancedOpen.value);
}

async function applyTtsRefer(): Promise<void> {
  await config.applyTtsReferToService();
}

function openEdgeTtsVoicePicker(): void {
  ui.openModal(EdgeTtsVoicePicker, {}, 'Edge TTS 语音管理');
}
</script>

<template>
  <section v-if="!totalFieldCount" class="module-section card full-span">
    <h3 class="section-title">提示</h3>
    <div class="module-section-content">
      <div class="empty-state">当前模块暂无可编辑字段。</div>
    </div>
  </section>

  <template v-else>
    <FieldCard
      v-for="key in basicPublic"
      :key="`public-${key}`"
      scope="public"
      :field-key="key"
    />
    <FieldCard
      v-for="key in basicPrivate"
      :key="`private-${key}`"
      scope="private"
      :field-key="key"
    />

    <template v-if="advancedCount">
      <div class="advanced-divider" :class="{ open: advancedOpen }" @click="toggleAdvanced">
        <span class="arrow">▶</span> 高级配置
        <span class="badge-adv">{{ advancedCount }} 项</span>
      </div>
      <div class="advanced-body" :class="{ open: advancedOpen }">
        <FieldCard
          v-for="key in advancedPublic"
          :key="`advanced-${key}`"
          scope="public"
          :field-key="key"
        />
        <FieldCard
          v-for="key in advancedPrivate"
          :key="`advanced-private-${key}`"
          scope="private"
          :field-key="key"
        />
      </div>
    </template>

    <article class="card">
      <h3 class="card-title">TTS 服务即时应用</h3>
      <p class="card-help">local TTS 模式下可把参考音频参数即时同步到 5000 端口服务。</p>
      <button class="btn btn-secondary" type="button" @click="applyTtsRefer">
        应用参考音频到 TTS 服务
      </button>
    </article>

    <article class="card">
      <h3 class="card-title">Edge TTS 语音选择器</h3>
      <p class="card-help">点击打开语音选择器，浏览和选择可用的 Edge TTS 语音。</p>
      <button class="btn btn-primary" type="button" @click="openEdgeTtsVoicePicker">
        选择 Edge TTS 语音
      </button>
    </article>
  </template>
</template>
