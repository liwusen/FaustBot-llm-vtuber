function _mcpSelectedItem() {
  return state.mcpServers.find((x) => String(x.server_id || x.id) === String(state.selectedMcpId)) || null;
}

function _mcpStatusText(item) {
  if (item && item.status === "running") return "运行中";
  if (item && item.status === "error") return "错误";
  return "已停止";
}

function _mcpStatusDot(item) {
  const color = item && item.status === "running" ? "#4caf50" : (item && item.status === "error" ? "#f44336" : "#8b95a7");
  const dot = document.createElement("span");
  dot.style.cssText = `display:inline-block;width:10px;height:10px;border-radius:50%;background:${color};margin-right:8px;vertical-align:middle`;
  return dot;
}

function _mcpArgsToText(args) {
  return Array.isArray(args) ? args.join("\n") : "";
}

function _mcpTextToArgs(text) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function openMcpEditorModal(existing, onSubmit) {
  const body = existing ? {
    server_id: existing.server_id || existing.id || "",
    enabled: existing.enabled !== false,
    description: existing.description || "",
    transport: existing.transport || "stdio",
    custom: !!existing.custom,
    command: existing.command || "node",
    args: Array.isArray(existing.args) ? existing.args : [],
    url: existing.url || "",
  } : {
    server_id: "",
    enabled: true,
    description: "",
    transport: "stdio",
    custom: false,
    command: "node",
    args: [],
    url: "",
  };

  const wrap = el("div", "plugin-form");
  const makeField = (labelText, input) => {
    const field = el("div", "plugin-field");
    field.append(el("label", "card-key", labelText), input);
    wrap.append(field);
    return field;
  };

  const idInput = el("input", "input");
  idInput.type = "text";
  idInput.value = body.server_id;
  if (existing) idInput.disabled = true;
  makeField("Server ID", idInput);

  const enabledInput = document.createElement("input");
  enabledInput.type = "checkbox";
  enabledInput.checked = !!body.enabled;
  makeField("启用", enabledInput);

  const descInput = el("input", "input");
  descInput.type = "text";
  descInput.value = body.description;
  makeField("描述", descInput);

  const transportWrap = el("div", "plugin-field");
  transportWrap.append(el("label", "card-key", "传输模式"));
  const transportRow = el("div", "toolbar compact");
  const stdioRadio = document.createElement("input");
  stdioRadio.type = "radio";
  stdioRadio.name = "mcpTransport";
  stdioRadio.value = "stdio";
  stdioRadio.checked = body.transport !== "sse";
  const sseRadio = document.createElement("input");
  sseRadio.type = "radio";
  sseRadio.name = "mcpTransport";
  sseRadio.value = "sse";
  sseRadio.checked = body.transport === "sse";
  transportRow.append(el("label", "", "stdio"), stdioRadio, el("label", "", "sse"), sseRadio);
  transportWrap.append(transportRow);
  wrap.append(transportWrap);

  const customInput = document.createElement("input");
  customInput.type = "checkbox";
  customInput.checked = !!body.custom;

  const stdioFields = el("div", "plugin-form");
  const customField = makeField("自定义 stdio server", customInput);
  stdioFields.append(customField);

  const commandInput = el("input", "input");
  commandInput.type = "text";
  commandInput.value = body.command;
  const commandField = el("div", "plugin-field");
  commandField.append(el("label", "card-key", "命令"), commandInput);
  stdioFields.append(commandField);

  const argsInput = el("textarea", "textarea");
  argsInput.value = _mcpArgsToText(body.args);
  const argsField = el("div", "plugin-field");
  argsField.append(el("label", "card-key", "参数（每行一个）"), argsInput);
  stdioFields.append(argsField);
  wrap.append(stdioFields);

  const sseFields = el("div", "plugin-form");
  const urlInput = el("input", "input");
  urlInput.type = "text";
  urlInput.value = body.url;
  const urlField = el("div", "plugin-field");
  urlField.append(el("label", "card-key", "SSE URL"), urlInput);
  sseFields.append(urlField);
  wrap.append(sseFields);

  const syncUi = () => {
    const transport = sseRadio.checked ? "sse" : "stdio";
    stdioFields.style.display = transport === "sse" ? "none" : "";
    sseFields.style.display = transport === "sse" ? "" : "none";
    commandInput.disabled = transport === "sse" || !customInput.checked;
    argsInput.disabled = transport === "sse" ? false : false;
  };
  stdioRadio.addEventListener("change", syncUi);
  sseRadio.addEventListener("change", syncUi);
  customInput.addEventListener("change", syncUi);
  syncUi();

  const actions = el("div", "toolbar");
  actions.append(
    makeButton("保存", async () => {
      const payload = {
        server_id: String(idInput.value || "").trim(),
        enabled: !!enabledInput.checked,
        description: String(descInput.value || "").trim(),
        transport: sseRadio.checked ? "sse" : "stdio",
        custom: !!customInput.checked,
        command: String(commandInput.value || "").trim(),
        args: _mcpTextToArgs(argsInput.value),
        url: String(urlInput.value || "").trim(),
      };
      if (!payload.server_id) {
        window.alert("Server ID 不能为空");
        return;
      }
      await onSubmit(payload);
      closeModal();
    }, "btn btn-primary"),
    makeButton("取消", () => closeModal())
  );

  openModal(existing ? `编辑 MCP Server: ${body.server_id}` : "新建 MCP Server", [wrap, actions]);
}

