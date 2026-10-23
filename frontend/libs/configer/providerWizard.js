// Provider 管理向导（AI 服务商模块）
// 独立文件：Providers 列表 + Models 列表（主模型单选 / Subagent 多选列），
// 添加/编辑 Provider 与模型均通过 Modal 完成；所有变更写入 state 的 dirty
// 标志，随"保存"按钮统一提交（无单独保存按钮）。
// 依赖（全局，已在 config-window.html 中先加载）：
//   field-render.js（renderConfigModule 调用 renderProviderWizard）、
//   ui-cards.js（makeSimpleTableCard）、dom-utils.js（el/makeButton）、
//   modal.js（openModal/closeModal）、state-core.js（state/refreshDirtyUI/cfgApi）。

// ── Provider 管理向导（AI 服务商模块顶部） ──
// 设计：Providers 列表 + Models 列表（主模型单选 / Subagent 多选列），
// 添加/编辑 Provider 与模型均通过 Modal 完成；所有变更写入 state 的 dirty
// 标志，随"保存"按钮统一提交（无单独保存按钮）。

function _allModelEntries() {
  // 返回 [{provider, model, spec}] 按 provider 顺序展平
  const out = [];
  for (const p of state.providers || []) {
    for (const m of p.models || []) {
      out.push({ provider: p.name, model: m, spec: `${p.name}::${m}` });
    }
  }
  return out;
}

function _markProvidersDirty() {
  state.dirty.providers = true;
  refreshDirtyUI();
}

function _markModelsDirty() {
  state.dirty.mainModel = true;
  state.dirty.subagentModels = true;
  refreshDirtyUI();
}

function rerenderAiModule() {
  // 本地重渲染 AI 模块：不重新拉取后端（保留未保存的 provider 改动）。
  const container = getModuleContainer("ai");
  container.innerHTML = "";
  setActiveContainer(container);
  renderConfigModule("ai");
}

function _findProvider(name) {
  return (state.providers || []).find((p) => p.name === name) || null;
}

function _saveProvidersToState(providers) {
  state.providers = providers;
  _markProvidersDirty();
}

// ── 上下文长度（token）工具 ──
// provider.model_context_lengths: { [模型名]: int }；未配置的模型不在其中，
// 由后端在使用时回退默认值 128000。

function _makeContextLengthInput(value) {
  const input = el("input", "input");
  input.type = "number";
  input.min = "1";
  input.step = "1";
  input.placeholder = "默认 128k";
  input.value = (value === undefined || value === null) ? "" : String(value);
  return input;
}

// 合法（正整数）返回 int；留空或非法返回 null（语义：未配置 / 清空该配置）
function _parseContextLength(raw) {
  const text = String(raw === undefined || raw === null ? "" : raw).trim();
  if (!text) return null;
  const n = Number(text);
  if (!Number.isFinite(n) || !Number.isInteger(n) || n <= 0) return null;
  return n;
}

// 就地写入 provider[providerName] 的 model_context_lengths[modelName]
function _setModelContextLength(providers, providerName, modelName, length) {
  const target = providers.find((p) => p.name === providerName);
  if (!target) return;
  if (!target.model_context_lengths) target.model_context_lengths = {};
  if (length === null) delete target.model_context_lengths[modelName];
  else target.model_context_lengths[modelName] = length;
}

// ── API 格式（多格式 Provider） ──
// 真实决策（含端点路径/模型族回退）在后端 llm_formats.resolve_route；这里只展示
// 「用户显式覆盖 / Provider 默认 / models.dev 目录」这三层前端已知的信息，
// 都没有时显示「自动」，表示由后端推断。
function _modelFormatInfo(provider, model) {
  if (!provider) return { format: "auto", source: "" };
  const manual = (provider.model_api_formats || {})[model];
  if (manual) return { format: manual, source: "manual" };
  if (provider.api_format && provider.api_format !== "auto") {
    return { format: provider.api_format, source: "provider_default" };
  }
  const route = (provider.model_catalog_routes || {})[model];
  if (route && route.format) return { format: route.format, source: "catalog" };
  return { format: "auto", source: "" };
}

function _formatText(format, source) {
  if (format === "auto") return "自动";
  const label = (window.API_FORMAT_LABELS || {})[format] || format;
  const sourceLabel = (window.FORMAT_SOURCE_LABELS || {})[source] || "";
  return sourceLabel ? `${label} · ${sourceLabel}` : label;
}

// 模型级字段（格式覆盖 / 最大输出 / 服务端工具）统一写入
function _setModelField(providers, providerName, modelName, field, value) {
  const target = providers.find((p) => p.name === providerName);
  if (!target) return;
  if (!target[field]) target[field] = {};
  if (value === null || value === undefined || value === "") delete target[field][modelName];
  else target[field][modelName] = value;
}

