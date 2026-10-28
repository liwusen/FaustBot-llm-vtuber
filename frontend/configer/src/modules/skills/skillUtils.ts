// 技能模块的纯逻辑：Agent 名称回退、slug 规范化、目录路径与详情行。
// 对照旧 modules/skills.js + skill-utils.js。

import type { InfoRow } from '@/config/types';

export interface SkillRecord {
  slug?: string;
  version?: string;
  missing?: boolean;
  enabled?: boolean;
  [key: string]: unknown;
}

export interface SkillMeta {
  name?: string;
  title?: string;
  author?: string;
  description?: string;
  repo?: string;
  homepage?: string;
  entry?: string;
  license?: string;
  version?: string;
  tags?: string[];
  [key: string]: unknown;
}

export interface SkillDetail {
  slug?: string;
  enabled?: boolean;
  installed_at?: string;
  source?: string;
  path?: string;
  meta?: SkillMeta;
  files?: unknown;
  skill_md?: string;
  [key: string]: unknown;
}

/**
 * 旧实现的三处回退：输入框 trim 后优先，其次是已保存的 skillsAgent、
 * runtime.current_agent、config.public.AGENT_NAME（后三者原样取第一个非空）。
 */
export function resolveAgentName(
  input: unknown,
  stored: unknown,
  runtimeAgent: unknown,
  configAgent: unknown,
): string {
  const typed = String(input ?? '').trim();
  if (typed) return typed;
  return String(stored || runtimeAgent || configAgent || '');
}

/** 「刷新」按钮只把输入框写入 skillsAgent（trim 后），不做其它回退。 */
export function normalizeAgentInput(input: unknown): string {
  return String(input ?? '').trim();
}

export function normalizeSlug(raw: unknown): string {
  return String(raw ?? '').trim();
}

/** 旧「打开目录」：${root}/agents/${agent}/skill.d/${slug}，并去掉结尾斜杠。 */
export function skillDirPath(root: string, agentName: string, slug?: string): string {
  const basePath = `${root}/agents/${agentName}/skill.d/${slug || ''}`;
  return basePath.replace(/\/$/, '');
}

/** 旧技能列表的状态列：缺失 > 已启用 > 已停用。 */
export function skillStatusText(skill: SkillRecord | null | undefined): string {
  if (!skill) return '已停用';
  if (skill.missing) return '缺失';
  return skill.enabled ? '已启用' : '已停用';
}

/** 旧详情页「Skill 文件清单」：.md 标文档，其余标文件。 */
export function skillFileRows(files: unknown): unknown[][] {
  const list = Array.isArray(files) ? files : [];
  return list.map((file) => {
    const name = String(file ?? '');
    return [name, name.endsWith('.md') ? '文档' : '文件'];
  });
}

export function skillMetaOf(detail: SkillDetail | null | undefined): SkillMeta {
  const meta = detail && detail.meta;
  return meta && typeof meta === 'object' ? meta : {};
}

/** 旧 makeInfoCard("技能基本信息") 的 6 行。 */
export function skillBasicRows(detail: SkillDetail | null | undefined): InfoRow[] {
  const meta = skillMetaOf(detail);
  return [
    { label: 'Slug', value: detail?.slug },
    { label: '版本', value: meta.version || '-' },
    { label: '启用状态', value: detail?.enabled },
    { label: '安装时间', value: detail?.installed_at },
    { label: '来源', value: detail?.source },
    { label: '路径', value: detail?.path },
  ];
}

/** 旧 makeInfoCard("相关信息") 的 6 行。 */
export function skillMetaRows(detail: SkillDetail | null | undefined): InfoRow[] {
  const meta = skillMetaOf(detail);
  return [
    { label: '名称', value: meta.name || meta.title || '-' },
    { label: '作者', value: meta.author || '-' },
    { label: '描述', value: meta.description || '-' },
    { label: '仓库', value: meta.repo || meta.homepage || '-' },
    { label: '入口', value: meta.entry || '-' },
    { label: '许可证', value: meta.license || '-' },
  ];
}

export function skillTags(detail: SkillDetail | null | undefined): string[] {
  const tags = skillMetaOf(detail).tags;
  return Array.isArray(tags) ? tags.map((tag) => String(tag)) : [];
}