function _showMcpLogs(item) {
  const box = el("textarea", "textarea");
  box.readOnly = true;
  box.style.minHeight = "360px";
  box.value = Array.isArray(item.log_tail) ? item.log_tail.join("\n") : "（无日志）";
  openModal(`MCP 日志: ${item.server_id || item.id}`, [box]);
}

function renderMcpModule() {
  const top = el("div", "toolbar");
  top.append(
    makeButton("刷新", async () => { await ensureModuleData("mcp"); refreshModule(); }),
    makeButton("添加 Server", async () => {
      openMcpEditorModal(null, async (payload) => {
        await cfgApi("PUT", `/faust/admin/mcp/${encodeURIComponent(payload.server_id)}`, payload);
        state.selectedMcpId = payload.server_id;
        await ensureModuleData("mcp");
        refreshModule();
      });
    }, "btn btn-primary")
  );
  addSection("MCP 操作", [top]);

  const list = el("div", "list-box");
  for (const item of state.mcpServers) {
    const serverId = String(item.server_id || item.id || "");
    const row = el("div", `list-row clickable ${state.selectedMcpId === serverId ? "selected" : ""}`.trim());
    const title = el("div", "");
    title.append(_mcpStatusDot(item), el("span", "mono", `${serverId} | ${item.transport || "stdio"} | ${_mcpStatusText(item)} | tools=${item.tool_count || 0}`));
    row.append(title);
    const ops = el("div", "toolbar compact");
    ops.addEventListener("click", (evt) => evt.stopPropagation());
    ops.append(
      makeButton(item.enabled ? "停用" : "启用", async () => {
        const path = item.enabled ? `/faust/admin/mcp/${encodeURIComponent(serverId)}/stop` : `/faust/admin/mcp/${encodeURIComponent(serverId)}/start`;
        await cfgApi("POST", path, {});
        await ensureModuleData("mcp");
        refreshModule();
      }),
      makeButton("编辑", async () => {
        state.selectedMcpId = serverId;
        openMcpEditorModal(item, async (payload) => {
          await cfgApi("PUT", `/faust/admin/mcp/${encodeURIComponent(serverId)}`, payload);
          await ensureModuleData("mcp");
          refreshModule();
        });
      }),
      makeButton("查看日志", async () => {
        const data = await cfgApi("GET", "/faust/admin/mcp/servers", null, { include_log: "true" });
        const fresh = (data.items || []).find((x) => String(x.server_id || x.id) === serverId) || item;
        _showMcpLogs(fresh);
      }),
      makeButton("删除", async () => {
        if (!window.confirm(`确定删除 MCP server ${serverId} ?`)) return;
        await cfgApi("DELETE", `/faust/admin/mcp/${encodeURIComponent(serverId)}`);
        if (state.selectedMcpId === serverId) state.selectedMcpId = "";
        await ensureModuleData("mcp");
        refreshModule();
      })
    );
    row.append(ops);
    row.addEventListener("click", () => {
      state.selectedMcpId = serverId;
      refreshModule();
    });
    list.append(row);
  }
  addSection("Server 列表", [list]);

  const selected = _mcpSelectedItem();
  if (!selected) {
    addSection("Server 详情", [el("div", "empty-state", "请选择 MCP server。")]);
    return;
  }

  appendToActiveModule(
    makeInfoCard("基本信息", [
      { label: "Server ID", value: selected.server_id || selected.id },
      { label: "状态", value: _mcpStatusText(selected) },
      { label: "启用", value: selected.enabled },
      { label: "传输", value: selected.transport || "stdio" },
      { label: "自定义", value: selected.custom },
      { label: "命令", value: selected.command || "builtin" },
      { label: "URL", value: selected.url || "-" },
      { label: "描述", value: selected.description || "-" },
      { label: "错误", value: selected.error || "-" },
    ])
  );

  const toolRows = (selected.tools || []).map((tool) => [tool.name || "-", tool.title || "-", tool.description || "-"]);
  appendToActiveModule(makeSimpleTableCard("工具列表", ["名称", "标题", "描述"], toolRows));
}