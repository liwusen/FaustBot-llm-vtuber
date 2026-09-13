// Memory detail pane: overview / tags / relations for the selected dir, file or entity.
// Entry: memRenderDetail(host) — called by explorer.js memRenderDetailHost(); state is read here, never owned.

var MEM_DETAIL_ENTITY_DESC_MAX = 80;

// ── 通用小工具 ──

function memDetailMsg(err) {
  return String((err && err.message) || err || "未知错误");
}

function memDetailDash(value) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "是" : "否";
  const text = String(value);
  return text ? text : "—";
}

// ISO 字符串补 Z 后本地化；解析失败原样返回
function memDetailFmtTime(raw) {
  const text = String(raw || "").trim();
  if (!text) return "—";
  const hasZone = text.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(text);
  const parsed = new Date(hasZone ? text : text + "Z");
  if (isNaN(parsed.getTime())) return text;
  return parsed.toLocaleString();
}

function memDetailTimeValue(raw) {
  const text = String(raw || "").trim();
  if (!text) return 0;
  const hasZone = text.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(text);
  const parsed = new Date(hasZone ? text : text + "Z");
  return isNaN(parsed.getTime()) ? 0 : parsed.getTime();
}

function memDetailKv(label, value) {
  const row = el("div", "mem-kv");
  row.append(el("span", "mem-kv-key", label), el("span", "mem-kv-value", memDetailDash(value)));
  return row;
}

function memDetailSection(title) {
  const box = el("div", "mem-detail-section");
  if (title) box.append(el("div", "mem-detail-title", title));
  return box;
}

function memDetailHeading(host, name) {
  host.append(el("div", "mem-detail-title", name));
}

function memDetailNodeName(path, fallback) {
  const node = memFindNode(path);
  if (node && node.name) return String(node.name);
  const tail = String(path || "").split("/").pop();
  return tail || String(fallback || path || "");
}

// 重绘令牌：每次 memRenderDetail 自增，异步回调落地前校验，避免覆盖新选中项
function memDetailNextToken() {
  state._memDetailSeq = Number(state._memDetailSeq || 0) + 1;
  return state._memDetailSeq;
}

function memDetailTokenValid(host, token) {
  if (token !== state._memDetailSeq) return false;
  if (!host || !host.isConnected) return false;
  return document.getElementById("memDetail") === host;
}

function memDetailIsFilePath(path) {
  const sel = state.memSel || {};
  return sel.kind === "file" && String(sel.path || "") === String(path || "");
}

function memDetailIsEntity(entityId) {
  const sel = state.memSel || {};
  return sel.kind === "entity" && String(sel.entityId || "") === String(entityId || "");
}

// ── 概览：目录 ──

function memDetailSubtreeStats(root) {
  const stats = { dirs: 0, files: 0, chunks: 0, updatedAt: "" };
  if (!root) return stats;
  const walk = (node) => {
    for (const child of node.children || []) {
      const type = String(child.type || "dir");
      if (type === "entity") continue;
      if (type === "file") {
        stats.files += 1;
        stats.chunks += Number(child.chunk_count || 0);
        const stamp = String(child.updated_at || "");
        if (stamp && memDetailTimeValue(stamp) >= memDetailTimeValue(stats.updatedAt)) stats.updatedAt = stamp;
      } else {
        stats.dirs += 1;
        walk(child);
      }
    }
  };
  walk(root);
  return stats;
}

function memRenderDirOverview(host, path) {
  const node = memFindNode(path);
  const stats = memDetailSubtreeStats(node);
  const section = memDetailSection("概览");
  section.append(memDetailKv("路径", path));
  section.append(memDetailKv("子目录", stats.dirs));
  section.append(memDetailKv("文件", stats.files));
  section.append(memDetailKv("索引块", stats.chunks));
  section.append(memDetailKv("最近更新", memDetailFmtTime(stats.updatedAt)));
  const description = node ? String(node.description || "") : "";
  if (description) section.append(memDetailKv("描述", description));
  host.append(section);
}

