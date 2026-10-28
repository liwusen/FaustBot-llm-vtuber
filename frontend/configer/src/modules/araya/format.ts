// Araya 页的纯逻辑：原因/状态/耗时/相对时间文案、运行概览、轨迹时间线分组。
// 逐行对照旧 modules/araya.js。

import type { InfoRow } from '@/config/types';

export interface ArayaRunLog {
  reason?: unknown;
  status?: unknown;
  duration_seconds?: unknown;
  started_at_iso?: unknown;
  finished_at_iso?: unknown;
  error?: unknown;
  response?: unknown;
  [key: string]: unknown;
}

export interface ArayaTraceMessage {
  role?: string;
  content?: unknown;
  tool_name?: unknown;
  call_id?: unknown;
  args?: unknown;
  result?: unknown;
  duration_seconds?: unknown;
  [key: string]: unknown;
}

export interface ArayaTrace {
  conversation_id?: unknown;
  reason?: unknown;
  status?: unknown;
  started_at_iso?: unknown;
  finished_at_iso?: unknown;
  messages?: ArayaTraceMessage[];
  tool_calls?: unknown[];
  [key: string]: unknown;
}

export interface ArayaStatus {
  enabled?: boolean;
  enabled_by_config?: boolean;
  idle_minutes?: number;
  idle_seconds?: number;
  target_agent?: unknown;
  run_in_progress?: boolean;
  running?: boolean;
  last_error?: unknown;
  last_main_activity_at?: unknown;
  updated_at?: unknown;
  last_log?: ArayaRunLog | null;
  [key: string]: unknown;
}

export function arayaReasonLabel(reason: unknown): string {
  const map: Record<string, string> = {
    idle: '空闲自动触发',
    manual: '手动触发',
    manual_from_configer: '配置中心手动触发',
  };
  const key = String(reason || '');
  return map[key] || (key || '-');
}

export function arayaStatusLabel(status: unknown): string {
  const map: Record<string, string> = { ok: '成功', running: '运行中', error: '失败', idle: '未运行' };
  const key = String(status || '');
  return map[key] || (key || '-');
}

