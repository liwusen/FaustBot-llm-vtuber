// 触发器 payload 规则：逐行对照旧 trigger-utils.js 与 trigger-editor.js 的提交逻辑。

export interface TriggerRecord {
  id?: string;
  type?: string;
  interval_seconds?: number | string;
  target?: string;
  eval_code?: string;
  remove_when?: string;
  recall_description?: string;
  lifespan?: number | string | null;
  priority?: string;
  run_background?: boolean;
  [key: string]: unknown;
}

export type TriggerPayload = Record<string, unknown>;

export interface TriggerFormState {
  id: string;
  type: string;
  recallDescription: string;
  lifespan: string;
  removeWhen: string;
  priority: string;
  runBackground: boolean;
  intervalSeconds: string;
  target: string;
  evalCode: string;
}

/**
 * 旧 buildTriggerUpdatePayload：编辑时用来填充编辑器的基础 payload。
 * 注意它不带 priority / remove_when（旧实现如此，编辑器会回落到「常规」）。
 */
export function buildTriggerUpdatePayload(source: TriggerRecord): TriggerPayload {
  const base: TriggerPayload = {
    id: String(source.id || '').trim(),
    type: String(source.type || '').trim(),
    recall_description: String(source.recall_description || ''),
    run_background: Boolean(source.run_background),
  };
  if (
    source.lifespan !== null
    && source.lifespan !== undefined
    && String(source.lifespan).trim() !== ''
  ) {
    base.lifespan = Number(source.lifespan);
  }
  if (base.type === 'interval') {
    base.interval_seconds = Number(source.interval_seconds || 60);
  } else if (base.type === 'datetime') {
    base.target = String(source.target || '').trim();
  } else if (base.type === 'py-eval') {
    base.eval_code = String(source.eval_code || '');
  }
  return base;
}

/**
 * 旧 trigger-editor.js 的「保存」payload：比 update payload 多 priority，
 * 同样从不提交 remove_when（旧实现的输入框仅展示）。
 */
export function buildTriggerSubmitPayload(form: TriggerFormState): TriggerPayload {
  const payload: TriggerPayload = {
    id: form.id.trim(),
    type: form.type,
    recall_description: form.recallDescription.trim(),
    run_background: Boolean(form.runBackground),
    priority: form.priority,
  };
  const lifespanRaw = form.lifespan.trim();
  if (lifespanRaw) payload.lifespan = Number(lifespanRaw);

  if (payload.type === 'interval') {
    payload.interval_seconds = Number(form.intervalSeconds || '60');
  } else if (payload.type === 'datetime') {
    payload.target = form.target.trim();
  } else {
    payload.eval_code = form.evalCode;
  }
  return payload;
}

export const TRIGGER_TYPE_LABELS: Record<string, string> = {
  interval: '间隔触发（每隔固定秒数）',
  datetime: '定时触发（指定时间点）',
  'py-eval': '表达式触发（条件满足时）',
};

export const TRIGGER_PRIORITY_OPTIONS: Array<[string, string]> = [
  ['normal', '常规（默认）'],
  ['interrupt', '立即唤醒'],
  ['batched', '低优先级（合并）'],
];
