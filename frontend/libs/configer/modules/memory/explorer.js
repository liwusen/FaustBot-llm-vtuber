// Memory module shell: left tree, breadcrumbs, view switcher, visualization card, graph view.
// Views: list (modules/memory/list.js) / graph (here).
// Visualization card (below everything): cloud / timeline / treemap (modules/memory/visuals.js).
// Detail pane: modules/memory/detail.js.

var MEM_VIEWS = [
  { id: "list", label: "列表" },
  { id: "graph", label: "图谱" },
];

var MEM_SEARCH_LIMIT = 200;

// 可视化卡片三个宽切换按钮（顺序即显示顺序）
var MEM_VIZ_TABS = [
  { id: "cloud", label: "词云" },
  { id: "timeline", label: "时间线" },
  { id: "treemap", label: "Treemap" },
];

// 可选列（名称列恒在）
var MEM_COLUMN_DEFS = [
  { id: "tags", label: "标签" },
  { id: "updated", label: "更新" },
  { id: "size", label: "规模" },
  { id: "declared_by", label: "创建者" },
  { id: "indexed", label: "索引" },
  { id: "score_patch", label: "权重" },
  { id: "content_type", label: "类型" },
];

var MEM_TREE_KEY = "faustbot.memory.tree";
var MEM_VIEW_KEY = "faustbot.memory.view";
var MEM_COLUMNS_KEY = "faustbot.memory.columns";
var MEM_VIZ_TAB_KEY = "faustbot.memory.viztab";

var _memGraph = { canvas: null, host: null, statusEl: null, legendEl: null, depth: 3, resultsEl: null };
var _memVisibilityHooked = false;

// ── 持久化（localStorage 不可用时降级为内存态，仅提示不中断） ──

function memReadLocal(key, fallback) {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    return JSON.parse(raw);
  } catch (err) {
    console.warn("[memory] 本地状态读取失败", key, err);
    return fallback;
  }
}

function memWriteLocal(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch (err) {
    console.warn("[memory] 本地状态写入失败", key, err);
  }
}

function memEnsureState() {
  if (!Array.isArray(state.memExpanded) || !state.memExpanded.length) {
    const saved = memReadLocal(MEM_TREE_KEY, ["/"]);
    state.memExpanded = Array.isArray(saved) ? saved : ["/"];
  }
  if (!Array.isArray(state.memColumns) || !state.memColumns.length) {
    const saved = memReadLocal(MEM_COLUMNS_KEY, ["tags", "updated", "size"]);
    state.memColumns = Array.isArray(saved) ? saved : ["tags", "updated", "size"];
  }
  if (!MEM_VIEWS.some((v) => v.id === state.memView)) {
    state.memView = memReadLocal(MEM_VIEW_KEY, "list");
    if (!MEM_VIEWS.some((v) => v.id === state.memView)) state.memView = "list";
  }
  if (!state.memSel || !state.memSel.kind) state.memSel = { kind: "dir", path: "/" };
  if (!state.memFilter) {
    state.memFilter = { query: "", tags: [], tagLogic: "AND", dateFrom: "", dateTo: "", sortBy: "relevance", sortOrder: "desc" };
  }
  if (typeof state.memSearchDraft !== "object") state.memSearchDraft = null;
  if (typeof state.memResultLabel !== "string") state.memResultLabel = "";
  if (typeof state.memSearching !== "boolean") state.memSearching = false;
  if (!MEM_VIZ_TABS.some((t) => t.id === state.memVizTab)) {
    state.memVizTab = memReadLocal(MEM_VIZ_TAB_KEY, "cloud");
    if (!MEM_VIZ_TABS.some((t) => t.id === state.memVizTab)) state.memVizTab = "cloud";
  }
  if (state.memEditor !== null && typeof state.memEditor !== "object") state.memEditor = null;
  if (!state.memSort || !state.memSort.by) state.memSort = { by: "updated_at", order: "desc" };
  if (typeof state.memPage !== "number" || state.memPage < 0) state.memPage = 0;
  if (!Array.isArray(state._memTeardowns)) state._memTeardowns = [];
}

// ── 选择态 ──

function memCurrentDir() {
  const sel = state.memSel || {};
  if (sel.kind === "dir") return normalizeKbPath(sel.path || "/");
  if (sel.kind === "file") return kbParentPath(sel.path);
  return "/";
}

function memSelectDir(path) {
  if (memEditorGuard(() => memSelectDir(path), "切换目录")) return;
  state.memSel = { kind: "dir", path: normalizeKbPath(path) };
  state.memDetail = null;
  state.memPage = 0;
  // 目录是检索范围：已有生效条件时必须按新范围重算，否则用户会看到旧范围的结果
  if (memFilterActive()) memRunSearch();
  else memRenderShell();
}

function memSelectFile(path, opts) {
  if (memEditorGuard(() => memSelectFile(path, opts), "切换文件")) return;
  const norm = normalizeKbPath(path);
  // 树导航文件 = 回到浏览态：检索结果/关联文件立即让位给父目录列表（列表内点结果行保留结果集）
  if (!(opts && opts.keepResults)) memExitResults();
  state.memSel = { kind: "file", path: norm };
  state.memDetail = null;
  state.memPage = 0;
  memRenderShell();
  memLoadDetail(norm, state._memRenderToken);
}

function memSelectEntity(entityId) {
  if (memEditorGuard(() => memSelectEntity(entityId), "切换选中")) return;
  state.memSel = { kind: "entity", entityId: String(entityId || "") };
  state.memDetail = null;
  memRenderShell();
}

async function memLoadDetail(path, token) {
  try {
    const data = await cfgApi("GET", "/faust/memory/get", null, { path });
    if (state._memRenderToken !== token || !state.memSel || state.memSel.path !== path) return;
    state.memDetail = { content: String(data.content || ""), meta: data.meta || {} };
  } catch (err) {
    console.error("[memory] 读取文件失败", path, err);
    if (state._memRenderToken !== token || !state.memSel || state.memSel.path !== path) return;
    state.memDetail = { content: "", meta: {}, error: String((err && err.message) || err) };
  }
  memRenderDetailHost();
}

// ── 筛选/检索态 ──
//
// 模型（本轮定稿）：
//   * 「当前目录」是全局唯一的共享范围（memSel），三条路径同步：左树 / 面包屑 / 搜索页目录框。
//   * 条件（关键词/标签/日期/创建者）一律在当前目录子树内生效。
//   * 有条件 → 由服务端 /advanced-search 产出唯一结果集（含正文匹配），列表与搜索页共用。
//   * 无条件 → 列表显示当前目录「这一层」（浏览），可视化显示当前目录「整棵子树」。
//   * 搜索页是两段式：改完条件点「搜索」（memApplySearch）才生效；可视化点击则立即生效。

function memScopeOrNull() {
  const dir = memCurrentDir();
  return dir === "/" ? null : dir;
}

function memInScope(path) {
  const dir = memCurrentDir();
  if (dir === "/") return true;
  const norm = normalizeKbPath(path);
  return norm === dir || norm.startsWith(dir + "/");
}

// 退出「结果集」态（检索结果 / 关联文件），回到当前目录的浏览列表
function memExitResults() {
  state.memFilter = { query: "", tags: [], tagLogic: "AND", dateFrom: "", dateTo: "", sortBy: "relevance", sortOrder: "desc" };
  state.memSearchResults = null;
  state.memResultLabel = "";
  state.memPage = 0;
  if (typeof memSearchSyncDraft === "function") memSearchSyncDraft();
}

function memClearFilter() {
  memExitResults();
  memRenderShell();
}