export function arayaDuration(seconds: unknown): string {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 0) return '-';
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} 秒`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s - m * 60);
  return rest ? `${m} 分 ${rest} 秒` : `${m} 分钟`;
}

/** 旧 arayaRelative；now 可注入，仅用于测试，默认与旧实现一样取当前时间。 */
export function arayaRelative(iso: unknown, now: number = Date.now()): string {
  const t = Date.parse(String(iso || ''));
  if (Number.isNaN(t)) return '-';
  const diff = Math.max(0, (now - t) / 1000);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

export function arayaText(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

export type ArayaTraceBlock =
  | { kind: 'assistant'; text: string }
  | {
      kind: 'tool';
      callId: string;
      name: string;
      args: unknown;
      result: unknown;
      duration: unknown;
      done: boolean;
    }
  | { kind: 'result'; name: string; result: unknown };

/**
 * 旧 createArayaTimeline 的消息分组：
 * - 连续 assistant chunk 合并成一段输出；
 * - tool / tool_result 按 call_id（缺失时按工具名）配对；
 * - 只有结果没有调用的截断 trace 单独成行。
 */
export function arayaTraceBlocks(messages: unknown): ArayaTraceBlock[] {
  const blocks: ArayaTraceBlock[] = [];
  const toolIndex = new Map<string, number>();
  let lastAssistant: Extract<ArayaTraceBlock, { kind: 'assistant' }> | null = null;

  for (const item of Array.isArray(messages) ? messages : []) {
    if (!item || typeof item !== 'object') continue;
    const message = item as ArayaTraceMessage;
    const role = String(message.role || '');
    if (role === 'user') continue; // 内部指令，不是给用户看的输出
    if (role === 'assistant') {
      const chunk = String(message.content ?? '');
      if (!chunk) continue;
      if (!lastAssistant) {
        lastAssistant = { kind: 'assistant', text: '' };
        blocks.push(lastAssistant);
      }
      lastAssistant.text += chunk;
      continue;
    }
    if (role === 'tool') {
      lastAssistant = null;
      const callId = String(message.call_id || '');
      const name = String(message.tool_name || '');
      const block: ArayaTraceBlock = {
        kind: 'tool',
        callId,
        name,
        args: message.args,
        result: '',
        duration: null,
        done: false,
      };
      blocks.push(block);
      toolIndex.set(callId || name, blocks.length - 1);
      continue;
    }
    if (role === 'tool_result') {
      lastAssistant = null;
      const callId = String(message.call_id || '');
      const name = String(message.tool_name || '');
      const index = toolIndex.get(callId) ?? toolIndex.get(name);
      const block = index === undefined ? undefined : blocks[index];
      if (!block || block.kind !== 'tool' || block.done) {
        blocks.push({ kind: 'result', name, result: message.result });
        continue;
      }
      block.result = message.result;
      block.duration = message.duration_seconds;
      block.done = true;
    }
  }
  return blocks;
}

/** 旧 endTool 的计时文案：可解析的 duration → 秒数，否则「完成」。 */
export function arayaToolTime(block: Extract<ArayaTraceBlock, { kind: 'tool' }>): string {
  if (!block.done) return '运行中…';
  const seconds = Number(block.duration);
  return Number.isFinite(seconds) ? `${seconds.toFixed(2)}s` : '完成';
}

export interface ArayaBadge {
  text: string;
  className: string;
}

export function arayaOverviewBadge(
  status: ArayaStatus | null | undefined,
  running: boolean,
): ArayaBadge {
  const data = status || {};
  if (running) return { text: '运行中', className: 'badge badge-run' };
  if (data.last_error) return { text: '上次运行异常', className: 'badge badge-dirty' };
  if (data.enabled === false) return { text: '自动触发已停用', className: 'badge badge-muted' };
  return { text: '等待触发', className: 'badge badge-valid' };
}

export interface ArayaStatCell {
  value: string;
  label: string;
}

export function arayaOverviewStats(
  status: ArayaStatus | null | undefined,
  lastLog: ArayaRunLog | null | undefined,
): ArayaStatCell[] {
  const data = status || {};
  const idleMinutes = Number(data.idle_minutes || 0);
  const idleSeconds = Math.max(0, Number(data.idle_seconds || 0));
  const remainSeconds = Math.max(0, idleMinutes * 60 - idleSeconds);
  return [
    { value: String(data.target_agent || '-'), label: '目标 Agent' },
    {
      value: arayaDuration(idleSeconds),
      label: `主 Agent 空闲（阈值 ${idleMinutes || '-'} 分钟）`,
    },
    {
      value: data.enabled === false ? '已停用' : arayaDuration(remainSeconds),
      label: '距下次自动触发',
    },
    {
      value: lastLog && lastLog.finished_at_iso ? arayaRelative(lastLog.finished_at_iso) : '无记录',
      label: '上次运行',
    },
  ];
}

export function arayaOverviewHelp(status: ArayaStatus | null | undefined): string {
  const data = status || {};
  return `主 Agent 最近活跃 ${data.last_main_activity_at || '-'} · 状态更新 ${data.updated_at || '-'}`;
}

/** 旧「最近一次运行」InfoCard 的行；错误行只在有错误时出现。 */
export function arayaRunRows(lastLog: ArayaRunLog | null | undefined): InfoRow[] {
  const log = lastLog || {};
  const rows: InfoRow[] = [
    { label: '触发原因', value: arayaReasonLabel(log.reason) },
    { label: '结果', value: arayaStatusLabel(log.status) },
    { label: '耗时', value: arayaDuration(log.duration_seconds) },
    { label: '开始时间', value: log.started_at_iso },
    { label: '结束时间', value: log.finished_at_iso },
  ];
  if (log.error) rows.push({ label: '错误', value: log.error });
  return rows;
}

export function arayaRunEmptyText(status: ArayaStatus | null | undefined): string {
  const error = (status || {}).last_error;
  return error
    ? `上次运行异常：${String(error)}`
    : '还没有运行记录。等空闲自动触发，或拖动「手动触发」里的滑块跑一次。';
}

export function arayaTraceTitle(live: boolean): string {
  return live ? '本次运行轨迹' : '上次运行轨迹';
}

/** 旧 setMeta：`N 次工具调用 · 相对时间`，时间不可解析时省略后半段。 */
export function arayaTraceMeta(
  toolCount: number,
  startedAtIso: unknown,
  now: number = Date.now(),
): string {
  const when = arayaRelative(startedAtIso, now);
  return when && when !== '-'
    ? `${toolCount} 次工具调用 · ${when}`
    : `${toolCount} 次工具调用`;
}

export const ARAYA_TRACE_EMPTY =
  '还没有运行记录。触发一次后，这里会按顺序显示 Araya 的输出与每次工具调用。';
export const ARAYA_TRACE_WAITING = '等待 Araya 输出…';

/** 渲染层的扁平行（避免模板里对联合类型做窄化）。 */
export interface ArayaTimelineRow {
  kind: 'assistant' | 'tool' | 'result';
  text?: string;
  name?: string;
  time?: string;
  argsText?: string;
  resultText?: string;
  done?: boolean;
  label?: string;
}

export function arayaTimelineRows(messages: unknown): ArayaTimelineRow[] {
  return arayaTraceBlocks(messages).map((block): ArayaTimelineRow => {
    if (block.kind === 'assistant') return { kind: 'assistant', text: block.text };
    if (block.kind === 'tool') {
      return {
        kind: 'tool',
        name: block.name || '工具调用',
        time: arayaToolTime(block),
        argsText: arayaText(block.args),
        resultText: arayaText(block.result),
        done: block.done,
      };
    }
    return {
      kind: 'result',
      label: `${block.name || '工具'} 结果`,
      resultText: arayaText(block.result),
    };
  });
}

export function arayaToolCount(rows: ArayaTimelineRow[]): number {
  return rows.filter((row) => row.kind === 'tool').length;
}
