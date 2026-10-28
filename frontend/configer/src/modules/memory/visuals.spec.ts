import { describe, expect, it } from 'vitest';
import {
  MEM_VIZ_EMPTY_DATA,
  MEM_VIZ_EMPTY_DATED,
  MEM_VIZ_EMPTY_TAGS,
  buildTimelineData,
  buildTreemapData,
  chartHasSeriesModel,
  cloudEmptyText,
  cloudEntries,
  cloudOption,
  cloudPickWord,
  collectTagFreq,
  dirOf,
  escapeHtml,
  timelineOption,
  timelinePickKey,
  treemapOption,
  treemapPickDir,
  utcDayKey,
  utcKeyToMs,
} from '@/memory/visuals';
import type { KbNode, MemRow } from '@/memory/types';

function file(partial: Partial<MemRow> = {}): MemRow {
  return {
    type: 'file',
    path: '/x/a.md',
    name: 'a.md',
    dir: '/x',
    description: '',
    tags: [],
    updated_at: '',
    declared_by: '',
    score_patch: 0,
    chunk_count: 0,
    indexed: false,
    content_type: '',
    ...partial,
  };
}

describe('cloud', () => {
  it('标签词频：同文件重复标签只计一次，次数降序、同次数按名称', () => {
    const entries = collectTagFreq([
      file({ tags: ['b', 'a', 'a'] }),
      file({ tags: ['a', 'c'] }),
      file({ tags: ['c', 'b'] }),
    ]);
    expect(entries).toEqual([
      { name: 'a', value: 2 },
      { name: 'b', value: 2 },
      { name: 'c', value: 2 },
    ]);
  });

  it('空标签/空白标签不计入', () => {
    expect(collectTagFreq([file({ tags: ['', '  ', 'x'] })])).toEqual([{ name: 'x', value: 1 }]);
  });

  it('词云最多 60 个词', () => {
    const tags = Array.from({ length: 70 }, (_, index) => `t${String(index).padStart(2, '0')}`);
    const entries = cloudEntries([file({ tags })]);
    expect(entries).toHaveLength(60);
    expect(entries[0].name).toBe('t00');
  });

  it('降级文案：无文件 → 暂无数据，有文件无标签 → 暂无标签数据', () => {
    expect(cloudEmptyText([], [])).toBe(MEM_VIZ_EMPTY_DATA);
    expect(cloudEmptyText([file()], [])).toBe(MEM_VIZ_EMPTY_TAGS);
    expect(cloudEmptyText([file()], [{ name: 'a', value: 1 }])).toBeNull();
  });

  it('option 结构与点击取词', () => {
    const option = cloudOption([{ name: 'a', value: 2 }]) as {
      series: Array<{ type: string; data: Array<{ name: string; value: number }> }>;
    };
    expect(option.series[0].type).toBe('wordCloud');
    expect(option.series[0].data[0]).toMatchObject({ name: 'a', value: 2 });
    expect(cloudPickWord({ name: ' 标签 ' })).toBe('标签');
    expect(cloudPickWord({ data: { name: 'x' } })).toBe('x');
    expect(cloudPickWord(null)).toBe('');
  });
});

describe('timeline', () => {
  const now = new Date('2026-09-10T12:00:00Z');

  it('UTC 日期键与解析', () => {
    expect(utcDayKey(now)).toBe('2026-09-10');
    expect(utcKeyToMs('2026-09-10')).toBe(Date.UTC(2026, 8, 10));
    expect(Number.isNaN(utcKeyToMs('bad'))).toBe(true);
  });

  it('无文件 / 无日期各自降级', () => {
    const empty = buildTimelineData([], now);
    expect(empty).toEqual({ ok: false, empty: MEM_VIZ_EMPTY_DATA });
    const undated = buildTimelineData([file({ updated_at: '' })], now);
    expect(undated).toEqual({ ok: false, empty: MEM_VIZ_EMPTY_DATED });
  });

  it('365 天日历桶 + 累计曲线', () => {
    const built = buildTimelineData(
      [
        file({ updated_at: '2026-09-10T03:00:00Z', chunk_count: 4 }),
        file({ updated_at: '2026-09-10T05:00:00Z', chunk_count: 1 }),
        file({ updated_at: '2025-01-01T00:00:00Z', chunk_count: 9 }),
        file({ updated_at: 'not-a-date', chunk_count: 2 }),
      ],
      now,
    );
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    const data = built.data;
    expect(data.startKey).toBe('2025-09-11');
    expect(data.endKey).toBe('2026-09-10');
    expect(data.heat).toHaveLength(365);
    expect(data.cumFiles).toHaveLength(365);
    expect(data.heat[364]).toEqual(['2026-09-10', 2]);
    expect(data.cumFiles[364]).toEqual(['2026-09-10', 2]);
    expect(data.cumChunks[364]).toEqual(['2026-09-10', 5]);
    expect(data.maxDaily).toBe(2);
    expect(timelineOption(data).series).toHaveLength(3);
  });

  it('点击取日期键', () => {
    expect(timelinePickKey({ seriesType: 'heatmap', value: ['2026-09-10', 2] })).toBe('2026-09-10');
    expect(timelinePickKey({ seriesType: 'line', value: [Date.UTC(2026, 8, 10), 3] })).toBe(
      '2026-09-10',
    );
    expect(timelinePickKey({ seriesType: 'heatmap', value: ['bad', 0] })).toBe('');
  });
});

