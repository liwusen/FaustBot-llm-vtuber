<script setup lang="ts">
import { ref } from 'vue';
import SwitchField from '@/components/ui/SwitchField.vue';
import { formatApiError } from '@/api/errors';
import { useUiStore } from '@/stores/ui';
import {
  buildTriggerSubmitPayload,
  TRIGGER_PRIORITY_OPTIONS,
  TRIGGER_TYPE_LABELS,
  type TriggerPayload,
  type TriggerRecord,
} from './payload';

const props = defineProps<{
  initial: TriggerRecord | null;
  onSubmit: (payload: TriggerPayload) => Promise<void> | void;
}>();

const ui = useUiStore();

const source: TriggerRecord = props.initial || {
  id: '',
  type: 'interval',
  interval_seconds: 60,
  target: '',
  eval_code: '',
  remove_when: '',
  recall_description: '',
  lifespan: '',
  run_background: false,
};

const editing = Boolean(props.initial && props.initial.id);
const id = ref(String(source.id || ''));
// 旧表单只有三种 option；遇到 event/nimble-expire 等非 UI 类型时，HTML select 会回落到第一项。
// Vue 的 v-model 不会自动回落，显式做同样处理，避免把没有匹配 option 的类型原样提交给后端。
const rawType = String(source.type || 'interval');
const type = ref(
  Object.prototype.hasOwnProperty.call(TRIGGER_TYPE_LABELS, rawType) ? rawType : 'interval',
);
const recallDescription = ref(String(source.recall_description || ''));
const lifespan = ref(
  source.lifespan === null || source.lifespan === undefined ? '' : String(source.lifespan),
);
const removeWhen = ref(String(source.remove_when || ''));
const priority = ref(String(source.priority || 'normal'));
const runBackground = ref(Boolean(source.run_background));
const intervalSeconds = ref(String(source.interval_seconds || 60));
const target = ref(String(source.target || ''));
const evalCode = ref(String(source.eval_code || ''));

async function submit(): Promise<void> {
  const payload = buildTriggerSubmitPayload({
    id: id.value,
    type: type.value,
    recallDescription: recallDescription.value,
    lifespan: lifespan.value,
    removeWhen: removeWhen.value,
    priority: priority.value,
    runBackground: runBackground.value,
    intervalSeconds: intervalSeconds.value,
    target: target.value,
    evalCode: evalCode.value,
  });
  if (!payload.id) {
    ui.showBanner('error', '触发器 ID 不能为空。');
    return;
  }
  try {
    await props.onSubmit(payload);
    ui.closeModal();
  } catch (err) {
    ui.showBanner('error', `保存触发器失败: ${formatApiError(err)}`);
  }
}
</script>

<template>
  <div class="trigger-form">
    <div class="trigger-field">
      <label class="trigger-field-label">触发器 ID</label>
      <p class="card-help">
        {{ editing
          ? '触发器的唯一标识，创建后不可修改。'
          : '触发器的唯一标识，用于区分与管理不同触发器，创建后不可修改。' }}
      </p>
      <input v-model="id" class="input" :disabled="editing" placeholder="例如 daily-report" />
    </div>

    <div class="trigger-field">
      <label class="trigger-field-label">类型</label>
      <p class="card-help">决定触发方式：间隔、指定时间点或表达式条件。切换后下方参数会自动变化。</p>
      <select v-model="type" class="select">
        <option v-for="(label, value) in TRIGGER_TYPE_LABELS" :key="value" :value="value">
          {{ label }}
        </option>
      </select>
    </div>

    <div class="trigger-field">
      <label class="trigger-field-label">描述</label>
      <p class="card-help">触发器的备注说明，触发时会作为提示回传给 Agent，便于其理解本次触发的意图。</p>
      <input v-model="recallDescription" class="input" placeholder="描述（可选）" />
    </div>

    <div class="trigger-field">
      <label class="trigger-field-label">有效期</label>
      <p class="card-help">触发器的存活秒数，超时后自动删除；留空表示长期有效。</p>
      <input
        v-model="lifespan"
        class="input"
        type="number"
        placeholder="存活秒数（可选，超时自动删除）"
      />
    </div>

    <div class="trigger-field">
      <label class="trigger-field-label">删除时间</label>
      <p class="card-help">自然语言，表示应该在什么时候删除它，例如 'after 24 hours' 或 'at midnight'</p>
      <input
        v-model="removeWhen"
        class="input"
        placeholder="例如 after 24 hours, at midnight, 或时条件满足"
      />
    </div>

    <div class="trigger-field">
      <label class="trigger-field-label">优先级</label>
      <p class="card-help">立即唤醒=告警/紧急事件；常规=默认；低优先级=高频感知事件，消费时按 30 秒窗口合并为一次唤醒。</p>
      <select v-model="priority" class="input">
        <option v-for="[value, label] in TRIGGER_PRIORITY_OPTIONS" :key="value" :value="value">
          {{ label }}
        </option>
      </select>
    </div>

    <div class="trigger-field">
      <label class="trigger-field-label">运行方式</label>
      <p class="card-help">后台任务的处理过程与结果不会推送给前端界面，适合静默执行的定时作业。</p>
      <SwitchField
        v-model="runBackground"
        :label="runBackground ? '后台任务' : '前台任务'"
      />
    </div>

    <div class="field-wrap">
      <div v-if="type === 'interval'" class="trigger-field">
        <label class="trigger-field-label">间隔秒数</label>
        <p class="card-help">每隔多少秒触发一次，最小 1 秒。</p>
        <input v-model="intervalSeconds" class="input" type="number" min="1" />
      </div>
      <div v-else-if="type === 'datetime'" class="trigger-field">
        <label class="trigger-field-label">触发时间</label>
        <p class="card-help">在指定的单一时间点触发，格式 YYYY-MM-DD HH:mm:ss（24 小时制）。</p>
        <input v-model="target" class="input" placeholder="YYYY-MM-DD HH:mm:ss" />
      </div>
      <div v-else class="trigger-field">
        <label class="trigger-field-label">eval 表达式</label>
        <p class="card-help">Python 表达式，由系统周期性求值，返回真值时触发；可访问运行时上下文变量。</p>
        <textarea v-model="evalCode" class="textarea code-area"></textarea>
      </div>
    </div>
  </div>

  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="submit">保存</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">取消</button>
  </div>
</template>
