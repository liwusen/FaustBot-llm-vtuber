import { describe, expect, it } from 'vitest';
import {
  ARAYA_TRACE_EMPTY,
  ARAYA_TRACE_WAITING,
  arayaDuration,
  arayaOverviewBadge,
  arayaOverviewHelp,
  arayaOverviewStats,
  arayaReasonLabel,
  arayaRelative,
  arayaRunEmptyText,
  arayaRunRows,
  arayaStatusLabel,
  arayaText,
  arayaTimelineRows,
  arayaToolCount,
  arayaToolTime,
  arayaTraceBlocks,
  arayaTraceMeta,
  arayaTraceTitle,
} from './format';

describe('Araya 触发原因与状态文案', () => {
  it('原因标签与旧实现一致，未知值回退原文', () => {
    expect(arayaReasonLabel('idle')).toBe('空闲自动触发');
    expect(arayaReasonLabel('manual')).toBe('手动触发');
    expect(arayaReasonLabel('manual_from_configer')).toBe('配置中心手动触发');
    expect(arayaReasonLabel('custom')).toBe('custom');
    expect(arayaReasonLabel('')).toBe('-');
    expect(arayaReasonLabel(null)).toBe('-');
  });

  it('状态标签与旧实现一致，未知值回退原文', () => {
    expect(arayaStatusLabel('ok')).toBe('成功');
    expect(arayaStatusLabel('running')).toBe('运行中');
    expect(arayaStatusLabel('error')).toBe('失败');
    expect(arayaStatusLabel('idle')).toBe('未运行');
    expect(arayaStatusLabel('weird')).toBe('weird');
    expect(arayaStatusLabel(undefined)).toBe('-');
  });
});

describe('Araya 耗时文案', () => {
  it('小于一分钟按秒显示，10 秒内保留一位小数', () => {
    expect(arayaDuration(0)).toBe('0.0 秒');
    expect(arayaDuration(5.44)).toBe('5.4 秒');
    expect(arayaDuration(9.99)).toBe('10.0 秒');
    expect(arayaDuration(10)).toBe('10 秒');
    expect(arayaDuration(59.6)).toBe('60 秒');
    expect(arayaDuration(null)).toBe('0.0 秒');
  });

  it('超过一分钟按分/秒显示，整分用分钟', () => {
    expect(arayaDuration(60)).toBe('1 分钟');
    expect(arayaDuration(61.4)).toBe('1 分 1 秒');
    expect(arayaDuration(90)).toBe('1 分 30 秒');
    expect(arayaDuration(3600)).toBe('60 分钟');
    expect(arayaDuration(3661)).toBe('61 分 1 秒');
  });

  it('非法/负数返回 -', () => {
    expect(arayaDuration(-1)).toBe('-');
    expect(arayaDuration('abc')).toBe('-');
    expect(arayaDuration(undefined)).toBe('-');
  });
});

describe('Araya 相对时间文案', () => {
  const now = Date.parse('2026-01-01T12:00:00Z');

  it('按刚刚/分钟/小时/天分档', () => {
    expect(arayaRelative(new Date(now - 30_000).toISOString(), now)).toBe('刚刚');
    expect(arayaRelative(new Date(now - 5 * 60_000).toISOString(), now)).toBe('5 分钟前');
    expect(arayaRelative(new Date(now - 2 * 3600_000).toISOString(), now)).toBe('2 小时前');
    expect(arayaRelative(new Date(now - 3 * 86400_000).toISOString(), now)).toBe('3 天前');
  });

  it('未来时间夹到刚刚，非法值返回 -', () => {
    expect(arayaRelative(new Date(now + 60_000).toISOString(), now)).toBe('刚刚');
    expect(arayaRelative('not-a-date', now)).toBe('-');
    expect(arayaRelative(null, now)).toBe('-');
  });

  it('轨迹 meta = 工具次数 + 相对时间，时间不可解析时省略后半段', () => {
    const nowIso = new Date(now - 5 * 60_000).toISOString();
    expect(arayaTraceMeta(2, nowIso, now)).toBe('2 次工具调用 · 5 分钟前');
    expect(arayaTraceMeta(0, 'bad', now)).toBe('0 次工具调用');
  });
});

