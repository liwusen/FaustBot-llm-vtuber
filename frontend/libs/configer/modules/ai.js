// AI 服务商页：分组布局（组目录 + 组卡片 + 字段行）
// 分组与行顺序来自 constants.js 的 AI_GROUPS（那也是本页的渲染白名单）。
// 本文件只负责「页面长什么样」；服务商/模型的数据操作与弹窗在 providerWizard.js。
// 所有本地改动都只写 state，随窗口顶部「保存」统一提交；重渲染一律走
// rerenderAiModule()，不重新拉后端，避免把未保存的服务商改动冲掉。

const AI_TABLE_PAGE_SIZE = 10;

// ── 组目录（跳转到对应分组） ──

function _aiDirectory(groups) {
  const nav = el("nav", "ai-dir");
  nav.setAttribute("aria-label", "AI 服务商页分组");
  for (const spec of groups) {
    const chip = el("button", "ai-dir-chip", spec.title);
    chip.type = "button";
    chip.addEventListener("click", () => {
      const target = document.getElementById(`group-${spec.id}`);
      if (!target) return;
      target.scrollIntoView({ block: "start", behavior: "smooth" });
      for (const node of nav.querySelectorAll(".ai-dir-chip")) node.classList.remove("active");
      chip.classList.add("active");
    });
    nav.append(chip);
  }
  return nav;
}

// ── 当前主模型摘要（组头右侧） ──

function _fillMainModelLine(container) {
  container.innerHTML = "";
  const spec = state.mainModel || "";
  if (!spec) {
    container.append(el("span", "ai-main-model-none", "还没有选择主模型"));
    return;
  }
  const idx = spec.indexOf("::");
  const providerName = idx >= 0 ? spec.slice(0, idx) : "";
  const modelName = idx >= 0 ? spec.slice(idx + 2) : spec;
  const provider = _findProvider(providerName);
  const decision = _formatDecision(provider, modelName);
  container.append(el("span", "ai-main-model-label", "主模型"));
  container.append(el("span", "ai-main-model-name", `${providerName} · ${modelName}`));
  container.append(el("span", "format-badge", _formatText(decision.format, decision.source)));
  const ctx = provider && provider.model_context_lengths ? provider.model_context_lengths[modelName] : null;
  container.append(el("span", "ai-main-model-meta", `上下文 ${_formatContext(ctx)}`));
  const tools = (provider && provider.model_server_tools && provider.model_server_tools[modelName]) || [];
  if (tools.length) container.append(el("span", "tag-chip", "服务端联网搜索"));
}

// ── 分组小标题（服务商 / 模型） ──

function _aiSubhead(title, extra) {
  const head = el("div", "ai-subhead");
  head.append(el("span", "ai-subhead-title", title));
  if (extra) head.append(extra);
  return head;
}

// ── 服务商表 ──

function _aiDeleteProvider(provider) {
  if (!window.confirm(`删除服务商「${provider.name}」？它的模型与相关设置会一起从待保存改动里移除。`)) return;
  state.providers = (state.providers || []).filter((p) => p.name !== provider.name);
  if (state.mainModel && state.mainModel.startsWith(`${provider.name}::`)) state.mainModel = "";
  state.subagentModels = (state.subagentModels || []).filter((s) => !s.startsWith(`${provider.name}::`));
  _markProvidersDirty();
  _markModelsDirty();
  rerenderAiModule();
  showBanner("success", `已删除服务商 ${provider.name}（点窗口顶部「保存」写入配置）`);
}

function _aiProvidersTable(providers) {
  const table = el("table", "simple-table ai-table");
  const thead = el("thead");
  const htr = el("tr");
  for (const [label, title] of [
    ["服务商", ""],
    ["接口地址", ""],
    ["密钥", "只显示有没有配，不显示具体值"],
    ["模型", ""],
    ["操作", ""],
  ]) {
    const th = el("th", "", label);
    if (title) th.title = title;
    htr.append(th);
  }
  thead.append(htr);

  const tbody = el("tbody");
  for (const provider of providers) {
    const tr = el("tr");

    const nameCell = el("td", "cell-primary");
    const nameWrap = el("div", "ai-provider-cell");
    nameWrap.append(el("span", "ai-provider-name", provider.name));
    const catalogId = provider.catalog_provider_id;
    nameWrap.append(el("span", catalogId ? "tag-chip" : "tag-chip tag-chip-muted",
      catalogId ? `目录 ${catalogId}` : "目录未识别"));
    nameCell.append(nameWrap);

    const urlCell = el("td");
    const url = el("span", "ai-url", provider.base_url || "—");
    url.title = provider.base_url || "";
    urlCell.append(url);

    const keyCell = el("td");
    keyCell.append(el("span", provider.key ? "ai-flag ok" : "ai-flag warn", _keyStatus(provider)));

    const countCell = el("td");
    const count = (provider.models || []).length;
    countCell.append(el("span", count ? "" : "ai-muted", count ? `${count} 个` : "未拉取"));

    const opsCell = el("td", "ai-ops-cell");
    const ops = el("div", "toolbar compact");
    ops.append(
      makeButton("编辑", () => openProviderModal(provider), "btn btn-ghost"),
      makeButton("删除", () => _aiDeleteProvider(provider), "btn btn-ghost btn-danger")
    );
    opsCell.append(ops);

    tr.append(nameCell, urlCell, keyCell, countCell, opsCell);
    tbody.append(tr);
  }
  table.append(thead, tbody);
  return table;
}

