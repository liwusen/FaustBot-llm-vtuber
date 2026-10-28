import { describe, expect, it } from 'vitest';
import { formatScalar, humanizeLabel, isMaskedSecret, normalizeNumberInput } from '@/utils/format';

describe('humanizeLabel', () => {
  it('保留旧别名映射', () => {
    expect(humanizeLabel('Meta')).toBe('相关信息');
    expect(humanizeLabel('Meta 字段')).toBe('相关信息');
    expect(humanizeLabel('Variant')).toBe('类型');
    expect(humanizeLabel('Slug')).toBe('标识');
    expect(humanizeLabel('homepage')).toBe('主页');
  });

  it('空值回落为 -，其余仅 trim', () => {
    expect(humanizeLabel('')).toBe('-');
    expect(humanizeLabel(null)).toBe('-');
    expect(humanizeLabel(undefined)).toBe('-');
    expect(humanizeLabel('  名称  ')).toBe('名称');
    expect(humanizeLabel(12)).toBe('12');
  });
});

describe('formatScalar', () => {
  it('空值与布尔值', () => {
    expect(formatScalar(null)).toBe('-');
    expect(formatScalar(undefined)).toBe('-');
    expect(formatScalar('')).toBe('-');
    expect(formatScalar(true)).toBe('是');
    expect(formatScalar(false)).toBe('否');
  });

  it('字符串布尔 / unknown 与数字原样输出', () => {
    expect(formatScalar('true')).toBe('是');
    expect(formatScalar('false')).toBe('否');
    expect(formatScalar('unknown')).toBe('未知');
    expect(formatScalar(0)).toBe('0');
    expect(formatScalar('running')).toBe('running');
  });

  it('状态标签使用旧映射，且大小写不敏感', () => {
    expect(formatScalar('running', '状态')).toBe('运行中');
    expect(formatScalar('ERROR', 'status')).toBe('异常');
    expect(formatScalar('idle', '服务状态')).toBe('空闲');
    expect(formatScalar('enabled', 'Status')).toBe('已启用');
    expect(formatScalar('weird', '状态')).toBe('weird');
  });

  it('时间标签按本地时间格式化，非法日期原样输出', () => {
    const iso = '2026-01-02T03:04:05Z';
    expect(formatScalar(iso, '更新时间')).toBe(new Date(iso).toLocaleString());
    expect(formatScalar(iso, 'updated_at')).toBe(new Date(iso).toLocaleString());
    expect(formatScalar('installed text', 'installed')).toBe('installed text');
    expect(formatScalar('not-a-date', '时间')).toBe('not-a-date');
  });
});

describe('isMaskedSecret', () => {
  it('只把纯 * 视为已设置的掩码', () => {
    expect(isMaskedSecret('****')).toBe(true);
    expect(isMaskedSecret('*')).toBe(true);
    expect(isMaskedSecret('sk-123')).toBe(false);
    expect(isMaskedSecret('')).toBe(false);
    expect(isMaskedSecret(null)).toBe(false);
  });
});

describe('normalizeNumberInput', () => {
  it('空值返回 null，可解析返回数值，不可解析返回 fallback', () => {
    expect(normalizeNumberInput('')).toBeNull();
    expect(normalizeNumberInput(null)).toBeNull();
    expect(normalizeNumberInput(undefined)).toBeNull();
    expect(normalizeNumberInput('2.5')).toBe(2.5);
    expect(normalizeNumberInput(0)).toBe(0);
    expect(normalizeNumberInput('abc')).toBe(0);
    expect(normalizeNumberInput('abc', 7)).toBe(7);
  });
});
