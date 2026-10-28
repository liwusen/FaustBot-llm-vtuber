import { describe, expect, it } from 'vitest';
import { AGENT_FILES } from '@/config/constants';
import {
  agentDirPath,
  agentFileLanguage,
  agentFileValue,
  agentFileValues,
  agentName,
  agentStatusText,
  buildAgentFilesPayload,
} from './agentFiles';

describe('Agent 列表文案', () => {
  it('当前角色显示当前使用，其余备用', () => {
    expect(agentStatusText({ name: 'faust', is_current: true })).toBe('当前使用');
    expect(agentStatusText({ name: 'other' })).toBe('备用');
    expect(agentStatusText(null)).toBe('备用');
    expect(agentName({ name: 'faust' })).toBe('faust');
    expect(agentName(undefined)).toBe('');
  });
});

describe('Agent 文件取值', () => {
  it('对象取 content/readonly，字符串原样，缺失为空', () => {
    expect(agentFileValue({ 'ROLE.md': { content: 'role', readonly: true } }, 'ROLE.md'))
      .toEqual({ content: 'role', readonly: true });
    expect(agentFileValue({ 'TASK.md': 'raw' }, 'TASK.md'))
      .toEqual({ content: 'raw', readonly: false });
    expect(agentFileValue({}, 'AGENT.md')).toEqual({ content: '', readonly: false });
    expect(agentFileValue(null, 'AGENT.md')).toEqual({ content: '', readonly: false });
  });

  it('四个 Tab 全部初始化且顺序固定', () => {
    const values = agentFileValues({
      'AGENT.md': { content: 'a', readonly: true },
      'ROLE.md': { content: 'r' },
    });
    expect(Object.keys(values)).toEqual(AGENT_FILES);
    expect(values['AGENT.md']).toEqual({ content: 'a', readonly: true });
    expect(values['COREMEMORY.md']).toEqual({ content: '', readonly: false });
  });
});

describe('Agent 文件保存 payload', () => {
  it('总是提交四个固定文件，缺失补空串', () => {
    expect(buildAgentFilesPayload({ 'ROLE.md': 'r' })).toEqual({
      files: {
        'AGENT.md': '',
        'ROLE.md': 'r',
        'COREMEMORY.md': '',
        'TASK.md': '',
      },
    });
  });

  it('保留空字符串并忽略额外键', () => {
    expect(buildAgentFilesPayload({ 'AGENT.md': '', other: 'x' })).toEqual({
      files: {
        'AGENT.md': '',
        'ROLE.md': '',
        'COREMEMORY.md': '',
        'TASK.md': '',
      },
    });
    expect(buildAgentFilesPayload(null).files['TASK.md']).toBe('');
  });

  it('编辑器语言与旧 guessEditorLanguage 一致', () => {
    expect(agentFileLanguage('AGENT.md')).toBe('markdown');
    expect(agentFileLanguage('card.JSON')).toBe('json');
    expect(agentFileLanguage('')).toBe('markdown');
  });

  it('Agent 目录路径拼接与旧实现一致', () => {
    expect(agentDirPath('C:/faustbot', 'faust')).toBe('C:/faustbot/agents/faust');
    expect(agentDirPath('', 'faust')).toBe('/agents/faust');
  });
});
