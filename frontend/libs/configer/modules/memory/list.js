// Memory list view: toolbar, column picker, sortable table, pager.
// Entry point: memRenderList(host) — called by the shell (modules/memory/explorer.js).
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
};

var _memListChunk = null;
var _memListFilterInput = null;
var _memListFilterTimer = 0;

// ── 入口 ──

function memRenderList(host) {
  if (!host) return;
  if (_memListChunk) {
    _memListChunk.cancel();
    _memListChunk = null;
  }
  _memListFilterInput = null;

  const rows = memRows() || [];
  memSortRows(rows);

  host.append(memListToolbar());

  if (Array.isArray(state.memSearchResults) && state.memSearchResults.length) {
    host.append(el("div", "card-help", "全文检索结果 " + state.memSearchResults.length + " 项（含正文匹配）"));
  }

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

// ── 工具栏 ──

function memListToolbar() {
  const bar = el("div", "mem-list-bar");
  bar.append(makeButton("刷新", () => memListRun("刷新", () => memReload())));
  bar.append(makeButton("新建文件", () => memListRun("新建文件", () => memNewFile(memCurrentDir()))));
  bar.append(makeButton("新建文件夹", () => memListRun("新建文件夹", () => memNewFolder(memCurrentDir()))));

  const sel = state.memSel || {};
  // 根目录不可删除（memDelete 自身也拒绝 "/"），此处先禁用避免无反馈的点击
  const deletable = (sel.kind === "file" || sel.kind === "dir") && Boolean(sel.path) && sel.path !== "/";
  const delBtn = makeButton("删除", () => memListRun("删除", () => memDelete(sel.path)));
  delBtn.disabled = !deletable;
  bar.append(delBtn);

  const input = el("input", "input");
  input.placeholder = "名称过滤（回车生效）";
  input.value = String((state.memFilter && state.memFilter.query) || "");
  let committed = input.value;
  const commit = (immediate) => {
    const value = String(input.value || "").trim();
    if (value === committed) return;
    committed = value;
    if (immediate) {
      memListApplyQuery(value);
      return;
    }
    // 失焦提交延后一拍：本次点击的目标按钮不会被重绘销毁
    if (_memListFilterTimer) window.clearTimeout(_memListFilterTimer);
    _memListFilterTimer = window.setTimeout(() => {
      _memListFilterTimer = 0;
      if (String((state.memFilter && state.memFilter.query) || "") === value) return;
      memListApplyQuery(value);
    }, 0);
  };
  input.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter") commit(true);
  });
  input.addEventListener("blur", () => commit(false));
  _memListFilterInput = input;
  bar.append(input);

  bar.append(makeButton("导入外部文件", () => memListRun("导入外部文件", () => memImportExternal())));
  bar.append(makeButton("全文检索", () => {
    memListSyncQuery();
    memListRun("全文检索", () => memFulltextSearch());
  }));
  bar.append(makeButton("列", () => memListOpenColumnPicker()));
  if (memFilterActive()) {
    bar.append(makeButton("清除筛选", () => memClearFilter()));
  }
  return bar;
}

function memListApplyQuery(value) {
  state.memFilter = state.memFilter || {};
  state.memFilter.query = value;
  memSetFilter({ query: value });
}

// 把输入框里尚未提交的值同步进 state（供「全文检索」等读 state 的动作使用）
function memListSyncQuery() {
  const input = _memListFilterInput;
  if (!input) return;
  const value = String(input.value || "").trim();
  state.memFilter = state.memFilter || {};
  if (value !== String(state.memFilter.query || "")) state.memFilter.query = value;
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

// ── 列选择 ──

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

function memListOpenColumnPicker() {
  const defs = Array.isArray(MEM_COLUMN_DEFS) ? MEM_COLUMN_DEFS : [];
  const chosen = new Set(Array.isArray(state.memColumns) ? state.memColumns : []);
  const body = el("div", "list-box");

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
    row.append(cb, el("span", "", def.label));
    body.append(row);
    boxes.set(def.id, cb);
  }

  const actions = el("div", "toolbar");
  actions.append(
    makeButton("确定", () => {
      state.memColumns = defs.filter((d) => {
        const cb = boxes.get(d.id);
        return Boolean(cb && cb.checked);
      }).map((d) => d.id);
      memWriteLocal(MEM_COLUMNS_KEY, state.memColumns);
      closeModal();
      memRenderShell();
    }, "btn btn-primary"),
    makeButton("取消", () => closeModal())
  );

  openModal("选择显示列", [body, actions]);
}

// ── 表格 ──

function memListHeaderRow() {
  const tr = el("tr");
  tr.append(memListHeaderCell("名称", "name"));
  for (const def of memListColumns()) tr.append(memListHeaderCell(def.label, def.id));
  return tr;
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
  for (const def of memListColumns()) tr.append(memListCell(row, def.id, isDir));

  tr.addEventListener("click", () => {
    if (isDir) memSelectDir(row.path);
    else memSelectFile(row.path);
  });
  tr.addEventListener("dblclick", () => {
    if (isDir) memSelectDir(row.path);
    else memListRun("打开", () => memOpenEditor(row.path));
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
  if (Array.isArray(state.memSearchResults) && typeof row.score === "number") {
    td.append(el("span", "card-help mono", " " + row.score.toFixed(2)));
  }
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
  const filtered = Array.isArray(state.memSearchResults) || memFilterActive();
  if (!filtered) {
    box.append(el("div", "", "当前目录为空"));
    return box;
  }
  box.append(el("div", "", "没有符合条件的文件"));
  const bar = el("div", "toolbar");
  bar.append(makeButton("清除筛选", () => memClearFilter()));
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
