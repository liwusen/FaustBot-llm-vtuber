// Memory search view: 多维度搜索表单，两段式（先编辑草稿，点「搜索」才生效）。
// Entry point: renderMemSearchView(host) — called by the shell (modules/memory/explorer.js).
// 表单按 state.memSearchDraft 渲染，外壳重绘不会丢正在输入的内容；
// state.memFilter 保存已生效条件。检索由外壳 memApplySearch() 负责，本文件不发任何请求。

var MEM_SEARCH_DEFAULT_SORT = "relevance";
var MEM_SEARCH_DEFAULT_ORDER = "desc";
var MEM_SEARCH_DEFAULT_TAG_LOGIC = "AND";

// ── 表单值 / 草稿 ──

function memSearchStr(value) {
  return value === undefined || value === null ? "" : String(value);
}

// 输入框/下拉当前值（DOM 为准；视图未挂载时退回草稿）
function memSearchFieldValue(id, fallback) {
  const node = document.getElementById(id);
  if (node && typeof node.value === "string") return node.value;
  return memSearchStr(fallback);
}

// 逗号分隔 → 去空、去重（保留首次出现的写法）
function memSearchParseTags(raw) {
  const out = [];
  const seen = new Set();
  for (const part of memSearchStr(raw).split(",")) {
    const tag = part.trim();
    if (!tag || seen.has(tag)) continue;
    seen.add(tag);
    out.push(tag);
  }
  return out;
}

// 表单当前内容（供外壳写入 state.memFilter；空串保持空串）
function memSearchFormValues() {
  const draft = memSearchDraft();
  const fallbackTags = Array.isArray(draft.tags) ? draft.tags.join(", ") : "";
  return {
    query: memSearchFieldValue("memSearchQuery", draft.query).trim(),
    tags: memSearchParseTags(memSearchFieldValue("memSearchTags", fallbackTags)),
    tagLogic: memSearchFieldValue("memSearchTagLogic", draft.tagLogic) || MEM_SEARCH_DEFAULT_TAG_LOGIC,
    dateFrom: memSearchFieldValue("memSearchDateFrom", draft.dateFrom).trim(),
    dateTo: memSearchFieldValue("memSearchDateTo", draft.dateTo).trim(),
    declaredBy: memSearchFieldValue("memSearchDeclaredBy", draft.declaredBy).trim(),
    sortBy: memSearchFieldValue("memSearchSortBy", draft.sortBy) || MEM_SEARCH_DEFAULT_SORT,
    sortOrder: memSearchFieldValue("memSearchSortOrder", draft.sortOrder) || MEM_SEARCH_DEFAULT_ORDER,
  };
}

function memSearchDraft() {
  if (!state.memSearchDraft || typeof state.memSearchDraft !== "object") memSearchSyncDraft();
  return state.memSearchDraft;
}

// 用已生效条件覆盖草稿（外壳 memApplySearch / memClearFilter 也会调用）
function memSearchSyncDraft() {
  const f = state.memFilter || {};
  state.memSearchDraft = {
    query: memSearchStr(f.query),
    tags: Array.isArray(f.tags) ? f.tags.map((tag) => memSearchStr(tag)) : [],
    tagLogic: memSearchStr(f.tagLogic) || MEM_SEARCH_DEFAULT_TAG_LOGIC,
    dateFrom: memSearchStr(f.dateFrom),
    dateTo: memSearchStr(f.dateTo),
    declaredBy: memSearchStr(f.declaredBy),
    sortBy: memSearchStr(f.sortBy) || MEM_SEARCH_DEFAULT_SORT,
    sortOrder: memSearchStr(f.sortOrder) || MEM_SEARCH_DEFAULT_ORDER,
  };
  return state.memSearchDraft;
}

// ── 脏提示（草稿 vs 已生效条件） ──

// tags 比较键：忽略顺序、忽略大小写、忽略空项
function memSearchTagsKey(tags) {
  return (Array.isArray(tags) ? tags : [])
    .map((tag) => memSearchStr(tag).trim().toLowerCase())
    .filter(Boolean)
    .sort()
    .join("\u0000");
}