function memToggleTag(tag) {
  const cur = Array.isArray(state.memFilter.tags) ? state.memFilter.tags : [];
  const next = cur.includes(tag) ? cur.filter((t) => t !== tag) : cur.concat([tag]);
  memApplyCondition({ tags: next }, { view: "list" });
}

function memFilterActive() {
  const f = state.memFilter || {};
  return Boolean(String(f.query || "").trim() || (f.tags && f.tags.length) || f.dateFrom || f.dateTo);
}

// 可视化等入口：立即改条件并重新检索（可选切视图）
// 编辑态下不拦截：条件变化只换结果集，编辑器 DOM 与内容原样保留
function memApplyCondition(patch, opts) {
  state.memFilter = Object.assign({}, state.memFilter || {}, patch || {});
  state.memPage = 0;
  if (opts && opts.view) state.memView = opts.view;
  if (typeof memSearchSyncDraft === "function") memSearchSyncDraft();
  memRunSearch();
}

// 搜索栏「搜索」按钮：以表单当前内容整体替换条件
function memApplySearch() {
  const values = typeof memSearchFormValues === "function" ? memSearchFormValues() : null;
  if (values) state.memFilter = Object.assign({}, state.memFilter || {}, values);
  state.memPage = 0;
  if (typeof memSearchSyncDraft === "function") memSearchSyncDraft();
  memRunSearch();
}

// 结果行：服务端条目 + 树元数据（chunk_count/indexed/content_type 只有树里有）
function memResultRow(item) {
  const path = normalizeKbPath(item.path);
  const node = memFindNode(path) || {};
  const tags = Array.isArray(item.tags) && item.tags.length ? item.tags : (Array.isArray(node.tags) ? node.tags : []);
  return {
    type: "file",
    path,
    name: String(node.name || String(item.path || "").split("/").pop() || path),
    dir: kbParentPath(path),
    description: String(item.description || node.description || ""),
    tags,
    updated_at: String(item.updated_at || node.updated_at || ""),
    declared_by: String(item.declared_by || node.declared_by || ""),
    score_patch: Number(item.score_patch || node.score_patch || 0),
    chunk_count: Number(node.chunk_count || 0),
    indexed: Boolean(node.indexed),
    content_type: String(node.content_type || ""),
    score: Number(item.score || 0),
  };
}

async function memRunSearch() {
  if (!memFilterActive()) {
    state.memSearchResults = null;
    state.memResultLabel = "";
    state.memSearching = false;
    memRenderShell();
    return;
  }
  const f = state.memFilter || {};
  const query = String(f.query || "").trim();
  const payload = {
    query: query || null,
    tags: f.tags && f.tags.length ? f.tags : null,
    scope: memScopeOrNull(),
    date_from: f.dateFrom || null,
    date_to: f.dateTo || null,
    // 无关键词时「相关度」没有意义（后端也不会排序）→ 明确按更新时间取数
    sort_by: query ? (f.sortBy || "relevance") : "updated_at",
    sort_order: query ? (f.sortOrder || "desc") : "desc",
    tag_logic: f.tagLogic || "AND",
    top_k: MEM_SEARCH_LIMIT,
  };
  state.memSearching = true;
  state.memResultLabel = "";
  // 取数顺序由搜索栏的「排序/顺序」决定，表头排序在这批结果内重排
  state.memSort = { by: payload.sort_by === "relevance" ? "score" : "updated_at", order: payload.sort_order };
  memRenderShell();
  try {
    const data = await cfgApi("POST", "/faust/memory/advanced-search", payload);
    state.memSearchResults = (data.items || []).map(memResultRow);
  } catch (err) {
    console.error("[memory] 检索失败", err);
    state.memSearchResults = null;
    showBanner("error", "检索失败: " + String((err && err.message) || err));
  } finally {
    state.memSearching = false;
  }
  memRenderShell();
}

// ── 数据 ──

function memFindNode(path) {
  return findKbNodeByPath(state.kbTree, path);
}

function memFlattenFiles() {
  const out = [];
  const walk = (node) => {
    if (!node) return;
    if (node.type === "file") {
      const path = normalizeKbPath(node.path || "/");
      out.push({
        path,
        name: String(node.name || path.split("/").pop() || path),
        dir: kbParentPath(path),
        description: String(node.description || ""),
        tags: Array.isArray(node.tags) ? node.tags : [],
        updated_at: String(node.updated_at || ""),
        chunk_count: Number(node.chunk_count || 0),
        indexed: Boolean(node.indexed),
        declared_by: String(node.declared_by || ""),
        score_patch: Number(node.score_patch || 0),
        content_type: String(node.content_type || ""),
        type: "file",
      });
      return;
    }
    for (const child of node.children || []) walk(child);
  };
  walk(state.kbTree);
  return out;
}

// 当前目录子树内的全部文件（可视化用）
function memSubtreeFiles() {
  return memFlattenFiles().filter((file) => memInScope(file.path));
}

// 可视化数据源：始终是当前目录整棵子树（忽略检索条件）
function memVizFiles() {
  return memSubtreeFiles();
}

// 列表行：有条件 → 共享结果集；否则 → 当前目录直接子项（浏览）
function memRows() {
  if (state.memSearchResults) {
    return state.memSearchResults.map((item) => Object.assign({ type: "file", dir: kbParentPath(item.path) }, item));
  }
  const node = memFindNode(memCurrentDir());
  const children = (node && node.children) || [];
  const dirs = [];
  const files = [];
  for (const child of children) {
    if (child.type === "entity") continue;
    if (child.type === "file") {
      files.push({
        path: normalizeKbPath(child.path),
        name: String(child.name || ""),
        dir: kbParentPath(child.path),
        description: String(child.description || ""),
        tags: Array.isArray(child.tags) ? child.tags : [],
        updated_at: String(child.updated_at || ""),
        chunk_count: Number(child.chunk_count || 0),
        indexed: Boolean(child.indexed),
        declared_by: String(child.declared_by || ""),
        score_patch: Number(child.score_patch || 0),
        content_type: String(child.content_type || ""),
        type: "file",
      });
    } else {
      dirs.push({ type: "dir", path: normalizeKbPath(child.path), name: String(child.name || ""), description: String(child.description || ""), childCount: (child.children || []).length });
    }
  }
  dirs.sort((a, b) => a.name.localeCompare(b.name));
  files.sort((a, b) => a.name.localeCompare(b.name));
  return dirs.concat(files);
}

function memSortRows(rows) {
  const sort = state.memSort || { by: "updated_at", order: "desc" };
  const dir = sort.order === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    let av;
    let bv;
    if (sort.by === "name") {
      av = String(a.name || "").toLowerCase();
      bv = String(b.name || "").toLowerCase();
      return av.localeCompare(bv) * dir;
    }
    if (sort.by === "size") {
      av = Number(a.chunk_count || 0);
      bv = Number(b.chunk_count || 0);
    } else if (sort.by === "score_patch") {
      av = Number(a.score_patch || 0);
      bv = Number(b.score_patch || 0);
    } else if (sort.by === "score") {
      av = Number(a.score || 0);
      bv = Number(b.score || 0);
    } else {
      av = String(a[sort.by] || "");
      bv = String(b[sort.by] || "");
    }
    if (av === bv) return String(a.name || "").localeCompare(String(b.name || ""));
    return av > bv ? dir : -dir;
  });
}

// ── 侧栏树 ──

function memRenderTree() {
  const host = document.getElementById("memTree");
  if (!host) return;
  host.innerHTML = "";
  const body = el("div", "mem-tree-body");
  body.id = "memTreeBody";
  host.append(body);
  memRenderTreeBody();
}

