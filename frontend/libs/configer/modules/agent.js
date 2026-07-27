// Agent module renderer

async function ensureModuleData(moduleId) {
  if (moduleId === "overview") {
    try {
      const [pl, sv, err] = await Promise.all([
        cfgApi("GET", "/faust/admin/plugins"),
        cfgApi("GET", "/faust/admin/services"),
        cfgApi("GET", "/faust/admin/log/recent-errors"),
      ]);
      state.plugins = pl.items || [];
      state.services = sv.items || [];
      state.recentErrors = err.errors || [];
    } catch (e) {
      console.warn("overview data fetch error", e);
    }
  }
  if (moduleId === "live2d") {
    const m = await cfgApi("GET", "/faust/admin/live2d/models");
    state.live2dModels = m.items || [];
  }
  if (moduleId === "agent") {
    const a = await cfgApi("GET", "/faust/admin/agents");
    state.agents = a.items || [];
    if (!state.selectedAgent && state.agents.length) {
      state.selectedAgent = state.agents.find((x) => x.is_current)?.name || state.agents[0].name;
    }
    if (state.selectedAgent) {
      const d = await cfgApi("GET", `/faust/admin/agents/${encodeURIComponent(state.selectedAgent)}`);
      state.agentDetail = d.detail || null;
    }
  }
  if (moduleId === "memory") {
    const treeRes = await cfgApi("GET", "/faust/memory/tree", null, { scope: state.kbScope || null });
    state.kbTree = treeRes.tree || null;
    state.kbCurrentDir = normalizeKbPath(state.kbCurrentDir || "/");
    if (!findKbNodeByPath(state.kbTree, state.kbCurrentDir)) {
      state.kbCurrentDir = "/";
    }
    const taskRes = await cfgApi("GET", "/faust/memory/tasks");
    state.kbTasks = taskRes.items || [];
    if (state.kbSelectedPath) {
      try {
        const nodeRes = await cfgApi("GET", "/faust/memory/get", null, { path: state.kbSelectedPath });
        state.kbSelectedContent = String(nodeRes.content || "");
        state.kbSelectedMeta = nodeRes.meta || {};
      } catch (_e) {
        state.kbSelectedPath = "";
        state.kbSelectedContent = "";
        state.kbSelectedMeta = null;
      }
    } else {
      state.kbSelectedContent = "";
      state.kbSelectedMeta = null;
    }
    try {
      const [entities, relations] = await Promise.all([
        cfgApi("GET", "/faust/memory/graph/entities"),
        cfgApi("GET", "/faust/memory/graph/relations"),
      ]);
      state.graphEntities = entities.items || [];
      state.graphRelations = relations.items || [];
    } catch (e) {
      state.graphEntities = [];
      state.graphRelations = [];
    }
  }
  if (moduleId === "runtime") {
    try {
      const sv = await cfgApi("GET", "/faust/admin/services", null, { include_log: true });
      state.services = sv.items || [];
      if (state.selectedService) {
        const sd = await cfgApi("GET", `/faust/admin/services/${encodeURIComponent(state.selectedService)}`, null, { include_log: true });
        state.serviceDetail = sd.item || null;
      }
    } catch (e) {
      console.warn("runtime data fetch error", e);
    }
  }
  if (moduleId === "triggers") {
    try {
      const tr = await cfgApi("GET", "/faust/admin/triggers");
      state.triggers = tr.items || [];
    } catch (e) {
      console.warn("triggers data fetch error", e);
    }
  }
  if (moduleId === "plugins") {
    const pl = await cfgApi("GET", "/faust/admin/plugins");
    state.plugins = pl.items || [];
    if (!state.selectedPluginId && state.plugins.length) {
      state.selectedPluginId = String(state.plugins[0].id || "");
    }
    const selected = state.plugins.find((x) => String(x.id) === String(state.selectedPluginId));
    state.pluginConfigDraft = {};
    if (selected && selected.config && selected.config.values) {
      for (const [k, v] of Object.entries(selected.config.values)) {
        state.pluginConfigDraft[k] = v;
      }
    }
  }
  if (moduleId === "araya") {
    try {
      const data = await cfgApi("GET", "/faust/araya/status");
      state.araya = data.araya || null;
    } catch (e) {
      console.warn("araya data fetch error", e);
      state.araya = null;
    }
  }
  if (moduleId === "components") {
    try {
      const data = await cfgApi("GET", "/faust/components/status");
      state.componentStatus = data;
    } catch (e) {
      console.warn("components data fetch error", e);
      state.componentStatus = null;
    }
  }
  if (moduleId === "mcp") {
    try {
      const data = await cfgApi("GET", "/faust/admin/mcp/servers", null, { include_log: "false" });
      state.mcpServers = data.items || [];
      if (!state.selectedMcpId && state.mcpServers.length) {
        state.selectedMcpId = String(state.mcpServers[0].server_id || state.mcpServers[0].id || "");
      }
      if (state.selectedMcpId && !state.mcpServers.find((x) => String(x.server_id || x.id) === String(state.selectedMcpId))) {
        state.selectedMcpId = state.mcpServers.length ? String(state.mcpServers[0].server_id || state.mcpServers[0].id || "") : "";
      }
    } catch (e) {
      console.warn("mcp data fetch error", e);
      state.mcpServers = [];
    }
  }
  if (moduleId === "skills") {
    try {
      const agentName = state.skillsAgent || "";
      const params = agentName ? { agent_name: agentName } : {};
      const sk = await cfgApi("GET", "/faust/admin/skills", null, params);
      state.skills = sk.items || [];
      state.skillDetail = null;
      // Load skill detail if one is selected
      if (state.selectedSkillSlug) {
        const sd = await cfgApi("GET", `/faust/admin/skills/${encodeURIComponent(state.selectedSkillSlug)}`, null, params);
        state.skillDetail = sd.detail || null;
      }
    } catch (e) {
      console.warn("skills data fetch error", e);
      state.skills = [];
    }
  }
}

