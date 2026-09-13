// Memory list view: search bar (basic + advanced), toolbar, sortable table, pager.
// Entry point: memRenderList(host) — called by the shell (modules/memory/explorer.js).
// memRenderResultTable(host) renders only the table + pager (also used after a search).
// All state is read from the global `state`; this file keeps only render-local handles.

var MEM_LIST_PAGE_SIZE = 200;
var MEM_LIST_CHUNK_SIZE = 100;

// 「更新刻度尺」高度/颜色档位，索引 = chunk_count 档位（0 / 1-4 / 5-19 / ≥20）
var MEM_SCALE_LEVELS = [
  { height: 3, color: "#c3d0e3" },
  { height: 5, color: "#9ab4e8" },
  { height: 8, color: "#5b87d6" },
  { height: 10, color: "#2f5fb3" },
];

// 列 id → memSort.by 的排序键（不在表内 = 该列不可排序）
var MEM_LIST_SORT_KEYS = {
  name: "name",
  updated: "updated_at",
  size: "size",
  score_patch: "score_patch",
  score: "score",
};

var _memListChunk = null;

// ── 入口 ──

function memRenderList(host) {
  if (!host) return;
  host.append(memSearchBar());
  host.append(memListToolbar());
  memRenderResultTable(host);
}

// ── 搜索栏（基本行 + 高级下拉；两段式：草稿 → 点「搜索」生效） ──

var MEM_SEARCH_DEFAULT = { query: "", tags: [], tagLogic: "AND", dateFrom: "", dateTo: "", sortBy: "relevance", sortOrder: "desc" };
var _memSearchAdvOpen = false;

function memSearchStr(value) {
  return value === undefined || value === null ? "" : String(value);
}

// 草稿：搜索栏输入框的内容（未点「搜索」前的编辑内容）；无草稿时以已生效条件为准
function memSearchDraft() {
  const base = state.memSearchDraft && typeof state.memSearchDraft === "object" ? state.memSearchDraft : state.memFilter || {};
  return Object.assign({}, MEM_SEARCH_DEFAULT, base);
}

// 用已生效条件覆盖草稿（检索/清除/可视化点击后调用）
function memSearchSyncDraft() {
  state.memSearchDraft = Object.assign({}, MEM_SEARCH_DEFAULT, state.memFilter || {});
}

function memSearchParseTags(raw) {
  const out = [];
  for (const part of String(raw || "").split(",")) {
    const tag = part.trim();
    if (tag && !out.includes(tag)) out.push(tag);
  }
  return out;
}

// 表单当前内容（DOM 为准，未挂载时退回草稿）
function memSearchFormValues() {
  const draft = memSearchDraft();
  const value = (id, fallback) => {
    const node = document.getElementById(id);
    return node ? node.value : fallback;
  };
  return {
    query: String(value("memSearchQuery", draft.query) || "").trim(),
    tags: memSearchParseTags(value("memSearchTags", (draft.tags || []).join(", "))),
    tagLogic: String(value("memSearchTagLogic", draft.tagLogic) || "AND"),
    dateFrom: String(value("memSearchDateFrom", draft.dateFrom) || ""),
    dateTo: String(value("memSearchDateTo", draft.dateTo) || ""),
    sortBy: String(value("memSearchSortBy", draft.sortBy) || "relevance"),
    sortOrder: String(value("memSearchSortOrder", draft.sortOrder) || "desc"),
  };
}

// 已生效条件 vs 草稿：只比内容（空串≡缺省），标签忽略顺序/大小写
function memSearchTagsKey(tags) {
  return (Array.isArray(tags) ? tags : []).map((t) => String(t).trim().toLowerCase()).filter(Boolean).sort().join("\u0000");
}