// 选中路径的祖先目录链（渲染时临时展开，不写入 localStorage）
function memRevealedAncestors() {
  const sel = state.memSel || {};
  const path = sel.kind === "dir" || sel.kind === "file" ? normalizeKbPath(sel.path || "") : "";
  const out = new Set();
  if (!path || path === "/") return out;
  const segs = path.split("/").filter(Boolean);
  let acc = "";
  for (let i = 0; i < segs.length - 1; i++) {
    acc += "/" + segs[i];
    out.add(acc);
  }
  return out;
}

function memRenderTreeBody() {
  const body = document.getElementById("memTreeBody");
  if (!body) return;
  body.innerHTML = "";
  if (state._memTreeChunked) {
    state._memTreeChunked.cancel();
    state._memTreeChunked = null;
  }

  const revealed = memRevealedAncestors();
  const isExpanded = (path) => state.memExpanded.includes(path) || revealed.has(path);

  const selectedPath = state.memSel && state.memSel.path ? normalizeKbPath(state.memSel.path) : "";

  const buildRow = (node, depth) => {
    const isDir = node.type === "dir";
    const path = normalizeKbPath(node.path);
    const row = el("div", "mem-tree-row");
    row.style.setProperty("--mem-depth", String(depth));
    if (selectedPath && path === selectedPath) row.classList.add("selected");
    row.dataset.path = path;

    const caret = el("span", "mem-tree-caret", isDir ? (isExpanded(path) ? "\u25BE" : "\u25B8") : "");
    const label = el("span", "mem-tree-label mono");
    const glyph = isDir ? "\u25AA" : (memIsImagePath(path) ? "\u25A4" : "\u25AA");
    label.textContent = glyph + " " + String(node.name || path);
    row.append(caret, label);

    const count = isDir ? (node.children || []).filter((c) => c.type !== "entity").length : 0;
    if (count) row.append(el("span", "mem-tree-count", String(count)));

    caret.addEventListener("click", (evt) => {
      evt.stopPropagation();
      if (!isDir) return;
      const idx = state.memExpanded.indexOf(path);
      if (idx >= 0) state.memExpanded.splice(idx, 1);
      else state.memExpanded.push(path);
      memWriteLocal(MEM_TREE_KEY, state.memExpanded);
      memRenderTree();
    });
    row.addEventListener("click", () => {
      if (isDir) {
        if (!state.memExpanded.includes(path)) {
          state.memExpanded.push(path);
          memWriteLocal(MEM_TREE_KEY, state.memExpanded);
        }
        memSelectDir(path);
      } else {
        memSelectFile(path);
      }
    });
    row.addEventListener("dblclick", () => {
      if (!isDir) memListRun("打开", () => memOpenEditor(path));
    });
    row.addEventListener("contextmenu", (evt) => {
      evt.preventDefault();
      memOpenContextMenu(evt.clientX, evt.clientY, { type: isDir ? "dir" : "file", path, name: String(node.name || "") });
    });
    return row;
  };

  const rows = [];
  const walk = (node, depth) => {
    const isDir = node.type === "dir";
    rows.push({ node, depth });
    if (isDir && isExpanded(normalizeKbPath(node.path))) {
      for (const child of node.children || []) {
        if (child.type === "entity") continue;
        walk(child, depth + 1);
      }
    }
  };
  const root = state.kbTree;
  if (root) {
    const rootPath = "/";
    const rootRow = el("div", "mem-tree-row");
    if (selectedPath === rootPath) rootRow.classList.add("selected");
    const rootCaret = el("span", "mem-tree-caret", isExpanded(rootPath) ? "\u25BE" : "\u25B8");
    rootRow.append(rootCaret, el("span", "mem-tree-label mono", "\u25AA /"));
    rootCaret.addEventListener("click", (evt) => {
      evt.stopPropagation();
      const idx = state.memExpanded.indexOf(rootPath);
      if (idx >= 0) state.memExpanded.splice(idx, 1);
      else state.memExpanded.push(rootPath);
      memWriteLocal(MEM_TREE_KEY, state.memExpanded);
      memRenderTreeBody();
    });
    rootRow.addEventListener("click", () => {
      if (!state.memExpanded.includes(rootPath)) {
        state.memExpanded.push(rootPath);
        memWriteLocal(MEM_TREE_KEY, state.memExpanded);
      }
      memSelectDir(rootPath);
    });
    rootRow.addEventListener("contextmenu", (evt) => {
      evt.preventDefault();
      memOpenContextMenu(evt.clientX, evt.clientY, { type: "dir", path: rootPath, name: "/" });
    });
    body.append(rootRow);
    if (isExpanded(rootPath)) {
      for (const child of root.children || []) {
        if (child.type === "entity") continue;
        walk(child, 1);
      }
    }
  }

  if (!rows.length) {
    body.append(el("div", "mem-empty", "记忆库为空"));
  }
  const rowsHost = el("div", "mem-tree-rows");
  body.append(rowsHost);
  state._memTreeChunked = appendChunked(rowsHost, rows, (row) => buildRow(row.node, row.depth), {
    // 选中行定位：仅在选中路径变化时滚动，避免每次重绘都把树拽回原位
    onDone: () => {
      if (!selectedPath || state._memTreeScrollKey === selectedPath) return;
      state._memTreeScrollKey = selectedPath;
      const target = rowsHost.querySelector('.mem-tree-row.selected');
      if (target && typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "nearest" });
    },
  });

  // 待处理任务
  if (state.kbTasks && state.kbTasks.length) {
    const taskBox = el("div", "mem-tree-tasks");
    taskBox.append(el("div", "mem-detail-title", "待处理任务 (" + state.kbTasks.length + ")"));
    for (const task of state.kbTasks) {
      const status = String(task.status || "waiting");
      const row = el("div", "mem-tree-task");
      row.append(el("span", "mem-task-status mem-task-" + status, status));
      row.append(el("span", "mono", String(task.target || "-")));
      taskBox.append(row);
    }
    body.append(taskBox);
  }
}

function memIsImagePath(path) {
  return /\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(String(path || ""));
}

// ── 面包屑 ──

function memBreadcrumbSegments() {
  const sel = state.memSel || { kind: "dir", path: "/" };
  const dir = sel.kind === "file" ? kbParentPath(sel.path) : memCurrentDir();
  const segs = [];
  if (dir && dir !== "/") {
    let acc = "";
    for (const part of dir.slice(1).split("/")) {
      acc += "/" + part;
      segs.push({ label: part, path: acc, kind: "dir" });
    }
  }
  if (sel.kind === "file") {
    segs.push({ label: sel.path.split("/").pop() || sel.path, path: sel.path, kind: "file" });
  }
  return segs;
}

function memRenderCrumbs() {
  const host = document.getElementById("memCrumbs");
  if (!host) return;
  host.innerHTML = "";
  const segs = memBreadcrumbSegments();

  const goDir = (path) => memSelectDir(path);
  const rootBtn = el("button", "mem-crumb", "记忆");
  rootBtn.type = "button";
  rootBtn.addEventListener("click", () => goDir("/"));
  host.append(rootBtn);

  const makeCrumb = (seg) => {
    const btn = el("button", "mem-crumb" + (seg.kind === "file" ? " mem-crumb-file" : ""), seg.label);
    btn.type = "button";
    btn.title = seg.path;
    btn.addEventListener("click", () => {
      if (seg.kind === "file") memSelectFile(seg.path);
      else goDir(seg.path);
    });
    return btn;
  };

  const appendSeg = (seg) => {
    host.append(el("span", "mem-crumb-sep", "/"));
    host.append(makeCrumb(seg));
  };

  if (segs.length > 4) {
    appendSeg(segs[0]);
    const more = el("button", "mem-crumb mem-crumb-more", "\u2026");
    more.type = "button";
    more.title = "选择同级目录";
    more.addEventListener("click", () => memOpenSiblingPicker(segs[1] ? kbParentPath(segs[1].path) : "/"));
    host.append(el("span", "mem-crumb-sep", "/"), more);
    for (const seg of segs.slice(-2)) appendSeg(seg);
  } else {
    for (const seg of segs) appendSeg(seg);
  }

  const target = segs.length ? segs[segs.length - 1].path : "/";
  host.title = target;
  // 复制路径改由右键菜单提供（面包屑/树/列表行统一）；host 常驻，只挂一次监听
  if (!host._memCopyHooked) {
    host._memCopyHooked = true;
    host.addEventListener("contextmenu", (evt) => {
      evt.preventDefault();
      memCopyPathMenu(evt.clientX, evt.clientY, host.title || "/");
    });
  }
}

