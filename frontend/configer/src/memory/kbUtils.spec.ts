import { describe, expect, it } from 'vitest';
import {
  findKbNodeByPath,
  getKbChildren,
  kbParentPath,
  normalizeKbPath,
} from '@/memory/kbUtils';
import type { KbNode } from '@/memory/types';

const tree: KbNode = {
  path: '/',
  name: '/',
  type: 'dir',
  children: [
    {
      path: '/notes',
      name: 'notes',
      type: 'dir',
      children: [
        { path: '/notes/a.md', name: 'a.md', type: 'file' },
        { path: '/notes/b.md', name: 'b.md', type: 'file' },
      ],
    },
    { path: '/zeta', name: 'zeta', type: 'dir', children: [] },
    { path: '/readme.md', name: 'readme.md', type: 'file' },
    { id: 'ent-1', name: '实体一', type: 'entity', entity_type: 'person', description: '人物' },
  ],
};

describe('normalizeKbPath', () => {
  it('空值与根统一为 /', () => {
    expect(normalizeKbPath('')).toBe('/');
    expect(normalizeKbPath(null)).toBe('/');
    expect(normalizeKbPath(undefined)).toBe('/');
    expect(normalizeKbPath('/')).toBe('/');
  });

  it('补前导 /、压缩重复 /、去尾部 /', () => {
    expect(normalizeKbPath('a/b')).toBe('/a/b');
    expect(normalizeKbPath('//a///b//')).toBe('/a/b');
    expect(normalizeKbPath('/a/b/')).toBe('/a/b');
  });

  it('反斜杠转正斜杠并 trim', () => {
    expect(normalizeKbPath('\\a\\b\\')).toBe('/a/b');
    expect(normalizeKbPath('  /a  ')).toBe('/a');
  });
});

describe('kbParentPath', () => {
  it('逐级取父目录', () => {
    expect(kbParentPath('/a/b/c')).toBe('/a/b');
    expect(kbParentPath('/a')).toBe('/');
    expect(kbParentPath('/')).toBe('/');
    expect(kbParentPath('a')).toBe('/');
    expect(kbParentPath('')).toBe('/');
  });
});

describe('findKbNodeByPath', () => {
  it('找到任意深度的节点（路径归一化后比较）', () => {
    expect(findKbNodeByPath(tree, '/notes/a.md')?.name).toBe('a.md');
    expect(findKbNodeByPath(tree, 'notes')?.name).toBe('notes');
    expect(findKbNodeByPath(tree, '\\notes\\b.md\\')?.name).toBe('b.md');
    expect(findKbNodeByPath(tree, '/')?.type).toBe('dir');
  });

  it('缺失路径/空树返回 null', () => {
    expect(findKbNodeByPath(tree, '/nope')).toBeNull();
    expect(findKbNodeByPath(null, '/')).toBeNull();
    expect(findKbNodeByPath(undefined, '/')).toBeNull();
  });
});

describe('getKbChildren', () => {
  it('目录 → 文件 → 实体排序，同级按名称', () => {
    const rows = getKbChildren(tree, '/');
    expect(rows.map((row) => `${row.type}:${row.name}`)).toEqual([
      'dir:notes',
      'dir:zeta',
      'file:readme.md',
      'entity:实体一',
    ]);
    expect(rows[0].path).toBe('/notes');
    expect(rows[3]).toMatchObject({
      id: 'ent-1',
      entity_type: 'person',
      description: '人物',
    });
  });

  it('文件与不存在的目录返回空数组', () => {
    expect(getKbChildren(tree, '/readme.md')).toEqual([]);
    expect(getKbChildren(tree, '/nothing')).toEqual([]);
    expect(getKbChildren(null, '/')).toEqual([]);
  });

  it('子项 path 归一化，缺 name 时退回 path', () => {
    const odd: KbNode = {
      path: '/',
      type: 'dir',
      children: [{ path: 'x//y.md', type: 'file' }],
    };
    const rows = getKbChildren(odd, '/');
    expect(rows).toHaveLength(1);
    expect(rows[0].path).toBe('/x/y.md');
    expect(rows[0].name).toBe('x//y.md');
  });
});