function memSearchIsDirty(form) {
  const f = Object.assign({}, MEM_SEARCH_DEFAULT, state.memFilter || {});
  const norm = (v) => String(v || "").trim();
  return (
    norm(form.query) !== norm(f.query) ||
    memSearchTagsKey(form.tags) !== memSearchTagsKey(f.tags) ||
    norm(form.dateFrom) !== norm(f.dateFrom) ||
    norm(form.dateTo) !== norm(f.dateTo) ||
    norm(form.tagLogic) !== norm(f.tagLogic) ||
    norm(form.sortBy) !== norm(f.sortBy) ||
    norm(form.sortOrder) !== norm(f.sortOrder)
  );
}

// 高级项里「已启用」的数量（逻辑非默认 / 日期 / 创建者 / 排序非默认）
function memSearchAdvancedCount() {
  const f = Object.assign({}, MEM_SEARCH_DEFAULT, state.memFilter || {});
  let n = 0;
  if (String(f.tagLogic || "AND") !== "AND") n += 1;
  if (f.dateFrom || f.dateTo) n += 1;
  if (String(f.sortBy || "relevance") !== "relevance" || String(f.sortOrder || "desc") !== "desc") n += 1;
  return n;
}

function memSearchAfterEdit() {
  state.memSearchDraft = memSearchFormValues();
  memSearchUpdateHint();
}

// 输入/变更后刷新「条件已修改」提示与高级角标（不重绘，避免输入焦点丢失）
function memSearchUpdateHint() {
  const hint = document.getElementById("memSearchHint");
  if (hint) {
    const dirty = memSearchIsDirty(memSearchFormValues());
    hint.hidden = !dirty;
    hint.textContent = dirty ? "条件已修改，点「搜索」生效" : "";
  }
  const badge = document.getElementById("memSearchAdvCount");
  if (badge) {
    const n = memSearchAdvancedCount();
    badge.textContent = n ? String(n) : "";
    badge.hidden = !n;
  }
}

function memSearchField(labelText, control) {
  const field = el("label", "mem-field", labelText + " ");
  field.append(control);
  return field;
}

function memSearchInput(id, value, placeholder) {
  const input = el("input", "input");
  input.id = id;
  input.value = memSearchStr(value);
  if (placeholder) input.placeholder = placeholder;
  return input;
}

function memSearchSelect(id, options, value) {
  const select = el("select", "select");
  select.id = id;
  for (const pair of options) {
    const option = el("option", null, pair[1]);
    option.value = pair[0];
    select.append(option);
  }
  select.value = value;
  return select;
}