// 复制路径右键菜单（面包屑 / 树节点 / 列表行共用）
function memCopyPathMenu(x, y, path) {
  const menu = el("div", "mem-menu");
  const item = el("button", "mem-menu-item", "复制路径");
  item.type = "button";
  item.addEventListener("click", () => {
    menu.remove();
    memCopyPath(path);
  });
  menu.append(item);
  menu.style.left = x + "px";
  menu.style.top = y + "px";
  document.body.append(menu);
  const close = (evt) => {
    if (menu.contains(evt.target)) return;
    menu.remove();
    document.removeEventListener("click", close);
  };
  window.setTimeout(() => document.addEventListener("click", close), 0);
}

async function memCopyPath(path) {
  try {
    await navigator.clipboard.writeText(path);
    showBanner("success", "已复制: " + path);
  } catch (err) {
    console.error("[memory] 复制路径失败", err);
    showBanner("error", "复制失败: " + String((err && err.message) || err));
  }
}

async function memOpenSiblingPicker(dirPath) {
  const parent = normalizeKbPath(dirPath);
  const node = memFindNode(parent === "" ? "/" : parent);
  const dirs = (node && node.children ? node.children : [])
    .filter((c) => c.type === "dir")
    .map((c) => normalizeKbPath(c.path));
  const box = el("div", "list-box");
  box.style.maxHeight = "320px";
  if (!dirs.length) {
    box.append(el("div", "mem-empty", "没有同级目录"));
  } else {
    for (const dir of dirs) {
      const row = el("div", "list-row clickable");
      row.append(el("span", "mono", dir));
      row.addEventListener("click", () => {
        closeModal();
        memSelectDir(dir);
      });
      box.append(row);
    }
  }
  const bar = el("div", "toolbar");
  bar.append(makeButton("关闭", closeModal));
  openModal("跳转目录", [box, bar]);
}

// ── 视图切换（列表 / 图谱） ──

function memRenderViewSwitcher() {
  const host = document.getElementById("memViews");
  if (!host) return;
  host.innerHTML = "";
  const seg = el("div", "seg");
  for (const view of MEM_VIEWS) {
    const btn = makeButton(view.label, () => {
      if (state.memView === view.id) return;
      if (memEditorGuard(() => memSetView(view.id), "切换视图")) return;
      memSetView(view.id);
    }, "seg-item" + (state.memView === view.id ? " active" : ""));
    btn.dataset.view = view.id;
    seg.append(btn);
  }
  host.append(seg);
}

function memSetView(viewId) {
  state.memView = viewId;
  memWriteLocal(MEM_VIEW_KEY, viewId);
  memRenderShell();
}

// ── 图谱视图 ──

function memRenderGraphHost(host) {
  _memGraph.host = host;
  host.innerHTML = "";
  _memGraph.canvas = null;

  const canvasWrap = el("div", "graph-canvas-wrap");
  canvasWrap.id = "memGraphCanvas";

  const tb = el("div", "graph-toolbar");
  const searchInput = el("input", "input");
  searchInput.placeholder = "搜索实体";
  searchInput.classList.add("mem-graph-search");
  const status = el("span", "card-help", "加载中\u2026");
  _memGraph.statusEl = status;
  const legend = el("div", "graph-legend");
  _memGraph.legendEl = legend;

  const depthLabel = el("span", "card-help", "深度: " + _memGraph.depth);
  const depthSlider = document.createElement("input");
  depthSlider.type = "range";
  depthSlider.min = "1";
  depthSlider.max = "3";
  depthSlider.value = String(_memGraph.depth);
  depthSlider.addEventListener("input", () => {
    _memGraph.depth = parseInt(depthSlider.value, 10) || 3;
    depthLabel.textContent = "深度: " + _memGraph.depth;
  });
  depthSlider.addEventListener("change", () => {
    memGraphLoad();
  });

  tb.append(
    searchInput,
    makeButton("搜索", () => memGraphSearch(searchInput.value), "btn btn-quiet"),
    status,
    document.createTextNode(" | "),
    makeButton("放大", () => { if (_memGraph.canvas) { _memGraph.canvas._viewScale *= 1.2; _memGraph.canvas.render(); } }, "btn btn-quiet"),
    makeButton("缩小", () => { if (_memGraph.canvas) { _memGraph.canvas._viewScale /= 1.2; _memGraph.canvas.render(); } }, "btn btn-quiet"),
    makeButton("适应", () => { if (_memGraph.canvas) _memGraph.canvas.fitToScreen(); }, "btn btn-quiet"),
    depthLabel,
    depthSlider
  );
  searchInput.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter") memGraphSearch(searchInput.value);
  });

  const results = el("div", "list-box");
  results.id = "memGraphResults";
  results.style.display = "none";
  _memGraph.resultsEl = results;

  host.append(tb, canvasWrap, legend, results);
  memGraphLoad();
}

function memGraphSetStatus(text) {
  if (_memGraph.statusEl) _memGraph.statusEl.textContent = text;
}

function memGraphEdgeKey(edge) {
  return edge.key || edge.source + "->" + edge.target;
}

function memGraphRenderLegend(nodes) {
  const legend = _memGraph.legendEl;
  if (!legend) return;
  legend.innerHTML = "";
  const types = [];
  for (const node of nodes || []) {
    const type = String(node.entity_type || node.type || "custom");
    if (!types.includes(type)) types.push(type);
  }
  if (!types.length) return;
  legend.append(el("span", "card-help", "图例:"));
  for (const type of types) {
    const item = el("span", "graph-legend-item");
    const dot = el("span", "graph-legend-dot");
    dot.style.background = GRAPH_COLORS[type] || "#95a5a6";
    item.append(dot, document.createTextNode(" " + type));
    legend.append(item);
  }
}