describe('Araya 概览卡', () => {
  it('状态徽标按运行中 / 上次异常 / 停用 / 等待触发排序', () => {
    expect(arayaOverviewBadge({ enabled: false, last_error: 'x' }, true))
      .toEqual({ text: '运行中', className: 'badge badge-run' });
    expect(arayaOverviewBadge({ last_error: 'x' }, false))
      .toEqual({ text: '上次运行异常', className: 'badge badge-dirty' });
    expect(arayaOverviewBadge({ enabled: false }, false))
      .toEqual({ text: '自动触发已停用', className: 'badge badge-muted' });
    expect(arayaOverviewBadge({}, false))
      .toEqual({ text: '等待触发', className: 'badge badge-valid' });
    expect(arayaOverviewBadge(null, false).text).toBe('等待触发');
  });

  it('四格统计与旧实现数值一致', () => {
    const cells = arayaOverviewStats(
      { target_agent: 'faust', idle_minutes: 30, idle_seconds: 600 },
      { finished_at_iso: new Date().toISOString() },
    );
    expect(cells.map((cell) => cell.label)).toEqual([
      '目标 Agent',
      '主 Agent 空闲（阈值 30 分钟）',
      '距下次自动触发',
      '上次运行',
    ]);
    expect(cells[0].value).toBe('faust');
    expect(cells[1].value).toBe('10 分钟');
    expect(cells[2].value).toBe('20 分钟');
    expect(cells[3].value).toBe('刚刚');
  });

  it('停用时距下次触发显示已停用，无记录显示无记录', () => {
    const cells = arayaOverviewStats({ enabled: false, idle_minutes: 0 }, null);
    expect(cells[0].value).toBe('-');
    expect(cells[1].label).toBe('主 Agent 空闲（阈值 - 分钟）');
    expect(cells[2].value).toBe('已停用');
    expect(cells[3].value).toBe('无记录');
  });

  it('概览脚注与空态文案', () => {
    expect(arayaOverviewHelp({ last_main_activity_at: 'A', updated_at: 'B' }))
      .toBe('主 Agent 最近活跃 A · 状态更新 B');
    expect(arayaOverviewHelp(null)).toBe('主 Agent 最近活跃 - · 状态更新 -');
    expect(arayaRunEmptyText({ last_error: 'boom' })).toBe('上次运行异常：boom');
    expect(arayaRunEmptyText({}))
      .toBe('还没有运行记录。等空闲自动触发，或拖动「手动触发」里的滑块跑一次。');
    expect(arayaTraceTitle(true)).toBe('本次运行轨迹');
    expect(arayaTraceTitle(false)).toBe('上次运行轨迹');
    expect(ARAYA_TRACE_EMPTY).toBe('还没有运行记录。触发一次后，这里会按顺序显示 Araya 的输出与每次工具调用。');
    expect(ARAYA_TRACE_WAITING).toBe('等待 Araya 输出…');
  });
});

describe('Araya 最近一次运行行', () => {
  it('五行固定顺序 + 有错误时追加一行', () => {
    const rows = arayaRunRows({
      reason: 'manual_from_configer',
      status: 'ok',
      duration_seconds: 12,
      started_at_iso: 'S',
      finished_at_iso: 'F',
    });
    expect(rows.map((row) => row.label)).toEqual([
      '触发原因', '结果', '耗时', '开始时间', '结束时间',
    ]);
    expect(rows.map((row) => row.value)).toEqual([
      '配置中心手动触发', '成功', '12 秒', 'S', 'F',
    ]);
  });

  it('有错误时追加错误行', () => {
    const rows = arayaRunRows({ status: 'error', error: 'model down' });
    expect(rows.at(-1)).toEqual({ label: '错误', value: 'model down' });
    expect(arayaRunRows(null)[1].value).toBe('-');
  });
});