function memSearchBar() {
  const draft = memSearchDraft();
  const wrap = el("div", "mem-search");

  const basic = el("div", "mem-search-basic");
  const queryInput = memSearchInput("memSearchQuery", draft.query, "关键词（文件名/描述/正文）");
  const tagsInput = memSearchInput("memSearchTags", (draft.tags || []).join(", "), "标签，逗号分隔");
  const searchBtn = makeButton("搜索", () => memApplySearch(), "btn btn-go");
  searchBtn.id = "memSearchGo";
  const advBtn = makeButton("", () => memSearchToggleAdvanced(), "btn btn-quiet");
  advBtn.id = "memSearchAdv";
  const advLabel = el("span", "mem-search-advlabel", "高级 \u25BE");
  advLabel.id = "memSearchAdvLabel";
  const advCount = el("span", "mem-search-advcount", "");
  advCount.id = "memSearchAdvCount";
  advCount.hidden = true;
  advBtn.append(advLabel, advCount);
  const hint = el("span", "mem-search-hint", "");
  hint.id = "memSearchHint";
  hint.hidden = true;
  basic.append(queryInput, tagsInput, searchBtn, advBtn, hint);

  if (state.memSearchResults) {
    const close = makeButton("关闭搜索", () => memClearFilter(), "btn btn-exit");
    close.id = "memSearchClose";
    basic.append(close);
  }
  wrap.append(basic);

  // 高级面板：逻辑 / 日期范围 / 创建者 / 排序 / 顺序
  const adv = el("div", "mem-search-grid");
  adv.id = "memSearchAdvanced";
  adv.hidden = !_memSearchAdvOpen;
  const logicSelect = memSearchSelect("memSearchTagLogic", [["AND", "同时满足"], ["OR", "任一满足"]], memSearchStr(draft.tagLogic) || "AND");
  const sortSelect = memSearchSelect("memSearchSortBy", [["relevance", "相关度"], ["updated_at", "更新时间"]], memSearchStr(draft.sortBy) || "relevance");
  const orderSelect = memSearchSelect("memSearchSortOrder", [["desc", "降序"], ["asc", "升序"]], memSearchStr(draft.sortOrder) || "desc");
  adv.append(
    memSearchField("标签逻辑", logicSelect),
    memSearchField("排序", sortSelect),
    memSearchField("顺序", orderSelect)
  );
  const dateField = el("div", "mem-field", "更新日期 ");
  const dateRow = el("div", "mem-search-dates");
  const fromInput = el("input");
  fromInput.type = "date";
  fromInput.id = "memSearchDateFrom";
  fromInput.value = memSearchStr(draft.dateFrom);
  const toInput = el("input");
  toInput.type = "date";
  toInput.id = "memSearchDateTo";
  toInput.value = memSearchStr(draft.dateTo);
  dateRow.append(fromInput, el("span", "mem-search-tilde", "~"), toInput);
  dateField.append(dateRow);
  adv.append(dateField);
  wrap.append(adv);

  // 草稿写入 + 脏提示；排序/顺序立即重排当前结果
  for (const input of [queryInput, tagsInput]) {
    input.addEventListener("input", () => memSearchAfterEdit());
    input.addEventListener("keydown", (evt) => {
      if (evt.key === "Enter") memApplySearch();
    });
  }
  for (const node of [logicSelect, fromInput, toInput]) {
    node.addEventListener("change", () => memSearchAfterEdit());
  }
  for (const node of [sortSelect, orderSelect]) {
    node.addEventListener("change", () => {
      state.memSearchDraft = memSearchFormValues();
      memApplySort();
    });
  }
  memSearchUpdateHint();
  return wrap;
}

function memSearchToggleAdvanced() {
  _memSearchAdvOpen = !_memSearchAdvOpen;
  const adv = document.getElementById("memSearchAdvanced");
  const label = document.getElementById("memSearchAdvLabel");
  if (adv) adv.hidden = !_memSearchAdvOpen;
  if (label) label.textContent = _memSearchAdvOpen ? "高级 \u25B4" : "高级 \u25BE";
  memSearchUpdateHint();
}

// 排序/顺序：不重跑检索，只把当前结果集按新键重排
function memApplySort() {
  const draft = memSearchFormValues();
  state.memFilter = Object.assign({}, state.memFilter || {}, { sortBy: draft.sortBy, sortOrder: draft.sortOrder });
  state.memSort = { by: draft.sortBy === "relevance" ? "score" : "updated_at", order: draft.sortOrder };
  state.memSearchDraft = draft;
  memSearchUpdateHint();
  memRenderShell();
}

// 结果表（列表视图与搜索视图共用；搜索视图只调这一个）
function memRenderResultTable(host) {
  if (!host) return;
  if (_memListChunk) {
    _memListChunk.cancel();
    _memListChunk = null;
  }
  const status = memListStatusLine();
  if (status) host.append(status);

  const rows = memRows() || [];
  memSortRows(rows);

  if (!rows.length) {
    host.append(memListEmptyState());
    return;
  }

  const totalPages = Math.max(1, Math.ceil(rows.length / MEM_LIST_PAGE_SIZE));
  const raw = Number(state.memPage);
  const page = Math.min(Math.max(0, Number.isFinite(raw) ? Math.floor(raw) : 0), totalPages - 1);
  state.memPage = page;
  const start = page * MEM_LIST_PAGE_SIZE;
  const pageRows = rows.slice(start, start + MEM_LIST_PAGE_SIZE);

  const table = el("table", "mem-list-table");
  const thead = el("thead");
  thead.append(memListHeaderRow());
  table.append(thead);
  const tbody = el("tbody");
  table.append(tbody);
  const scroll = el("div", "mem-list-scroll");
  scroll.append(table);
  host.append(scroll);

  if (rows.length > MEM_LIST_PAGE_SIZE) {
    // 大列表：按帧分批 append，句柄在下次渲染时取消
    _memListChunk = appendChunked(tbody, pageRows, (row) => memListRow(row), { chunkSize: MEM_LIST_CHUNK_SIZE });
  } else {
    for (const row of pageRows) tbody.append(memListRow(row));
  }

  if (totalPages > 1) host.append(memListPager(totalPages));
}