async function memGraphLoad() {
  const wrap = document.getElementById("memGraphCanvas");
  if (!wrap) return;
  const sel = state.memSel || { kind: "dir", path: "/" };
  memGraphSetStatus("请求数据中\u2026");
  let nodes = [];
  let edges = [];
  try {
    if (sel.kind === "entity" && sel.entityId) {
      const expanded = await cfgApi("GET", "/faust/memory/graph/expand", null, { entity_id: sel.entityId, depth: _memGraph.depth });
      const detail = await memGraphEntityHead(sel.entityId);
      nodes.push({ id: sel.entityId, name: detail.name || sel.entityId, entity_type: detail.entity_type || "custom", type: "entity", description: detail.description || "" });
      const seen = new Set([sel.entityId]);
      for (const item of expanded.items || []) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        nodes.push({ id: item.id, name: item.name, entity_type: item.entity_type || item.type, type: "entity", description: item.description });
      }
      for (const edge of expanded.edges || []) {
        edges.push({ source: edge.source, target: edge.target, type: edge.type, key: memGraphEdgeKey(edge) });
      }
    } else {
      const centerPath = sel.kind === "file" ? sel.path : memCurrentDir();
      const centerId = "path:" + centerPath;
      const children = await cfgApi("GET", "/faust/memory/graph/entity-children", null, { path: centerPath });
      const entityChildren = children.items || [];
      if (entityChildren.length) {
        const seen = new Set();
        for (const ent of entityChildren) {
          if (seen.has(ent.id)) continue;
          seen.add(ent.id);
          nodes.push({ id: ent.id, name: ent.name, entity_type: ent.entity_type, type: "entity", description: ent.description });
        }
      }
      const expanded = await cfgApi("GET", "/faust/memory/graph/expand", null, { entity_id: centerId, depth: _memGraph.depth });
      const seen = new Set([centerId]);
      const centerName = centerPath === "/" ? "/" : centerPath.split("/").pop() || centerPath;
      nodes.unshift({ id: centerId, name: centerName, entity_type: sel.kind === "file" ? "file" : "dir", type: "entity" });
      for (const item of expanded.items || []) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        nodes.push({ id: item.id, name: item.name, entity_type: item.entity_type || item.type, type: "entity", description: item.description });
      }
      for (const edge of expanded.edges || []) {
        edges.push({ source: edge.source, target: edge.target, type: edge.type, key: memGraphEdgeKey(edge) });
      }
    }
  } catch (err) {
    console.error("[memory] 图谱加载失败", err);
    memGraphSetStatus("加载失败: " + String((err && err.message) || err));
    return;
  }

  memGraphMount(nodes, edges);
  const truncated = nodes.length > 500 ? " (超 500 已截断)" : "";
  memGraphSetStatus("实体: " + Math.min(nodes.length, 500) + truncated + " | 关系: " + edges.length + " | 深度: " + _memGraph.depth);
}

async function memGraphEntityHead(entityId) {
  try {
    const resp = await cfgApi("GET", "/faust/memory/graph/entity-detail", null, { entity_id: entityId });
    return (resp && resp.detail) || {};
  } catch (err) {
    console.warn("[memory] 实体概要读取失败", entityId, err);
    return {};
  }
}

function memGraphMount(nodes, edges) {
  const wrap = document.getElementById("memGraphCanvas");
  if (!wrap) return;
  if (!_memGraph.canvas) {
    _memGraph.canvas = new GraphCanvas(wrap);
    _memGraph.canvas.onNodeClick((node) => {
      _memGraph.canvas._selectedNode = node;
      _memGraph.canvas.render();
      memSelectEntity(node.id);
      memGraphBfs(node.id, node.name, node.entity_type || "custom");
    });
    wrap.addEventListener("dblclick", () => {});
    _memGraph.canvas.canvas.addEventListener("dblclick", (evt) => {
      const rect = _memGraph.canvas.canvas.getBoundingClientRect();
      const world = _memGraph.canvas._screenToWorld(evt.clientX - rect.left, evt.clientY - rect.top);
      const hit = _memGraph.canvas._hitTest(world.x, world.y);
      if (hit) memGraphOpenLinkedFile(hit.id);
    });
  }
  _memGraph.canvas.clearExpanded();
  _memGraph.canvas.setData(nodes, edges);
  memGraphRenderLegend(nodes);
  if (state.memSel && state.memSel.kind === "entity" && state.memSel.entityId) {
    _memGraph.canvas.focusNode(state.memSel.entityId);
  }
  _memGraph.canvas.fitToScreen();
}

async function memGraphBfs(centerId, centerName, centerType) {
  const wrap = document.getElementById("memGraphCanvas");
  if (!wrap) return;
  memGraphSetStatus("BFS 展开 " + centerName + "\u2026");
  try {
    const expanded = await cfgApi("GET", "/faust/memory/graph/expand", null, { entity_id: centerId, depth: _memGraph.depth });
    const nodes = [{ id: centerId, name: centerName, entity_type: centerType || "entity", type: "entity" }];
    const seen = new Set([centerId]);
    for (const item of expanded.items || []) {
      if (seen.has(item.id)) continue;
      seen.add(item.id);
      nodes.push({ id: item.id, name: item.name, entity_type: item.entity_type || item.type, type: "entity", description: item.description });
    }
    const edges = [];
    const edgeKeys = new Set();
    for (const edge of expanded.edges || []) {
      const key = memGraphEdgeKey(edge);
      if (edgeKeys.has(key)) continue;
      edgeKeys.add(key);
      edges.push({ source: edge.source, target: edge.target, type: edge.type, key });
    }
    if (!_memGraph.canvas) return;
    _memGraph.canvas.clearExpanded();
    _memGraph.canvas.setData(nodes, edges);
    _memGraph.canvas.focusNode(centerId);
    memGraphRenderLegend(nodes);
    memGraphSetStatus("实体: " + Math.min(nodes.length, 500) + " | 关系: " + edges.length + " (深度: " + _memGraph.depth + ")");
  } catch (err) {
    console.error("[memory] BFS 展开失败", centerId, err);
    memGraphSetStatus("BFS 失败: " + String((err && err.message) || err));
  }
}

async function memGraphSearch(query) {
  const q = String(query || "").trim();
  if (!q) return;
  const box = _memGraph.resultsEl;
  if (!box) return;
  memGraphSetStatus("搜索中\u2026");
  box.style.display = "none";
  box.innerHTML = "";
  try {
    const data = await cfgApi("GET", "/faust/memory/graph/search", null, { query: q, top_k: 20 });
    const items = data.items || [];
    if (!items.length) {
      memGraphSetStatus("未找到匹配实体");
      return;
    }
    if (_memGraph.canvas) _memGraph.canvas.highlightIds(items.map((it) => it.id));
    box.style.display = "block";
    for (const item of items) {
      const row = el("div", "list-row clickable");
      const dot = el("span", "graph-legend-dot");
      dot.style.background = GRAPH_COLORS[item.entity_type] || "#95a5a6";
      const label = el("span", "mono", "[" + (item.entity_type || "entity") + "] " + item.name);
      row.append(dot, label);
      if (item.description) row.append(el("span", "card-help", String(item.description).slice(0, 80)));
      row.addEventListener("click", () => {
        memSelectEntity(item.id);
        memGraphBfs(item.id, item.name, item.entity_type || "entity");
      });
      box.append(row);
    }
    memGraphSetStatus("命中 " + items.length + " 个实体");
  } catch (err) {
    console.error("[memory] 实体搜索失败", err);
    memGraphSetStatus("搜索失败: " + String((err && err.message) || err));
  }
}

async function memGraphOpenLinkedFile(entityId) {
  const id = String(entityId || "");
  if (!id) return;
  if (id.startsWith("path:")) {
    const path = normalizeKbPath(id.slice("path:".length) || "/");
    if (path && path !== "/") {
      state.memView = "list";
      memSelectFile(path);
    }
    return;
  }
  const detail = await memGraphEntityHead(id);
  const files = Array.isArray(detail.linked_files) ? detail.linked_files : [];
  if (!files.length) {
    showBanner("info", "该实体没有关联文件。");
    return;
  }
  state.memView = "list";
  memSelectFile(files[0]);
}

// ── 可视化卡片（通栏，位于整页最下方） ──
//
// 两态：选中目录 = 展开（三个宽切换按钮 + 固定高度内容区，只挂载当前 tab）；
//       选中文件/实体 = 收起为一行灰字。
// 数据源 = 当前目录整棵子树，忽略检索条件（与列表的检索结果无关）。
// 重建缓存：目录/tab/可用性/树数据任一变化才重挂 echarts，避免每次重绘都重建图表。

var _memVizTreeRef = null;

