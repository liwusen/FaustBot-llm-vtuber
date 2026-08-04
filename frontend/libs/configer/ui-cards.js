// UI card builders for configer

function humanizeLabel(label) {
  const raw = String(label || "").trim();
  if (!raw) return "-";
  const aliases = {
    Meta: "相关信息",
    "Meta 字段": "相关信息",
    Variant: "类型",
    Slug: "标识",
    homepage: "主页",
  };
  if (aliases[raw]) return aliases[raw];
  return raw;
}

function formatScalar(v, label) {
  if (v === null || v === undefined || v === "") return "-";
  if (typeof v === "boolean") return v ? "是" : "否";
  const key = String(label || "").trim().toLowerCase();
  const value = String(v);
  if (key.includes("时间") || key.includes("updated") || key.includes("installed")) {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString();
  }
  if (key.includes("状态") || key.includes("status")) {
    const map = {
      true: "是",
      false: "否",
      unknown: "未知",
      running: "运行中",
      idle: "空闲",
      pending: "排队中",
      stopping: "停止中",
      stopped: "已停止",
      error: "异常",
      enabled: "已启用",
      disabled: "已禁用",
      on: "已开启",
      off: "已关闭",
    };
    if (map[value.toLowerCase()]) return map[value.toLowerCase()];
  }
  if (value === "true") return "是";
  if (value === "false") return "否";
  if (value === "unknown") return "未知";
  return value;
}

function createTableCell(content, className = "") {
  const td = el("td", className);
  if (content instanceof HTMLElement) {
    td.append(content);
  } else {
    td.textContent = String(content == null ? "-" : content);
  }
  return td;
}

function makeDataView(value, depth = 0) {
  if (value === null || value === undefined || typeof value !== "object") {
    return el("span", "kv-inline-value", formatScalar(value));
  }

  if (Array.isArray(value)) {
    const wrap = el("div", "kv-stack");
    if (!value.length) {
      wrap.append(el("div", "empty-state", "空数组"));
      return wrap;
    }
    value.forEach((item, index) => {
      const row = el("div", "kv-row");
      row.append(el("div", "kv-key", `第 ${index + 1} 项`));
      const valueWrap = el("div", "kv-value");
      valueWrap.append(makeDataView(item, depth + 1));
      row.append(valueWrap);
      wrap.append(row);
    });
    return wrap;
  }

  const entries = Object.entries(value);
  const table = el("table", "simple-table simple-table-compact");
  const tbody = el("tbody", "");
  if (!entries.length) {
    const tr = el("tr", "");
    const td = createTableCell("空对象", "table-empty");
    td.colSpan = 2;
    tr.append(td);
    tbody.append(tr);
    table.append(tbody);
    return table;
  }
  for (const [k, v] of entries) {
    const tr = el("tr", "");
    tr.append(createTableCell(humanizeLabel(k), "cell-label"));
    const valueCell = el("td", "cell-value");
    if (v !== null && typeof v === "object") {
      const details = el("details", "kv-details");
      const summary = el("summary", "kv-summary", Array.isArray(v) ? `数组 (${v.length})` : "展开详情");
      details.append(summary, makeDataView(v, depth + 1));
      valueCell.append(details);
    } else {
      valueCell.append(el("div", "kv-inline-value", formatScalar(v, k)));
    }
    tr.append(valueCell);
    tbody.append(tr);
  }
  table.append(tbody);
  return table;
}

function makeInfoCard(title, rows) {
  const card = el("article", "card");
  card.append(el("h3", "card-title", humanizeLabel(title)));
  const table = el("table", "simple-table info-table");
  const tbody = el("tbody", "");
  for (const row of rows) {
    const tr = el("tr", "");
    tr.append(createTableCell(humanizeLabel(row.label), "cell-label"));
    tr.append(createTableCell(formatScalar(row.value, row.label), "cell-value"));
    tbody.append(tr);
  }
  table.append(tbody);
  card.append(table);
  return card;
}

function makeTagListCard(title, tags) {
  const card = el("article", "card");
  card.append(el("h3", "card-title", humanizeLabel(title)));
  const wrap = el("div", "tag-list");
  const arr = Array.isArray(tags) ? tags : [];
  if (!arr.length) {
    wrap.append(el("div", "empty-state", "无"));
  } else {
    for (const t of arr) wrap.append(el("span", "tag-chip", String(t)));
  }
  card.append(wrap);
  return card;
}

