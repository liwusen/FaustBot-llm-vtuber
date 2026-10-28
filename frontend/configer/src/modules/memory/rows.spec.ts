import { describe, expect, it } from 'vitest';
import {
  browseRows,
  buildRows,
  hasScoreColumn,
  linkedFileRows,
  scopedTree,
  searchResultRow,
  subtreeFiles,
} from '@/memory/rows';
import { defaultFilter } from '@/memory/search';
import type { KbNode, MemRow } from '@/memory/types';

const tree: KbNode = {
  path: '/',
  name: '/',
  type: 'dir',
  children: [
    {
      path: '/beta',
      name: 'beta',
      type: 'dir',
      description: '目录 B',
      children: [
        {
          path: '/beta/b.md',
          name: 'b.md',
          type: 'file',
          updated_at: '2026-02-01T00:00:00Z',
          tags: ['x'],
          chunk_count: 3,
          indexed: true,
          declared_by: 'agent',
          score_patch: 0.5,
          content_type: 'text/markdown',
        },
      ],
    },
    { path: '/alpha', name: 'alpha', type: 'dir', description: '', children: [] },
    {
      path: '/z.md',
      name: 'z.md',
      type: 'file',
      updated_at: '2026-01-01T00:00:00Z',
      tags: [],
      chunk_count: 0,
      indexed: false,
      declared_by: '',
      score_patch: 0,
      content_type: '',
    },
    { id: 'e1', name: '实体', type: 'entity' },
  ],
};

describe('browseRows', () => {
  it('目录在前按名称、文件在后按名称，跳过实体', () => {
    const rows = browseRows(tree);
    expect(rows.map((row) => `${row.type}:${row.name}`)).toEqual([
      'dir:alpha',
      'dir:beta',
      'file:z.md',
    ]);
    expect(rows[1].path).toBe('/beta');
    expect(rows[1].childCount).toBe(1);
    expect(rows[2].path).toBe('/z.md');
    expect(rows[2].dir).toBe('/');
  });
});

describe('buildRows', () => {
  it('有结果集时以结果集为准，并补齐 type/dir', () => {
    const results: MemRow[] = [
      {
        type: 'file',
        path: '/beta/b.md',
        name: 'b.md',
        dir: '/beta',
        description: '',
        tags: [],
        updated_at: '',
        declared_by: '',
        score_patch: 0,
        chunk_count: 0,
        indexed: false,
        content_type: '',
      },
    ];
    const rows = buildRows(results, tree, '/');
    expect(rows).toHaveLength(1);
    expect(rows[0].type).toBe('file');
    expect(rows[0].dir).toBe('/beta');
  });

  it('无结果集时浏览当前目录', () => {
    const rows = buildRows(null, tree, '/beta');
    expect(rows.map((row) => row.name)).toEqual(['b.md']);
  });
});

describe('searchResultRow', () => {
  it('服务端条目 + 树元数据合并（树里有 chunk/indexed/content_type）', () => {
    const node = (tree.children as KbNode[])[0].children![0];
    const row = searchResultRow(
      {
        path: '/beta/b.md',
        description: '服务端描述',
        tags: ['server'],
        updated_at: '2026-03-01T00:00:00Z',
        declared_by: 'server',
        score_patch: 0.9,
        score: 0.75,
      },
      node,
    );
    expect(row.path).toBe('/beta/b.md');
    expect(row.name).toBe('b.md');
    expect(row.dir).toBe('/beta');
    expect(row.description).toBe('服务端描述');
    expect(row.tags).toEqual(['server']);
    expect(row.updated_at).toBe('2026-03-01T00:00:00Z');
    expect(row.declared_by).toBe('server');
    expect(row.score_patch).toBe(0.9);
    expect(row.chunk_count).toBe(3);
    expect(row.indexed).toBe(true);
    expect(row.content_type).toBe('text/markdown');
    expect(row.score).toBe(0.75);
  });

  it('服务端缺字段时回退树元数据；无节点时名称取路径尾段', () => {
    const node = (tree.children as KbNode[])[0].children![0];
    const row = searchResultRow({ path: '/beta/b.md' }, node);
    expect(row.description).toBe('');
    expect(row.tags).toEqual(['x']);
    expect(row.updated_at).toBe('2026-02-01T00:00:00Z');

    const orphan = searchResultRow({ path: '/some/where/file.md' }, null);
    expect(orphan.name).toBe('file.md');
    expect(orphan.chunk_count).toBe(0);
    expect(orphan.indexed).toBe(false);
  });
});

describe('子树 / 关联文件 / 相关度列', () => {
  it('subtreeFiles 只收当前目录子树内的文件', () => {
    expect(subtreeFiles(tree, '/').map((row) => row.path)).toEqual(['/beta/b.md', '/z.md']);
    expect(subtreeFiles(tree, '/beta').map((row) => row.path)).toEqual(['/beta/b.md']);
  });

  it('linkedFileRows 用树元数据补全，缺失节点仍生成行', () => {
    const rows = linkedFileRows(['/beta/b.md', '/missing.md'], tree);
    expect(rows).toHaveLength(2);
    expect(rows[0].name).toBe('b.md');
    expect(rows[0].chunk_count).toBe(3);
    expect(rows[1].name).toBe('missing.md');
    expect(rows[1].tags).toEqual([]);
  });

  it('相关度列只在有关键词的结果集里出现', () => {
    expect(hasScoreColumn([], defaultFilter())).toBe(false);
    expect(hasScoreColumn(null, { ...defaultFilter(), query: 'x' })).toBe(false);
    expect(hasScoreColumn([], { ...defaultFilter(), query: 'x' })).toBe(true);
    expect(hasScoreColumn([], { ...defaultFilter(), tags: ['x'] })).toBe(false);
  });

  it('scopedTree 以当前目录为根克隆，找不到时回退整棵树', () => {
    const scoped = scopedTree(tree, '/beta');
    expect(scoped?.name).toBe('beta');
    expect(scoped?.children).toHaveLength(1);
    expect(scopedTree(tree, '/nope')).toBe(tree);
  });
});