function memRenderVizCard() {
  const host = document.getElementById("memVizCard");
  if (!host) return;
  const dirSelected = (state.memSel || {}).kind === "dir";
  const sig = [dirSelected ? "dir" : "other", state.memVizTab, memCurrentDir()].join("|");
  const treeChanged = state.kbTree !== _memVizTreeRef;
  if (!treeChanged && host._memVizSig === sig && host.childElementCount) return;
  _memVizTreeRef = state.kbTree;
  host._memVizSig = sig;

  if (typeof MemoryVisuals !== "undefined") MemoryVisuals.dispose(host);
  host.innerHTML = "";
  host.classList.toggle("mem-viz-off", !dirSelected);

  if (!dirSelected) {
    host.append(el("div", "mem-viz-hint", "可视化不可用，请选择文件夹"));
    return;
  }

  const head = el("div", "mem-viz-head");
  head.append(el("span", "mem-viz-title", "可视化"));
  const seg = el("div", "seg mem-viz-tabs");
  for (const tab of MEM_VIZ_TABS) {
    const btn = makeButton(tab.label, () => {
      if (state.memVizTab === tab.id) return;
      state.memVizTab = tab.id;
      memWriteLocal(MEM_VIZ_TAB_KEY, tab.id);
      memRenderVizCard();
    }, "seg-item" + (state.memVizTab === tab.id ? " active" : ""));
    btn.dataset.viz = tab.id;
    seg.append(btn);
  }
  head.append(seg);
  host.append(head);

  const body = el("div", "mem-viz-body");
  host.append(body);

  const kind = state.memVizTab;
  const files = memVizFiles();
  if (!files.length) {
    body.append(el("div", "mem-empty", "当前范围内没有可统计的文件"));
    return;
  }
  if (typeof MemoryVisuals === "undefined") {
    body.append(el("div", "mem-error", "可视化组件未加载（visuals.js 缺失）。"));
    return;
  }
  // 注意：visuals.js 读的是 opts.<kind>.onPick*，这里必须传整张表（早前按 kind 取值导致所有点击回调为 null）
  const opts = {
    cloud: { onPickTag: (tag) => memToggleTag(tag) },
    timeline: { onPickRange: (from, to) => memApplyCondition({ dateFrom: from, dateTo: to }, { view: "list" }) },
    // Treemap 点目录 = 换当前目录（与点左树同义）
    treemap: { onPickDir: (path) => memSelectDir(path) },
  };
  const data = kind === "treemap" ? { treemap: { tree: memScopedTree() } } : { [kind]: { files } };
  MemoryVisuals.mount(kind, body, data, opts);
}

// Treemap 只画当前目录这一层起（当前目录 = 它的根）
function memScopedTree() {
  const dir = memCurrentDir();
  const node = memFindNode(dir);
  if (node) {
    const clone = { path: node.path, name: node.name || dir, type: "dir", description: node.description || "", children: node.children || [] };
    return clone;
  }
  return state.kbTree;
}

// ── 渲染入口 ──

function memRenderShell() {
  memEnsureState();
  const crumbs = document.getElementById("memCrumbs");
  const switcher = document.getElementById("memViews");
  if (!crumbs || !switcher) return;
  memRenderCrumbs();
  memRenderViewSwitcher();
  memRenderTree();

  const hosts = { list: "memViewList", graph: "memViewGraph" };
  for (const [view, id] of Object.entries(hosts)) {
    const host = document.getElementById(id);
    if (!host) continue;
    const active = state.memView === view;
    if (!active) {
      // 切走即销毁：echarts 实例、图谱 rAF、canvas 一律释放，不留后台开销
      if (host.childElementCount) {
        if (typeof MemoryVisuals !== "undefined") MemoryVisuals.dispose(host);
        if (view === "graph" && _memGraph.canvas) {
          _memGraph.canvas.destroy();
          _memGraph.canvas = null;
        }
        host.innerHTML = "";
      }
      host.hidden = true;
      continue;
    }
    host.hidden = false;
    if (view === "list" && state.memEditor) {
      // 编辑态：主列的列表区让位给内联编辑器（活实例不重建，保住光标与撤销栈）
      memRenderEditor(host);
      continue;
    }
    host.innerHTML = "";
    if (view === "list") {
      if (typeof memRenderList === "function") memRenderList(host);
      else host.append(el("div", "mem-error", "列表组件未加载（list.js 缺失）。"));
    } else {
      memRenderGraphHost(host);
    }
  }
  memRenderDetailHost();
  memRenderVizCard();
}

function memRenderDetailHost() {
  const host = document.getElementById("memDetail");
  if (!host) return;
  host.innerHTML = "";
  if (typeof memRenderDetail === "function") memRenderDetail(host);
  else host.append(el("div", "mem-error", "详情组件未加载（detail.js 缺失）。"));
}

function memReload() {
  return ensureModuleData("memory").then(() => {
    // 有生效条件时结果集来自服务端，文件增删后必须重算，否则列表会显示已经不存在的文件
    if (memFilterActive()) return memRunSearch();
    memRenderShell();
    return null;
  });
}

function memRegisterTeardown(fn) {
  if (typeof fn !== "function") return;
  if (!Array.isArray(state._memTeardowns)) state._memTeardowns = [];
  state._memTeardowns.push(fn);
}

function memTeardownAll() {
  const list = Array.isArray(state._memTeardowns) ? state._memTeardowns : [];
  state._memTeardowns = [];
  for (const fn of list) {
    try {
      fn();
    } catch (err) {
      console.error("[memory] 释放资源失败", err);
    }
  }
}

function memHandleVisibility(moduleId) {
  const visible = moduleId === "memory";
  const canvas = _memGraph.canvas;
  if (!canvas) return;
  if (visible) canvas.resume();
  else canvas.pause();
}

// ── 右键菜单与文件操作 ──

function memOpenContextMenu(x, y, node) {
  const old = document.getElementById("memContextMenu");
  if (old) old.remove();
  const menu = el("div", "context-menu mem-context-menu");
  menu.id = "memContextMenu";
  menu.style.left = x + "px";
  menu.style.top = y + "px";

  const parentDir = node.type === "dir" ? node.path : kbParentPath(node.path);
  const items = [];
  if (node.type === "file") {
    items.push({ label: "编辑", action: () => memOpenEditor(node.path) });
    items.push({ label: "新建文件", action: () => memNewFile(parentDir) });
    items.push({ label: "新建文件夹", action: () => memNewFolder(parentDir) });
  } else {
    items.push({ label: "新建文件", action: () => memNewFile(node.path) });
    items.push({ label: "新建文件夹", action: () => memNewFolder(node.path) });
  }
  items.push(null);
  items.push({ label: "重命名", action: () => memRename(node) });
  if (node.type === "file") {
    items.push({ label: "复制", action: () => memClipboard("copy", node) });
    items.push({ label: "剪切", action: () => memClipboard("cut", node) });
  }
  items.push({ label: "粘贴", disabled: !state.memClipboard, action: () => memPaste(parentDir) });
  items.push(null);
  items.push({ label: "复制路径", action: () => memCopyPath(node.path) });
  items.push({ label: node.type === "file" ? "删除文件" : "删除目录", danger: true, action: () => memDelete(node.path) });

  for (const item of items) {
    if (!item) {
      menu.append(el("div", "mem-menu-sep"));
      continue;
    }
    const row = el("div", "context-menu-item" + (item.disabled ? " disabled" : "") + (item.danger ? " danger" : ""), item.label);
    if (!item.disabled) {
      row.addEventListener("click", async () => {
        menu.remove();
        await item.action();
      });
    }
    menu.append(row);
  }
  document.body.append(menu);
  const close = (evt) => {
    if (!menu.contains(evt.target)) {
      menu.remove();
      document.removeEventListener("click", close);
    }
  };
  document.addEventListener("click", close);
}