// 结果区状态行：非条件型结果集（关联文件）→ 来源 + 返回浏览；条件检索 → 命中数 + 排序说明
function memListStatusLine() {
  const box = el("div", "mem-list-status");
  if (state.memSearching) {
    box.append(el("span", "card-help", "检索中\u2026"));
    return box;
  }
  if (!state.memSearchResults) return null;
  if (!memFilterActive()) {
    box.append(el("span", "mem-chip-info", (state.memResultLabel || "结果") + " (" + state.memSearchResults.length + ")"));
    box.append(makeButton("返回浏览", () => {
      state.memSearchResults = null;
      state.memResultLabel = "";
      memRenderShell();
    }, "btn btn-quiet"));
    return box;
  }
  const count = state.memSearchResults.length;
  box.append(el("span", "card-help", count >= MEM_SEARCH_LIMIT
    ? "命中 \u2265" + count + " 条（仅显示前 " + MEM_SEARCH_LIMIT + " 条，请收紧条件）"
    : "命中 " + count + " 条"));
  box.append(el("span", "card-help", "· 表头排序仅重排当前结果，取数顺序在「高级」里改"));
  return box;
}

// ── 工具栏 ──

function memListToolbar() {
  const bar = el("div", "mem-list-bar");
  bar.append(makeButton("刷新", () => memListRun("刷新", () => memReload()), "btn btn-quiet"));
  bar.append(makeButton("新建文件", () => memListRun("新建文件", () => memNewFile(memCurrentDir())), "btn btn-quiet"));
  bar.append(makeButton("新建文件夹", () => memListRun("新建文件夹", () => memNewFolder(memCurrentDir())), "btn btn-quiet"));

  const sel = state.memSel || {};
  // 根目录不可删除（memDelete 自身也拒绝 "/"），此处先禁用避免无反馈的点击
  const deletable = (sel.kind === "file" || sel.kind === "dir") && Boolean(sel.path) && sel.path !== "/";
  const delBtn = makeButton("删除", () => memListRun("删除", () => memDelete(sel.path)), "btn btn-danger");
  delBtn.disabled = !deletable;
  bar.append(delBtn);

  bar.append(makeButton("导入外部文件", () => memListRun("导入外部文件", () => memImportExternal()), "btn btn-quiet"));
  return bar;
}

function memListRun(label, fn) {
  const fail = (err) => {
    console.error("[memory] " + label + "失败", err);
    showBanner("error", label + "失败: " + String((err && err.message) || err));
  };
  try {
    const result = fn();
    if (result && typeof result.catch === "function") result.catch(fail);
  } catch (err) {
    fail(err);
  }
}

// ── 列选择（表头行右侧 ⋯ 下拉） ──

function memListColumns() {
  const defs = Array.isArray(MEM_COLUMN_DEFS) ? MEM_COLUMN_DEFS : [];
  const byId = new Map(defs.map((d) => [d.id, d]));
  const out = [];
  for (const id of (Array.isArray(state.memColumns) ? state.memColumns : [])) {
    const def = byId.get(id);
    if (def) out.push(def);
  }
  return out;
}

