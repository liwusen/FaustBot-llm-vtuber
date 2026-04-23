// UI card builders for configer

function formatScalar(v) {
  if (v === null || v === undefined) return "-";
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function makeDataView(value, depth = 0) {
  if (value === null || value === undefined || typeof value !== "object") {
    return el("span", "mono", formatScalar(value));
  }

  if (Array.isArray(value)) {
    const wrap = el("div", "kv-group");
    if (!value.length) {
      wrap.append(el("div", "empty-state", "空数组"));
      return wrap;
    }
    for (const item of value) {
      const row = el("div", "kv-row");
      row.append(makeDataView(item, depth + 1));
      wrap.append(row);
    }
    return wrap;
  }

  const entries = Object.entries(value);
  const grid = el("div", "kv-grid");
  if (!entries.length) {
    grid.append(el("div", "empty-state", "空对象"));
    return grid;
  }
  for (const [k, v] of entries) {
    const item = el("div", "kv-item");
    item.append(el("div", "kv-key", k));
    if (v !== null && typeof v === "object") {
      const details = el("details", "kv-details");
      const summary = el("summary", "kv-summary", Array.isArray(v) ? `数组 (${v.length})` : "对象");
      details.append(summary, makeDataView(v, depth + 1));
      item.append(details);
    } else {
      item.append(el("div", "kv-value", formatScalar(v)));
    }
    grid.append(item);
  }
  return grid;
}

function makeInfoCard(title, rows) {
  const card = el("article", "card");
  card.append(el("h3", "card-title", title));
  const grid = el("div", "info-grid");
  for (const row of rows) {
    const item = el("div", "info-item");
    item.append(el("div", "info-key", String(row.label || "-")));
    item.append(el("div", "info-value", formatScalar(row.value)));
    grid.append(item);
  }
  card.append(grid);
  return card;
}

function makeTagListCard(title, tags) {
  const card = el("article", "card");
  card.append(el("h3", "card-title", title));
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

function makeSimpleTableCard(title, columns, rows) {
  const card = el("article", "card full-span");
  card.append(el("h3", "card-title", title));
  const table = el("table", "simple-table");
  const thead = el("thead", "");
  const htr = el("tr", "");
  for (const col of columns) htr.append(el("th", "", col));
  thead.append(htr);
  table.append(thead);
  const tbody = el("tbody", "");
  if (!rows.length) {
    const tr = el("tr", "");
    const td = el("td", "", "无数据");
    td.colSpan = columns.length;
    tr.append(td);
    tbody.append(tr);
  } else {
    for (const row of rows) {
      const tr = el("tr", "");
      for (const c of row) tr.append(el("td", "", formatScalar(c)));
      tbody.append(tr);
    }
  }
  table.append(tbody);
  card.append(table);
  return card;
}