// 空串与缺省等价：只比内容，不比「有没有这个键」
function memSearchIsDirty(form) {
  const f = state.memFilter || {};
  const values = form || memSearchFormValues();
  if (values.query !== memSearchStr(f.query).trim()) return true;
  if (memSearchTagsKey(values.tags) !== memSearchTagsKey(f.tags)) return true;
  if (values.tagLogic !== (memSearchStr(f.tagLogic) || MEM_SEARCH_DEFAULT_TAG_LOGIC)) return true;
  if (values.dateFrom !== memSearchStr(f.dateFrom).trim()) return true;
  if (values.dateTo !== memSearchStr(f.dateTo).trim()) return true;
  if (values.declaredBy !== memSearchStr(f.declaredBy).trim()) return true;
  if (values.sortBy !== (memSearchStr(f.sortBy) || MEM_SEARCH_DEFAULT_SORT)) return true;
  if (values.sortOrder !== (memSearchStr(f.sortOrder) || MEM_SEARCH_DEFAULT_ORDER)) return true;
  return false;
}

function memSearchNoteText() {
  const f = state.memFilter || {};
  let text = "范围：" + memCurrentDir() + " 子树";
  if (!memSearchStr(f.query).trim()) text += " · 未填关键词，按更新时间排序";
  return text;
}

function memSearchUpdateDirtyHint() {
  const hint = document.getElementById("memSearchHint");
  if (hint) hint.hidden = !memSearchIsDirty();
  const note = document.getElementById("memSearchNote");
  if (note) note.textContent = memSearchNoteText();
}

// 任意输入/变更后：当前值写回草稿，再刷新脏提示
function memSearchAfterEdit() {
  state.memSearchDraft = memSearchFormValues();
  memSearchUpdateDirtyHint();
}

// ── 动作 ──

// 「搜索」按钮 / 文本输入回车：交给外壳整体替换条件并检索
function memSearchApply() {
  if (typeof memApplySearch !== "function") {
    showBanner("error", "搜索动作不可用（外壳未加载）");
    return;
  }
  memApplySearch();
}

// 「清除」：外壳清空条件并重绘，这里兜底再同步一次草稿
function memSearchClear() {
  if (typeof memClearFilter === "function") {
    memClearFilter();
  } else {
    state.memFilter = {
      query: "",
      tags: [],
      tagLogic: MEM_SEARCH_DEFAULT_TAG_LOGIC,
      dateFrom: "",
      dateTo: "",
      declaredBy: "",
      sortBy: MEM_SEARCH_DEFAULT_SORT,
      sortOrder: MEM_SEARCH_DEFAULT_ORDER,
    };
    state.memSearchResults = null;
    memSearchSyncDraft();
    memRenderShell();
  }
  memSearchSyncDraft();
}

// ── 表单 ──