function memListOpenColumnPicker(anchor) {
  const defs = Array.isArray(MEM_COLUMN_DEFS) ? MEM_COLUMN_DEFS : [];
  const chosen = new Set(Array.isArray(state.memColumns) ? state.memColumns : []);
  const body = el("div", "mem-menu-box");

  const nameRow = el("label", "list-row");
  const nameCb = el("input");
  nameCb.type = "checkbox";
  nameCb.checked = true;
  nameCb.disabled = true;
  nameRow.append(nameCb, el("span", "", "名称"));
  body.append(nameRow);

  const boxes = new Map();
  for (const def of defs) {
    const row = el("label", "list-row");
    const cb = el("input");
    cb.type = "checkbox";
    cb.checked = chosen.has(def.id);
    cb.addEventListener("change", () => {
      // 即时生效：勾选即重绘表格（下拉不关闭，方便连续勾选）
      const next = defs.filter((d) => {
        const box = boxes.get(d.id);
        return Boolean(box && box.checked);
      }).map((d) => d.id);
      state.memColumns = next;
      memWriteLocal(MEM_COLUMNS_KEY, next);
      const host = document.getElementById("memViewList");
      if (host) {
        host.innerHTML = "";
        memRenderList(host);
      }
    });
    row.append(cb, el("span", "", def.label));
    body.append(row);
    boxes.set(def.id, cb);
  }
  memOpenDropdown(anchor, body, "选择显示列");
}

// ── 表格 ──

function memListHeaderRow() {
  const tr = el("tr");
  tr.append(memListHeaderCell("名称", "name"));
  if (memHasScoreColumn()) tr.append(memListHeaderCell("相关度", "score"));
  for (const def of memListColumns()) tr.append(memListHeaderCell(def.label, def.id));
  // 表头行最右：小尺寸 ⋯ 按钮（列选择下拉），不参与排序
  const th = el("th", "mem-col-dots");
  const dots = makeButton("\u22EF", () => memListOpenColumnPicker(dots), "btn-icon mem-dots");
  dots.id = "memColDots";
  dots.title = "选择显示列";
  th.append(dots);
  tr.append(th);
  return tr;
}

// 表头 ⋯ 下拉（锚在按钮下方，点击外部关闭）
function memOpenDropdown(anchor, content, title) {
  const old = document.getElementById("memDropdown");
  if (old) old.remove();
  const menu = el("div", "mem-dropdown");
  menu.id = "memDropdown";
  if (title) menu.append(el("div", "mem-dropdown-title", title));
  menu.append(content);
  document.body.append(menu);
  const rect = anchor.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.round(rect.right - menu.offsetWidth)) + "px";
  menu.style.top = Math.round(rect.bottom + 4) + "px";
  const close = (evt) => {
    if (menu.contains(evt.target) || anchor.contains(evt.target)) return;
    menu.remove();
    document.removeEventListener("click", close);
  };
  window.setTimeout(() => document.addEventListener("click", close), 0);
}

// 相关度只在「填了关键词的检索」下有意义
function memHasScoreColumn() {
  const f = state.memFilter || {};
  return Boolean(state.memSearchResults && String(f.query || "").trim());
}

function memListHeaderCell(label, id) {
  const sortKey = MEM_LIST_SORT_KEYS[id] || null;
  const sort = state.memSort || {};
  const active = Boolean(sortKey && sort.by === sortKey);
  const th = el("th", sortKey ? "sortable" : "", label + (active ? (sort.order === "asc" ? " ↗" : " ↘") : ""));
  if (!sortKey) return th;
  th.addEventListener("click", () => {
    const order = active ? (sort.order === "asc" ? "desc" : "asc") : (sortKey === "name" ? "asc" : "desc");
    state.memSort = { by: sortKey, order: order };
    memRenderShell();
  });
  return th;
}