// ── 内联编辑器（取代模态编辑） ──
//
// 编辑态下主列的列表区整体让位给 CodeMirror：树 / 面包屑 / 详情栏 / 可视化卡片保持可见。
// 脏状态由「退出编辑 / 切换选中 / 切换视图 / 离开记忆页」四处守卫，统一走 memEditorGuard。

function memEditorDirty() {
  return Boolean(state.memEditor && state.memEditor.dirty);
}

// 返回 true = 已拦截（用户正在确认）；返回 false = 放行（必要时已关闭干净的编辑器）
function memEditorGuard(proceed, reason) {
  const ed = state.memEditor;
  if (!ed) return false;
  if (!ed.dirty) {
    memEditorClose();
    return false;
  }
  const box = el("div", "list-box");
  box.append(el("div", "", (reason || "继续操作") + " 前需要处理未保存的改动："));
  box.append(el("div", "card-help mono", String(ed.path || "")));
  const bar = el("div", "toolbar");
  const saveAndGo = async () => {
    const ok = await memEditorSave();
    if (!ok) return;
    closeModal();
    memEditorClose();
    memRenderShell();
    await proceed();
  };
  const discard = () => {
    closeModal();
    memEditorClose();
    memRenderShell();
    proceed();
  };
  bar.append(
    makeButton("保存并继续", saveAndGo, "btn btn-go"),
    makeButton("放弃修改", discard, "btn btn-danger"),
    makeButton("取消", closeModal, "btn btn-quiet")
  );
  openModal("未保存的改动", [box, bar]);
  return true;
}

function memEditorClose() {
  const ed = state.memEditor;
  if (ed && ed.view && typeof ed.view.destroy === "function") ed.view.destroy();
  state.memEditor = null;
}

async function memOpenEditor(path, opts) {
  if (memEditorGuard(() => memOpenEditor(path, opts), "打开文件")) return;
  const target = normalizeKbPath(path);
  if (memIsImagePath(target)) {
    showBanner("info", "图片文件不支持文本编辑，请在详情栏预览。");
    return;
  }
  try {
    const data = await cfgApi("GET", "/faust/memory/get", null, { path: target });
    const content = String(data.content || "");
    if (!(opts && opts.keepResults)) memExitResults();
    state.memEditor = {
      path: target,
      original: content,
      content,
      dirty: false,
      isNew: false,
      index: true,
      meta: data.meta || {},
      view: null,
      host: null,
      statusEl: null,
    };
    state.memSel = { kind: "file", path: target };
    // 内容与元数据刚取过，直接喂给详情栏，避免再发一次请求
    state.memDetail = { content, meta: data.meta || {} };
    state.memView = "list";
    memRenderShell();
  } catch (err) {
    console.error("[memory] 打开编辑器失败", target, err);
    showBanner("error", "打开失败: " + String((err && err.message) || err));
  }
}

function memRenderEditor(host) {
  const ed = state.memEditor;
  if (!ed) return;
  // 活实例复用：shell 重绘不重建 CodeMirror，保住光标位置与撤销栈
  if (ed.host === host && ed.view && ed.view.dom && ed.view.dom.isConnected) {
    memEditorUpdateStatus(ed);
    return;
  }
  if (ed.view && typeof ed.view.destroy === "function") ed.view.destroy();
  host.innerHTML = "";

  const bar = el("div", "mem-editor-bar");
  const exitBtn = makeButton("退出编辑", () => memEditorExit(), "btn btn-exit");
  const name = el("span", "mem-editor-name mono", ed.path);
  const saveBtn = makeButton("保存", () => memEditorSave(), "btn btn-go");
  bar.append(exitBtn, name, saveBtn);

  const status = el("div", "mem-editor-status");
  bar.append(status);

  const metaRow = el("div", "mem-editor-meta");
  const indexLbl = el("label", "switch-text");
  const indexChk = document.createElement("input");
  indexChk.type = "checkbox";
  indexChk.checked = Boolean(ed.index);
  indexChk.addEventListener("change", () => {
    ed.index = indexChk.checked;
  });
  indexLbl.append(indexChk, el("span", "", "保存后加入索引"));
  const metaText = el("span", "card-help mono", memEditorMetaText(ed));
  metaRow.append(indexLbl, metaText, el("span", "card-help", "标签在详情栏编辑"));

  const box = el("div", "mem-editor-box");
  host.append(bar, metaRow, box);

  ed.host = host;
  ed.statusEl = status;
  ed.metaEl = metaText;
  memEditorUpdateStatus(ed);

  const view = createCodeMirrorEditor(box, ed.content, {
    language: guessEditorLanguage(ed.path),
    onChange: (value) => {
      const current = state.memEditor;
      if (!current) return;
      current.content = value;
      current.dirty = value !== current.original;
      memEditorUpdateStatus(current);
    },
  });
  ed.view = view;
}

function memEditorMetaText(ed) {
  const meta = ed.meta || {};
  const parts = [];
  if (meta.updated_at) parts.push("更新 " + String(meta.updated_at));
  if (meta.chunk_count !== undefined && meta.chunk_count !== null) parts.push("分块 " + String(meta.chunk_count));
  if (meta.indexed !== undefined && meta.indexed !== null) parts.push("索引 " + (meta.indexed ? "已加入" : "未加入"));
  if (ed.isNew) parts.push("未创建");
  return parts.join(" · ");
}

function memEditorUpdateStatus(ed) {
  if (!ed || !ed.statusEl) return;
  ed.statusEl.textContent = ed.dirty ? "未保存" : ed.isNew ? "未创建" : "已保存";
  ed.statusEl.classList.toggle("dirty", Boolean(ed.dirty));
}

function memEditorExit() {
  if (memEditorGuard(() => memEditorExit(), "退出编辑")) return;
  memEditorClose();
  memRenderShell();
}

async function memEditorSave() {
  const ed = state.memEditor;
  if (!ed) return false;
  const path = normalizeKbPath(ed.path);
  if (!path || path === "/") {
    showBanner("error", "请输入有效的 KB 文件路径。");
    return false;
  }
  const content = ed.view ? ed.view.getValue() : ed.content;
  try {
    const data = await cfgApi("POST", "/faust/memory/save", {
      path,
      content,
      declared_by: "config-center",
      index: Boolean(ed.index),
    });
    const current = state.memEditor;
    if (current && current.path === path) {
      current.content = content;
      current.original = content;
      current.dirty = false;
      current.isNew = false;
      current.meta = Object.assign({}, current.meta || {}, data.meta || {});
      if (current.metaEl) current.metaEl.textContent = memEditorMetaText(current);
      memEditorUpdateStatus(current);
    }
    state.memSel = { kind: "file", path };
    await ensureModuleData("memory");
    // 只刷新周边（树/卡片），不动编辑器 DOM
    memRenderTree();
    memRenderVizCard();
    showBanner("success", "KB 已保存: " + path);
    return true;
  } catch (err) {
    console.error("[memory] 保存失败", path, err);
    showBanner("error", "保存失败: " + String((err && err.message) || err));
    return false;
  }
}

function memPromptPath(title, defaultValue, onConfirm) {
  const input = el("input", "input");
  input.value = defaultValue;
  const bar = el("div", "toolbar");
  const confirm = async () => {
    const value = String(input.value || "").trim();
    if (!value) {
      showBanner("error", "请输入路径");
      return;
    }
    closeModal();
    await onConfirm(normalizeKbPath(value));
  };
  bar.append(makeButton("创建", confirm, "btn btn-go"), makeButton("取消", closeModal, "btn btn-quiet"));
  input.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter") confirm();
  });
  openModal(title, [input, bar]);
}

