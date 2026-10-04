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

  const field = (label, node) => {
    const box = el("div", "form-field");
    box.append(el("label", "form-field-label", label));
    const ctl = el("div", "form-field-control");
    ctl.append(node);
    box.append(ctl);
    return box;
  };

  // 手动添加模型（Modal 内）
  const modelInput = el("input", "input");
  modelInput.placeholder = "模型名（如 deepseek-chat）";
  const addModelBtn = makeButton("添加模型", () => {
    const m = String(modelInput.value || "").trim();
    if (!m) { showBanner("error", "请输入模型名"); return; }
    const providers = JSON.parse(JSON.stringify(state.providers || []));
    const pname = (existing ? existing.name : String(nameInput.value || "").trim());
    let target = providers.find((p) => p.name === pname);
    if (!target) {
      // 新增场景：表单里的 provider 尚未进入 state，先按表单值占位
      const base = String(urlInput.value || "").trim();
      if (!pname || !base) { showBanner("error", "请先填写名称与 Base URL"); return; }
      target = {
        name: pname,
        base_url: base,
        key: (keyInput.value && keyInput.value !== "********") ? keyInput.value : null,
        models: [],
        thinking_type: thinkingSelect.value,
        opencode_go: ogCheckbox.checked,
      };
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
    const name = isEdit ? existing.name : String(nameInput.value || "").trim();
    const base = String(urlInput.value || "").trim();
    if (!name || !base) { showBanner("error", "请填写名称与 Base URL"); return; }
    const key = keyInput.value && keyInput.value !== "********" ? keyInput.value : (existing ? existing.key : null);
    try {
      // 1) 先保存：把当前表单（含新 provider）提交到后端统一保存接口
      const providers = JSON.parse(JSON.stringify(state.providers || []));
      let target = providers.find((p) => p.name === name);
      if (!target) {
        providers.push({ name, base_url: base, key: key || null, models: [], thinking_type: thinkingSelect.value, opencode_go: ogCheckbox.checked });
      } else {
        target.base_url = base;
        if (key) target.key = key;
        target.thinking_type = thinkingSelect.value;
        target.opencode_go = ogCheckbox.checked;
      }
      state.providers = providers;
      await cfgApi("POST", "/faust/admin/config", {
        providers: state.providers,
        main_model: state.mainModel,
        subagent_models: state.subagentModels,
      });
      // 2) 再加载模型（后端会清空该 provider 已有模型后重新拉取）
      const r = await cfgApi("POST", `/faust/admin/providers/${encodeURIComponent(name)}/load-models`,
        null, null);
      // 3) 用最新列表覆盖该 provider 的 models（不覆盖其它 provider 的未保存改动）；
      //    同时接收后端从 /models 元数据自动填充的上下文长度
      const next = JSON.parse(JSON.stringify(state.providers || []));
      const t = next.find((p) => p.name === name);
      if (t) {
        t.models = (r.models || []).slice();
        if (r.model_context_lengths) t.model_context_lengths = { ...r.model_context_lengths };
      }
      _saveProvidersToState(next);
      const filled = Object.keys(r.model_context_lengths || {}).length;
      showBanner(
        "success",
        `模型加载成功: ${(r.models || []).length} 个`
          + (filled ? `，其中 ${filled} 个模型的上下文长度由 /models 自动填充` : "")
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
      const base = String(urlInput.value || "").trim();
      if (!name || !base) { showBanner("error", "名称与 Base URL 必填"); return; }
      const providers = JSON.parse(JSON.stringify(state.providers || []));
      if (isEdit) {
        const target = providers.find((p) => p.name === name);
        if (target) {
          target.base_url = base;
          if (keyInput.value && keyInput.value !== "********") target.key = keyInput.value;
          target.thinking_type = thinkingSelect.value;
          target.opencode_go = ogCheckbox.checked;
        }
      } else {
        if (providers.some((p) => p.name === name)) { showBanner("error", "Provider 已存在"); return; }
        providers.push({ name, base_url: base, key: (keyInput.value && keyInput.value !== "********") ? keyInput.value : null, models: [], thinking_type: thinkingSelect.value, opencode_go: ogCheckbox.checked });
      }
      _saveProvidersToState(providers);
      closeModal();
      rerenderAiModule();
      showBanner("success", isEdit ? "Provider 已更新（保存后生效）" : "Provider 已添加（保存后生效）");
    }, "btn btn-primary"),
    makeButton("关闭", closeModal)
  );

  const tip = el("p", "card-help", "新增 Provider 后可在下方 Models 列表勾选主模型与 Subagent 模型；所有改动随顶部「保存」统一生效。「Opencode Go 适配头」适用于 OpenCode Go 订阅端点：启用后每个请求自动附带 x-opencode-session 会话头（用于路由优化与提示缓存）。");
  openModal(isEdit ? `编辑 Provider - ${existing.name}` : "添加 AI Provider", [
    field("名称", nameInput),
    field("Base URL", urlInput),
    field("API Key", keyInput),
    field("Thinking 格式", thinkingSelect),
    field("Opencode Go 适配头", ogRow),
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
      // 上下文长度随重命名从旧模型名迁移到新模型名
      _setModelContextLength(providers, entry.provider, entry.model, null);
      _setModelContextLength(providers, entry.provider, newName, _parseContextLength(ctxInput.value));
      // 同步主/Subagent 选择里的 spec 引用
      if (state.mainModel === oldSpec) state.mainModel = `${entry.provider}::${newName}`;
      state.subagentModels = (state.subagentModels || []).map((s) => (s === oldSpec ? `${entry.provider}::${newName}` : s));
      _saveProvidersToState(providers);
      _markModelsDirty();
      closeModal();
      rerenderAiModule();
      showBanner("success", "模型已重命名（保存后生效）");
    }, "btn btn-primary"),
    makeButton("删除模型", () => {
      const providers = JSON.parse(JSON.stringify(state.providers || []));
      const target = providers.find((p) => p.name === entry.provider);
      if (target) {
        target.models = (target.models || []).filter((m) => m !== entry.model);
        // 同步删除该模型名的上下文长度配置
        _setModelContextLength(providers, entry.provider, entry.model, null);
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
  openModal(`编辑模型 - ${entry.spec}`, [nameInput, ctxField, bar]);
}

// ── 模块渲染入口 ──

function renderProviderWizard() {
  // ── Providers 列表 ──
  const providerRows = (state.providers || []).map((p) => [
    p.name,
    p.base_url,
    p.thinking_type || "qwen",
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
  const providersCard = makeSimpleTableCard("AI Providers", ["名称", "Base URL", "Thinking", "模型数", "操作"], providerRows);

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

    return [entry.provider, entry.model, ctxWrap, radioWrap, subWrap, ops];
  });
  const modelsCard = makeSimpleTableCard("Models", ["Provider", "模型", "上下文长度", "主模型", "Subagent", "操作"], modelRows, {
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
    }, "btn btn-secondary")
  );

  addSection("", [addBar]);
  addSection("", [providersCard]);
  addSection("", [modelsCard]);
  if (!modelEntries.length) {
    addSection("提示", [el("div", "empty-state", "暂无模型。添加 Provider 后点击「自动加载模型」或手动添加。")]);
  }
}
