// 与旧 libs/configer/ui-cards.js 相同的格式化规则，逐字保留别名、状态映射与日期规则。

const LABEL_ALIASES: Record<string, string> = {
  Meta: '相关信息',
  'Meta 字段': '相关信息',
  Variant: '类型',
  Slug: '标识',
  homepage: '主页',
};

const STATUS_MAP: Record<string, string> = {
  true: '是',
  false: '否',
  unknown: '未知',
  running: '运行中',
  idle: '空闲',
  pending: '排队中',
  stopping: '停止中',
  stopped: '已停止',
  error: '异常',
  enabled: '已启用',
  disabled: '已禁用',
  on: '已开启',
  off: '已关闭',
};

export function humanizeLabel(label: unknown): string {
  const raw = String(label || '').trim();
  if (!raw) return '-';
  if (LABEL_ALIASES[raw]) return LABEL_ALIASES[raw];
  return raw;
}

export function formatScalar(value: unknown, label?: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'boolean') return value ? '是' : '否';
  const key = String(label || '').trim().toLowerCase();
  const text = String(value);
  if (key.includes('时间') || key.includes('updated') || key.includes('installed')) {
    const date = new Date(text);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString();
  }
  if (key.includes('状态') || key.includes('status')) {
    const mapped = STATUS_MAP[text.toLowerCase()];
    if (mapped) return mapped;
  }
  if (text === 'true') return '是';
  if (text === 'false') return '否';
  if (text === 'unknown') return '未知';
  return text;
}

/** 旧 field-render.js 的 isMaskedSecret：后端用纯 * 表示“已设置”。 */
export function isMaskedSecret(value: unknown): boolean {
  return typeof value === 'string' && /^\*+$/.test(value);
}

/**
 * 旧 field-render.js 的 normalizeNumberInput：
 * 空串/null/undefined → null；可解析 → 数值；不可解析 → fallback。
 */
export function normalizeNumberInput(raw: unknown, fallback = 0): number | null {
  if (raw === '' || raw === null || raw === undefined) return null;
  const num = Number(raw);
  return Number.isFinite(num) ? num : fallback;
}
