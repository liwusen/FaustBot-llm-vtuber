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

function _mcpHeadersToText(headers) {
  if (!headers || typeof headers !== "object") return "";
  return Object.entries(headers)
    .map(([key, value]) => `${key}: ${value}`)
    .join("\n");
}

function _mcpTextToHeaders(text) {
  const headers = {};
  for (const line of String(text || "").split(/\r?\n/)) {
    const raw = line.trim();
    if (!raw) continue;
    const idx = raw.indexOf(":");
    if (idx <= 0) continue;
    const key = raw.slice(0, idx).trim();
    const value = raw.slice(idx + 1).trim();
    if (!key) continue;
    headers[key] = value;
  }
  return headers;
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
    headers: existing.headers && typeof existing.headers === "object" ? existing.headers : {},
  } : {
    server_id: "",
    enabled: true,
    description: "",
    transport: "stdio",
    custom: false,
    command: "node",
    args: [],
    url: "",
    headers: {},
  };

  const wrap = el("div", "form-grid");
  // 通用字段：标题 + 描述 + 控件
  const makeField = (labelText, control, hintText) => {
    const field = el("div", "form-field");
    field.append(el("label", "form-field-label", labelText));
    if (hintText) field.append(el("p", "form-hint", hintText));
    field.append(control);
    return field;
  };
  // 开关型字段：复选框与状态文案同排
  const makeToggleField = (labelText, checkbox, hintText, onText, offText) => {
    const field = el("div", "form-field");
    field.append(el("label", "form-field-label", labelText));
    if (hintText) field.append(el("p", "form-hint", hintText));
    const row = el("label", "switch-row");
    row.style.cursor = "pointer";
    const stateText = el("span", "switch-text", checkbox.checked ? onText : offText);
    checkbox.addEventListener("change", () => { stateText.textContent = checkbox.checked ? onText : offText; });
    row.append(checkbox, stateText);
    field.append(row);
    return field;
  };

  const idInput = el("input", "input");
  idInput.type = "text";
  idInput.value = body.server_id;
  idInput.placeholder = "例如 filesystem";
  if (existing) idInput.disabled = true;
  wrap.append(makeField(
    "服务器标识",
    idInput,
    existing ? "服务器的唯一 ID，创建后不可修改。" : "服务器的唯一 ID，用于区分不同 MCP 服务，创建后不可修改。建议使用小写字母与连字符。"
  ));

  const enabledInput = document.createElement("input");
  enabledInput.type = "checkbox";
  enabledInput.checked = !!body.enabled;
  wrap.append(makeToggleField("启用", enabledInput, "关闭后该服务器不会启动，其提供的工具对 Agent 不可见。", "已启用", "已停用"));

  const descInput = el("input", "input");
  descInput.type = "text";
  descInput.value = body.description;
  descInput.placeholder = "简要说明该服务器提供的能力";
  wrap.append(makeField("描述", descInput, "对该 MCP 服务的备注说明，仅用于在列表中辨识，不影响运行。"));

  const stdioRadio = document.createElement("input");
  stdioRadio.type = "radio";
  stdioRadio.name = "mcpTransport";
  stdioRadio.value = "stdio";
  stdioRadio.checked = body.transport !== "sse" && body.transport !== "streamable-http";
  const sseRadio = document.createElement("input");
  sseRadio.type = "radio";
  sseRadio.name = "mcpTransport";
  sseRadio.value = "sse";
  sseRadio.checked = body.transport === "sse";
  const streamableHttpRadio = document.createElement("input");
  streamableHttpRadio.type = "radio";
  streamableHttpRadio.name = "mcpTransport";
  streamableHttpRadio.value = "streamable-http";
  streamableHttpRadio.checked = body.transport === "streamable-http";
  const segment = el("div", "form-segment");
  const segOption = (radio, text) => {
    const lbl = document.createElement("label");
    lbl.append(radio, el("span", "", text));
    return lbl;
  };
  segment.append(
    segOption(stdioRadio, "stdio（本地进程）"),
    segOption(sseRadio, "sse（远程）"),
    segOption(streamableHttpRadio, "streamable-http（远程）")
  );
  wrap.append(makeField(
    "传输模式",
    segment,
    "stdio 通过本地子进程通信；sse / streamable-http 连接远程 HTTP 服务。选择后下方字段会自动切换。"
  ));

  const customInput = document.createElement("input");
  customInput.type = "checkbox";
  customInput.checked = !!body.custom;

  const stdioFields = el("div", "form-grid");
  stdioFields.append(makeToggleField(
    "自定义 stdio server",
    customInput,
    "开启后可自定义启动命令与参数；关闭时使用内置命令，命令字段将被锁定。",
    "自定义命令",
    "使用内置命令"
  ));

  const commandInput = el("input", "input");
  commandInput.type = "text";
  commandInput.value = body.command;
  commandInput.placeholder = "例如 node、python、npx";
  stdioFields.append(makeField("命令", commandInput, "启动 MCP server 的可执行程序，需在系统 PATH 中或使用绝对路径。"));

  const argsInput = el("textarea", "textarea");
  argsInput.value = _mcpArgsToText(body.args);
  argsInput.placeholder = "@modelcontextprotocol/server-filesystem\n/path/to/dir";
  stdioFields.append(makeField("参数", argsInput, "传递给命令的启动参数，每行一个，按顺序拼接。"));
  wrap.append(stdioFields);

  const sseFields = el("div", "form-grid");
  const urlInput = el("input", "input");
  urlInput.type = "text";
  urlInput.value = body.url;
  urlInput.placeholder = "https://example.com/mcp";
  const urlField = makeField("SSE URL", urlInput, "远程 MCP 服务的完整访问地址（含协议与路径）。");
  sseFields.append(urlField);
  const headersInput = el("textarea", "textarea");
  headersInput.value = _mcpHeadersToText(body.headers);
  headersInput.placeholder = "Authorization: Bearer xxxxx";
  sseFields.append(makeField("HTTP Headers", headersInput, "连接远程服务时附带的请求头，每行一条，格式为 key: value（如鉴权 Token）。"));
  wrap.append(sseFields);

  const syncUi = () => {
    const transport = sseRadio.checked ? "sse" : (streamableHttpRadio.checked ? "streamable-http" : "stdio");
    const isHttpLike = transport !== "stdio";
    stdioFields.style.display = isHttpLike ? "none" : "";
    sseFields.style.display = isHttpLike ? "" : "none";
    urlField.querySelector(".form-field-label").textContent = transport === "sse" ? "SSE URL" : "Streamable HTTP URL";
    commandInput.disabled = isHttpLike || !customInput.checked;
  };
  stdioRadio.addEventListener("change", syncUi);
  sseRadio.addEventListener("change", syncUi);
  streamableHttpRadio.addEventListener("change", syncUi);
  customInput.addEventListener("change", syncUi);
  syncUi();

  const actions = el("div", "toolbar");
  actions.append(
    makeButton("保存", async () => {
      const payload = {
        server_id: String(idInput.value || "").trim(),
        enabled: !!enabledInput.checked,
        description: String(descInput.value || "").trim(),
        transport: sseRadio.checked ? "sse" : (streamableHttpRadio.checked ? "streamable-http" : "stdio"),
        custom: !!customInput.checked,
        command: String(commandInput.value || "").trim(),
        args: _mcpTextToArgs(argsInput.value),
        url: String(urlInput.value || "").trim(),
        headers: _mcpTextToHeaders(headersInput.value),
      };
      if (!payload.server_id) {
        window.alert("服务器标识不能为空");
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
  addSection("服务器列表", [list]);

  const selected = _mcpSelectedItem();
  if (!selected) {
    addSection("服务器详情", [el("div", "empty-state", "请选择 MCP 服务器。")]);
    return;
  }

  appendToActiveModule(
    makeInfoCard("基本信息", [
      { label: "服务器标识", value: selected.server_id || selected.id },
      { label: "状态", value: _mcpStatusText(selected) },
      { label: "启用", value: selected.enabled },
      { label: "传输", value: selected.transport || "stdio" },
      { label: "自定义", value: selected.custom },
      { label: "命令", value: selected.command || "builtin" },
      { label: "URL", value: selected.url || "-" },
      { label: "请求头", value: Object.keys(selected.headers || {}).length ? JSON.stringify(selected.headers) : "-" },
      { label: "描述", value: selected.description || "-" },
      { label: "错误", value: selected.error || "-" },
    ])
  );

  const toolRows = (selected.tools || []).map((tool) => [tool.name || "-", tool.title || "-", tool.description || "-"]);
  appendToActiveModule(makeSimpleTableCard("工具列表", ["名称", "标题", "描述"], toolRows));
}