// ── 模型表（搜索 + 分页；主模型单选 / 子模型多选） ──

function _aiModelCell(entry) {
  const cell = el("td", "cell-primary");
  const wrap = el("div", "ai-model-spec");
  wrap.append(el("span", "ai-model-provider", entry.provider));
  wrap.append(el("span", "ai-model-sep", "::"));
  wrap.append(el("span", "ai-model-name", entry.model));
  cell.append(wrap);
  return cell;
}

function _aiFormatCell(provider, modelName) {
  const decision = _formatDecision(provider, modelName);
  const cell = el("td");
  const badge = el("span",
    decision.source === "error" ? "format-badge format-badge-error" : "format-badge",
    _formatText(decision.format, decision.source));
  badge.title = decision.source === "manual"
    ? "在「设置」里手动指定"
    : "由模型目录、接口地址或模型类型自动判断；不对时可在「设置」里覆盖";
  cell.append(badge);
  return cell;
}

function _aiContextCell(provider, modelName) {
  const cell = el("td");
  const exact = provider && provider.model_context_lengths
    ? provider.model_context_lengths[modelName] : null;
  const span = el("span", exact ? "" : "ai-muted", _formatContext(exact));
  span.title = exact ? `${exact} token` : "未配置，后端按 128000 计算";
  cell.append(span);
  return cell;
}

function _aiModelsBlock(mainMeta) {
  const wrap = el("div", "ai-models");

  const search = el("input", "input ai-search");
  search.type = "search";
  search.placeholder = "搜索模型或服务商…";
  search.value = state.aiTable.query || "";
  wrap.append(_aiSubhead("模型", search));

  const table = el("table", "simple-table ai-table");
  const thead = el("thead");
  const htr = el("tr");
  const columns = [
    { label: "模型", title: "「服务商::模型名」就是它在配置里的写法" },
    { label: "接口格式", title: "由模型目录、接口地址或模型类型自动判断；不对时可在「设置」里覆盖" },
    { label: "上下文", title: "这个模型最多能记住多少 token" },
    { label: "主模型", title: "AI 对话与工具调用使用它，只能选一个", cls: "ai-pick-cell" },
    { label: "子模型", title: "允许作为子任务代理使用，可以多选", cls: "ai-pick-cell" },
    { label: "操作", title: "" },
  ];
  for (const col of columns) {
    const th = el("th", col.cls || "", col.label);
    if (col.title) th.title = col.title;
    htr.append(th);
  }
  thead.append(htr);
  const tbody = el("tbody");
  table.append(thead, tbody);
  wrap.append(table);

  const pager = el("div", "toolbar table-pager");
  const prevBtn = makeButton("上一页", () => { state.aiTable.page -= 1; render(); });
  const pageInfo = el("span", "card-help", "");
  const nextBtn = makeButton("下一页", () => { state.aiTable.page += 1; render(); });
  pager.append(prevBtn, pageInfo, nextBtn);
  wrap.append(pager);

  const entries = _allModelEntries();

  function render() {
    const query = String(state.aiTable.query || "").trim().toLowerCase();
    const list = query
      ? entries.filter((e) => `${e.provider} ${e.model}`.toLowerCase().includes(query))
      : entries;
    const totalPages = Math.max(1, Math.ceil(list.length / AI_TABLE_PAGE_SIZE));
    if (state.aiTable.page > totalPages) state.aiTable.page = totalPages;
    if (state.aiTable.page < 1) state.aiTable.page = 1;
    const start = (state.aiTable.page - 1) * AI_TABLE_PAGE_SIZE;
    const pageRows = list.slice(start, start + AI_TABLE_PAGE_SIZE);

    tbody.innerHTML = "";
    if (!pageRows.length) {
      const tr = el("tr");
      const td = el("td", "table-empty", query ? "没有匹配的模型" : "还没有模型：在服务商里点「编辑 › 从服务商拉取模型」");
      td.colSpan = 6;
      tr.append(td);
      tbody.append(tr);
    }
    for (const entry of pageRows) {
      const provider = _findProvider(entry.provider);
      const tr = el("tr");

      const mainCell = el("td", "ai-pick-cell");
      const radio = document.createElement("input");
      radio.type = "radio";
      radio.className = "provider-pick";
      radio.name = "ai-main-model";
      radio.checked = state.mainModel === entry.spec;
      radio.title = "设为主模型";
      radio.setAttribute("aria-label", `把 ${entry.spec} 设为主模型`);
      radio.addEventListener("change", () => {
        state.mainModel = entry.spec;
        _markModelsDirty();
        _fillMainModelLine(mainMeta);
      });
      mainCell.append(radio);

      const subCell = el("td", "ai-pick-cell");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.className = "provider-pick";
      checkbox.checked = new Set(state.subagentModels || []).has(entry.spec);
      checkbox.title = "允许作为子模型";
      checkbox.setAttribute("aria-label", `允许 ${entry.spec} 作为子模型`);
      checkbox.addEventListener("change", () => {
        const next = new Set(state.subagentModels || []);
        if (checkbox.checked) next.add(entry.spec);
        else next.delete(entry.spec);
        state.subagentModels = [...next];
        _markModelsDirty();
      });
      subCell.append(checkbox);

      const opsCell = el("td", "ai-ops-cell");
      const ops = el("div", "toolbar compact");
      ops.append(makeButton("设置", () => openModelModal(entry), "btn btn-ghost"));
      opsCell.append(ops);

      tr.append(
        _aiModelCell(entry),
        _aiFormatCell(provider, entry.model),
        _aiContextCell(provider, entry.model),
        mainCell,
        subCell,
        opsCell,
      );
      tbody.append(tr);
    }

    pager.classList.toggle("hidden", totalPages <= 1);
    prevBtn.disabled = state.aiTable.page <= 1;
    nextBtn.disabled = state.aiTable.page >= totalPages;
    pageInfo.textContent = `第 ${state.aiTable.page} / ${totalPages} 页 · 共 ${list.length} 个模型`;
  }

  search.addEventListener("input", () => {
    state.aiTable.query = search.value;
    state.aiTable.page = 1;
    render();
  });

  render();
  return wrap;
}

