// Plugins module renderer

function parsePluginFieldValue(fieldType, rawValue) {
  const t = String(fieldType || "str").toLowerCase();
  if (t === "bool") return Boolean(rawValue);
  if (t === "int") return Number.parseInt(String(rawValue || "0"), 10) || 0;
  if (t === "float") return Number.parseFloat(String(rawValue || "0")) || 0;
  if (t === "json") {
    if (!String(rawValue || "").trim()) return null;
    return JSON.parse(String(rawValue));
  }
  return rawValue;
}

function renderPluginsModule() {
  const top = el("div", "toolbar");
  top.append(
    makeButton("刷新", async () => { await ensureModuleData("plugins"); refreshModule(); }),
    makeButton("重载插件", async () => { await cfgApi("POST", "/faust/admin/plugins/reload", { apply_runtime: true, no_initial_chat: true }); await ensureModuleData("plugins"); refreshModule(); }, "btn btn-secondary"),
    makeButton("从 ZIP 安装", async () => {
      const zipPath = await window.api.configOpenFile({ title: "选择插件 ZIP 文件", filters: [{ name: "ZIP", extensions: ["zip"] }] });
      if (!zipPath) return;
      const overwrite = window.confirm("若插件已存在是否覆盖安装?");
      await cfgApi("POST", "/faust/admin/plugins/install-zip", { zip_path: zipPath, overwrite, apply_runtime: true, no_initial_chat: true, reset_dialog: false });
      await ensureModuleData("plugins");
      refreshModule();
    }),
    makeButton("打包为 ZIP", async () => {
      if (!state.selectedPluginId) return;
      const outputDir = await window.api.configOpenDirectory({ title: "选择 ZIP 输出目录" });
      const zipName = window.prompt("ZIP 文件名（可留空）") || "";
      const payload = { plugin_id: state.selectedPluginId };
      if (outputDir) payload.output_dir = outputDir;
      if (zipName.trim()) payload.zip_name = zipName.trim();
      const data = await cfgApi("POST", "/faust/admin/plugins/package-zip", payload);
      showBanner("success", `插件已打包: ${(data.package || {}).zip_path || "-"}`);
    })
  );
  addSection("插件操作", [top]);

  const list = el("div", "list-box");
  for (const p of state.plugins) {
    const pid = String(p.id || "");
    const row = el("div", `list-row clickable ${state.selectedPluginId === pid ? "selected" : ""}`.trim());
    row.append(el("span", "mono", `[${p.enabled ? "ON" : "OFF"}] ${pid} | ${p.version || "-"}`));
    row.addEventListener("click", async () => {
      state.selectedPluginId = pid;
      await ensureModuleData("plugins");
      refreshModule();
    });
    const ops = el("div", "toolbar compact");
    ops.append(
      makeButton("启用", async () => { await cfgApi("POST", `/faust/admin/plugins/${encodeURIComponent(pid)}/enable`, { apply_runtime: true, no_initial_chat: true, reset_dialog: true }); await ensureModuleData("plugins"); refreshModule(); }),
      makeButton("禁用", async () => { await cfgApi("POST", `/faust/admin/plugins/${encodeURIComponent(pid)}/disable`, { apply_runtime: true, no_initial_chat: true, reset_dialog: true }); await ensureModuleData("plugins"); refreshModule(); }),
      makeButton("删除", async () => {
        if (!window.confirm(`确定删除插件 ${pid} ?`)) return;
        await cfgApi("DELETE", `/faust/admin/plugins/${encodeURIComponent(pid)}`, null, { apply_runtime: "true", reset_dialog: "false", no_initial_chat: "true" });
        await ensureModuleData("plugins");
        refreshModule();
      })
    );
    row.append(ops);
    list.append(row);
  }
  addSection("插件列表", [list]);

  const selected = state.plugins.find((x) => String(x.id) === String(state.selectedPluginId));
  if (!selected) {
    addSection("插件详情", [el("div", "empty-state", "请选择插件。")]);
    return;
  }

  const health = selected.health && typeof selected.health === "object" ? selected.health : {};
  const triggerControl = selected.trigger_control && typeof selected.trigger_control === "object" ? selected.trigger_control : {};
  appendToActiveModule(makeInfoCard("插件基本信息", [
    { label: "标识", value: selected.id },
    { label: "名称", value: selected.name },
    { label: "版本", value: selected.version },
    { label: "作者", value: selected.author },
    { label: "主页", value: selected.homepage },
    { label: "启用", value: selected.enabled },
    { label: "优先级", value: selected.priority },
    { label: "描述", value: selected.description },
  ]),
  makeInfoCard("健康状态", [
    { label: "状态", value: health.status || "unknown" },
    { label: "错误信息", value: health.error || "-" },
    { label: "追加过滤器", value: triggerControl.supports_append_filter },
    { label: "触发过滤器", value: triggerControl.supports_fire_filter },
  ]));
  appendToActiveModule(makeTagListCard("权限", selected.permissions || []));

  const toolRows = (selected.tools || []).map((x) => [x.name, x.enabled, x.description || "-"]);
  appendToActiveModule(makeSimpleTableCard("工具注册", ["名称", "启用", "描述"], toolRows));

  const middlewareRows = (selected.middlewares || []).map((x) => [x.name, x.priority, x.enabled, x.description || "-"]);
  appendToActiveModule(makeSimpleTableCard("中间件注册", ["名称", "优先级", "启用", "描述"], middlewareRows));

  const schema = ((selected.config || {}).schema) || [];
  if (!schema.length) {
    addSection("插件配置", [el("div", "empty-state", "该插件未注册配置项。")]);
  } else {
    const form = el("div", "plugin-form");
    for (const item of schema) {
      const key = String(item.key || "");
      if (!key) continue;
      const type = String(item.type || "str");
      const wrap = el("div", "plugin-field");
      wrap.append(el("label", "card-key", `${item.label || key} (${key})`));
      let input;
      const val = state.pluginConfigDraft[key];
      if (type === "bool") {
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(val);
      } else if (type === "json") {
        input = el("textarea", "textarea");
        input.value = toText(val);
      } else if (type === "int" || type === "float") {
        input = el("input", "number");
        input.type = "number";
        input.value = String(val ?? "");
      } else {
        input = el("input", "input");
        input.type = SECRET_KEYS.has(key) ? "password" : "text";
        input.value = val === null || val === undefined ? "" : String(val);
      }
      if (item.description) {
        input.title = String(item.description);
      }
      input.addEventListener("input", () => {
        if (type === "bool") state.pluginConfigDraft[key] = Boolean(input.checked);
        else state.pluginConfigDraft[key] = input.value;
      });
      input.addEventListener("change", () => {
        if (type === "bool") state.pluginConfigDraft[key] = Boolean(input.checked);
      });
      wrap.append(input);
      form.append(wrap);
    }

    const saveBtn = makeButton("保存插件配置并重载", async () => {
      const values = {};
      for (const item of schema) {
        const key = String(item.key || "");
        if (!key) continue;
        values[key] = parsePluginFieldValue(item.type || "str", state.pluginConfigDraft[key]);
      }
      await cfgApi("POST", `/faust/admin/plugins/${encodeURIComponent(selected.id)}/config`, {
        values,
        apply_runtime: true,
        reset_dialog: false,
        no_initial_chat: true,
      });
      await ensureModuleData("plugins");
      showBanner("success", `插件 ${selected.id} 配置已保存并重载。`);
      refreshModule();
    }, "btn btn-primary");

    addSection("插件配置", [form, saveBtn]);
  }
}