// ── 概览：文件 ──

function memDetailFileOverview(path, meta) {
  const section = memDetailSection("概览");
  section.append(memDetailKv("路径", path));
  section.append(memDetailKv("更新时间", memDetailFmtTime(meta.updated_at)));
  section.append(memDetailKv("创建者", meta.declared_by));
  section.append(memDetailKv("索引块", meta.chunk_count));
  section.append(memDetailKv("权重", meta.score_patch));
  section.append(memDetailKv("索引状态", meta.indexed ? "已索引" : "未索引"));
  if (meta.content_type) section.append(memDetailKv("类型", meta.content_type));
  if (meta.description) section.append(memDetailKv("描述", meta.description));

  const shown = ["path", "updated_at", "declared_by", "chunk_count", "score_patch", "indexed", "content_type", "description", "tags"];
  const extras = el("div", "mem-detail-section");
  extras.hidden = true;
  const extraKeys = Object.keys(meta).filter((key) => shown.indexOf(key) < 0);
  for (const key of extraKeys) {
    const value = meta[key];
    if (value === null || value === undefined) continue;
    if (typeof value === "object") continue;
    extras.append(memDetailKv(key, value));
  }
  let expanded = false;
  const toggle = makeButton("展开全部", () => {
    expanded = !expanded;
    extras.hidden = !expanded;
    toggle.textContent = expanded ? "收起" : "展开全部";
  }, "btn btn-quiet");
  section.append(toggle, extras);
  return section;
}

// ── 标签段（仅文件） ──

function memDetailReadTags(path) {
  const detail = state.memDetail;
  if (detail && detail.meta && Array.isArray(detail.meta.tags)) return detail.meta.tags.map(String);
  const node = memFindNode(path);
  return node && Array.isArray(node.tags) ? node.tags.map(String) : [];
}

async function memDetailWriteTags(path, nextTags, errBox) {
  try {
    await cfgApi("POST", "/faust/memory/tags", { path, tags: nextTags });
  } catch (err) {
    console.error("[memory] 保存标签失败", path, err);
    const text = "保存标签失败：" + memDetailMsg(err);
    if (errBox) {
      errBox.hidden = false;
      errBox.textContent = text;
    }
    showBanner("error", text);
    return false;
  }
  if (state.memDetail && state.memDetail.meta) state.memDetail.meta.tags = nextTags.slice();
  const node = memFindNode(path);
  if (node) node.tags = nextTags.slice();
  if (errBox) {
    errBox.hidden = true;
    errBox.textContent = "";
  }
  memReload();
  return true;
}

function memDetailTagSection(host, path) {
  const section = memDetailSection("标签");
  const wrap = el("div", "mem-tag-edit");
  const errBox = el("div", "mem-error");
  errBox.hidden = true;
  let tags = memDetailReadTags(path);

  const addTag = (tag) => {
    const value = String(tag || "").trim();
    if (!value || tags.indexOf(value) >= 0) return;
    memDetailWriteTags(path, tags.concat([value]), errBox);
  };

  const draw = () => {
    wrap.innerHTML = "";
    for (const tag of tags) {
      const chip = el("span", "mem-chip");
      chip.append(el("span", "", tag));
      const close = el("button", "mem-chip-close", "\u00D7");
      close.type = "button";
      close.title = "移除该标签";
      close.addEventListener("click", () => {
        memDetailWriteTags(path, tags.filter((item) => item !== tag), errBox);
      });
      chip.append(close);
      wrap.append(chip);
    }
    const input = el("input", "input");
    input.placeholder = "新标签";
    const add = makeButton("添加", () => {
      const value = String(input.value || "").trim();
      if (!value) {
        errBox.hidden = false;
        errBox.textContent = "请输入标签名";
        return;
      }
      addTag(value);
    }, "btn btn-quiet");
    input.addEventListener("keydown", (evt) => {
      if (evt.key === "Enter") {
        evt.preventDefault();
        add.click();
      }
    });
    wrap.append(input, add);
  };

  draw();
  if (!tags.length) section.append(el("div", "card-help", "暂无标签"));
  section.append(wrap, errBox);
  host.append(section);
}

