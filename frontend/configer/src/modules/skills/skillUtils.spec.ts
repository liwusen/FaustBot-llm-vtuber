import { describe, expect, it } from 'vitest';
import {
  normalizeAgentInput,
  normalizeSlug,
  resolveAgentName,
  skillBasicRows,
  skillDirPath,
  skillFileRows,
  skillMetaRows,
  skillStatusText,
  skillTags,
  type SkillMeta,
} from './skillUtils';

describe('resolveAgentName', () => {
  it('prefers the trimmed input, then stored agent, runtime agent and config agent', () => {
    expect(resolveAgentName('  faust ', 'stored', 'runtime', 'cfg')).toBe('faust');
    expect(resolveAgentName('   ', 'stored', 'runtime', 'cfg')).toBe('stored');
    expect(resolveAgentName('', '', 'runtime', 'cfg')).toBe('runtime');
    expect(resolveAgentName('', '', '', 'cfg')).toBe('cfg');
    expect(resolveAgentName('', '', '', '')).toBe('');
  });

  it('treats null/undefined as empty', () => {
    expect(resolveAgentName(null, undefined, null, 'cfg')).toBe('cfg');
  });

  it('refresh only normalizes the input without falling back', () => {
    expect(normalizeAgentInput('  faust  ')).toBe('faust');
    expect(normalizeAgentInput('   ')).toBe('');
  });
});

describe('slug and skill directory', () => {
  it('trims slugs', () => {
    expect(normalizeSlug('  web-search \n')).toBe('web-search');
    expect(normalizeSlug(undefined)).toBe('');
  });

  it('builds the agent skill.d path', () => {
    expect(skillDirPath('C:/faust', 'faust', 'web-search'))
      .toBe('C:/faust/agents/faust/skill.d/web-search');
  });

  it('strips the trailing slash when no skill is selected', () => {
    expect(skillDirPath('C:/faust', 'faust')).toBe('C:/faust/agents/faust/skill.d');
    expect(skillDirPath('C:/faust', 'faust', '')).toBe('C:/faust/agents/faust/skill.d');
  });
});

describe('skill list and detail helpers', () => {
  it('maps missing/enabled/disabled to status text', () => {
    expect(skillStatusText({ slug: 'a', missing: true, enabled: true })).toBe('缺失');
    expect(skillStatusText({ slug: 'a', enabled: true })).toBe('已启用');
    expect(skillStatusText({ slug: 'a', enabled: false })).toBe('已停用');
    expect(skillStatusText(null)).toBe('已停用');
  });

  it('labels markdown files as 文档 and others as 文件', () => {
    expect(skillFileRows(['SKILL.md', 'scripts/run.py'])).toEqual([
      ['SKILL.md', '文档'],
      ['scripts/run.py', '文件'],
    ]);
    expect(skillFileRows(undefined)).toEqual([]);
  });

  it('builds the basic info rows with meta version fallback', () => {
    const rows = skillBasicRows({
      slug: 'web-search',
      enabled: true,
      installed_at: '2026-01-01T00:00:00Z',
      source: 'zip',
      path: 'C:/faust/agents/faust/skill.d/web-search',
      meta: { version: '1.2.3' },
    });
    expect(rows.map((row) => row.label)).toEqual(['Slug', '版本', '启用状态', '安装时间', '来源', '路径']);
    expect(rows[0].value).toBe('web-search');
    expect(rows[1].value).toBe('1.2.3');
    expect(rows[2].value).toBe(true);
  });

  it('builds the meta rows with name/repo fallbacks', () => {
    const rows = skillMetaRows({
      meta: { title: '搜索技能', author: 'me', homepage: 'https://example.com' },
    });
    expect(rows[0].value).toBe('搜索技能');
    expect(rows[1].value).toBe('me');
    expect(rows[2].value).toBe('-');
    expect(rows[3].value).toBe('https://example.com');
    expect(rows[4].value).toBe('-');
    expect(rows[5].value).toBe('-');
  });

  it('keeps tags as strings and tolerates malformed meta', () => {
    expect(skillTags({ meta: { tags: ['a', 1] } as unknown as SkillMeta })).toEqual(['a', '1']);
    expect(skillTags({ meta: { tags: 'not-array' } as unknown as SkillMeta })).toEqual([]);
    expect(skillTags(null)).toEqual([]);
  });
});