function renderAgentModule() {
  const actions = el("div", "toolbar");
  actions.append(
    makeButton("刷新", async () => { await ensureModuleData("agent"); refreshModule(); }),
    makeButton("新建", async () => {
      const name = window.prompt("请输入 Agent 名称");
      if (!name || !name.trim()) return;
      await cfgApi("POST", "/faust/admin/agents", { agent_name: name.trim() });
      await ensureModuleData("agent");
      showBanner("success", `已创建 Agent: ${name.trim()}`);
      refreshModule();
    }),
    makeButton("切换为当前", async () => {
      if (!state.selectedAgent) return;
      await cfgApi("POST", "/faust/admin/agents/switch", { agent_name: state.selectedAgent });
      await reloadAll();
      showBanner("success", `已切换 Agent: ${state.selectedAgent}`);
    }, "btn btn-secondary"),
    makeButton("删除", async () => {
      if (!state.selectedAgent) return;
      const ok = window.confirm(`确定删除 ${state.selectedAgent} ?`);
      if (!ok) return;
      await cfgApi("DELETE", `/faust/admin/agents/${encodeURIComponent(state.selectedAgent)}`);
      state.selectedAgent = "";
      await ensureModuleData("agent");
      refreshModule();
    }),
    makeButton("删除 Checkpoint", async () => {
      if (!state.selectedAgent) return;
      const ok = window.confirm(`确定删除 ${state.selectedAgent} 的 checkpoint?`);
      if (!ok) return;
      await cfgApi("DELETE", `/faust/admin/agents/${encodeURIComponent(state.selectedAgent)}/checkpoint`);
      showBanner("success", "Checkpoint 已删除。");
    })
  );
  addSection("Agent 操作", [actions]);

  const agentRows = state.agents.map((item) => {
    const name = el("span", "mono", String(item.name || ""));
    const editBtn = makeButton("编辑文件", async () => {
      state.selectedAgent = String(item.name || "");
      await ensureModuleData("agent");
      openAgentFilesModal(state.selectedAgent, (state.agentDetail && state.agentDetail.files) || {});
    });
    editBtn.addEventListener("click", (evt) => evt.stopPropagation());
    return [name, item.is_current ? "当前使用" : "备用", editBtn];
  });
  const agentTable = makeSimpleTableCard("Agent 列表", ["名称", "状态", "操作"], agentRows);
  agentTable.querySelectorAll("tbody tr").forEach((tr, index) => {
    const item = state.agents[index];
    if (!item) return;
    tr.classList.add("clickable");
    if (state.selectedAgent === item.name) tr.classList.add("selected");
    tr.addEventListener("click", async () => {
      state.selectedAgent = String(item.name || "");
      await ensureModuleData("agent");
      refreshModule();
    });
  });
  addSection("", [agentTable]);

  if (!state.agentDetail || !state.agentDetail.files) {
    addSection("Agent 文件", [el("div", "empty-state", "请选择 Agent 后编辑文件。")]);
    return;
  }

  const files = state.agentDetail.files || {};
  const controls = el("div", "toolbar");
  controls.append(
    makeButton("编辑 Agent 文件", () => openAgentFilesModal(state.selectedAgent, files), "btn btn-primary"),
    makeButton("打开 Agent 目录", async () => {
      const root = await window.api.getFaustbotRoot();
      const dir = `${root}/agents/${state.selectedAgent}`;
      await window.api.configOpenPath(dir);
    })
  );
  addSection("Agent 文件操作", [controls]);
}