// 模型重命名时把所有「按模型名索引」的配置一起迁移
function _renameModelConfig(providers, providerName, oldName, newName) {
  const target = providers.find((p) => p.name === providerName);
  if (!target) return;
  for (const field of [
    "model_context_lengths", "model_max_output_tokens",
    "model_api_formats", "model_server_tools", "model_api_capabilities",
  ]) {
    const map = target[field];
    if (!map || !(oldName in map)) continue;
    const value = map[oldName];
    delete map[oldName];
    if (newName) map[newName] = value;
  }
}

// 把 Provider 编辑表单写进 provider 对象（新增与编辑共用，避免字段漏掉）
function _applyProviderForm(target, form) {
  target.base_url = form.base_url;
  if (form.key) target.key = form.key;
  target.thinking_type = form.thinking_type;
  target.opencode_go = form.opencode_go;
  target.api_format = form.api_format || "auto";
  target.stream_usage = form.stream_usage;
  target.format_base_urls = form.format_base_urls || {};
  if (form.catalog_provider_id !== undefined) {
    target.catalog_provider_id = form.catalog_provider_id;
  }
  return target;
}

// 新建 Provider 的完整默认结构（后端 ModelProvider 也有同样的默认值）
function _newProviderRecord(name, form) {
  return _applyProviderForm({
    name,
    base_url: "",
    key: null,
    models: [],
    model_context_lengths: {},
    thinking_type: "qwen",
    opencode_go: false,
    catalog_provider_id: null,
    model_catalog_routes: {},
    catalog_synced_at: null,
    api_format: "auto",
    model_api_formats: {},
    format_base_urls: {},
    model_max_output_tokens: {},
    model_api_capabilities: {},
    model_server_tools: {},
    stream_usage: null,
  }, form);
}

// 目录匹配状态的一行摘要（Modal 内展示）
function _catalogSummary(provider) {
  if (!provider) {
    return "尚未匹配 models.dev 目录。保存后点「重新匹配目录」，或直接指定 API 格式。";
  }
  const routes = provider.model_catalog_routes || {};
  const count = Object.keys(routes).length;
  const providerId = provider.catalog_provider_id;
  if (!providerId) {
    return count
      ? `已按模型名匹配到目录（${count} 个模型有路由），但未记录目录 Provider ID。`
      : "尚未匹配 models.dev 目录（需要 Provider 已保存且目录已加载）。";
  }
  const counts = {};
  for (const route of Object.values(routes)) {
    const format = route && route.format;
    if (format) counts[format] = (counts[format] || 0) + 1;
  }
  const parts = Object.entries(counts)
    .map(([format, n]) => `${(window.API_FORMAT_LABELS || {})[format] || format} ${n}`)
    .join(" / ");
  return `目录 Provider: ${providerId}${parts ? `；路由: ${parts}` : ""}`
    + (count ? `（共 ${count} 个模型）` : "（该 Provider 还没有模型）");
}

// 目录匹配接口返回后的摘要
function _catalogResultText(result) {
  if (!result) return "目录匹配未返回结果";
  if (!result.provider_id) {
    const missing = (result.missing || []).length;
    return `未匹配到目录条目${missing ? `（${missing} 个模型无目录信息）` : ""}；`
      + "可手动填写目录 Provider ID 后重试，或直接指定 API 格式。";
  }
  const parts = [
    `目录 Provider: ${result.provider_id}`
      + (result.host_matched ? "" : "（host 不同，仅采用格式，端点仍用 Base URL）"),
    `路由模型: ${result.routed || 0}`,
  ];
  if ((result.missing || []).length) parts.push(`目录缺失: ${result.missing.length}`);
  const candidates = result.candidates || [];
  if (candidates.length > 1) {
    parts.push(`候选: ${candidates.slice(0, 3).map((c) => c.provider_id).join(", ")}`);
  }
  return parts.join("；");
}

// 只重新拉取并替换「当前 Provider」的派生字段，保留其它 Provider 的未保存改动
async function _reloadProviderFromBackend(name) {
  const payload = await cfgApi("GET", "/faust/admin/providers");
  const fresh = (payload.providers || []).find((p) => p.name === name);
  if (!fresh) return null;
  state.providers = (state.providers || []).map((p) => (p.name === name ? fresh : p));
  _markProvidersDirty();
  return fresh;
}


// ── Provider 添加/编辑 Modal ──