describe('treemap', () => {
  const tree: KbNode = {
    path: '/',
    name: '/',
    type: 'dir',
    children: [
      {
        path: '/docs',
        name: 'docs',
        type: 'dir',
        children: [{ path: '/docs/a.md', name: 'a.md', type: 'file', chunk_count: 3 }],
      },
      { id: 'e', name: '实体', type: 'entity' },
    ],
  };

  it('根目录不画自己，顶层是子项；文件权重至少 1', () => {
    const built = buildTreemapData(tree);
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    expect(built.data.degraded).toBe(false);
    expect(built.data.datums).toHaveLength(1);
    expect(built.data.datums[0].name).toBe('docs');
    const child = built.data.datums[0].children![0];
    expect(child.value).toBe(3);
    expect(child._memPath).toBe('/docs/a.md');
    expect(child._memIsDir).toBe(false);
  });

  it('空目录/只有实体 → 暂无数据', () => {
    const emptyDir = buildTreemapData({ path: '/e', name: 'e', type: 'dir', children: [] });
    expect(emptyDir).toEqual({ ok: false, empty: MEM_VIZ_EMPTY_DATA });
    expect(buildTreemapData(null)).toEqual({ ok: false, empty: MEM_VIZ_EMPTY_DATA });
  });

  it('option 深度与点击目录映射', () => {
    const built = buildTreemapData(tree);
    if (!built.ok) throw new Error('expected treemap data');
    const option = treemapOption(built.data.datums, built.data.degraded) as {
      series: Array<{ type: string; maxDepth: number; leafDepth: number }>;
    };
    expect(option.series[0].type).toBe('treemap');
    expect(option.series[0].maxDepth).toBe(3);
    expect(option.series[0].leafDepth).toBe(2);

    expect(treemapPickDir({ data: { _memPath: '/docs/a.md', _memIsDir: false } })).toBe('/docs');
    expect(treemapPickDir({ data: { _memPath: '/docs', _memIsDir: true } })).toBe('/docs');
    expect(treemapPickDir({ data: {} })).toBe('');
    expect(dirOf('/a/b.md')).toBe('/a');
    expect(dirOf('/a.md')).toBe('/');
  });
});

describe('通用', () => {
  it('escapeHtml 与 textContent→innerHTML 等价', () => {
    expect(escapeHtml('<a href="x">&</a>')).toBe('&lt;a href="x"&gt;&amp;&lt;/a&gt;');
    expect(escapeHtml(null)).toBe('');
  });

  it('chartHasSeriesModel：只有真正建模的 series 才算可用', () => {
    expect(chartHasSeriesModel({ getModel: () => ({ getSeriesByType: () => [1] }) }, 'wordCloud')).toBe(
      true,
    );
    expect(chartHasSeriesModel({ getModel: () => ({ getSeriesByType: () => [] }) }, 'wordCloud')).toBe(
      false,
    );
    expect(chartHasSeriesModel({}, 'wordCloud')).toBe(false);
    expect(
      chartHasSeriesModel(
        {
          getModel: () => {
            throw new Error('boom');
          },
        },
        'wordCloud',
      ),
    ).toBe(false);
  });
});
