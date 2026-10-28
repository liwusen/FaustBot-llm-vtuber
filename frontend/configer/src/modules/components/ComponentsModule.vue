<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import Icon from '@/components/ui/Icon.vue';
import { useComponentsStore } from '@/stores/components';
import { useUiStore } from '@/stores/ui';
import ComponentLogsModal from './ComponentLogsModal.vue';
import {
  FUNASR_VARIANTS,
  TTS_VARIANTS,
  funasrInstallText,
  funasrTorchText,
  gpuRows,
  serviceSummary,
  serviceTone,
  ttsInstallText,
  ttsVariantText,
} from './keyMap';

defineOptions({ name: 'module-components' });

const store = useComponentsStore();
const ui = useUiStore();

const gpu = computed(() => store.status?.gpu);
const asrService = computed(() => store.status?.services?.asr);
const ttsService = computed(() => store.status?.services?.tts);
const mcService = computed(() => store.status?.services?.minecraft);
const funasr = computed(() => store.status?.components?.funasr);
const tts = computed(() => store.status?.components?.tts);
const minecraftBridge = computed(() => store.status?.components?.minecraft_bridge);

const gpuInfoRows = computed(() => gpuRows(gpu.value));

const funasrVariant = ref('cpu');
const useAliyunMirror = ref(false);
const ttsVariant = ref('standard');
const logArea = ref<HTMLElement | null>(null);

// 旧实现每次整页重渲染都会按检测到的变体重置下拉框。
watch(
  funasr,
  (data) => {
    if (data) funasrVariant.value = String(data.torch_variant || 'cpu');
  },
  { immediate: true },
);

watch(
  tts,
  (data) => {
    if (data) ttsVariant.value = String(data.variant || 'standard');
  },
  { immediate: true },
);

watch(
  () => store.installLogs.length,
  async () => {
    await nextTick();
    if (logArea.value) logArea.value.scrollTop = logArea.value.scrollHeight;
  },
);

function showLogs(serviceKey: string): void {
  ui.openModal(ComponentLogsModal, { serviceKey }, `${serviceKey.toUpperCase()} 日志`);
}

function installFunasr(): void {
  void store.startInstall('funasr', {
    torchVariant: funasrVariant.value,
    useAliyunMirror: useAliyunMirror.value,
  });
}

function installTts(): void {
  void store.startInstall('tts', { ttsVariant: ttsVariant.value });
}

onMounted(async () => {
  await store.initialLoad();
});

onBeforeUnmount(() => {
  store.dispose();
});
</script>

