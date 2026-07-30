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

function pluginHealthLabel(plugin) {
  const status = String(((plugin || {}).health || {}).status || "unknown").toLowerCase();
  const map = {
    healthy: "正常",
    ok: "正常",
    warning: "注意",
    error: "异常",
    unknown: "未知",
  };
  return map[status] || status;
}

function mountPluginChart(host, plugin) {
  if (!host || !window.echarts) return;
  const chart = window.echarts.init(host, null, { renderer: "svg" });
  const status = String(((plugin || {}).health || {}).status || "unknown").toLowerCase();
  const accent = status === "error"
    ? "#e54447"
    : status === "warning"
      ? "#f59e0b"
      : (plugin && plugin.enabled ? "#3f6be8" : "#b8c6d8");
  chart.setOption({
    animation: false,
    tooltip: { show: false },
    series: [
      {
        type: "pie",
        radius: ["62%", "84%"],
        silent: true,
        label: { show: false },
        data: [
          { value: plugin && plugin.enabled ? 72 : 38, itemStyle: { color: accent } },
          { value: plugin && plugin.enabled ? 28 : 62, itemStyle: { color: "#e6edf7" } },
        ],
      },
    ],
    graphic: [
      {
        type: "circle",
        left: "center",
        top: "center",
        shape: { r: 12 },
        style: { fill: "#ffffff", stroke: accent, lineWidth: 1.5 },
      },
      {
        type: "text",
        left: "center",
        top: "center",
        style: {
          text: String((plugin && (plugin.name || plugin.id) || "P")).slice(0, 1).toUpperCase(),
          fill: accent,
          fontSize: 12,
          fontWeight: 700,
          textAlign: "center",
          textVerticalAlign: "middle",
        },
      },
    ],
  });
}

function createPluginFieldHeader(item, key) {
  const header = el("div", "field-card-head");
  const wrap = el("div", "field-card-title-wrap");
  wrap.append(el("div", "card-title", item.label || key));
  wrap.append(el("div", "card-key", key));
  header.append(wrap);
  return header;
}