function openProviderModal(existing) {
  const isEdit = !!existing;
  const nameInput = el("input", "input");
  nameInput.placeholder = "名称（如 deepseek）";
  nameInput.value = existing ? existing.name : "";
  if (isEdit) nameInput.disabled = true;  // 名称不可改（作为标识）
  const urlInput = el("input", "input");
  urlInput.placeholder = "Base URL（如 https://api.deepseek.com/v1）";
  urlInput.value = existing ? existing.base_url : "";
  const keyInput = el("input", "input");
  keyInput.placeholder = "API Key（留空表示不变）";
  keyInput.type = "password";
  keyInput.value = existing && existing.key ? "********" : "";
  // Thinking 格式：per-provider（qwen/deepseek/openai/none），全局强度由 REASONING_CONFIG 控制
  const thinkingSelect = el("select", "select");
  const thinkingOptions = (window.FIELD_OPTIONS && FIELD_OPTIONS.THINKING_TYPE) || ["none", "openai", "qwen", "deepseek"];
  for (const t of thinkingOptions) {
    const opt = el("option", "", t);
    opt.value = t;
    thinkingSelect.append(opt);
  }
  thinkingSelect.value = existing && existing.thinking_type ? existing.thinking_type : "qwen";

  // OpenCode Go 适配头：启用后请求自动附带 x-opencode-session 会话头
  const ogCheckbox = el("input");
  ogCheckbox.type = "checkbox";
  ogCheckbox.checked = !!(existing && existing.opencode_go);
  const ogText = el("span", "switch-text", ogCheckbox.checked ? "已启用" : "已禁用");
  const ogLabel = el("label", "switch");
  ogLabel.append(ogCheckbox, el("span", "switch-slider"));
  ogCheckbox.addEventListener("change", () => {
    ogText.textContent = ogCheckbox.checked ? "已启用" : "已禁用";
  });
  const ogRow = el("div", "switch-row");
  ogRow.append(ogText, ogLabel);

  // API 格式：Provider 级默认。auto = 由 models.dev 目录 / 端点 / 模型族推断。
  const apiFormatSelect = el("select", "select");
  for (const opt of (window.API_FORMAT_OPTIONS || [{ value: "auto", label: "自动" }])) {
    const node = el("option", "", opt.label);
    node.value = opt.value;
    apiFormatSelect.append(node);
  }
  apiFormatSelect.value = (existing && existing.api_format) || "auto";

  // 流式 token 用量：部分网关拒绝 stream_options/include_usage，需要能关掉
  const streamSelect = el("select", "select");
  for (const opt of [
    { value: "auto", label: "自动（按格式默认）" },
    { value: "on", label: "开启（统计用量，可触发自动压缩）" },
    { value: "off", label: "关闭（网关不支持 stream_options 时）" },
  ]) {
    const node = el("option", "", opt.label);
    node.value = opt.value;
    streamSelect.append(node);
  }
  const existingStreamUsage = existing ? existing.stream_usage : null;
  streamSelect.value = existingStreamUsage === true ? "on"
    : existingStreamUsage === false ? "off" : "auto";

  // 格式端点（高级）：留空 = 用目录推导 / Provider Base URL
  const formatBaseInputs = {};
  const formatBaseRows = el("div", "format-base-rows");
  for (const opt of (window.API_FORMAT_OPTIONS || []).filter((o) => o.value !== "auto")) {
    const input = el("input", "input");
    input.placeholder = "默认：models.dev 目录或 Base URL";
    input.value = (existing && existing.format_base_urls && existing.format_base_urls[opt.value]) || "";
    formatBaseInputs[opt.value] = input;
    const row = el("div", "form-field");
    row.append(el("label", "form-field-label", API_FORMAT_LABELS[opt.value] || opt.value));
    const control = el("div", "form-field-control");
    control.append(input);
    row.append(control);
    formatBaseRows.append(row);
  }
  const formatBaseHelp = el(
    "p", "card-help",
    "格式端点用于目录缺失或需要单独指定路径的网关。留空时：host 与 models.dev 条目一致则用目录端点，否则用上面的 Base URL。"
  );

  // models.dev 目录：展示匹配结果 + 手动 pin / 重新匹配
  const catalogPinInput = el("input", "input");
  catalogPinInput.placeholder = "目录 Provider ID（留空 = 自动匹配，如 opencode-go）";
  catalogPinInput.value = (existing && existing.catalog_provider_id) || "";
  const catalogSummary = el("div", "card-help", _catalogSummary(existing));
  const catalogBar = el("div", "toolbar");

  const field = (label, node) => {
    const box = el("div", "form-field");
    box.append(el("label", "form-field-label", label));
    const ctl = el("div", "form-field-control");
    ctl.append(node);
    box.append(ctl);
    return box;
  };

  // 读取表单 → Provider 字段（新增/编辑/先保存再操作 都走这一份）
  const readForm = () => {
    const formatBaseUrls = {};
    for (const [format, input] of Object.entries(formatBaseInputs)) {
      const value = String(input.value || "").trim();
      if (value) formatBaseUrls[format] = value;
    }
    const rawKey = keyInput.value && keyInput.value !== "********" ? keyInput.value : "";
    return {
      base_url: String(urlInput.value || "").trim(),
      key: rawKey || null,
      thinking_type: thinkingSelect.value,
      opencode_go: ogCheckbox.checked,
      api_format: apiFormatSelect.value,
      stream_usage: streamSelect.value === "auto" ? null : streamSelect.value === "on",
      format_base_urls: formatBaseUrls,
      catalog_provider_id: String(catalogPinInput.value || "").trim() || null,
    };
  };

  // 把当前表单提交到后端（目录匹配 / 加载模型都要求 provider 已存在于后端）
  const persistForm = async () => {
    const name = isEdit ? existing.name : String(nameInput.value || "").trim();
    const form = readForm();
    if (!name || !form.base_url) {
      showBanner("error", "请填写名称与 Base URL");
      return null;
    }
    const providers = JSON.parse(JSON.stringify(state.providers || []));
    let target = providers.find((p) => p.name === name);
    if (!target) {
      target = _newProviderRecord(name, form);
      providers.push(target);
    } else {
      _applyProviderForm(target, form);
    }
    state.providers = providers;
    await cfgApi("POST", "/faust/admin/config", {
      providers: state.providers,
      main_model: state.mainModel,
      subagent_models: state.subagentModels,
    });
    return name;
  };

  const rematchCatalog = makeButton("重新匹配目录", async () => {
    try {
      const name = await persistForm();
      if (!name) return;
      const r = await cfgApi(
        "POST",
        `/faust/admin/providers/${encodeURIComponent(name)}/catalog-match`,
        { provider_id: String(catalogPinInput.value || "").trim() || null },
      );
      await _reloadProviderFromBackend(name);
      const result = r.result || {};
      catalogSummary.textContent = _catalogResultText(result);
      showBanner(
        "success",
        result.provider_id
          ? `已匹配 models.dev 目录: ${result.provider_id}（${result.routed || 0} 个模型有路由）`
          : "未匹配到目录条目：可手动填写目录 Provider ID 后重试，或直接指定 API 格式",
      );
    } catch (e) {
      showBanner("error", "目录匹配失败: " + (e && e.detail ? e.detail : String(e)));
    }
  }, "btn btn-secondary");

  const refreshCatalog = makeButton("刷新 models.dev 目录", async () => {
    try {
      const r = await cfgApi("POST", "/faust/admin/models-dev/refresh");
      // 刷新会影响全部 Provider：整表重拉（目录刷新本就要求先保存，本地不会有未保存改动）
      const payload = await cfgApi("GET", "/faust/admin/providers");
      state.providers = payload.providers || [];
      _markProvidersDirty();
      const name = isEdit ? existing.name : String(nameInput.value || "").trim();
      catalogSummary.textContent = _catalogSummary(_findProvider(name));
      showBanner(
        "success",
        `目录已刷新: ${r.providers || 0} 个 Provider，匹配 ${r.matched || 0} 个（保存后生效）`,
      );
    } catch (e) {
      showBanner("error", "刷新目录失败: " + (e && e.detail ? e.detail : String(e)));
    }
  }, "btn btn-ghost");

  catalogBar.append(catalogPinInput, rematchCatalog, refreshCatalog);

  // 手动添加模型（Modal 内）
  const modelInput = el("input", "input");
  modelInput.placeholder = "模型名（如 deepseek-chat）";
  const addModelBtn = makeButton("添加模型", () => {
    const m = String(modelInput.value || "").trim();
    if (!m) { showBanner("error", "请输入模型名"); return; }
    const form = readForm();
    const providers = JSON.parse(JSON.stringify(state.providers || []));
    const pname = (existing ? existing.name : String(nameInput.value || "").trim());
    let target = providers.find((p) => p.name === pname);
    if (!target) {
      // 新增场景：表单里的 provider 尚未进入 state，先按表单值占位
      if (!pname || !form.base_url) { showBanner("error", "请先填写名称与 Base URL"); return; }
      target = _newProviderRecord(pname, form);
      providers.push(target);
      _saveProvidersToState(providers);
    }
    if (!target.models) target.models = [];
    if (target.models.includes(m)) { showBanner("error", "模型已存在"); return; }
    target.models.push(m);
    _saveProvidersToState(providers);
    modelInput.value = "";
    showBanner("success", "模型已加入（保存后生效）");
  }, "btn btn-ghost");
  const modelRow = el("div", "toolbar");
  modelRow.append(modelInput, addModelBtn);

  // 自动加载模型：先保存（统一保存接口，确保后端已有该 provider），再拉取模型列表
  const loadBtn = makeButton("自动加载模型", async () => {
    try {
      // 1) 先保存：把当前表单（含新 provider）提交到后端统一保存接口
      const name = await persistForm();
      if (!name) return;
      // 2) 再加载模型（后端会清空该 provider 已有模型后重新拉取，
      //    并用 models.dev 目录补全格式路由与缺失的上下文/输出长度）
      const r = await cfgApi("POST", `/faust/admin/providers/${encodeURIComponent(name)}/load-models`,
        null, null);
      // 3) 用最新结果覆盖该 provider 的派生字段（不覆盖其它 provider 的未保存改动）
      const next = JSON.parse(JSON.stringify(state.providers || []));
      const t = next.find((p) => p.name === name);
      if (t) {
        t.models = (r.models || []).slice();
        if (r.model_context_lengths) t.model_context_lengths = { ...r.model_context_lengths };
        if (r.model_max_output_tokens) t.model_max_output_tokens = { ...r.model_max_output_tokens };
        if (r.model_api_capabilities) t.model_api_capabilities = { ...r.model_api_capabilities };
        if (r.catalog && r.catalog.provider_id) t.catalog_provider_id = r.catalog.provider_id;
        if (r.catalog) catalogSummary.textContent = _catalogResultText(r.catalog, r.formats);
      }
      _saveProvidersToState(next);
      const filled = Object.keys(r.model_context_lengths || {}).length;
      const routed = (r.catalog && r.catalog.routed) || 0;
      showBanner(
        "success",
        `模型加载成功: ${(r.models || []).length} 个`
          + (routed ? `，${routed} 个已按 models.dev 目录确定 API 格式` : "")
          + (filled ? `，${filled} 个模型有上下文长度` : "")
          + "（保存后生效）",
      );
    } catch (e) {
      showBanner("error", "模型加载失败: " + (e && e.detail ? e.detail : String(e)));
    }
  }, "btn btn-secondary");

  const bar = el("div", "toolbar");
  bar.append(
    makeButton("保存 Provider", () => {
      const name = String(nameInput.value || "").trim();
      const form = readForm();
      if (!name || !form.base_url) { showBanner("error", "名称与 Base URL 必填"); return; }
      const providers = JSON.parse(JSON.stringify(state.providers || []));
      if (isEdit) {
        const target = providers.find((p) => p.name === name);
        if (target) _applyProviderForm(target, form);
      } else {
        if (providers.some((p) => p.name === name)) { showBanner("error", "Provider 已存在"); return; }
        providers.push(_newProviderRecord(name, form));
      }
      _saveProvidersToState(providers);
      closeModal();
      rerenderAiModule();
      showBanner("success", isEdit ? "Provider 已更新（保存后生效）" : "Provider 已添加（保存后生效）");
    }, "btn btn-primary"),
    makeButton("关闭", closeModal)
  );

  const tip = el("p", "card-help", "新增 Provider 后可在下方 Models 列表勾选主模型与 Subagent 模型；所有改动随顶部「保存」统一生效。「API 格式」默认自动：优先用 models.dev 目录为每个模型选择 OpenAI Chat / OpenAI Responses / Anthropic / Gemini，同一个 Provider 可以同时提供多种格式。清空「目录 Provider ID」再保存即恢复自动匹配；填了 ID 则锁定该目录条目。「Opencode Go 适配头」适用于 OpenCode Go 订阅端点：启用后每个请求自动附带 x-opencode-session 会话头。");
  openModal(isEdit ? `编辑 Provider - ${existing.name}` : "添加 AI Provider", [
    field("名称", nameInput),
    field("Base URL", urlInput),
    field("API Key", keyInput),
    field("Thinking 格式", thinkingSelect),
    field("API 格式", apiFormatSelect),
    field("流式 Token 用量", streamSelect),
    field("Opencode Go 适配头", ogRow),
    el("h4", "card-title", "models.dev 目录"),
    catalogSummary,
    catalogBar,
    el("h4", "card-title", "格式端点（高级）"),
    formatBaseRows,
    formatBaseHelp,
    loadBtn,
    el("h4", "card-title", "模型管理"),
    modelRow,
    tip,
    bar,
  ]);
}

