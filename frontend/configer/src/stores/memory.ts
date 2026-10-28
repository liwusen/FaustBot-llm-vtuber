// 记忆模块状态：三栏（树/列表/详情）+ 搜索/分页/列/树展开/编辑器/剪贴板/详情 token。
// 状态字段、默认值、localStorage 键名与旧 modules/memory/*.js 保持一致。

import { computed, ref } from 'vue';
import { defineStore } from 'pinia';
import { API } from '@/api/endpoints';
import { cfgApi, openFileDialog } from '@/api/ipc';
import { useUiStore } from '@/stores/ui';
import { findKbNodeByPath, kbParentPath, normalizeKbPath } from '@/memory/kbUtils';
import { computeDirty, defaultNewFilePath, defaultNewFolderPath, isValidEditorPath } from '@/memory/editor';
import {
  MEM_COLUMNS_KEY,
  MEM_TREE_KEY,
  MEM_VIEW_KEY,
  MEM_VIZ_TAB_KEY,
  memReadLocal,
  memReadNonEmptyArray,
  memWriteLocal,
} from '@/memory/persist';
import { breadcrumbSegments, currentDirOf, revealedAncestors, scopeOfDir } from '@/memory/paths';
import {
  MEM_SEARCH_DEFAULT,
  advancedCount as computeAdvancedCount,
  buildSearchPayload,
  defaultFilter,
  filterActive as computeFilterActive,
  isSearchDirty,
  sortFromPayload,
} from '@/memory/search';
import {
  MEM_LIST_PAGE_SIZE,
  buildRows,
  hasScoreColumn,
  linkedFileRows,
  pageSlice,
  scopedTree as buildScopedTree,
  searchResultRow,
  subtreeFiles,
  totalPagesOf,
} from '@/memory/rows';
import { sortRows } from '@/memory/sort';
import type {
  GraphEdge,
  KbNode,
  KbTask,
  MemClipboard,
  MemDetailState,
  MemEditorState,
  MemFilter,
  MemRow,
  MemSelection,
  MemSort,
  MemView,
  MemVizTab,
} from '@/memory/types';

export const MEM_VIEWS: ReadonlyArray<{ id: MemView; label: string }> = [
  { id: 'list', label: '列表' },
  { id: 'graph', label: '图谱' },
];

/** 可视化卡片三个宽切换按钮（顺序即显示顺序）。 */
export const MEM_VIZ_TABS: ReadonlyArray<{ id: MemVizTab; label: string }> = [
  { id: 'cloud', label: '词云' },
  { id: 'timeline', label: '时间线' },
  { id: 'treemap', label: 'Treemap' },
];

/** 可选列（名称列恒在）。 */
export const MEM_COLUMN_DEFS: ReadonlyArray<{ id: string; label: string }> = [
  { id: 'tags', label: '标签' },
  { id: 'updated', label: '更新' },
  { id: 'size', label: '规模' },
  { id: 'declared_by', label: '创建者' },
  { id: 'indexed', label: '索引' },
  { id: 'score_patch', label: '权重' },
  { id: 'content_type', label: '类型' },
];

const MEM_COLUMNS_DEFAULT = ['tags', 'updated', 'size'];

export interface EntityChild {
  id: string;
  name: string;
  entity_type?: string;
  type?: string;
  description?: string;
}

export interface EntityDetail {
  id?: string;
  name?: string;
  entity_type?: string;
  description?: string;
  relations_count?: number;
  created_at?: string;
  properties?: Record<string, unknown>;
  linked_files?: string[];
  [key: string]: unknown;
}

export interface GraphEntityItem {
  id: string;
  name?: string;
  entity_type?: string;
  type?: string;
  description?: string;
}

export interface TreeRow {
  node: KbNode;
  depth: number;
  isDir: boolean;
  path: string;
  name: string;
  count: number;
}

export type EditorGuard = (reason: string, proceed: () => void | Promise<void>) => boolean;

function errorMessage(err: unknown): string {
  const value = err as { message?: unknown } | null | undefined;
  return String((value && value.message) || err);
}

function readView(): MemView {
  const saved = memReadLocal<unknown>(MEM_VIEW_KEY, 'list');
  return saved === 'graph' ? 'graph' : 'list';
}