function memSearchField(labelText, control) {
  const field = el("label", "mem-field", labelText + " ");
  field.append(control);
  return field;
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

function memSearchTextInput(id, value, placeholder) {
  const input = el("input", "input");
  input.id = id;
  input.value = memSearchStr(value);
  if (placeholder) input.placeholder = placeholder;
  return input;
}

function renderMemSearchView(host) {
  if (!host) return;
  const draft = memSearchDraft();

  const root = el("div", "mem-search");
  const grid = el("div", "mem-search-grid");

  const queryInput = memSearchTextInput("memSearchQuery", draft.query, "");
  grid.append(memSearchField("关键词", queryInput));

  const tagsInput = memSearchTextInput("memSearchTags", Array.isArray(draft.tags) ? draft.tags.join(", ") : "", "逗号分隔");
  grid.append(memSearchField("标签", tagsInput));

  grid.append(
    memSearchField(
      "逻辑",
      memSearchSelect("memSearchTagLogic", [["AND", "同时满足"], ["OR", "任一满足"]], memSearchStr(draft.tagLogic) || MEM_SEARCH_DEFAULT_TAG_LOGIC)
    )
  );

  // 目录：只读当前目录 + 选择器（选目录立即生效，不属于「草稿→搜索」两段式）
  const dirField = el("div", "mem-field", "目录 ");
  const dirRow = el("div", "mem-search-dir");
  const dirText = el("span", "mono", memCurrentDir());
  dirText.id = "memSearchDir";
  const pickBtn = makeButton("选择目录", () => memSearchPickDir(), "btn btn-ghost");
  pickBtn.id = "memSearchPickDir";
  dirRow.append(dirText, pickBtn);
  dirField.append(dirRow);
  grid.append(dirField);

  const dateField = el("div", "mem-field", "日期范围 ");
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
  grid.append(dateField);

  const byInput = memSearchTextInput("memSearchDeclaredBy", draft.declaredBy, "");
  grid.append(memSearchField("创建者", byInput));

  grid.append(
    memSearchField(
      "排序",
      memSearchSelect("memSearchSortBy", [["relevance", "相关度"], ["updated_at", "更新时间"]], memSearchStr(draft.sortBy) || MEM_SEARCH_DEFAULT_SORT)
    )
  );
  grid.append(
    memSearchField(
      "顺序",
      memSearchSelect("memSearchSortOrder", [["desc", "降序"], ["asc", "升序"]], memSearchStr(draft.sortOrder) || MEM_SEARCH_DEFAULT_ORDER)
    )
  );

  root.append(grid);

  const toolbar = el("div", "toolbar");
  const runBtn = makeButton("搜索", () => memSearchApply(), "btn btn-primary");
  runBtn.id = "memSearchRun";
  const clearBtn = makeButton("清除", () => memSearchClear(), "btn btn-ghost");
  clearBtn.id = "memSearchClear";
  const hint = el("span", "mem-search-hint", "条件已改，点「搜索」生效");
  hint.id = "memSearchHint";
  hint.hidden = true;
  const note = el("span", "card-help", memSearchNoteText());
  note.id = "memSearchNote";
  toolbar.append(runBtn, clearBtn, hint, note);
  root.append(toolbar);

  // 任意输入/变更 → 落草稿 + 脏提示；不做局部重绘，避免打断输入
  root.addEventListener("input", () => memSearchAfterEdit());
  root.addEventListener("change", () => memSearchAfterEdit());
  const onEnter = (evt) => {
    if (evt.key !== "Enter") return;
    evt.preventDefault();
    memSearchApply();
  };
  for (const input of [queryInput, tagsInput, byInput]) input.addEventListener("keydown", onEnter);

  host.append(root);

  const results = el("div", "mem-search-results");
  results.id = "memSearchResults";
  host.append(results);
  memSearchRenderResults(results);

  memSearchUpdateDirtyHint();
}

// ── 目录选择器 ──

// 目录树 → 全部目录路径（根 "/" + 各级目录，按路径字母序）
function memSearchCollectDirs(tree) {
  const out = [];
  const seen = new Set();
  const visited = new Set();

  const push = (path) => {
    const norm = normalizeKbPath(path);
    if (seen.has(norm)) return;
    seen.add(norm);
    out.push(norm);
  };

  const walk = (node) => {
    if (!node || typeof node !== "object" || visited.has(node)) return;
    visited.add(node);
    const type = String(node.type || "dir");
    if (type === "file" || type === "entity") return;
    push(node.path || "/");
    for (const child of node.children || []) walk(child);
  };

  walk(tree);
  push("/");
  out.sort((a, b) => a.localeCompare(b));
  return out;
}

function memSearchPickDir() {
  try {
    const tree = state.kbTree;
    const empty = !tree || !Array.isArray(tree.children) || !tree.children.length;
    const body = el("div", "list-box");

    if (empty) {
      body.append(el("div", "mem-empty", "记忆库为空"));
      openModal("选择目录", [body]);
      return;
    }

    const dirs = memSearchCollectDirs(tree);
    const filter = el("input", "input");
    filter.placeholder = "过滤路径";
    const list = el("div", "list-box");
    const rows = [];
    for (const dir of dirs) {
      const row = el("div", "list-row clickable");
      row.append(el("span", "mono", dir));
      row.addEventListener("click", () => {
        closeModal();
        memSelectDir(dir);
      });
      rows.push({ dir, row });
    }

    const applyFilter = () => {
      const q = String(filter.value || "").trim().toLowerCase();
      list.textContent = "";
      let shown = 0;
      for (const item of rows) {
        if (q && item.dir.toLowerCase().indexOf(q) === -1) continue;
        list.append(item.row);
        shown += 1;
      }
      if (!shown) list.append(el("div", "mem-empty", "无匹配目录"));
    };
    filter.addEventListener("input", applyFilter);

    body.append(filter, list);
    applyFilter();
    openModal("选择目录", [body]);
  } catch (err) {
    console.error("[memory] 目录选择器打开失败", err);
    showBanner("error", "目录选择器打开失败: " + String((err && err.message) || err));
  }
}

// ── 结果区 ──

function memSearchFilterActive() {
  if (typeof memFilterActive === "function") return memFilterActive();
  const f = state.memFilter || {};
  return Boolean(
    memSearchStr(f.query).trim() ||
      (Array.isArray(f.tags) && f.tags.length) ||
      memSearchStr(f.dateFrom) ||
      memSearchStr(f.dateTo) ||
      memSearchStr(f.declaredBy).trim()
  );
}

function memSearchRenderResults(host) {
  if (!host) return;
  host.textContent = "";
  if (!memSearchFilterActive()) {
    host.append(el("div", "mem-empty", "设置条件后点「搜索」"));
    return;
  }
  if (typeof memRenderResultTable === "function") {
    memRenderResultTable(host);
  } else {
    host.append(el("div", "mem-error", "结果组件未加载（list.js 缺失）。"));
  }
}