// ── 批量操作（收起，避免和「日常要做的事」抢注意力） ──

async function _reloadProvidersFromBackend() {
  const pr = await cfgApi("GET", "/faust/admin/providers");
  state.providers = pr.providers || [];
  state.mainModel = pr.main_model || "";
  state.subagentModels = pr.subagent_models || [];
  state.resolvedFormats = pr.resolved_formats || {};
  state.providerCatalog = pr.catalog || {};
  _markProvidersDirty();
}

function _aiBulkActions() {
  const note = el("p", "form-hint",
    "这几个动作会直接写配置，通常只在新增服务商后做一次。日常改服务商请用上面的「编辑」。");

  const bar = el("div", "toolbar");
  bar.append(
    makeButton("拉取没加载模型的服务商", async () => {
      const pending = (state.providers || []).filter((p) => !(p.models && p.models.length));
      if (!pending.length) { showBanner("info", "所有服务商都已经有模型了"); return; }
      let ok = 0, fail = 0;
      for (const provider of pending) {
        try {
          const r = await cfgApi("POST", `/faust/admin/providers/${encodeURIComponent(provider.name)}/load-models`);
          if ((r.models || []).length) ok++; else fail++;
        } catch (_e) {
          fail++;
        }
      }
      await _reloadProvidersFromBackend();
      rerenderAiModule();
      showBanner(fail ? "error" : "success", `拉取完成：成功 ${ok} 个，失败 ${fail} 个（保存后生效）`);
    }, "btn btn-secondary"),

    makeButton("补全上下文长度", async () => {
      const all = state.providers || [];
      if (!all.length) { showBanner("info", "还没有服务商"); return; }
      let ok = 0, fail = 0;
      for (const provider of all) {
        try {
          await cfgApi("POST", `/faust/admin/providers/${encodeURIComponent(provider.name)}/load-models`);
          ok++;
        } catch (_e) {
          fail++;
        }
      }
      await _reloadProvidersFromBackend();
      const filled = (state.providers || []).reduce(
        (n, p) => n + Object.keys(p.model_context_lengths || {}).length, 0);
      rerenderAiModule();
      showBanner(
        fail ? "error" : "success",
        `上下文长度已补全：成功 ${ok} 个服务商，失败 ${fail} 个；共 ${filled} 个模型有明确长度（保存后生效）`,
      );
    }, "btn btn-secondary"),

    makeButton("更新模型目录", async () => {
      try {
        const r = await cfgApi("POST", "/faust/admin/models-dev/refresh");
        await _reloadProvidersFromBackend();
        rerenderAiModule();
        showBanner(
          r.status === "ok" ? "success" : "error",
          `模型目录已更新：${r.providers || 0} 个服务商，识别出 ${r.matched || 0} 个`
            + (r.error ? `；错误：${r.error}` : ""),
        );
      } catch (e) {
        showBanner("error", "更新目录失败: " + (e && e.detail ? e.detail : String(e)));
      }
    }, "btn btn-ghost"),
  );

  return makeFieldFold("高级操作", [note, bar], "ai-bulk", {});
}