// 分页/搜索的 UI 状态缓存（key = 卡片标题），组件重渲染（如 rerenderAiModule）后保留页码与搜索词
const _tableUIState = {};

// opts: { pageSize: 每页项数（>0 启用分页）, searchKey: 以该列索引为主键做简单搜索（>=0 启用） }
function makeSimpleTableCard(title, columns, rows, opts = {}) {
  const pageSize = Number(opts.pageSize) > 0 ? Number(opts.pageSize) : 0;
  const searchKey = (opts.searchKey !== undefined && opts.searchKey !== null && Number(opts.searchKey) >= 0)
    ? Number(opts.searchKey) : -1;
  const uiKey = String(title || "table");
  const ui = _tableUIState[uiKey] || (_tableUIState[uiKey] = { page: 1, query: "" });

  const card = el("article", "card full-span");
  const table = el("table", "simple-table");
  const thead = el("thead", "");
  const htr = el("tr", "");
  for (const col of columns) htr.append(el("th", "", humanizeLabel(col)));
  thead.append(htr);
  table.append(thead);
  const tbody = el("tbody", "");
  table.append(tbody);

  // 搜索框（列表标题栏上方）
  let searchBox = null;
  if (searchKey >= 0) {
    searchBox = el("input", "input table-search");
    searchBox.placeholder = `搜索${humanizeLabel(columns[searchKey])}...`;
    searchBox.value = ui.query;
    searchBox.addEventListener("input", () => {
      ui.query = String(searchBox.value || "").trim().toLowerCase();
      ui.page = 1;
      render();
    });
    card.append(searchBox);
  }
  card.append(el("h3", "card-title", humanizeLabel(title)));

  // 分页控件（表格下方）
  let prevBtn = null;
  let pageInfo = null;
  let nextBtn = null;
  if (pageSize > 0) {
    prevBtn = makeButton("上一页", () => {
      if (ui.page > 1) { ui.page--; render(); }
    });
    pageInfo = el("span", "card-help", "");
    nextBtn = makeButton("下一页", () => {
      ui.page++; render();
    });
    const pager = el("div", "toolbar table-pager");
    pager.append(prevBtn, pageInfo, nextBtn);
    card.append(table, pager);
  } else {
    card.append(table);
  }

  function filteredRows() {
    if (!ui.query) return rows;
    return rows.filter((row) => {
      const cell = row[searchKey];
      const text = cell instanceof HTMLElement ? (cell.textContent || "") : String(cell == null ? "" : cell);
      return text.toLowerCase().includes(ui.query);
    });
  }

  function render() {
    const list = filteredRows();
    const totalPages = pageSize > 0 ? Math.max(1, Math.ceil(list.length / pageSize)) : 1;
    if (ui.page > totalPages) ui.page = totalPages;
    if (ui.page < 1) ui.page = 1;
    const start = pageSize > 0 ? (ui.page - 1) * pageSize : 0;
    const pageRows = pageSize > 0 ? list.slice(start, start + pageSize) : list;

    tbody.innerHTML = "";
    if (!pageRows.length) {
      const tr = el("tr", "");
      const td = el("td", "table-empty", ui.query ? "无匹配结果" : "无数据");
      td.colSpan = columns.length;
      tr.append(td);
      tbody.append(tr);
    } else {
      for (const row of pageRows) {
        const tr = el("tr", "");
        row.forEach((c, index) => {
          const colLabel = Array.isArray(columns) ? columns[index] : "";
          const cellValue = c instanceof HTMLElement ? c : formatScalar(c, colLabel);
          tr.append(createTableCell(cellValue, index === 0 ? "cell-primary" : ""));
        });
        tbody.append(tr);
      }
    }
    if (prevBtn) {
      prevBtn.disabled = ui.page <= 1;
      nextBtn.disabled = ui.page >= totalPages;
      pageInfo.textContent = `第 ${ui.page} / ${totalPages} 页 · 共 ${list.length} 项`;
    }
  }

  render();
  return card;
}