describe('Araya 轨迹分组', () => {
  it('合并连续 assistant、按 call_id 配对工具结果、忽略 user 消息', () => {
    const messages = [
      { role: 'user', content: '内部指令' },
      { role: 'assistant', content: 'A' },
      { role: 'assistant', content: 'B' },
      { role: 'tool', tool_name: 'read', call_id: 'call_1', args: { path: '/a' } },
      { role: 'tool_result', tool_name: 'read', call_id: 'call_1', result: 'ok', duration_seconds: 1.234 },
      { role: 'assistant', content: 'C' },
      { role: 'tool', tool_name: 'write', call_id: 'call_2', args: {} },
      { role: 'tool_result', tool_name: 'other', call_id: 'missing', result: 'lost' },
    ];
    const blocks = arayaTraceBlocks(messages);
    expect(blocks.map((block) => block.kind)).toEqual([
      'assistant', 'tool', 'assistant', 'tool', 'result',
    ]);
    expect(blocks[0]).toEqual({ kind: 'assistant', text: 'AB' });
    expect(blocks[1]).toMatchObject({ kind: 'tool', name: 'read', done: true, result: 'ok' });
    expect(blocks[2]).toEqual({ kind: 'assistant', text: 'C' });
    expect(blocks[3]).toMatchObject({ kind: 'tool', name: 'write', done: false });
    expect(blocks[4]).toEqual({ kind: 'result', name: 'other', result: 'lost' });
    expect(arayaToolCount(arayaTimelineRows(messages))).toBe(2);
  });

  it('只有结果没有调用的截断 trace 单独成行', () => {
    const rows = arayaTimelineRows([
      { role: 'tool_result', tool_name: 'read', call_id: 'x', result: 'r' },
    ]);
    expect(rows).toEqual([{ kind: 'result', label: 'read 结果', resultText: 'r' }]);
  });

  it('工具名缺省为工具调用，重复 result 不覆盖已有行', () => {
    const rows = arayaTimelineRows([
      { role: 'assistant', content: '' },
      { role: 'tool', call_id: 'c', args: { a: 1 } },
      { role: 'tool_result', call_id: 'c', result: 'r1', duration_seconds: null },
      { role: 'tool_result', call_id: 'c', result: 'r2' },
    ]);
    expect(rows[0]).toMatchObject({ kind: 'tool', name: '工具调用', time: '0.00s', done: true });
    expect(rows[0].argsText).toBe('{\n  "a": 1\n}');
    expect(rows[0].resultText).toBe('r1');
    expect(rows[1]).toEqual({ kind: 'result', label: '工具 结果', resultText: 'r2' });
  });

  it('非对象/空数组输入返回空列表', () => {
    expect(arayaTraceBlocks(null)).toEqual([]);
    expect(arayaTraceBlocks([])).toEqual([]);
    expect(arayaTimelineRows([null, 1, 'x'])).toEqual([]);
  });

  it('工具计时文案：运行中 / 秒数 / 完成', () => {
    const base = {
      kind: 'tool' as const,
      callId: 'c',
      name: 'n',
      args: {},
      result: '',
      duration: 0,
      done: true,
    };
    expect(arayaToolTime({ ...base, done: false })).toBe('运行中…');
    expect(arayaToolTime(base)).toBe('0.00s');
    expect(arayaToolTime({ ...base, duration: 2.5 })).toBe('2.50s');
    expect(arayaToolTime({ ...base, duration: undefined })).toBe('完成');
  });

  it('arayaText：字符串原样、对象缩进 JSON、空值空串', () => {
    expect(arayaText('x')).toBe('x');
    expect(arayaText({ a: 1 })).toBe('{\n  "a": 1\n}');
    expect(arayaText(null)).toBe('');
    expect(arayaText(undefined)).toBe('');
  });
});