// ── 「对话模型服务商」组 ──

function _aiProvidersGroup(spec) {
  const providers = state.providers || [];
  const mainMeta = el("div", "ai-main-model");
  _fillMainModelLine(mainMeta);

  const group = makeFieldGroup({
    id: spec.id,
    title: spec.title,
    desc: spec.desc,
    meta: mainMeta,
  }, []);

  if (!providers.length) {
    const empty = el("div", "ai-empty");
    empty.append(el("p", "ai-empty-text",
      "还没有服务商。填三项就能开始用：名称、接口地址、密钥；模型可以一键拉取。"));
    empty.append(makeButton("添加服务商", () => openProviderModal(null), "btn btn-primary"));
    group.append(empty);
    return group;
  }

  const subhead = _aiSubhead("服务商", el("span", "ai-subhead-count", `${providers.length} 个`));
  subhead.append(makeButton("添加服务商", () => openProviderModal(null), "btn btn-primary ai-subhead-btn"));
  group.append(subhead);
  group.append(_aiProvidersTable(providers));
  group.append(_aiModelsBlock(mainMeta));
  group.append(_aiBulkActions());
  return group;
}

// ── 其余分组（字段行 + 组内高级折叠） ──

function _aiFieldGroup(spec) {
  const rows = [];
  const advancedRows = [];
  for (const rowSpec of spec.keys || []) {
    const scope = rowSpec.scope || "public";
    const bucket = state.config[scope] || {};
    if (!(rowSpec.key in bucket)) continue;          // 该键在当前配置里不存在
    if (rowSpec.gated && !state.config.public[rowSpec.gated]) continue;
    const row = makeFieldRow(scope, rowSpec.key, {
      rerender: rerenderAiModule,
      readonly: spec.id === "agent" || rowSpec.readonly === true,
    });
    if (rowSpec.advanced) advancedRows.push(row);
    else rows.push(row);
  }
  if (!rows.length && !advancedRows.length) return null;

  if (advancedRows.length) {
    const forceOpen = advancedRows.some((row) => row.classList.contains("dirty"));
    rows.push(makeFieldFold(
      `高级（${advancedRows.length} 项）`,
      advancedRows,
      `ai-adv-${spec.id}`,
      { forceOpen },
    ));
  }

  const dirtyCount = (spec.keys || [])
    .filter((row) => state.dirty[row.scope || "public"].has(row.key)).length;

  return makeFieldGroup({
    id: spec.id,
    title: spec.title,
    desc: spec.desc,
    meta: dirtyCount ? `${dirtyCount} 项已修改` : null,
    actions: spec.id === "agent"
      ? [makeButton("前往「角色」模块", () => {
        const btn = document.querySelector('[data-module="agent"]');
        if (btn) btn.click();
      }, "btn btn-secondary")]
      : null,
    foot: spec.footer || null,
    folded: !!spec.folded,
    foldLabel: `展开（${rows.length} 项）`,
    foldKey: `ai-group-${spec.id}`,
  }, rows);
}

// ── 页面入口 ──

function renderAiModule() {
  const groups = window.AI_GROUPS || [];
  if (!groups.length) {
    addSection("提示", [el("div", "empty-state", "分组配置没有加载（AI_GROUPS 缺失）。")]);
    return;
  }

  // 白名单里没被任何分组覆盖的键：只告警，不静默丢弃（另有静态测试守护）
  const covered = new Set();
  for (const spec of groups) for (const row of spec.keys || []) covered.add(row.key);
  const missing = [...(window.AI_PUBLIC_KEYS || []), ...(window.AI_PRIVATE_KEYS || [])]
    .filter((key) => !covered.has(key));
  if (missing.length) console.warn("[ai] 这些 AI 配置键没有分组，界面上不会出现:", missing);

  appendToActiveModule(_aiDirectory(groups));
  for (const spec of groups) {
    if (spec.custom === "providers") {
      appendToActiveModule(_aiProvidersGroup(spec));
    } else {
      const group = _aiFieldGroup(spec);
      if (group) appendToActiveModule(group);
    }
  }
}
