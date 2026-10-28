import { describe, expect, it } from 'vitest';
import {
  buildCardImportBody,
  CARD_IMPORT_NAME_HELP,
  CARD_IMPORT_NAME_INVALID,
  CARD_IMPORT_TIMEOUT_MS,
  cardImportBanner,
  cardImportNameState,
  cardSummaryRows,
} from './cardImport';

describe('角色卡目录名校验', () => {
  it('接受字母、数字、下划线、横线和点', () => {
    for (const name of ['Faust', 'faust_2', 'my-agent.v3', 'A1_b-c.d']) {
      expect(cardImportNameState(name).ok).toBe(true);
    }
  });

  it('拒绝空值、空格、中文与路径字符', () => {
    for (const name of ['', '   ', '有 空格', '角色', 'a/b', 'a\\b', '../x', 'a:b', 'a*b']) {
      const state = cardImportNameState(name);
      expect(state.ok).toBe(false);
      expect(state.conflict).toBe(false);
      expect(state.message).toBe(CARD_IMPORT_NAME_INVALID);
    }
  });

  it('按旧实现 trim 后再校验', () => {
    expect(cardImportNameState('  faust  ').ok).toBe(true);
    expect(buildCardImportBody('/tmp/card.png', '  faust  ')).toEqual({
      card_path: '/tmp/card.png',
      agent_name: 'faust',
    });
  });

  it('目录名帮助文案与旧弹窗一致', () => {
    expect(CARD_IMPORT_NAME_HELP).toBe(
      '只能包含字母、数字、下划线、横线和点；创建为 ~/.faustbot/agents/<目录名>，AGENT.md 恒为 faust 模板。',
    );
    expect(CARD_IMPORT_TIMEOUT_MS).toBe(600_000);
  });
});

describe('角色卡覆盖选项 / 同名冲突', () => {
  it('已存在的目录名被拦下（后端 import-card 不支持覆盖）', () => {
    const state = cardImportNameState('faust', ['Faust', 'araya']);
    expect(state.ok).toBe(false);
    expect(state.conflict).toBe(true);
    expect(state.message).toBe('Agent 已存在：faust，请换一个目录名。');
  });

  it('大小写不同也视为同名（Windows 目录不区分大小写）', () => {
    expect(cardImportNameState('FAUST', ['faust']).conflict).toBe(true);
  });

  it('没有同名时允许导入', () => {
    expect(cardImportNameState('new_role', ['faust', 'araya'])).toEqual({
      ok: true,
      message: '',
      conflict: false,
    });
  });
});

describe('角色卡预览摘要', () => {
  const preview = {
    name: '爱丽丝',
    spec: 'chara_card_v2',
    creator: 'tester',
    character_version: '1.2',
    tags: ['奇幻', '少女'],
    lorebook: { book: '世界设定', total: 5, enabled: 3 },
    has_avatar: true,
    has_system_prompt: true,
    has_post_history_instructions: false,
    source_format: 'png',
    source_path: 'C:/cards/alice.png',
  };

  it('按旧顺序生成 9 行并格式化世界书/立绘/附加约束', () => {
    const rows = cardSummaryRows(preview);
    expect(rows.map((row) => row.label)).toEqual([
      '卡片名称', '规格', '作者', '版本', '标签', '世界书', '立绘', '卡片附加约束', '来源',
    ]);
    expect(rows.map((row) => row.value)).toEqual([
      '爱丽丝',
      'chara_card_v2',
      'tester',
      '1.2',
      '奇幻、少女',
      '世界设定（启用 3 / 共 5）',
      '有 → 写入记忆附件',
      'system_prompt',
      'png · C:/cards/alice.png',
    ]);
  });

  it('空字段按旧实现显示 - / 无', () => {
    const rows = cardSummaryRows({});
    expect(rows[0].value).toBe('-');
    expect(rows[4].value).toBe('-');
    expect(rows[5].value).toBe('无');
    expect(rows[6].value).toBe('无');
    expect(rows[7].value).toBe('-');
    expect(rows[8].value).toBe(' · ');
    expect(cardSummaryRows(null).length).toBe(9);
  });

  it('无世界书条数时世界书显示无', () => {
    const rows = cardSummaryRows({ lorebook: { book: '空书', total: 0, enabled: 0 } });
    expect(rows[5].value).toBe('无');
  });
});

describe('角色卡导入结果文案', () => {
  it('成功时列出世界书数量与立绘', () => {
    expect(cardImportBanner({
      agent_name: 'alice',
      avatar: { path: '/images/alice.png' },
      lorebook: { enabled: 3, imported: 2, error_count: 0 },
    })).toEqual({
      type: 'success',
      text: '已从角色卡创建 Agent: alice（世界书 2/3，立绘已存入记忆）',
    });
  });

  it('没有世界书/立绘时只报名字', () => {
    expect(cardImportBanner({ agent_name: 'bob', avatar: null, lorebook: { enabled: 0 } })).toEqual({
      type: 'success',
      text: '已从角色卡创建 Agent: bob',
    });
  });

  it('世界书写入失败时用 error banner，并只带前 3 条明细', () => {
    const banner = cardImportBanner({
      agent_name: 'alice',
      lorebook: {
        enabled: 3,
        imported: 1,
        error_count: 4,
        errors: ['a: e1', 'b: e2', 'c: e3', 'd: e4'],
      },
    });
    expect(banner.type).toBe('error');
    expect(banner.text).toBe(
      '已创建 Agent alice，但世界书有 4 条写入失败：a: e1；b: e2；c: e3',
    );
  });
});