// ── 关联段：实体列表 ──

function memDetailEntityRow(item, onPick) {
  const type = String(item.entity_type || item.type || "custom");
  const row = el("div", "mem-entity-row");
  row.title = "在图谱中查看该实体";
  const head = el("div");
  const dot = el("span", "graph-legend-dot");
  dot.style.background = (typeof GRAPH_COLORS !== "undefined" && GRAPH_COLORS[type]) || "#95a5a6";
  head.append(dot, el("span", "mono", "[" + type + "] " + String(item.name || "(未命名)")));
  row.append(head);
  const description = String(item.description || "");
  if (description) {
    row.append(el("div", "card-help", description.length > MEM_DETAIL_ENTITY_DESC_MAX ? description.slice(0, MEM_DETAIL_ENTITY_DESC_MAX) + "\u2026" : description));
  }
  row.addEventListener("click", () => onPick(String(item.id || "")));
  return row;
}

async function memDetailLoadEntities(host, section, path, token) {
  const status = el("div", "card-help", "加载关联实体\u2026");
  section.append(status);
  let items = [];
  try {
    const resp = await cfgApi("GET", "/faust/memory/graph/entity-children", null, { path });
    items = Array.isArray(resp && resp.items) ? resp.items : [];
  } catch (err) {
    console.error("[memory] 加载关联实体失败", path, err);
    if (!memDetailTokenValid(host, token) || !memDetailIsFilePath(path)) return;
    status.replaceWith(el("div", "mem-error", "加载关联实体失败：" + memDetailMsg(err)));
    return;
  }
  if (!memDetailTokenValid(host, token) || !memDetailIsFilePath(path)) return;
  status.remove();
  if (!items.length) {
    section.append(el("div", "card-help", "暂无关联实体"));
    return;
  }
  for (const item of items) {
    section.append(memDetailEntityRow(item, (id) => {
      if (!id) return;
      state.memView = "graph";
      memSelectEntity(id);
    }));
  }
}

// ── 图片 / 正文预览（仅文件） ──

function memDetailImageDataUrl(resp) {
  const base64 = String((resp && resp.content_base64) || "");
  if (!base64) return "";
  return "data:" + String((resp && resp.content_type) || "image/png") + ";base64," + base64;
}

function memDetailOpenImageModal(src) {
  const full = el("img", "mem-image-full");
  full.alt = "图片预览";
  full.src = src;
  openModal("图片预览", [full]);
}

async function memDetailLoadImage(host, section, path, token) {
  const status = el("div", "card-help", "加载图片\u2026");
  section.append(status);
  try {
    const resp = await cfgApi("GET", "/faust/memory/attachment", null, { path });
    if (!memDetailTokenValid(host, token) || !memDetailIsFilePath(path)) return;
    const src = memDetailImageDataUrl(resp);
    if (!src) {
      status.replaceWith(el("div", "mem-error", "图片内容为空"));
      return;
    }
    const img = el("img", "mem-image");
    img.alt = path;
    img.src = src;
    img.title = "点击查看大图";
    img.addEventListener("click", () => memDetailOpenImageModal(src));
    status.replaceWith(img);
  } catch (err) {
    console.error("[memory] 加载图片失败", path, err);
    if (!memDetailTokenValid(host, token) || !memDetailIsFilePath(path)) return;
    status.replaceWith(el("div", "mem-error", "图片加载失败：" + memDetailMsg(err)));
  }
}

// ── 概览：实体 ──

