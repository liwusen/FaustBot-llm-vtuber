// AI 服务商：数据操作层（服务商 / 模型的弹窗与工具函数）
// 页面布局在 modules/ai.js（renderAiModule）；本文件只提供：
//   · 服务商与模型的增删改弹窗（基础字段常显，工程化配置收进「高级设置」）
//   · 所有写进内存的小工具（改动先记在 state，随窗口顶部「保存」统一提交）
//   · 展示格式化：上下文长度、接口格式与来源、密钥状态、目录识别结果
// 依赖（全局，已在 config-window.html 中先加载）：
//   field-render.js（makeFieldFold/FIELD_OPTION_LABELS 等）、dom-utils.js（el/makeButton）、
//   modal.js（openModal/closeModal）、state-core.js（state/refreshDirtyUI/cfgApi）。

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

// 本地重渲染 AI 模块：不重新拉取后端（保留未保存的 provider 改动）。
function rerenderAiModule() {
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

// ── 展示格式化 ──

// 上下文长度：1048576 → "1M"，131072 → "128K"，400000 → "400K"；未配置时说明后端默认值
function _formatContext(tokens) {
  const n = Number(tokens);
  if (!Number.isFinite(n) || n <= 0) return "默认 128K";
  if (n % 1048576 === 0) return `${n / 1048576}M`;
  if (n % 1000000 === 0) return `${n / 1000000}M`;
  if (n >= 1048576) return `${Number((n / 1048576).toFixed(1))}M`;
  if (n % 1024 === 0) return `${n / 1024}K`;
  if (n >= 1000) return `${Math.round(n / 1000)}K`;
  return String(n);
}

// 密钥状态：只说有没有配，不显示值
function _keyStatus(provider) {
  return provider && provider.key ? "已设置" : "未设置";
}

// 模型用哪种接口格式：本地手动覆盖 → 后端解析结果 → 服务商默认 → 目录 → 自动
// 返回 { format, source }；format 为 "auto" 表示由后端按端点 / 模型类型推断。
function _formatDecision(provider, model) {
  if (!provider) return { format: "auto", source: "" };
  const manual = (provider.model_api_formats || {})[model];
  if (manual) return { format: manual, source: "manual" };
  const resolved = ((state.resolvedFormats || {})[provider.name] || {})[model];
  if (resolved) {
    if (resolved.source === "error") return { format: "auto", source: "error" };
    if (resolved.api_format) return { format: resolved.api_format, source: resolved.source || "" };
  }
  if (provider.api_format && provider.api_format !== "auto") {
    return { format: provider.api_format, source: "provider_default" };
  }
  const route = (provider.model_catalog_routes || {})[model];
  if (route && route.format) return { format: route.format, source: "catalog" };
  return { format: "auto", source: "" };
}

function _formatText(format, source) {
  const label = (window.API_FORMAT_LABELS || {})[format] || (format === "auto" ? "自动" : format);
  const sourceLabel = (window.FORMAT_SOURCE_LABELS || {})[source] || "";
  return sourceLabel ? `${label} · ${sourceLabel}` : label;
}

// ── 上下文长度（token）工具 ──
// provider.model_context_lengths: { [模型名]: int }；未配置的模型不在其中，
// 由后端在使用时回退默认值 128000。

function _makeContextLengthInput(value) {
  const input = el("input", "input");
  input.type = "number";
  input.min = "1";
  input.step = "1";
  input.placeholder = "留空按默认 128K";
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

// 把服务商编辑表单写进 provider 对象（新增与编辑共用，避免字段漏掉）
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

// 新建服务商的完整默认结构（后端 ModelProvider 也有同样的默认值）
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

// 服务商在模型目录里的识别结果，一句话说清（弹窗内展示）
function _catalogSummary(provider) {
  if (!provider) {
    return "添加服务商并拉取模型后，这里会显示模型目录的识别结果。";
  }
  const routes = provider.model_catalog_routes || {};
  const count = Object.keys(routes).length;
  const catalog = (state.providerCatalog || {})[provider.name] || null;
  const providerId = provider.catalog_provider_id || (catalog && catalog.provider_id) || null;
  if (providerId) {
    const hostNote = catalog && catalog.host_matched === false
      ? "接口地址与目录不同（只采用格式，地址仍用你填的）"
      : "接口地址与目录一致";
    return `已识别为目录里的「${providerId}」：${hostNote}，${count} 个模型有格式结论。`;
  }
  if (count) {
    return `已有 ${count} 个模型按模型名对上了目录，但没记录目录条目；可以点「自动识别服务商」再试一次。`;
  }
  return "还没有识别出对应的目录条目。可以点「自动识别服务商」，或直接在下面填目录条目名。";
}

// 目录识别接口返回后的摘要
function _catalogResultText(result) {
  if (!result) return "没有返回结果。";
  if (!result.provider_id) {
    const missing = (result.missing || []).length;
    return `没找到对应的目录条目${missing ? `（${missing} 个模型在目录里没有记录）` : ""}。`
      + "可以直接填目录条目名，或在模型设置里手动指定接口格式。";
  }
  const parts = [`识别为目录条目「${result.provider_id}」`, `${result.routed || 0} 个模型有格式结论`];
  if (!result.host_matched) parts.push("接口地址与目录不同，只采用了格式");
  if ((result.missing || []).length) parts.push(`${result.missing.length} 个模型目录里没有`);
  return `${parts.join("；")}。`;
}

// 只重新拉取并替换「当前服务商」的派生字段，保留其它服务商的未保存改动
async function _reloadProviderFromBackend(name) {
  const payload = await cfgApi("GET", "/faust/admin/providers");
  const fresh = (payload.providers || []).find((p) => p.name === name);
  if (!fresh) return null;
  state.providers = (state.providers || []).map((p) => (p.name === name ? fresh : p));
  if (payload.resolved_formats && payload.resolved_formats[name]) {
    state.resolvedFormats = { ...(state.resolvedFormats || {}), [name]: payload.resolved_formats[name] };
  }
  if (payload.catalog && payload.catalog[name]) {
    state.providerCatalog = { ...(state.providerCatalog || {}), [name]: payload.catalog[name] };
  }
  _markProvidersDirty();
  return fresh;
}

// ── 服务商添加/编辑弹窗 ──

function openProviderModal(existing) {
  const isEdit = !!existing;

  // 通用字段容器：标题 + 一句说明 + 控件
  const field = (label, node, hint) => {
    const box = el("div", "form-field");
    box.append(el("label", "form-field-label", label));
    if (hint) box.append(el("p", "form-hint", hint));
    const ctl = el("div", "form-field-control");
    ctl.append(node);
    box.append(ctl);
    return box;
  };

  // ── 基础：怎么连上服务商 ──
  const nameInput = el("input", "input");
  nameInput.placeholder = "例如 deepseek";
  nameInput.value = existing ? existing.name : "";
  if (isEdit) nameInput.disabled = true;  // 名称不可改（它是模型标识的一部分）

  const urlInput = el("input", "input");
  urlInput.placeholder = "例如 https://api.deepseek.com/v1";
  urlInput.value = existing ? existing.base_url : "";

  const keyInput = el("input", "input");
  keyInput.type = "password";
  keyInput.placeholder = existing && existing.key ? "已设置，留空表示不修改" : "粘贴服务商提供的密钥";
  keyInput.value = existing && existing.key ? "********" : "";

  // ── 高级：思考 / 格式 / 用量 / 适配头 / 目录 / 各格式地址 ──
  const thinkingSelect = el("select", "select");
  const thinkingOptions = (window.FIELD_OPTIONS && FIELD_OPTIONS.THINKING_TYPE)
    || ["none", "openai", "qwen", "deepseek"];
  const thinkingLabels = (window.FIELD_OPTION_LABELS && FIELD_OPTION_LABELS.THINKING_TYPE) || {};
  for (const t of thinkingOptions) {
    const opt = el("option", "", thinkingLabels[t] || t);
    opt.value = t;
    thinkingSelect.append(opt);
  }
  thinkingSelect.value = existing && existing.thinking_type ? existing.thinking_type : "qwen";

  const apiFormatSelect = el("select", "select");
  for (const opt of (window.API_FORMAT_OPTIONS || [{ value: "auto", label: "自动识别" }])) {
    const node = el("option", "", opt.label);
    node.value = opt.value;
    apiFormatSelect.append(node);
  }
  apiFormatSelect.value = (existing && existing.api_format) || "auto";

  // 用量统计：部分网关拒绝 stream_options / include_usage，需要能关掉
  const streamSelect = el("select", "select");
  for (const opt of [
    { value: "auto", label: "自动（按接口格式决定）" },
    { value: "on", label: "统计（能触发自动压缩）" },
    { value: "off", label: "不统计（网关不支持用量字段时）" },
  ]) {
    const node = el("option", "", opt.label);
    node.value = opt.value;
    streamSelect.append(node);
  }
  const existingStreamUsage = existing ? existing.stream_usage : null;
  streamSelect.value = existingStreamUsage === true ? "on"
    : existingStreamUsage === false ? "off" : "auto";

  // OpenCode Go 专用会话头
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

  // 模型目录：识别结果 + 手动指条目 + 重新识别
  const catalogPinInput = el("input", "input");
  catalogPinInput.placeholder = "目录条目名（留空表示自动识别）";
  catalogPinInput.value = (existing && existing.catalog_provider_id) || "";
  const catalogSummary = el("p", "form-hint", _catalogSummary(existing));

  // 各格式接口地址（只有同服务商多格式走不同路径时才需要）
  const formatBaseInputs = {};
  const formatBaseRows = el("div", "format-base-rows");
  for (const opt of (window.API_FORMAT_OPTIONS || []).filter((o) => o.value !== "auto")) {
    const input = el("input", "input");
    input.placeholder = "留空 = 用模型目录或上面填的接口地址";
    input.value = (existing && existing.format_base_urls && existing.format_base_urls[opt.value]) || "";
    formatBaseInputs[opt.value] = input;
    formatBaseRows.append(field(API_FORMAT_LABELS[opt.value] || opt.value, input));
  }

  // 读取表单 → 服务商字段（新增 / 编辑 / 先保存再操作 都走这一份）
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

  // 把当前表单提交到后端（目录识别 / 拉取模型都要求服务商已存在于后端）
  const persistForm = async () => {
    const name = isEdit ? existing.name : String(nameInput.value || "").trim();
    const form = readForm();
    if (!name || !form.base_url) {
      showBanner("error", "请先填写名称与接口地址");
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

  // ── 模型：只放「怎么把模型弄进来」 ──
  const modelCountText = el("p", "form-hint", "");
  const refreshModelCount = () => {
    const name = isEdit ? existing.name : String(nameInput.value || "").trim();
    const provider = _findProvider(name);
    const count = provider && provider.models ? provider.models.length : 0;
    modelCountText.textContent = count
      ? `已加载 ${count} 个模型，回到页面就能在模型表里挑主模型与子模型。`
      : "还没有模型。点「从服务商拉取模型」自动读取，或手动输入模型名添加。";
  };
  nameInput.addEventListener("input", refreshModelCount);

  // 自动拉取模型：先保存（统一保存接口，确保后端已有该服务商），再读 /models
  const loadBtn = makeButton("从服务商拉取模型", async () => {
    try {
      const name = await persistForm();
      if (!name) return;
      const r = await cfgApi("POST", `/faust/admin/providers/${encodeURIComponent(name)}/load-models`,
        null, null);
      const next = JSON.parse(JSON.stringify(state.providers || []));
      const t = next.find((p) => p.name === name);
      if (t) {
        t.models = (r.models || []).slice();
        if (r.model_context_lengths) t.model_context_lengths = { ...r.model_context_lengths };
        if (r.model_max_output_tokens) t.model_max_output_tokens = { ...r.model_max_output_tokens };
        if (r.model_api_capabilities) t.model_api_capabilities = { ...r.model_api_capabilities };
        if (r.catalog && r.catalog.provider_id) t.catalog_provider_id = r.catalog.provider_id;
      }
      _saveProvidersToState(next);
      refreshModelCount();
      if (r.catalog) catalogSummary.textContent = _catalogResultText(r.catalog);
      const routed = (r.catalog && r.catalog.routed) || 0;
      showBanner(
        "success",
        `拉取到 ${(r.models || []).length} 个模型`
          + (routed ? `，${routed} 个已按模型目录确定接口格式` : "")
          + "（回到页面点「保存」写入配置）",
      );
    } catch (e) {
      showBanner("error", "拉取模型失败: " + (e && e.detail ? e.detail : String(e)));
    }
  }, "btn btn-secondary");

  const modelInput = el("input", "input");
  modelInput.placeholder = "模型名，例如 deepseek-chat";
  const addModelBtn = makeButton("添加", () => {
    const m = String(modelInput.value || "").trim();
    if (!m) { showBanner("error", "请输入模型名"); return; }
    const form = readForm();
    const providers = JSON.parse(JSON.stringify(state.providers || []));
    const pname = (existing ? existing.name : String(nameInput.value || "").trim());
    let target = providers.find((p) => p.name === pname);
    if (!target) {
      // 新增场景：表单里的服务商尚未进入 state，先按表单值占位
      if (!pname || !form.base_url) { showBanner("error", "先填写名称与接口地址"); return; }
      target = _newProviderRecord(pname, form);
      providers.push(target);
      _saveProvidersToState(providers);
    }
    if (!target.models) target.models = [];
    if (target.models.includes(m)) { showBanner("error", "这个模型已经加过了"); return; }
    target.models.push(m);
    _saveProvidersToState(providers);
    modelInput.value = "";
    refreshModelCount();
    showBanner("success", `已添加模型 ${m}（保存后生效）`);
  }, "btn btn-ghost");
  const modelRow = el("div", "toolbar modal-inline");
  modelRow.append(modelInput, addModelBtn);
  const loadRow = el("div", "toolbar");
  loadRow.append(loadBtn);

  refreshModelCount();

  // 目录识别 / 更新目录（都会立即写后端）
  const rematchCatalog = makeButton("自动识别服务商", async () => {
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
      showBanner("success", result.provider_id
        ? `已识别为目录条目「${result.provider_id}」（${result.routed || 0} 个模型有格式结论）`
        : "没找到对应的目录条目：可以直接填目录条目名，或在模型设置里手动指定接口格式");
    } catch (e) {
      showBanner("error", "识别失败: " + (e && e.detail ? e.detail : String(e)));
    }
  }, "btn btn-secondary");

  const refreshCatalog = makeButton("更新模型目录", async () => {
    try {
      const r = await cfgApi("POST", "/faust/admin/models-dev/refresh");
      // 更新目录会影响全部服务商：整表重拉（目录更新本就要求先保存）
      const payload = await cfgApi("GET", "/faust/admin/providers");
      state.providers = payload.providers || [];
      state.resolvedFormats = payload.resolved_formats || {};
      state.providerCatalog = payload.catalog || {};
      _markProvidersDirty();
      const name = isEdit ? existing.name : String(nameInput.value || "").trim();
      catalogSummary.textContent = _catalogSummary(_findProvider(name));
      showBanner(
        "success",
        `模型目录已更新：${r.providers || 0} 个服务商，识别出 ${r.matched || 0} 个（保存后生效）`,
      );
    } catch (e) {
      showBanner("error", "更新目录失败: " + (e && e.detail ? e.detail : String(e)));
    }
  }, "btn btn-ghost");

  const catalogBar = el("div", "toolbar modal-inline");
  catalogBar.append(catalogPinInput, rematchCatalog, refreshCatalog);

  const advanced = makeFieldFold("高级设置", [
    field("思考参数格式", thinkingSelect,
      "OpenAI Chat 格式下决定思考参数怎么写；Responses / Anthropic / Gemini 只按「思考强度」换算，选「不使用」可强制关闭思考。"),
    field("默认接口格式", apiFormatSelect,
      "自动识别时按模型目录与接口地址判断；同一个服务商的不同模型可以用不同格式。"),
    field("用量统计", streamSelect,
      "关掉后不再读取 token 用量，自动压缩也不会被触发。"),
    field("OpenCode Go 专用会话头", ogRow,
      "仅 OpenCode Go 订阅端点需要：开启后每个请求自动附带 x-opencode-session 会话头。"),
    (() => {
      const box = el("div", "form-field");
      box.append(el("label", "form-field-label", "模型目录"));
      box.append(catalogSummary);
      const ctl = el("div", "form-field-control");
      ctl.append(catalogBar);
      box.append(ctl);
      box.append(el("p", "form-hint",
        "模型目录用来判断每个模型该用哪种接口格式，也能补全上下文长度；识别要求服务商已保存且已拉取模型。"));
      return box;
    })(),
    (() => {
      const box = el("div", "form-field");
      box.append(el("label", "form-field-label", "各格式接口地址"));
      box.append(el("p", "form-hint",
        "只有同一服务商的多种格式走不同路径时才需要填。留空时：地址与目录一致就用目录里的地址，否则用上面的接口地址。"));
      box.append(formatBaseRows);
      return box;
    })(),
  ], "provider-advanced", {});

  const bar = el("div", "toolbar");
  bar.append(
    makeButton("保存到配置", () => {
      const name = String(nameInput.value || "").trim();
      const form = readForm();
      if (!name || !form.base_url) { showBanner("error", "名称与接口地址必填"); return; }
      const providers = JSON.parse(JSON.stringify(state.providers || []));
      if (isEdit) {
        const target = providers.find((p) => p.name === name);
        if (target) _applyProviderForm(target, form);
      } else {
        if (providers.some((p) => p.name === name)) { showBanner("error", "这个服务商已经存在"); return; }
        providers.push(_newProviderRecord(name, form));
      }
      _saveProvidersToState(providers);
      closeModal();
      rerenderAiModule();
      showBanner("success", `服务商已${isEdit ? "更新" : "添加"}（点窗口顶部「保存」写入配置）`);
    }, "btn btn-primary"),
    makeButton("关闭", closeModal)
  );

  openModal(isEdit ? `编辑服务商 · ${existing.name}` : "添加服务商", [
    field("名称", nameInput, "自定义的名字，模型会记成「名称::模型名」；保存后不可改名。"),
    field("接口地址", urlInput, "服务商给的 API 地址，通常以 /v1 结尾。"),
    field("密钥", keyInput, "只存在本地配置文件里，不会写进公共配置。"),
    el("h4", "modal-section-title", "模型"),
    modelCountText,
    loadRow,
    modelRow,
    advanced,
    el("p", "form-hint",
      "「保存到配置」只是把改动放进待保存；要真正写入配置，请点窗口顶部的「保存」。"
      + "拉取模型与目录识别会立即保存这个服务商。"),
    bar,
  ]);
}

// ── 模型设置弹窗 ──

function openModelModal(entry) {
  const provider = _findProvider(entry.provider);
  const decision = _formatDecision(provider, entry.model);

  const field = (label, node, hint) => {
    const box = el("div", "form-field");
    box.append(el("label", "form-field-label", label));
    if (hint) box.append(el("p", "form-hint", hint));
    const ctl = el("div", "form-field-control");
    ctl.append(node);
    box.append(ctl);
    return box;
  };

  const nameInput = el("input", "input");
  nameInput.value = entry.model;

  const ctxInput = _makeContextLengthInput(
    provider && provider.model_context_lengths ? provider.model_context_lengths[entry.model] : undefined
  );
  const configuredMaxOut = provider && provider.model_max_output_tokens
    ? provider.model_max_output_tokens[entry.model] : undefined;
  const maxOutInput = el("input", "input");
  maxOutInput.type = "number";
  maxOutInput.min = "1";
  maxOutInput.placeholder = "留空按服务商返回的默认值";
  maxOutInput.value = configuredMaxOut ? String(configuredMaxOut) : "";

  const exactCtx = provider && provider.model_context_lengths
    ? provider.model_context_lengths[entry.model] : null;
  const info = el("p", "form-hint",
    `当前格式：${_formatText(decision.format, decision.source)}`
      + `｜上下文：${exactCtx ? `${_formatContext(exactCtx)}（${exactCtx} token）` : "未配置，按默认 128K"}`
      + `｜服务商：${entry.provider}`);

  // 接口格式覆盖：自动 = 跟随服务商 / 模型目录 / 后端推断
  const formatSelect = el("select", "select");
  for (const opt of (window.API_FORMAT_OPTIONS || [{ value: "auto", label: "自动识别" }])) {
    const node = el("option", "", opt.label);
    node.value = opt.value;
    formatSelect.append(node);
  }
  formatSelect.value = decision.source === "manual" ? decision.format : "auto";

  // 服务端工具：勾选后由服务商在服务端执行（OpenAI Chat 不支持）
  const tools = [];
  const toolsWrap = el("div", "field-wrap");
  const enabledTools = new Set(
    (provider && provider.model_server_tools ? provider.model_server_tools[entry.model] : []) || []
  );
  for (const tool of (window.SERVER_TOOL_OPTIONS || [])) {
    const row = el("label", "switch-row tool-row");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = enabledTools.has(tool.value);
    checkbox.title = tool.help || "";
    row.append(checkbox, el("span", "switch-text", tool.label));
    toolsWrap.append(row);
    tools.push({ value: tool.value, checkbox, row, formats: tool.formats || [] });
  }
  const toolNote = el("p", "form-hint", "");
  toolsWrap.append(toolNote);

  const syncTools = () => {
    const effective = formatSelect.value === "auto" ? decision.format : formatSelect.value;
    let blocked = 0;
    for (const item of tools) {
      const supported = !effective || effective === "auto" || item.formats.includes(effective);
      item.checkbox.disabled = !supported;
      item.row.classList.toggle("disabled", !supported);
      if (!supported && item.checkbox.checked) item.checkbox.checked = false;
      if (!supported) blocked++;
    }
    toolNote.textContent = blocked
      ? `当前接口格式（${_formatText(effective, "")}）不支持服务端工具，换个格式才会生效。`
      : "由服务商在服务端执行，不会出现在本地工具列表里。";
  };
  formatSelect.addEventListener("change", syncTools);
  syncTools();

  const advanced = makeFieldFold("高级设置", [
    field("接口格式", formatSelect,
      "只在自动识别不对时才需要改；保存并应用后生效。"),
    field("服务端工具", toolsWrap,
      "服务商自带的联网搜索：由服务商检索后直接作答，不占用本地工具。"),
  ], `model-advanced-${entry.spec}`, {});

  const bar = el("div", "toolbar");
  bar.append(
    makeButton("保存到配置", () => {
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
      const toolsOn = tools.filter((item) => item.checkbox.checked).map((item) => item.value);
      _setModelField(providers, entry.provider, newName, "model_server_tools",
        toolsOn.length ? toolsOn : null);
      // 同步主 / 子模型选择里的标识
      if (state.mainModel === oldSpec) state.mainModel = `${entry.provider}::${newName}`;
      state.subagentModels = (state.subagentModels || []).map((s) => (s === oldSpec ? `${entry.provider}::${newName}` : s));
      _saveProvidersToState(providers);
      _markModelsDirty();
      closeModal();
      rerenderAiModule();
      showBanner("success", "模型已保存（点窗口顶部「保存」写入配置）");
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
      showBanner("success", "模型已删除（点窗口顶部「保存」写入配置）");
    }, "btn btn-ghost"),
    makeButton("关闭", closeModal)
  );

  openModal(`设置模型 · ${entry.spec}`, [
    info,
    field("模型名", nameInput, "要与服务商文档里的模型 ID 完全一致。"),
    field("上下文长度（Token）", ctxInput,
      "这个模型最多能记住多少 token。留空按默认 128K；拉取模型时会尽量自动填。"),
    field("最大输出", maxOutInput,
      "单次回答最多生成多少 token。留空按服务商返回的默认值。"),
    advanced,
    el("p", "form-hint", "改完点「保存到配置」，再点窗口顶部的「保存」才会写入配置文件。"),
    bar,
  ]);
}