<template>
  <template v-if="store.status">
    <!-- GPU 信息 -->
    <article v-if="gpu" class="card">
      <div class="card-title-line">
        <Icon name="server" :size="15" />
        <span>GPU 信息</span>
      </div>
      <div v-for="row in gpuInfoRows" :key="row.label" class="service-row">
        <span class="k">{{ row.label }}</span>
        <span class="v">{{ row.value }}</span>
      </div>
    </article>

    <!-- ASR 服务 -->
    <article class="card">
      <div class="card-title-line">
        <span class="cfg-dot" :class="serviceTone(asrService)"></span>
        <Icon name="mic" :size="15" />
        <span>ASR 服务</span>
      </div>
      <div class="card-help">
        本地语音识别服务，Whisper 模式与 FunASR 模式共用。无论选择哪种 ASR 模式，都需要先启动此服务；切换模式无需重装，只需重启服务。
      </div>
      <div class="service-row">
        <span class="k">服务</span>
        <span class="v">{{ serviceSummary(asrService) }}</span>
      </div>
      <div class="card-actions">
        <button class="btn btn-primary" type="button" @click="store.startService('asr')">
          <Icon name="play" :size="13" /><span>启动服务</span>
        </button>
        <button class="btn btn-danger" type="button" @click="store.stopService('asr')">
          <Icon name="stop" :size="13" /><span>停止服务</span>
        </button>
        <button class="btn btn-ghost" type="button" @click="showLogs('asr')">
          <Icon name="doc" :size="13" /><span>日志</span>
        </button>
      </div>
    </article>

    <!-- FunASR / Torch 依赖 -->
    <article v-if="funasr" class="card">
      <div class="card-title-line">
        <span class="cfg-dot" :class="funasr.installed ? 'is-ok' : 'is-stop'"></span>
        <Icon name="package" :size="15" />
        <span>FunASR / Torch 依赖</span>
      </div>
      <div class="card-help">
        仅「FunASR 模式」需要：下载安装 PyTorch 与 FunASR 模型（体积较大）。若使用「Whisper 模式」则无需安装此依赖，只需启动上方的 ASR 服务即可。
      </div>
      <div class="service-row">
        <span class="k">安装状态</span>
        <span class="v">{{ funasrInstallText(funasr) }}</span>
      </div>
      <div class="service-row">
        <span class="k">PyTorch 环境</span>
        <span class="v">{{ funasrTorchText(funasr) }}</span>
      </div>
      <div class="card-actions">
        <select v-model="funasrVariant" class="select select-compact">
          <option v-for="option in FUNASR_VARIANTS" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
        <label class="check-inline">
          <input v-model="useAliyunMirror" type="checkbox" />
          <span>使用阿里云镜像</span>
        </label>
        <button class="btn btn-primary" type="button" @click="installFunasr">
          <Icon name="refresh" :size="13" /><span>安装/重装</span>
        </button>
      </div>
    </article>

    <!-- TTS -->
    <article v-if="tts" class="card">
      <div class="card-title-line">
        <span class="cfg-dot" :class="tts.installed ? 'is-ok' : 'is-stop'"></span>
        <Icon name="speaker" :size="15" />
        <span>TTS</span>
      </div>
      <div class="service-row">
        <span class="k">状态</span>
        <span class="v">{{ ttsInstallText(tts) }}</span>
      </div>
      <div class="service-row">
        <span class="k">TTS GPU 类型</span>
        <span class="v">{{ ttsVariantText(tts) }}</span>
      </div>
      <div class="service-row">
        <span class="k">服务</span>
        <span class="v">{{ serviceSummary(ttsService) }}</span>
      </div>
      <div class="card-actions">
        <select v-model="ttsVariant" class="select select-compact">
          <option v-for="option in TTS_VARIANTS" :key="option.value" :value="option.value">
            {{ option.label }}
          </option>
        </select>
        <button class="btn btn-primary" type="button" @click="installTts">
          <Icon name="refresh" :size="13" /><span>重新下载</span>
        </button>
        <button class="btn btn-ghost" type="button" @click="store.startService('tts')">
          <Icon name="play" :size="13" /><span>启动服务</span>
        </button>
        <button class="btn btn-danger" type="button" @click="store.stopService('tts')">
          <Icon name="stop" :size="13" /><span>停止服务</span>
        </button>
        <button class="btn btn-ghost" type="button" @click="showLogs('tts')">
          <Icon name="doc" :size="13" /><span>日志</span>
        </button>
      </div>
    </article>

    <!-- Minecraft 操作桥 -->
    <article v-if="minecraftBridge" class="card">
      <div class="card-title-line">
        <span class="cfg-dot" :class="serviceTone(mcService)"></span>
        <Icon name="gamepad" :size="15" />
        <span>Minecraft 操作桥</span>
      </div>
      <div class="service-row">
        <span class="k">状态</span>
        <span class="v">{{ serviceSummary(mcService) }}</span>
      </div>
      <div class="card-actions">
        <button class="btn btn-ghost" type="button" @click="store.startService('minecraft')">
          <Icon name="play" :size="13" /><span>启动服务</span>
        </button>
        <button class="btn btn-danger" type="button" @click="store.stopService('minecraft')">
          <Icon name="stop" :size="13" /><span>停止服务</span>
        </button>
        <button class="btn btn-ghost" type="button" @click="showLogs('minecraft')">
          <Icon name="doc" :size="13" /><span>日志</span>
        </button>
      </div>
      <div class="service-note">后端启动时自动运行，无需下载</div>
    </article>

    <!-- 安装进度区 -->
    <div v-show="store.installVisible" id="componentProgressArea" style="margin-top: 8px">
      <div class="progress-track">
        <div class="progress-fill" :style="{ width: `${store.installPercent}%` }"></div>
      </div>
      <div class="progress-note">{{ store.installMessage }}</div>
      <button
        class="btn btn-danger"
        type="button"
        :style="{ display: store.cancelVisible ? 'inline-block' : 'none', marginTop: '8px' }"
        :disabled="store.installCancelling"
        @click="store.cancelInstall()"
      >
        终止
      </button>
      <div
        ref="logArea"
        class="install-log"
        :style="{ display: store.installLogs.length ? 'block' : 'none' }"
      >{{ store.installLogText }}</div>
    </div>
  </template>
</template>