function memListRow(row) {
  const isDir = row.type === "dir";
  const sel = state.memSel || {};
  const tr = el("tr");
  const selected = isDir
    ? sel.kind === "dir" && sel.path === row.path
    : sel.kind === "file" && sel.path === row.path;
  if (selected) tr.classList.add("selected");

  tr.append(memListNameCell(row, isDir));
  if (memHasScoreColumn()) tr.append(el("td", "mem-col-score mono", isDir ? "—" : Number(row.score || 0).toFixed(2)));
  for (const def of memListColumns()) tr.append(memListCell(row, def.id, isDir));

  // 目录：单击进入；文件：单击选中（详情栏），双击打开编辑器
  // 列表内点击/打开保留当前结果集（结果行是「看结果」，不是「离开检索」）
  tr.addEventListener("click", () => {
    if (isDir) memSelectDir(row.path);
    else memSelectFile(row.path, { keepResults: true });
  });
  tr.addEventListener("dblclick", () => {
    if (isDir) memSelectDir(row.path);
    else memListRun("打开", () => memOpenEditor(row.path, { keepResults: true }));
  });
  tr.addEventListener("contextmenu", (evt) => {
    evt.preventDefault();
    memOpenContextMenu(evt.clientX, evt.clientY, {
      type: isDir ? "dir" : "file",
      path: row.path,
      name: String(row.name || ""),
    });
  });
  return tr;
}

function memListNameCell(row, isDir) {
  const td = el("td", "mem-col-name");
  const glyph = isDir ? "▸" : memIsImagePath(row.path) ? "▤" : "▪";
  td.append(el("span", "", glyph + " " + String(row.name || row.path || "")));
  return td;
}

function memListCell(row, id, isDir) {
  if (id === "tags") return memListTagsCell(row, isDir);
  if (id === "updated") return memListUpdatedCell(row, isDir);
  if (id === "size") return el("td", "mem-col-size mono", isDir ? "—" : String(Number(row.chunk_count || 0)));
  if (id === "indexed") {
    if (isDir) return el("td", "", "—");
    return el("td", "", row.indexed ? "已索引" : "未索引");
  }
  if (id === "score_patch") return el("td", "mono", isDir ? "—" : String(Number(row.score_patch || 0)));
  const key = id === "declared_by" ? "declared_by" : id === "content_type" ? "content_type" : null;
  if (!key) return el("td", "", "—");
  const value = isDir ? "" : String(row[key] || "");
  return el("td", "", value || "—");
}

function memListTagsCell(row, isDir) {
  const td = el("td");
  const tags = !isDir && Array.isArray(row.tags) ? row.tags : [];
  if (!tags.length) {
    td.textContent = "—";
    return td;
  }
  tags.forEach((tag, index) => {
    if (index) td.append(document.createTextNode(" "));
    const chip = el("span", "mem-chip");
    chip.append(el("span", "", String(tag)));
    chip.addEventListener("click", (evt) => {
      evt.stopPropagation();
      memToggleTag(String(tag));
    });
    td.append(chip);
  });
  return td;
}

function memListUpdatedCell(row, isDir) {
  const td = el("td", "mem-col-updated");
  const stamp = isDir ? "" : String(row.updated_at || "");
  const date = memListParseDate(stamp);
  if (!date) {
    td.textContent = "—";
    return td;
  }
  td.append(memListScale(row, date, stamp), el("span", "card-help mono", memListRelative(date)));
  return td;
}

// ── 「更新刻度尺」：横轴 = 新鲜度（越靠左越新），高度/颜色 = 规模档位 ──