function memNewFile(dir) {
  if (memEditorGuard(() => memNewFile(dir), "新建文件")) return;
  const base = dir === "/" ? "" : dir.slice(1) + "/";
  memPromptPath("新建文件", base + "new.md", (target) => {
    // 先只开编辑器：文件在首次「保存」时才真正落盘，退出未保存则什么都不产生
    state.memEditor = {
      path: target,
      original: "",
      content: "",
      dirty: false,
      isNew: true,
      index: true,
      meta: {},
      view: null,
      host: null,
      statusEl: null,
    };
    state.memSel = { kind: "file", path: target };
    state.memView = "list";
    memRenderShell();
  });
}

function memNewFolder(dir) {
  const base = dir === "/" ? "" : dir.slice(1) + "/";
  memPromptPath("新建文件夹", base + "new-folder", async (target) => {
    try {
      await cfgApi("POST", "/faust/memory/mkdir", { path: target });
      memSelectDir(target);
      await memReload();
      showBanner("success", "已创建目录: " + target);
    } catch (err) {
      console.error("[memory] 新建目录失败", target, err);
      showBanner("error", "新建失败: " + String((err && err.message) || err));
    }
  });
}

async function memRename(node) {
  const newName = window.prompt("新名称:", node.name);
  if (!newName || newName === node.name) return;
  try {
    await cfgApi("POST", "/faust/memory/rename", { path: node.path, new_name: newName });
    // 选中项改指向新路径，否则 ensureModuleData 找不到旧路径会退回根目录
    const newPath = normalizeKbPath(kbParentPath(node.path) + "/" + newName);
    if (node.type === "dir") {
      state.memSel = { kind: "dir", path: newPath };
      const idx = state.memExpanded.indexOf(node.path);
      if (idx >= 0) state.memExpanded[idx] = newPath;
      memWriteLocal(MEM_TREE_KEY, state.memExpanded);
    } else {
      state.memSel = { kind: "file", path: newPath };
      state.memDetail = null;
    }
    await memReload();
    if (state.memSel.kind === "file") memLoadDetail(newPath, state._memRenderToken);
    showBanner("success", "已重命名为 " + newName);
  } catch (err) {
    console.error("[memory] 重命名失败", node.path, err);
    showBanner("error", "重命名失败: " + String((err && err.message) || err));
  }
}

function memClipboard(mode, node) {
  state.memClipboard = { mode, path: node.path, name: node.name };
  showBanner("info", (mode === "copy" ? "已复制: " : "已剪切: ") + node.name);
}

async function memPaste(targetDir) {
  const clip = state.memClipboard;
  if (!clip) {
    showBanner("info", "剪贴板为空");
    return;
  }
  try {
    if (clip.mode === "copy") {
      await cfgApi("POST", "/faust/memory/copy", { path: clip.path, dest: normalizeKbPath(targetDir + "/" + clip.name) });
    } else {
      await cfgApi("POST", "/faust/memory/move", { path: clip.path, dest_dir: targetDir });
      state.memClipboard = null;
    }
    await memReload();
    showBanner("success", "已粘贴: " + clip.name);
  } catch (err) {
    console.error("[memory] 粘贴失败", clip.path, err);
    showBanner("error", "粘贴失败: " + String((err && err.message) || err));
  }
}

async function memDelete(path) {
  if (!path || path === "/") return;
  if (!window.confirm("确定删除 " + path + " ?")) return;
  try {
    await cfgApi("DELETE", "/faust/memory/delete", null, { path });
    if (state.memSel && state.memSel.path === path) state.memSel = { kind: "dir", path: kbParentPath(path) };
    state.memDetail = null;
    await memReload();
    showBanner("success", "已删除: " + path);
  } catch (err) {
    console.error("[memory] 删除失败", path, err);
    showBanner("error", "删除失败: " + String((err && err.message) || err));
  }
}

async function memImportExternal() {
  try {
    const filePath = await window.api.configOpenFile({ title: "选择外部文件" });
    if (!filePath) return;
    const kbPath = window.prompt("KB 路径（可留空自动）") || "";
    await cfgApi("POST", "/faust/memory/declare-update", { file_path: filePath, kb_path: kbPath.trim() || null });
    await memReload();
    showBanner("success", "文件已导入。");
  } catch (err) {
    console.error("[memory] 导入失败", err);
    showBanner("error", "导入失败: " + String((err && err.message) || err));
  }
}

// 详情栏「查看关联文件」：把关联路径集合送入列表（非条件型结果集）
function memShowLinkedFiles(paths) {
  const list = (Array.isArray(paths) ? paths : []).map((raw) => {
    const path = normalizeKbPath(raw);
    const node = memFindNode(path) || {};
    return {
      path,
      name: String(node.name || path.split("/").pop() || path),
      dir: kbParentPath(path),
      description: String(node.description || ""),
      tags: Array.isArray(node.tags) ? node.tags : [],
      updated_at: String(node.updated_at || ""),
      chunk_count: Number(node.chunk_count || 0),
      indexed: Boolean(node.indexed),
      declared_by: String(node.declared_by || ""),
      score_patch: Number(node.score_patch || 0),
      content_type: String(node.content_type || ""),
      type: "file",
    };
  });
  state.memFilter = { query: "", tags: [], tagLogic: "AND", dateFrom: "", dateTo: "", sortBy: "relevance", sortOrder: "desc" };
  state.memSearchResults = list;
  state.memResultLabel = "关联文件";
  state.memPage = 0;
  state.memView = "list";
  if (typeof memSearchSyncDraft === "function") memSearchSyncDraft();
  memRenderShell();
}

// ── 模块入口 ──

function renderMemoryModule() {
  memTeardownAll();
  memEnsureState();
  state._memRenderToken = Symbol("memory-render");

  const shell = el("div", "mem-shell");
  const treePane = el("div", "mem-tree");
  treePane.id = "memTree";

  const main = el("div", "mem-main");

  const top = el("div", "mem-top");
  const crumbs = el("div", "mem-crumbs");
  crumbs.id = "memCrumbs";
  const switcher = el("div", "mem-views");
  switcher.id = "memViews";
  top.append(crumbs, switcher);

  const views = el("div", "mem-views-host");
  for (const view of MEM_VIEWS) {
    const host = el("div", "mem-view");
    host.id = { list: "memViewList", graph: "memViewGraph" }[view.id];
    host.hidden = true;
    views.append(host);
  }

  main.append(top, views);

  const detail = el("div", "mem-detail");
  detail.id = "memDetail";

  // 可视化卡片：通栏，位于三栏之后（grid-column: 1 / -1）
  const vizCard = el("div", "mem-viz-card");
  vizCard.id = "memVizCard";

  shell.append(treePane, main, detail, vizCard);
  const shellWrap = el("div", "mem-shell-wrap");
  shellWrap.append(shell);
  addSection("记忆", [shellWrap]);

  if (!_memVisibilityHooked) {
    onModuleVisibilityChange(memHandleVisibility);
    // 编辑中离开记忆页 → 先处理未保存改动（守卫返回 false 取消切换）
    onModuleLeave((target) => {
      if (target === "memory") return true;
      return !memEditorGuard(() => switchModule(target), "离开记忆页");
    });
    _memVisibilityHooked = true;
  }
  memRegisterTeardown(() => {
    if (state.memEditor && state.memEditor.view && typeof state.memEditor.view.destroy === "function") {
      state.memEditor.view.destroy();
      state.memEditor = null;
    }
    if (_memGraph.canvas) {
      _memGraph.canvas.destroy();
      _memGraph.canvas = null;
    }
    if (typeof MemoryVisuals !== "undefined") MemoryVisuals.disposeAll();
    _memVizTreeRef = null;
    if (state._memTreeChunked) {
      state._memTreeChunked.cancel();
      state._memTreeChunked = null;
    }
  });

  memRenderShell();
}