// ── 模型编辑 Modal ──

function openModelModal(entry) {
  const nameInput = el("input", "input");
  nameInput.value = entry.model;
  const entryProvider = _findProvider(entry.provider);
  const ctxInput = _makeContextLengthInput(
    entryProvider && entryProvider.model_context_lengths
      ? entryProvider.model_context_lengths[entry.model]
      : undefined
  );
  const ctxField = el("div", "form-field");
  ctxField.append(el("label", "form-field-label", "上下文长度"));
  const ctxControl = el("div", "form-field-control");
  ctxControl.append(ctxInput);
  ctxField.append(ctxControl);

  // 最大输出 token：留空 = 由 /models 或 models.dev 目录自动填充
  const maxOutInput = el("input", "input");
  maxOutInput.type = "number";
  maxOutInput.min = "1";
  maxOutInput.placeholder = "自动（/models 或 models.dev）";
  const configuredMaxOut = entryProvider && entryProvider.model_max_output_tokens
    ? entryProvider.model_max_output_tokens[entry.model]
    : undefined;
  maxOutInput.value = configuredMaxOut ? String(configuredMaxOut) : "";
  const maxOutField = el("div", "form-field");
  maxOutField.append(el("label", "form-field-label", "最大输出 Token"));
  const maxOutControl = el("div", "form-field-control");
  maxOutControl.append(maxOutInput);
  maxOutField.append(maxOutControl);

  // API 格式覆盖：留空 = 跟随 Provider / 目录 / 推断
  const formatSelect = el("select", "select");
  for (const opt of (window.API_FORMAT_OPTIONS || [{ value: "auto", label: "自动" }])) {
    const node = el("option", "", opt.label);
    node.value = opt.value;
    formatSelect.append(node);
  }
  const formatInfo = _modelFormatInfo(entryProvider, entry.model);
  formatSelect.value = formatInfo.source === "manual" ? formatInfo.format : "auto";
  const formatField = el("div", "form-field");
  formatField.append(el("label", "form-field-label", "API 格式（覆盖）"));
  const formatControl = el("div", "form-field-control");
  formatControl.append(formatSelect);
  formatField.append(formatControl);

  // 服务端工具：勾选后由服务商在服务端执行（openai_chat 不支持）
  const toolWrap = el("div", "form-field");
  toolWrap.append(el("label", "form-field-label", "服务端工具"));
  const toolControl = el("div", "form-field-control");
  const enabledTools = new Set(
    (entryProvider && entryProvider.model_server_tools
      ? entryProvider.model_server_tools[entry.model]
      : []) || []
  );
  const toolCheckboxes = {};
  for (const tool of (window.SERVER_TOOL_OPTIONS || [])) {
    const row = el("label", "switch-row");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = enabledTools.has(tool.value);
    checkbox.title = tool.help || "";
    toolCheckboxes[tool.value] = checkbox;
    row.append(checkbox, el("span", "switch-text", tool.label));
    toolControl.append(row);
  }
  const toolHelp = el("p", "card-help", "服务端工具由服务商执行（如联网搜索），不会出现在本地工具列表；OpenAI Chat 格式不支持。");
  toolControl.append(toolHelp);
  toolWrap.append(toolControl);

  const bar = el("div", "toolbar");
  bar.append(
    makeButton("保存", () => {
      const newName = String(nameInput.value || "").trim();
      if (!newName) { showBanner("error", "模型名不能为空"); return; }
      const providers = JSON.parse(JSON.stringify(state.providers || []));
      const target = providers.find((p) => p.name === entry.provider);
      if (!target) { closeModal(); return; }
      const oldSpec = entry.spec;
      target.models = (target.models || []).map((m) => (m === entry.model ? newName : m));
      // 所有「按模型名索引」的配置随重命名一起迁移
      _renameModelConfig(providers, entry.provider, entry.model, newName);
      // 上下文长度 / 最大输出 / 格式覆盖 按表单写入
      _setModelContextLength(providers, entry.provider, newName, _parseContextLength(ctxInput.value));
      _setModelField(providers, entry.provider, newName, "model_max_output_tokens",
        _parseContextLength(maxOutInput.value));
      _setModelField(providers, entry.provider, newName, "model_api_formats",
        formatSelect.value === "auto" ? null : formatSelect.value);
      const tools = Object.entries(toolCheckboxes)
        .filter(([, node]) => node.checked)
        .map(([value]) => value);
      _setModelField(providers, entry.provider, newName, "model_server_tools",
        tools.length ? tools : null);
      // 同步主/Subagent 选择里的 spec 引用
      if (state.mainModel === oldSpec) state.mainModel = `${entry.provider}::${newName}`;
      state.subagentModels = (state.subagentModels || []).map((s) => (s === oldSpec ? `${entry.provider}::${newName}` : s));
      _saveProvidersToState(providers);
      _markModelsDirty();
      closeModal();
      rerenderAiModule();
      showBanner("success", "模型已保存（保存后生效）");
    }, "btn btn-primary"),
    makeButton("删除模型", () => {
      const providers = JSON.parse(JSON.stringify(state.providers || []));
      const target = providers.find((p) => p.name === entry.provider);
      if (target) {
        target.models = (target.models || []).filter((m) => m !== entry.model);
        // 同步删除该模型名的所有派生配置
        _renameModelConfig(providers, entry.provider, entry.model, null);
      }
      if (state.mainModel === entry.spec) state.mainModel = "";
      state.subagentModels = (state.subagentModels || []).filter((s) => s !== entry.spec);
      _saveProvidersToState(providers);
      _markModelsDirty();
      closeModal();
      rerenderAiModule();
      showBanner("success", "模型已删除（保存后生效）");
    }, "btn btn-ghost"),
    makeButton("关闭", closeModal)
  );
  openModal(`编辑模型 - ${entry.spec}`, [
    nameInput, ctxField, maxOutField, formatField, toolWrap, bar,
  ]);
}