function memListScale(row, date, stamp) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "mem-scale");
  svg.setAttribute("viewBox", "0 0 60 12");
  svg.setAttribute("width", "60");
  svg.setAttribute("height", "12");
  svg.setAttribute("role", "img");

  const ageDays = Math.max(0, (Date.now() - date.getTime()) / 86400000);
  const frac = Math.min(1, ageDays / 365);
  const x = 2 + frac * 52;
  const count = Number(row.chunk_count || 0);
  const level = count <= 0 ? 0 : count <= 4 ? 1 : count <= 19 ? 2 : 3;
  const spec = MEM_SCALE_LEVELS[level];
  const empty = !row.indexed || count === 0;

  const rect = document.createElementNS(ns, "rect");
  rect.setAttribute("x", x.toFixed(2));
  rect.setAttribute("y", String(11 - spec.height));
  rect.setAttribute("width", "3");
  rect.setAttribute("height", String(spec.height));
  rect.setAttribute("rx", "1");
  if (empty) {
    rect.setAttribute("fill", "none");
    rect.setAttribute("stroke", spec.color);
    rect.setAttribute("stroke-width", "1");
  } else {
    rect.setAttribute("fill", spec.color);
  }
  const title = document.createElementNS(ns, "title");
  title.textContent = String(stamp || "").slice(0, 10) + " · " + count + " 块";
  rect.append(title);
  svg.append(rect);

  if (!empty) return svg;
  const wrap = el("span");
  wrap.append(svg, el("span", "mem-scale-empty", "未索引"));
  return wrap;
}

function memListParseDate(stamp) {
  const raw = String(stamp || "").trim();
  if (!raw) return null;
  let iso = raw;
  if (/^\d{4}-\d{2}-\d{2}$/.test(raw)) iso = raw + "T00:00:00Z";
  else if (!/[zZ]$|[+-]\d{2}:?\d{2}$/.test(raw)) iso = raw + "Z";
  const date = new Date(iso);
  return isNaN(date.getTime()) ? null : date;
}

function memListRelative(date) {
  const minutes = Math.floor((Date.now() - date.getTime()) / 60000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return minutes + " 分钟前";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + " 小时前";
  const days = Math.floor(hours / 24);
  if (days < 30) return days + " 天前";
  const months = Math.floor(days / 30);
  if (months < 12) return months + " 个月前";
  return Math.floor(days / 365) + " 年前";
}

// ── 空态与分页 ──

function memListEmptyState() {
  const box = el("div", "mem-empty");
  if (state.memSearching) {
    box.append(el("div", "", "检索中\u2026"));
    return box;
  }
  if (!memFilterActive() && !state.memSearchResults) {
    const dir = memCurrentDir();
    box.append(el("div", "", dir === "/" ? "记忆库为空" : "当前目录为空"));
    return box;
  }
  const dir = memCurrentDir();
  const scope = dir === "/" ? "全库" : "当前目录（" + dir + " 子树）";
  box.append(el("div", "", "没有符合条件的结果"));
  box.append(el("div", "card-help", "检索范围：" + scope + "；共有 " + memSubtreeFiles().length + " 个候选文件"));
  const bar = el("div", "toolbar");
  bar.append(makeButton("关闭搜索", () => memClearFilter(), "btn btn-quiet"));
  bar.append(makeButton("回到搜索栏", () => {
    const input = document.getElementById("memSearchQuery");
    if (input && typeof input.focus === "function") input.focus();
  }, "btn btn-quiet"));
  box.append(bar);
  return box;
}

function memListPager(totalPages) {
  const page = Math.min(Math.max(0, Number(state.memPage) || 0), totalPages - 1);
  const prev = makeButton("上一页", () => {
    if (Number(state.memPage) <= 0) return;
    state.memPage = Number(state.memPage) - 1;
    memRenderShell();
  });
  prev.disabled = page <= 0;
  const next = makeButton("下一页", () => {
    if (Number(state.memPage) >= totalPages - 1) return;
    state.memPage = Number(state.memPage) + 1;
    memRenderShell();
  });
  next.disabled = page >= totalPages - 1;
  const bar = el("div", "toolbar table-pager");
  bar.append(prev, el("span", "card-help", "第 " + (page + 1) + " / " + totalPages + " 页"), next);
  return bar;
}