function readVizTab(): MemVizTab {
  const saved = memReadLocal<unknown>(MEM_VIZ_TAB_KEY, 'cloud');
  return saved === 'timeline' || saved === 'treemap' ? saved : 'cloud';
}

function readColumns(): string[] {
  const saved = memReadNonEmptyArray(MEM_COLUMNS_KEY, MEM_COLUMNS_DEFAULT);
  const known = new Set(MEM_COLUMN_DEFS.map((def) => def.id));
  const filtered = saved.filter((id) => known.has(id));
  return filtered.length ? filtered : [...MEM_COLUMNS_DEFAULT];
}

export const useMemoryStore = defineStore('configer-memory', () => {
  const ui = useUiStore();

  // ── 数据 ──
  const kbTree = ref<KbNode | null>(null);
  const kbTasks = ref<KbTask[]>([]);
  const treeVersion = ref(0);
  const loaded = ref(false);

  // ── 选中/详情 ──
  const selection = ref<MemSelection>({ kind: 'dir', path: '/' });
  const detail = ref<MemDetailState | null>(null);
  const detailSeq = ref(0);

  // ── 检索 ──
  const filter = ref<MemFilter>(defaultFilter());
  const searchDraft = ref<MemFilter>(defaultFilter());
  const searchResults = ref<MemRow[] | null>(null);
  const resultLabel = ref('');
  const searching = ref(false);
  const sort = ref<MemSort>({ by: 'updated_at', order: 'desc' });
  const page = ref(0);
  const searchAdvancedOpen = ref(false);
  let searchSeq = 0;

  // ── 视图/列/树 ──
  const view = ref<MemView>(readView());
  const vizTab = ref<MemVizTab>(readVizTab());
  const columns = ref<string[]>(readColumns());
  const expanded = ref<string[]>(memReadNonEmptyArray(MEM_TREE_KEY, ['/']));

  // ── 编辑器/剪贴板 ──
  const editor = ref<MemEditorState | null>(null);
  const clipboard = ref<MemClipboard | null>(null);

  // ── 可见性 / 守卫 ──
  const moduleVisible = ref(true);
  let editorGuardHook: EditorGuard | null = null;

  function setEditorGuard(fn: EditorGuard | null): void {
    editorGuardHook = fn;
  }

  /** 返回 true = 已拦截；false = 放行（必要时已关闭干净的编辑器）。 */
  function guardEditor(reason: string, proceed: () => void | Promise<void>): boolean {
    if (!editorGuardHook) return false;
    return editorGuardHook(reason, proceed);
  }

  // ── 派生 ──
  const currentDir = computed(() => currentDirOf(selection.value));
  const scope = computed(() => scopeOfDir(currentDir.value));
  const filterActive = computed(() => computeFilterActive(filter.value));
  const searchDirty = computed(() => isSearchDirty(searchDraft.value, filter.value));
  const advancedCount = computed(() => computeAdvancedCount(filter.value));
  const columnsUsed = computed(() => {
    const byId = new Map(MEM_COLUMN_DEFS.map((def) => [def.id, def]));
    const out: Array<{ id: string; label: string }> = [];
    for (const id of columns.value) {
      const def = byId.get(id);
      if (def) out.push(def);
    }
    return out;
  });
  const rows = computed(() => buildRows(searchResults.value, kbTree.value, currentDir.value));
  const sortedRows = computed(() => sortRows(rows.value, sort.value));
  const totalPages = computed(() => totalPagesOf(sortedRows.value.length, MEM_LIST_PAGE_SIZE));
  const pageInfo = computed(() => pageSlice(sortedRows.value, page.value, MEM_LIST_PAGE_SIZE));
  const currentPage = computed(() => pageInfo.value.page);
  const pageRows = computed(() => pageInfo.value.rows);
  const hasScore = computed(() => hasScoreColumn(searchResults.value, filter.value));
  const selectedPath = computed(() => {
    const sel = selection.value;
    return sel.kind === 'dir' || sel.kind === 'file' ? normalizeKbPath(sel.path) : '';
  });
  const crumbs = computed(() => breadcrumbSegments(selection.value));
  const revealed = computed(() => revealedAncestors(selection.value));
  const vizFiles = computed(() => subtreeFiles(kbTree.value, currentDir.value));
  const vizScopedTree = computed(() => buildScopedTree(kbTree.value, currentDir.value));

  function isExpanded(path: string): boolean {
    return expanded.value.includes(path) || revealed.value.has(path);
  }

  /** 树里可见的行（不含根行），与旧 memRenderTreeBody 的 walk 一致。 */
  const treeRows = computed<TreeRow[]>(() => {
    const out: TreeRow[] = [];
    const root = kbTree.value;
    if (!root) return out;
    if (!isExpanded('/')) return out;
    const walk = (node: KbNode, depth: number): void => {
      const isDir = node.type === 'dir';
      const path = normalizeKbPath(node.path);
      out.push({
        node,
        depth,
        isDir,
        path,
        name: String(node.name || path),
        count: isDir ? (node.children || []).filter((child) => child.type !== 'entity').length : 0,
      });
      if (isDir && isExpanded(path)) {
        for (const child of node.children || []) {
          if (child.type === 'entity') continue;
          walk(child, depth + 1);
        }
      }
    };
    for (const child of root.children || []) {
      if (child.type === 'entity') continue;
      walk(child, 1);
    }
    return out;
  });


  // ── 持久化 ──
  function persistExpanded(): void {
    memWriteLocal(MEM_TREE_KEY, expanded.value);
  }

  function toggleExpanded(path: string): void {
    const index = expanded.value.indexOf(path);
    if (index >= 0) expanded.value.splice(index, 1);
    else expanded.value.push(path);
    persistExpanded();
  }

  function setView(next: MemView, persist = true): void {
    view.value = next;
    if (persist) memWriteLocal(MEM_VIEW_KEY, next);
  }

  function setVizTab(next: MemVizTab): void {
    vizTab.value = next;
    memWriteLocal(MEM_VIZ_TAB_KEY, next);
  }

  function setColumns(next: string[]): void {
    columns.value = next.slice();
    memWriteLocal(MEM_COLUMNS_KEY, columns.value);
  }

  function toggleColumn(id: string, checked: boolean): void {
    const chosen = new Set(columns.value);
    if (checked) chosen.add(id);
    else chosen.delete(id);
    // 顺序始终按定义表顺序，与旧列选择器一致
    const next = MEM_COLUMN_DEFS.filter((def) => chosen.has(def.id)).map((def) => def.id);
    setColumns(next);
  }

  function toggleSearchAdvanced(): void {
    searchAdvancedOpen.value = !searchAdvancedOpen.value;
  }

  // ── 数据加载 ──
  async function loadTree(): Promise<void> {
    const treeRes = await cfgApi<{ tree?: KbNode | null }>('GET', API.memoryTree, null, {
      include_metadata: true,
    });
    kbTree.value = treeRes.tree || null;
    treeVersion.value += 1;
    const taskRes = await cfgApi<{ items?: KbTask[] }>('GET', API.memoryTasks);
    kbTasks.value = taskRes.items || [];
    const sel = selection.value;
    if (sel.kind !== 'entity' && !findKbNodeByPath(kbTree.value, sel.path || '/')) {
      selection.value = { kind: 'dir', path: '/' };
      detail.value = null;
      detailSeq.value += 1;
    }
    loaded.value = true;
  }

  /** 文件增删后重算：有生效条件必须重跑服务端检索。 */
  async function reload(): Promise<void> {
    await loadTree();
    if (filterActive.value) await runSearch();
  }

  function findNode(path: unknown): KbNode | null {
    return findKbNodeByPath(kbTree.value, path);
  }

  // ── 检索 ──
  function syncDraft(): void {
    searchDraft.value = { ...MEM_SEARCH_DEFAULT, ...filter.value, tags: [...(filter.value.tags || [])] };
  }

  async function runSearch(): Promise<void> {
    if (!filterActive.value) {
      searchResults.value = null;
      resultLabel.value = '';
      searching.value = false;
      return;
    }
    const payload = buildSearchPayload(filter.value, scope.value);
    const seq = ++searchSeq;
    searching.value = true;
    resultLabel.value = '';
    // 取数顺序由搜索栏的「排序/顺序」决定，表头排序在这批结果内重排
    sort.value = sortFromPayload(payload);
    try {
      const data = await cfgApi<{ items?: KbNode[] }>('POST', API.memoryAdvancedSearch, payload);
      if (seq !== searchSeq) return;
      searchResults.value = (data.items || []).map((item) =>
        searchResultRow(item, findNode(normalizeKbPath(item.path))),
      );
    } catch (err) {
      if (seq !== searchSeq) return;
      console.error('[memory] 检索失败', err);
      searchResults.value = null;
      ui.showBanner('error', `检索失败: ${errorMessage(err)}`);
    } finally {
      if (seq === searchSeq) searching.value = false;
    }
  }

  /** 退出「结果集」态（检索结果 / 关联文件），回到当前目录的浏览列表。 */
  function exitResults(): void {
    filter.value = defaultFilter();
    searchResults.value = null;
    resultLabel.value = '';
    page.value = 0;
    syncDraft();
  }

  function clearFilter(): void {
    exitResults();
  }

  /** 非条件型结果集（关联文件）的「返回浏览」：只清结果集，不动条件与页码。 */
  function backToBrowse(): void {
    searchResults.value = null;
    resultLabel.value = '';
  }

  function toggleTag(tag: string): void {
    const current = Array.isArray(filter.value.tags) ? filter.value.tags : [];
    const next = current.includes(tag) ? current.filter((item) => item !== tag) : current.concat([tag]);
    void applyCondition({ tags: next }, { view: 'list' });
  }

  /** 可视化等入口：立即改条件并重新检索（可选切视图，不写 localStorage）。 */
  async function applyCondition(patch: Partial<MemFilter>, opts?: { view?: MemView }): Promise<void> {
    filter.value = { ...filter.value, ...patch };
    page.value = 0;
    if (opts && opts.view) view.value = opts.view;
    syncDraft();
    await runSearch();
  }

  /** 搜索栏「搜索」按钮：以表单当前内容整体替换条件。 */
  async function applySearch(values: Partial<MemFilter>): Promise<void> {
    filter.value = { ...filter.value, ...values };
    page.value = 0;
    syncDraft();
    await runSearch();
  }

  /** 排序/顺序：不重跑检索，只把当前结果集按新键重排（保留尚未生效的草稿条件）。 */
  function applySort(sortBy: string, sortOrder: string): void {
    filter.value = { ...filter.value, sortBy, sortOrder };
    sort.value = {
      by: sortBy === 'relevance' ? 'score' : 'updated_at',
      order: sortOrder === 'asc' ? 'asc' : 'desc',
    };
    searchDraft.value = { ...searchDraft.value, sortBy, sortOrder };
  }

  function setSort(next: MemSort): void {
    sort.value = next;
  }

  function setPage(next: number): void {
    page.value = next;
  }

  // ── 选中 ──
  function selectDir(path: unknown): void {
    const target = normalizeKbPath(path);
    if (guardEditor('切换目录', () => selectDir(target))) return;
    selection.value = { kind: 'dir', path: target };
    detail.value = null;
    detailSeq.value += 1;
    page.value = 0;
    // 目录是检索范围：已有生效条件时必须按新范围重算
    if (filterActive.value) void runSearch();
  }

  function selectFile(path: unknown, opts?: { keepResults?: boolean }): void {
    const target = normalizeKbPath(path);
    if (guardEditor('切换文件', () => selectFile(target, opts))) return;
    // 树导航文件 = 回到浏览态；列表内点结果行保留结果集
    if (!(opts && opts.keepResults)) exitResults();
    selection.value = { kind: 'file', path: target };
    detail.value = null;
    page.value = 0;
    const seq = ++detailSeq.value;
    void loadDetail(target, seq);
  }

  function selectEntity(entityId: unknown): void {
    const id = String(entityId || '');
    if (guardEditor('切换选中', () => selectEntity(id))) return;
    selection.value = { kind: 'entity', entityId: id };
    detail.value = null;
    detailSeq.value += 1;
  }

  async function loadDetail(path: string, seq: number): Promise<void> {
    try {
      const data = await cfgApi<{ content?: string; meta?: Record<string, unknown> }>(
        'GET',
        API.memoryGet,
        null,
        { path },
      );
      if (seq !== detailSeq.value || selection.value.kind !== 'file' || selection.value.path !== path) {
        return;
      }
      detail.value = { content: String(data.content || ''), meta: data.meta || {} };
    } catch (err) {
      console.error('[memory] 读取文件失败', path, err);
      if (seq !== detailSeq.value || selection.value.kind !== 'file' || selection.value.path !== path) {
        return;
      }
      detail.value = { content: '', meta: {}, error: errorMessage(err) };
    }
  }

  // ── 编辑器 ──
  function updateEditorContent(value: string): void {
    const current = editor.value;
    if (!current) return;
    computeDirty(current, value);
  }

  function toggleEditorIndex(value: boolean): void {
    if (editor.value) editor.value.index = value;
  }

  function closeEditor(): void {
    editor.value = null;
  }

  async function openEditor(path: unknown, opts?: { keepResults?: boolean }): Promise<void> {
    const target = normalizeKbPath(path);
    if (guardEditor('打开文件', () => openEditor(target, opts))) return;
    if (isImage(target)) {
      ui.showBanner('info', '图片文件不支持文本编辑，请在详情栏预览。');
      return;
    }
    try {
      const data = await cfgApi<{ content?: string; meta?: Record<string, unknown> }>(
        'GET',
        API.memoryGet,
        null,
        { path: target },
      );
      const content = String(data.content || '');
      if (!(opts && opts.keepResults)) exitResults();
      editor.value = {
        path: target,
        original: content,
        content,
        dirty: false,
        isNew: false,
        index: true,
        meta: data.meta || {},
      };
      selection.value = { kind: 'file', path: target };
      // 内容与元数据刚取过，直接喂给详情栏，避免再发一次请求
      detail.value = { content, meta: data.meta || {} };
      detailSeq.value += 1;
      view.value = 'list';
    } catch (err) {
      console.error('[memory] 打开编辑器失败', target, err);
      ui.showBanner('error', `打开失败: ${errorMessage(err)}`);
    }
  }

  async function saveEditor(): Promise<boolean> {
    const current = editor.value;
    if (!current) return false;
    const path = normalizeKbPath(current.path);
    if (!isValidEditorPath(path)) {
      ui.showBanner('error', '请输入有效的 KB 文件路径。');
      return false;
    }
    const content = current.content;
    try {
      const data = await cfgApi<{ meta?: Record<string, unknown> }>('POST', API.memorySave, {
        path,
        content,
        declared_by: 'config-center',
        index: Boolean(current.index),
      });
      const live = editor.value;
      if (live && live.path === path) {
        live.content = content;
        live.original = content;
        live.dirty = false;
        live.isNew = false;
        live.meta = { ...(live.meta || {}), ...(data.meta || {}) };
      }
      selection.value = { kind: 'file', path };
      await loadTree();
      ui.showBanner('success', `KB 已保存: ${path}`);
      return true;
    } catch (err) {
      console.error('[memory] 保存失败', path, err);
      ui.showBanner('error', `保存失败: ${errorMessage(err)}`);
      return false;
    }
  }

  function isImage(path: string): boolean {
    return /\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(String(path || ''));
  }

  // ── 文件操作 ──
  function newFile(dir: string, targetPath?: string): void {
    if (guardEditor('新建文件', () => newFile(dir, targetPath))) return;
    const target = normalizeKbPath(targetPath || defaultNewFilePath(dir));
    // 先只开编辑器：文件在首次「保存」时才真正落盘，退出未保存则什么都不产生
    editor.value = {
      path: target,
      original: '',
      content: '',
      dirty: false,
      isNew: true,
      index: true,
      meta: {},
    };
    selection.value = { kind: 'file', path: target };
    detail.value = null;
    detailSeq.value += 1;
    view.value = 'list';
  }

  async function newFolder(dir: string, targetPath?: string): Promise<void> {
    const target = normalizeKbPath(targetPath || defaultNewFolderPath(dir));
    try {
      await cfgApi('POST', API.memoryMkdir, { path: target });
      selectDir(target);
      await reload();
      ui.showBanner('success', `已创建目录: ${target}`);
    } catch (err) {
      console.error('[memory] 新建目录失败', target, err);
      ui.showBanner('error', `新建失败: ${errorMessage(err)}`);
    }
  }

  async function renameNode(node: { type?: string; path?: string; name?: string }): Promise<void> {
    const currentName = String(node.name || '');
    const newName = window.prompt('新名称:', currentName);
    if (!newName || newName === currentName) return;
    try {
      await cfgApi('POST', API.memoryRename, { path: node.path, new_name: newName });
      const newPath = normalizeKbPath(`${kbParentPath(node.path)}/${newName}`);
      if (node.type === 'dir') {
        selection.value = { kind: 'dir', path: newPath };
        detail.value = null;
        detailSeq.value += 1;
        const index = expanded.value.indexOf(String(node.path || ''));
        if (index >= 0) expanded.value[index] = newPath;
        persistExpanded();
      } else {
        selection.value = { kind: 'file', path: newPath };
        detail.value = null;
        detailSeq.value += 1;
      }
      await reload();
      if (selection.value.kind === 'file') {
        const seq = ++detailSeq.value;
        await loadDetail(newPath, seq);
      }
      ui.showBanner('success', `已重命名为 ${newName}`);
    } catch (err) {
      console.error('[memory] 重命名失败', node.path, err);
      ui.showBanner('error', `重命名失败: ${errorMessage(err)}`);
    }
  }

  function setClipboard(mode: 'copy' | 'cut', node: { path?: string; name?: string }): void {
    clipboard.value = { mode, path: String(node.path || ''), name: String(node.name || '') };
    ui.showBanner('info', `${mode === 'copy' ? '已复制: ' : '已剪切: '}${node.name}`);
  }

  async function paste(targetDir: string): Promise<void> {
    const clip = clipboard.value;
    if (!clip) {
      ui.showBanner('info', '剪贴板为空');
      return;
    }
    try {
      if (clip.mode === 'copy') {
        await cfgApi('POST', API.memoryCopy, {
          path: clip.path,
          dest: normalizeKbPath(`${targetDir}/${clip.name}`),
        });
      } else {
        await cfgApi('POST', API.memoryMove, { path: clip.path, dest_dir: targetDir });
        clipboard.value = null;
      }
      await reload();
      ui.showBanner('success', `已粘贴: ${clip.name}`);
    } catch (err) {
      console.error('[memory] 粘贴失败', clip.path, err);
      ui.showBanner('error', `粘贴失败: ${errorMessage(err)}`);
    }
  }

  async function deletePath(path: unknown): Promise<void> {
    const target = String(path || '');
    if (!target || target === '/') return;
    if (!window.confirm(`确定删除 ${target} ?`)) return;
    try {
      await cfgApi('DELETE', API.memoryDelete, null, { path: target });
      const sel = selection.value;
      if ((sel.kind === 'dir' || sel.kind === 'file') && sel.path === target) {
        selection.value = { kind: 'dir', path: kbParentPath(target) };
        detailSeq.value += 1;
      }
      detail.value = null;
      await reload();
      ui.showBanner('success', `已删除: ${target}`);
    } catch (err) {
      console.error('[memory] 删除失败', target, err);
      ui.showBanner('error', `删除失败: ${errorMessage(err)}`);
    }
  }

  async function importExternal(): Promise<void> {
    try {
      const filePath = await openFileDialog({ title: '选择外部文件' });
      if (!filePath) return;
      const kbPath = window.prompt('KB 路径（可留空自动）') || '';
      await cfgApi('POST', API.memoryDeclareUpdate, {
        file_path: filePath,
        kb_path: kbPath.trim() || null,
      });
      await reload();
      ui.showBanner('success', '文件已导入。');
    } catch (err) {
      console.error('[memory] 导入失败', err);
      ui.showBanner('error', `导入失败: ${errorMessage(err)}`);
    }
  }

  async function copyPath(path: unknown): Promise<void> {
    const target = String(path || '');
    try {
      await navigator.clipboard.writeText(target);
      ui.showBanner('success', `已复制: ${target}`);
    } catch (err) {
      console.error('[memory] 复制路径失败', err);
      ui.showBanner('error', `复制失败: ${errorMessage(err)}`);
    }
  }

  /** 详情栏「查看关联文件」：把关联路径集合送入列表（非条件型结果集）。 */
  function showLinkedFiles(paths: unknown[]): void {
    const list = linkedFileRows(paths, kbTree.value);
    filter.value = defaultFilter();
    searchResults.value = list;
    resultLabel.value = '关联文件';
    page.value = 0;
    view.value = 'list';
    syncDraft();
  }

  /** 保存标签：失败返回错误文案（详情栏内联展示 + 横幅）。 */
  async function writeTags(path: string, nextTags: string[]): Promise<string | null> {
    try {
      await cfgApi('POST', API.memoryTags, { path, tags: nextTags });
    } catch (err) {
      console.error('[memory] 保存标签失败', path, err);
      const text = `保存标签失败：${errorMessage(err)}`;
      ui.showBanner('error', text);
      return text;
    }
    if (detail.value && detail.value.meta) detail.value.meta.tags = nextTags.slice();
    const node = findNode(path);
    if (node) node.tags = nextTags.slice();
    void reload();
    return null;
  }

  // ── 详情异步数据 ──
  async function loadEntityChildren(path: string): Promise<EntityChild[]> {
    const resp = await cfgApi<{ items?: EntityChild[] }>('GET', API.memoryGraphEntityChildren, null, {
      path,
    });
    const items = resp && resp.items;
    return Array.isArray(items) ? items : [];
  }

  async function loadAttachment(path: string): Promise<unknown> {
    return cfgApi('GET', API.memoryAttachment, null, { path });
  }

  async function loadEntityDetail(entityId: string): Promise<EntityDetail | null> {
    const resp = await cfgApi<{ detail?: EntityDetail | null }>('GET', API.memoryGraphEntityDetail, null, {
      entity_id: entityId,
    });
    return (resp && resp.detail) || null;
  }

  // 实体图谱数据：与旧 memGraphLoad / memGraphBfs 完全一致的三段请求。
  async function loadEntityHead(entityId: string): Promise<EntityDetail> {
    try {
      const resp = await cfgApi<{ detail?: EntityDetail | null }>(
        'GET',
        API.memoryGraphEntityDetail,
        null,
        { entity_id: entityId },
      );
      return (resp && resp.detail) || {};
    } catch (err) {
      console.warn('[memory] 实体概要读取失败', entityId, err);
      return {};
    }
  }

  function graphEdgeKey(edge: GraphEdge): string {
    return edge.key || `${edge.source}->${edge.target}`;
  }

  async function expandGraph(
    entityId: string,
    depth: number,
  ): Promise<{ items: GraphEntityItem[]; edges: GraphEdge[] }> {
    const data = await cfgApi<{ items?: GraphEntityItem[]; edges?: GraphEdge[] }>(
      'GET',
      API.memoryGraphExpand,
      null,
      { entity_id: entityId, depth },
    );
    return { items: data.items || [], edges: data.edges || [] };
  }

  async function searchEntities(query: string, topK = 20): Promise<GraphEntityItem[]> {
    const data = await cfgApi<{ items?: GraphEntityItem[] }>('GET', API.memoryGraphSearch, null, {
      query,
      top_k: topK,
    });
    return data.items || [];
  }

  return {
    // 数据
    kbTree,
    kbTasks,
    treeVersion,
    loaded,
    // 选中/详情
    selection,
    detail,
    detailSeq,
    currentDir,
    scope,
    selectedPath,
    crumbs,
    revealed,
    // 检索
    filter,
    searchDraft,
    searchResults,
    resultLabel,
    searching,
    sort,
    page,
    searchAdvancedOpen,
    filterActive,
    searchDirty,
    advancedCount,
    rows,
    sortedRows,
    totalPages,
    currentPage,
    pageRows,
    hasScore,
    // 视图/列/树
    view,
    vizTab,
    columns,
    columnsUsed,
    expanded,
    treeRows,
    // 编辑器/剪贴板
    editor,
    clipboard,
    moduleVisible,
    vizFiles,
    vizScopedTree,
    // 持久化/视图动作
    toggleExpanded,
    isExpanded,
    setView,
    setVizTab,
    setColumns,
    toggleColumn,
    toggleSearchAdvanced,
    // 数据
    loadTree,
    reload,
    findNode,
    // 检索
    runSearch,
    exitResults,
    clearFilter,
    backToBrowse,
    toggleTag,
    applyCondition,
    applySearch,
    applySort,
    setSort,
    setPage,
    // 选中
    selectDir,
    selectFile,
    selectEntity,
    // 编辑器
    updateEditorContent,
    toggleEditorIndex,
    closeEditor,
    openEditor,
    saveEditor,
    // 导航/文件操作
    newFile,
    newFolder,
    renameNode,
    setClipboard,
    paste,
    deletePath,
    importExternal,
    copyPath,
    showLinkedFiles,
    writeTags,
    // 详情/图谱请求
    loadEntityChildren,
    loadAttachment,
    loadEntityDetail,
    loadEntityHead,
    expandGraph,
    searchEntities,
    graphEdgeKey,
    // 守卫
    setEditorGuard,
    guardEditor,
  };
});