// ── 模块渲染入口 ──

function renderProviderWizard() {
  // ── Providers 列表 ──
  const providerRows = (state.providers || []).map((p) => [
    p.name,
    p.base_url,
    p.thinking_type || "qwen",
    // models.dev 目录匹配结果（未匹配时提示可 pin）
    p.catalog_provider_id || "未匹配",
    String((p.models || []).length),
    (() => {
      const wrap = el("div", "toolbar compact");
      wrap.append(
        makeButton("编辑", () => openProviderModal(p), "btn btn-ghost"),
        makeButton("删除", () => {
          const providers = JSON.parse(JSON.stringify(state.providers || []));
          const next = providers.filter((x) => x.name !== p.name);
          _saveProvidersToState(next);
          if (state.mainModel && state.mainModel.startsWith(`${p.name}::`)) state.mainModel = "";
          state.subagentModels = (state.subagentModels || []).filter((s) => !s.startsWith(`${p.name}::`));
          _markModelsDirty();
          rerenderAiModule();
          showBanner("success", "Provider 已删除（保存后生效）");
        }, "btn btn-ghost")
      );
      return wrap;
    })(),
  ]);
  const providersCard = makeSimpleTableCard("AI Providers", ["名称", "Base URL", "Thinking", "目录", "模型数", "操作"], providerRows);

  // ── Models 列表（主模型单选 / Subagent 多选） ──
  const modelEntries = _allModelEntries();
  const mainSelected = state.mainModel || "";
  const subSelected = new Set(state.subagentModels || []);

  const modelRows = modelEntries.map((entry) => {
    // 主模型 radio（现代风格 switch 单选用 radio 行）
    const radioWrap = el("div", "switch-row");
    const radio = document.createElement("input");
    radio.type = "radio";
    radio.className = "provider-pick";
    radio.name = "provider-main-model";
    radio.checked = mainSelected === entry.spec;
    radio.addEventListener("change", () => {
      state.mainModel = radio.checked ? entry.spec : "";
      _markModelsDirty();
      // 不整表重渲染：radio 原生单选视觉由浏览器维护，
      // 整表重建会导致已勾选的其它行引用失效。
    });
    radioWrap.append(radio);

    // Subagent checkbox
    const subWrap = el("div", "switch-row");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "provider-pick";
    cb.checked = subSelected.has(entry.spec);
    cb.addEventListener("change", () => {
      const next = new Set(state.subagentModels || []);
      if (cb.checked) next.add(entry.spec);
      else next.delete(entry.spec);
      state.subagentModels = [...next];
      _markModelsDirty();
      // 同上：checkbox 原生状态已更新，无需整表重建。
    });
    subWrap.append(cb);

    // 上下文长度：number 输入，留空 = 未配置（后端默认 128000）
    const providerEntry = _findProvider(entry.provider);
    const ctxWrap = el("div", "toolbar compact");
    const ctxInput = _makeContextLengthInput(
      providerEntry && providerEntry.model_context_lengths
        ? providerEntry.model_context_lengths[entry.model]
        : undefined
    );
    ctxInput.style.maxWidth = "140px";  // 表格内收窄（.input 默认 width:100%）
    ctxInput.addEventListener("change", () => {
      const length = _parseContextLength(ctxInput.value);
      const providers = JSON.parse(JSON.stringify(state.providers || []));
      _setModelContextLength(providers, entry.provider, entry.model, length);
      _saveProvidersToState(providers);
      ctxInput.value = length === null ? "" : String(length);  // 非法输入回退为清空
      // 不整表重渲染：输入框原生状态已更新（同下方 radio/checkbox 的做法）。
    });
    ctxWrap.append(ctxInput);

    const ops = el("div", "toolbar compact");
    ops.append(makeButton("编辑", () => openModelModal(entry), "btn btn-ghost"));

    // API 格式：前端展示用户覆盖 / Provider 默认 / 目录三层；「自动」由后端推断
    const formatInfo = _modelFormatInfo(providerEntry, entry.model);
    const formatCell = el("span", "format-badge", _formatText(formatInfo.format, formatInfo.source));
    formatCell.title = formatInfo.source === "catalog"
      ? "来自 models.dev 目录；可在「编辑」里覆盖"
      : "自动：后端按 models.dev 目录 / 端点 / 模型族推断";

    return [entry.provider, entry.model, formatCell, ctxWrap, radioWrap, subWrap, ops];
  });
  const modelsCard = makeSimpleTableCard("Models", ["Provider", "模型", "API 格式", "上下文长度", "主模型", "Subagent", "操作"], modelRows, {
    pageSize: 10,
    searchKey: 1,
  });

  // ── 添加 Provider 按钮 ──
  const addBar = el("div", "toolbar");
  addBar.append(
    makeButton("手动添加 Provider", () => openProviderModal(null), "btn btn-primary"),
    makeButton("加载所有 Provider 的模型列表", async () => {
      // 对当前未加载模型的 provider 逐个拉取
      const pending = (state.providers || []).filter((p) => !(p.models && p.models.length));
      if (!pending.length) { showBanner("info", "没有需要加载模型的 Provider"); return; }
      let ok = 0, fail = 0;
      for (const p of pending) {
        try {
          const r = await cfgApi("POST", `/faust/admin/providers/${encodeURIComponent(p.name)}/load-models`);
          if ((r.models || []).length) ok++;
          else fail++;
        } catch (e) {
          fail++;
        }
      }
      const pr = await cfgApi("GET", "/faust/admin/providers");
      state.providers = pr.providers || [];
      state.mainModel = pr.main_model || "";
      state.subagentModels = pr.subagent_models || [];
      _markProvidersDirty();
      rerenderAiModule();
      showBanner(fail ? "error" : "success", `模型加载完成: 成功 ${ok} 个, 失败 ${fail} 个（保存后生效）`);
    }, "btn btn-secondary"),
    makeButton("同步上下文长度（来自 /models）", async () => {
      // 已加载过模型的 Provider 不会被上面的按钮覆盖，但它们的上下文长度
      // 可能仍是空的。这里对**全部** Provider 重跑一次 /models，用 API 元数据
      // 填充缺失的上下文长度（用户手工填过的值不会被覆盖）。
      const all = state.providers || [];
      if (!all.length) { showBanner("info", "还没有 Provider"); return; }
      let ok = 0, fail = 0;
      for (const p of all) {
        try {
          await cfgApi("POST", `/faust/admin/providers/${encodeURIComponent(p.name)}/load-models`);
          ok++;
        } catch (e) {
          fail++;
        }
      }
      const pr = await cfgApi("GET", "/faust/admin/providers");
      state.providers = pr.providers || [];
      state.mainModel = pr.main_model || "";
      state.subagentModels = pr.subagent_models || [];
      const filled = (state.providers || []).reduce(
        (n, p) => n + Object.keys(p.model_context_lengths || {}).length, 0);
      _markProvidersDirty();
      rerenderAiModule();
      showBanner(
        fail ? "error" : "success",
        `上下文长度同步完成: 成功 ${ok} 个 Provider, 失败 ${fail} 个；`
          + `当前共 ${filled} 个模型有明确上下文长度（保存后生效）`,
      );
    }, "btn btn-secondary"),
    makeButton("刷新 models.dev 目录", async () => {
      // 拉取最新的 models.dev 目录，并把每个 Provider 的模型重新路由到正确格式
      // （这条会写入配置文件；随后整表重拉以反映新的目录/格式/上下文长度）
      try {
        const r = await cfgApi("POST", "/faust/admin/models-dev/refresh");
        const pr = await cfgApi("GET", "/faust/admin/providers");
        state.providers = pr.providers || [];
        state.mainModel = pr.main_model || "";
        state.subagentModels = pr.subagent_models || [];
        _markProvidersDirty();
        rerenderAiModule();
        showBanner(
          (r.status === "ok") ? "success" : "error",
          `models.dev 目录刷新: ${r.providers || 0} 个 Provider，`
            + `匹配 ${r.matched || 0} 个` + (r.error ? `；错误: ${r.error}` : ""),
        );
      } catch (e) {
        showBanner("error", "刷新目录失败: " + (e && e.detail ? e.detail : String(e)));
      }
    }, "btn btn-ghost")
  );

  addSection("", [addBar]);
  addSection("", [providersCard]);
  addSection("", [modelsCard]);
  if (!modelEntries.length) {
    addSection("提示", [el("div", "empty-state", "暂无模型。添加 Provider 后点击「自动加载模型」或手动添加。")]);
  }
}