function memDetailEntityOverview(detail) {
  const section = memDetailSection("概览");
  const type = String(detail.entity_type || "custom");
  const nameRow = el("div", "mem-kv");
  const nameValue = el("span", "mem-kv-value");
  const dot = el("span", "graph-legend-dot");
  dot.style.background = (typeof GRAPH_COLORS !== "undefined" && GRAPH_COLORS[type]) || "#95a5a6";
  nameValue.append(dot, document.createTextNode(" " + String(detail.name || "(未命名)")));
  nameRow.append(el("span", "mem-kv-key", "名称"), nameValue);
  section.append(nameRow);
  section.append(memDetailKv("类型", type));
  section.append(memDetailKv("关系数", detail.relations_count));
  section.append(memDetailKv("创建时间", detail.created_at ? memDetailFmtTime(detail.created_at) : "—"));
  if (detail.description) section.append(memDetailKv("描述", detail.description));

  const properties = detail.properties || {};
  const keys = Object.keys(properties);
  if (keys.length) {
    section.append(el("div", "mem-detail-title", "属性"));
    for (const key of keys) section.append(memDetailKv(key, properties[key]));
  }

  const files = Array.isArray(detail.linked_files) ? detail.linked_files : [];
  if (files.length) {
    section.append(makeButton("查看关联文件 " + files.length + " 个", () => memShowLinkedFiles(files), "btn btn-quiet"));
  }
  return section;
}

async function memRenderEntityDetail(host, entityId, token) {
  host.append(el("div", "mem-empty", "加载中\u2026"));
  let payload = null;
  try {
    payload = await cfgApi("GET", "/faust/memory/graph/entity-detail", null, { entity_id: entityId });
  } catch (err) {
    console.error("[memory] 加载实体详情失败", entityId, err);
    if (!memDetailTokenValid(host, token) || !memDetailIsEntity(entityId)) return;
    host.innerHTML = "";
    host.append(el("div", "mem-error", "加载实体详情失败：" + memDetailMsg(err)));
    return;
  }
  if (!memDetailTokenValid(host, token) || !memDetailIsEntity(entityId)) return;
  const detail = (payload && payload.detail) || null;
  host.innerHTML = "";
  if (!detail) {
    host.append(el("div", "mem-error", "实体不存在：" + entityId));
    return;
  }
  memDetailHeading(host, String(detail.name || entityId));
  host.append(memDetailEntityOverview(detail));
}

// ── 入口 ──

async function memRenderDetail(host) {
  if (!host) return;
  const token = memDetailNextToken();
  const sel = state.memSel || {};
  if (sel.kind === "entity" && sel.entityId) {
    await memRenderEntityDetail(host, String(sel.entityId), token);
    return;
  }
  if (sel.kind === "file" && sel.path) {
    const path = normalizeKbPath(sel.path);
    memDetailHeading(host, memDetailNodeName(path));
    const detail = state.memDetail;
    if (!detail) {
      host.append(el("div", "mem-empty", "加载中\u2026"));
      return;
    }
    if (detail.error) {
      host.append(el("div", "mem-error", "读取文件失败：" + String(detail.error)));
      return;
    }
    const meta = detail.meta || {};
    host.append(memDetailFileOverview(path, meta));
    memDetailTagSection(host, path);
    const relationSection = memDetailSection("关联");
    host.append(relationSection);
    memDetailLoadEntities(host, relationSection, path, token);
    const isImage = String(meta.content_type || "").indexOf("image/") === 0 || memIsImagePath(path);
    if (isImage) {
      const imageSection = memDetailSection("图片");
      host.append(imageSection);
      memDetailLoadImage(host, imageSection, path, token);
    }
    return;
  }
  if (sel.kind === "dir" && sel.path) {
    const path = normalizeKbPath(sel.path);
    memDetailHeading(host, memDetailNodeName(path, "/"));
    memRenderDirOverview(host, path);
    return;
  }
  host.append(el("div", "mem-empty", "未选中节点"));
}