function renderPluginsModule() {
  async function refreshPluginUi() {
    await loadPluginAssets(true);
    await ensureModuleData("plugins");
    refreshModule();
  }

  const top = el("div", "toolbar");
  top.append(
    makeButton("刷新", async () => { await ensureModuleData("plugins"); refreshModule(); }),
    makeButton("重载插件", async () => { await cfgApi("POST", "/faust/admin/plugins/reload", { apply_runtime: true, no_initial_chat: true }); await refreshPluginUi(); }, "btn btn-secondary"),
    makeButton("从 ZIP 安装", async () => {
      const zipPath = await window.api.configOpenFile({ title: "选择插件 ZIP 文件", filters: [{ name: "ZIP", extensions: ["zip"] }] });
      if (!zipPath) return;
      const overwrite = window.confirm("若插件已存在是否覆盖安装?");
      await cfgApi("POST", "/faust/admin/plugins/install-zip", { zip_path: zipPath, overwrite, apply_runtime: true, no_initial_chat: true, reset_dialog: false });
      await refreshPluginUi();
    }),
    makeButton("检查更新", async () => {
      const data = await cfgApi("GET", "/faust/admin/plugin-market/check-updates");
      state.pluginUpdates = data;
      const updates = Array.isArray(data.updates) ? data.updates : [];
      if (updates.length) {
        const names = updates.map((u) => `${u.name || u.id} (${u.installed_version || "?"} → ${u.latest_version})`).join("、");
        showBanner("success", `发现 ${updates.length} 个可更新插件: ${names}`);
      } else {
        showBanner("success", "所有插件均为最新版本。");
      }
      refreshModule();
    }, "btn btn-secondary"),
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
  const proxyWrap = el("div", "switch-row");
  const proxyText = el("span", "switch-text", "gh-proxy 镜像加速");
  const proxyLabel = el("label", "switch");
  const proxyInput = document.createElement("input");
  proxyInput.type = "checkbox";
  proxyInput.checked = Boolean((state.config.public || {}).PLUGIN_MARKET_USE_GH_PROXY);
  const proxySlider = el("span", "switch-slider");
  proxyInput.addEventListener("change", async () => {
    const enabled = Boolean(proxyInput.checked);
    await cfgApi("POST", "/faust/admin/config", { public: { PLUGIN_MARKET_USE_GH_PROXY: enabled } });
    if (state.config.public) state.config.public.PLUGIN_MARKET_USE_GH_PROXY = enabled;
    showBanner("success", enabled ? "插件市场已启用 gh-proxy 镜像。" : "插件市场已关闭 gh-proxy 镜像。");
  });
  proxyLabel.append(proxyInput, proxySlider);
  proxyWrap.append(proxyText, proxyLabel);
  top.append(proxyWrap);
  addSection("插件操作", [top]);

  const listCard = el("article", "card full-span");
  listCard.append(el("h3", "card-title", "插件列表"));
  const listTable = el("table", "simple-table plugin-list-table");
  const listHead = el("thead", "");
  listHead.innerHTML = "<tr><th>插件</th><th>版本</th><th>状态</th><th>操作</th></tr>";
  listTable.append(listHead);
  const listBody = el("tbody", "");
  const chartMounts = [];
  for (const p of state.plugins) {
    const pid = String(p.id || "");
    const row = el("tr", state.selectedPluginId === pid ? "selected" : "");
    row.addEventListener("click", async () => {
      state.selectedPluginId = pid;
      await ensureModuleData("plugins");
      refreshModule();
    });

    const pluginCell = el("td", "plugin-cell");
    const pluginMain = el("div", "plugin-row-main");
    const chartHost = el("div", "plugin-chart-badge");
    chartMounts.push(() => mountPluginChart(chartHost, p));
    const pluginText = el("div", "plugin-row-text");
    pluginText.append(
      el("strong", "plugin-row-title", p.name || pid),
      el("div", "plugin-row-subtitle", p.description || pid)
    );
    pluginMain.append(chartHost, pluginText);
    pluginCell.append(pluginMain);

    const updateEntry = ((state.pluginUpdates || {}).updates || []).find((u) => String(u.id) === pid) || null;
    const versionCell = el("td", "", String(p.version || "-"));
    if (updateEntry) {
      versionCell.append(el("span", "tag-chip", `可更新 → ${updateEntry.latest_version}`));
    }
    const statusCell = el("td", "");
    statusCell.append(el("span", `tag-chip ${p.enabled ? "" : "tag-chip-muted"}`.trim(), p.enabled ? "已启用" : "已关闭"));
    statusCell.append(el("div", "plugin-status-note", pluginHealthLabel(p)));

    const opsCell = el("td", "");
    const ops = el("div", "toolbar compact plugin-row-actions");
    const switchWrap = el("div", "switch-row");
    const switchText = el("span", "switch-text", p.enabled ? "已启用" : "已禁用");
    const switchLabel = el("label", "switch");
    const switchInput = document.createElement("input");
    switchInput.type = "checkbox";
    switchInput.checked = Boolean(p.enabled);
    const switchSlider = el("span", "switch-slider");
    switchInput.addEventListener("change", async () => {
      const enabled = Boolean(switchInput.checked);
      switchText.textContent = enabled ? "已启用" : "已禁用";
      await cfgApi("POST", `/faust/admin/plugins/${encodeURIComponent(pid)}/${enabled ? "enable" : "disable"}`, { apply_runtime: true, no_initial_chat: true, reset_dialog: true });
      await refreshPluginUi();
    });
    switchLabel.append(switchInput, switchSlider);
    switchWrap.append(switchText, switchLabel);
    ops.append(
      switchWrap,
      makeButton("删除", async (evt) => {
        if (evt && typeof evt.stopPropagation === "function") evt.stopPropagation();
        if (!window.confirm(`确定删除插件 ${pid} ?`)) return;
        await cfgApi("DELETE", `/faust/admin/plugins/${encodeURIComponent(pid)}`, null, { apply_runtime: "true", reset_dialog: "false", no_initial_chat: "true" });
        await refreshPluginUi();
      }, "btn btn-ghost")
    );
    if (updateEntry) {
      ops.append(makeButton("更新", async (evt) => {
        if (evt && typeof evt.stopPropagation === "function") evt.stopPropagation();
        await cfgApi("POST", "/faust/admin/plugin-market/sync", { plugin_id: pid, apply_runtime: true, no_initial_chat: true, reset_dialog: false });
        if (state.pluginUpdates && Array.isArray(state.pluginUpdates.updates)) {
          state.pluginUpdates.updates = state.pluginUpdates.updates.filter((u) => String(u.id) !== pid);
        }
        showBanner("success", `插件 ${pid} 已更新到 ${updateEntry.latest_version}`);
        await refreshPluginUi();
      }, "btn btn-primary"));
    }
    opsCell.append(ops);

    row.append(pluginCell, versionCell, statusCell, opsCell);
    listBody.append(row);
  }
  if (!state.plugins.length) {
    const emptyRow = el("tr", "");
    const emptyCell = el("td", "table-empty", "当前没有已安装插件。");
    emptyCell.colSpan = 4;
    emptyRow.append(emptyCell);
    listBody.append(emptyRow);
  }
  listTable.append(listBody);
  listCard.append(listTable);
  appendToActiveModule(listCard);
  requestAnimationFrame(() => chartMounts.forEach((mount) => mount()));

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
      const wrap = el("div", "plugin-field field-card");
      const header = createPluginFieldHeader(item, key);
      wrap.append(header);
      let input;
      const val = state.pluginConfigDraft[key];
      if (type === "bool") {
        const help = item.description ? el("p", "card-help", String(item.description)) : null;
        const row = el("div", "switch-row");
        const txt = el("span", "switch-text", Boolean(val) ? "已启用" : "已禁用");
        const label = el("label", "switch");
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(val);
        const slider = el("span", "switch-slider");
        label.append(input, slider);
        row.append(txt, label);
        input.addEventListener("input", () => {
          state.pluginConfigDraft[key] = Boolean(input.checked);
          txt.textContent = Boolean(input.checked) ? "已启用" : "已禁用";
        });
        input.addEventListener("change", () => {
          state.pluginConfigDraft[key] = Boolean(input.checked);
          txt.textContent = Boolean(input.checked) ? "已启用" : "已禁用";
        });
        if (help) wrap.append(help);
        wrap.append(row);
        form.append(wrap);
        continue;
      } else if (type === "json") {
        if (item.description) wrap.append(el("p", "card-help", String(item.description)));
        input = el("textarea", "textarea");
        input.value = toText(val);
      } else if (type === "int" || type === "float") {
        if (item.description) wrap.append(el("p", "card-help", String(item.description)));
        const spec = getNumberFieldSpec(key, Number(val ?? 0));
        if (spec.type === "range") {
          const sliderWrap = el("div", "slider-field");
          input = el("input", "range-input");
          input.type = "range";
          input.min = String(spec.min);
          input.max = String(spec.max);
          input.step = String(spec.step);
          input.value = String(val ?? spec.min);
          const meter = el("div", "slider-meter");
          const valueText = el("strong", "slider-value", "");
          const hintText = el("span", "slider-hint", `${spec.min} - ${spec.max}${spec.unit || ""}`);
          meter.append(valueText, hintText);
          const syncSlider = () => {
            valueText.textContent = `${Number(input.value || 0).toFixed(spec.step < 1 ? 2 : 0)}${spec.unit || ""}`;
            const ratio = ((Number(input.value) - spec.min) / (spec.max - spec.min || 1)) * 100;
            input.style.setProperty("--range-fill", `${Math.max(0, Math.min(100, ratio))}%`);
          };
          syncSlider();
          input.addEventListener("input", () => {
            syncSlider();
            state.pluginConfigDraft[key] = input.value;
          });
          sliderWrap.append(input, meter);
          wrap.append(sliderWrap);
          form.append(wrap);
          continue;
        }
        input = el("input", "number");
        input.type = "number";
        input.value = String(val ?? "");
      } else {
        if (item.description) wrap.append(el("p", "card-help", String(item.description)));
        input = el("input", "input");
        input.type = SECRET_KEYS.has(key) ? "password" : "text";
        input.value = val === null || val === undefined ? "" : String(val);
      }
      if (item.description && input) input.title = String(item.description);
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
      await loadPluginAssets(true);
      await ensureModuleData("plugins");
      showBanner("success", `插件 ${selected.id} 配置已保存并重载。`);
      refreshModule();
    }, "btn btn-primary");

    addSection("插件配置", [form, saveBtn]);
  }
}
