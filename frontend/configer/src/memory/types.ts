// 记忆模块共享类型。字段命名与后端 `/faust/memory/*` 返回体、旧 memory 模块一致。

/** 知识库树节点（目录 / 文件 / 实体），旧代码按需读取字段，这里保留索引签名。 */
export interface KbNode {
  path?: string;
  name?: string;
  type?: string;
  id?: string;
  entity_type?: string;
  description?: string;
  children?: KbNode[];
  updated_at?: string;
  tags?: string[];
  chunk_count?: number;
  indexed?: boolean;
  declared_by?: string;
  score_patch?: number;
  content_type?: string;
  [key: string]: unknown;
}

export interface KbTask {
  status?: string;
  target?: string;
  [key: string]: unknown;
}

/** 选中态：目录 / 文件 / 实体三选一。 */
export type MemSelection =
  | { kind: 'dir'; path: string }
  | { kind: 'file'; path: string }
  | { kind: 'entity'; entityId: string };

export type MemView = 'list' | 'graph';

export type MemVizTab = 'cloud' | 'timeline' | 'treemap';

export type MemSortOrder = 'asc' | 'desc';

export interface MemSort {
  by: string;
  order: MemSortOrder;
}

/** 搜索条件（已生效）。 */
export interface MemFilter {
  query: string;
  tags: string[];
  tagLogic: string;
  dateFrom: string;
  dateTo: string;
  sortBy: string;
  sortOrder: string;
}

/** 列表行：目录行与文件行共用。 */
export interface MemRow {
  type: 'dir' | 'file';
  path: string;
  name: string;
  dir: string;
  description: string;
  tags: string[];
  updated_at: string;
  declared_by: string;
  score_patch: number;
  chunk_count: number;
  indexed: boolean;
  content_type: string;
  score?: number;
  childCount?: number;
}

export interface MemDetailState {
  content: string;
  meta: Record<string, unknown>;
  error?: string;
}

export interface MemEditorState {
  path: string;
  original: string;
  content: string;
  dirty: boolean;
  isNew: boolean;
  index: boolean;
  meta: Record<string, unknown>;
}

export interface MemClipboard {
  mode: 'copy' | 'cut';
  path: string;
  name: string;
}

export type MemFileMeta = Record<string, unknown>;

/** 图谱节点 / 边（与 graph-renderer.js 的运行时结构一致）。 */
export interface GraphNode {
  id: string;
  name?: string;
  x: number;
  y: number;
  vx: number;
  vy: number;
  fixed?: boolean;
  entity_type?: string;
  type?: string;
  description?: string;
  [key: string]: unknown;
}

export interface GraphEdge {
  source: string;
  target: string;
  type?: string;
  key?: string;
}
