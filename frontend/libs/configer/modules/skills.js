// Skills module renderer

function renderSkillsModule() {
  const agentInput = el("input", "input");
  agentInput.value = state.skillsAgent || state.runtime.current_agent || state.config.public.AGENT_NAME || "";
  agentInput.placeholder = "Agent 名称";

  const top = el("div", "toolbar");
  top.append(
    agentInput,
    makeButton("刷新", async () => {
      state.skillsAgent = agentInput.value.trim();
      await ensureModuleData("skills");
      refreshModule();
    }),
    makeButton("安装(Slug)", async () => {
      const agentName = (agentInput.value || "").trim() || state.skillsAgent || state.runtime.current_agent || state.config.public.AGENT_NAME || "";
      if (!agentName) {
        showBanner("error", "请先填写 Agent 名称。");
        return;
      }
      const slug = window.prompt("输入 Skill slug");
      if (!slug) return;
      const overwrite = window.confirm("若已存在是否覆盖安装?");
      await cfgApi("POST", "/faust/admin/skills/install", { slug: slug.trim(), agent_name: agentName, overwrite });
      state.skillsAgent = agentName;
      await ensureModuleData("skills");
      showBanner("success", `Skill ${slug.trim()} 已安装。`);
      refreshModule();
    }, "btn btn-primary"),
    makeButton("从 ZIP 安装", async () => {
      const agentName = (agentInput.value || "").trim() || state.skillsAgent || state.runtime.current_agent || state.config.public.AGENT_NAME || "";
      if (!agentName) {
        showBanner("error", "请先填写 Agent 名称。");
        return;
      }
      const zipPath = await window.api.configOpenFile({ title: "选择 Skill ZIP 文件", filters: [{ name: "ZIP", extensions: ["zip"] }] });
      if (!zipPath) return;
      const overwrite = window.confirm("若已存在是否覆盖安装?");
      await cfgApi("POST", "/faust/admin/skills/install-zip", { zip_path: zipPath, agent_name: agentName, overwrite });
      state.skillsAgent = agentName;
      await ensureModuleData("skills");
      showBanner("success", "Skill ZIP 安装完成。" );
      refreshModule();
    }),
    makeButton("打开目录", async () => {
      const agentName = (agentInput.value || "").trim() || state.skillsAgent;
      if (!agentName) {
        showBanner("error", "请先填写 Agent 名称。" );
        return;
      }
      const root = await window.api.getFaustbotRoot();
      const basePath = `${root}/agents/${agentName}/skill.d/${state.selectedSkillSlug || ""}`;
      await window.api.configOpenPath(basePath.replace(/\/$/, ""));
    })
  );
  addSection("技能操作", [top]);

  const listCard = el("article", "card full-span");
  listCard.append(el("h3", "card-title", "技能列表"));
  const list = el("table", "simple-table");
  list.innerHTML = "<thead><tr><th>技能标识</th><th>版本</th><th>状态</th><th>操作</th></tr></thead>";
  const tbody = el("tbody", "");
  for (const sk of state.skills) {
    const slug = String(sk.slug || "");
    const row = el("tr", state.selectedSkillSlug === slug ? "selected" : "");
    const statusText = sk.missing ? "缺失" : (sk.enabled ? "已启用" : "已停用");
    row.append(
      el("td", "cell-primary", slug),
      el("td", "", sk.version || "-"),
      el("td", "", statusText)
    );
    row.addEventListener("click", async () => {
      state.selectedSkillSlug = slug;
      await ensureModuleData("skills");
      refreshModule();
    });
    const ops = el("div", "toolbar compact");
    ops.append(
      makeButton("启用", async () => { await cfgApi("POST", `/faust/admin/skills/${encodeURIComponent(slug)}/enable`, { agent_name: state.skillsAgent }); await ensureModuleData("skills"); refreshModule(); }),
      makeButton("禁用", async () => { await cfgApi("POST", `/faust/admin/skills/${encodeURIComponent(slug)}/disable`, { agent_name: state.skillsAgent }); await ensureModuleData("skills"); refreshModule(); }),
      makeButton("删除", async () => {
        if (!window.confirm(`确定删除 Skill ${slug} ?`)) return;
        await cfgApi("DELETE", `/faust/admin/skills/${encodeURIComponent(slug)}`, null, { agent_name: state.skillsAgent });
        await ensureModuleData("skills");
        refreshModule();
      })
    );
    const opsCell = el("td", "");
    opsCell.append(ops);
    row.append(opsCell);
    tbody.append(row);
  }
  if (!state.skills.length) {
    const row = el("tr", "");
    const empty = el("td", "table-empty", "当前没有已安装技能。");
    empty.colSpan = 4;
    row.append(empty);
    tbody.append(row);
  }
  list.append(tbody);
  listCard.append(list);
  appendToActiveModule(listCard);
  if (!state.skillDetail) {
    addSection("技能详情", [el("div", "empty-state", "请选择技能查看详情。")]);
    return;
  }

  const detail = state.skillDetail || {};
  const meta = detail.meta && typeof detail.meta === "object" ? detail.meta : {};
  const files = Array.isArray(detail.files) ? detail.files : [];

  appendToActiveModule(makeInfoCard("技能基本信息", [
    { label: "Slug", value: detail.slug },
    { label: "版本", value: meta.version || "-" },
    { label: "启用状态", value: detail.enabled },
    { label: "安装时间", value: detail.installed_at },
    { label: "来源", value: detail.source },
    { label: "路径", value: detail.path },
  ]),
  makeInfoCard("相关信息", [
    { label: "名称", value: meta.name || meta.title || "-" },
    { label: "作者", value: meta.author || "-" },
    { label: "描述", value: meta.description || "-" },
    { label: "仓库", value: meta.repo || meta.homepage || "-" },
    { label: "入口", value: meta.entry || "-" },
    { label: "许可证", value: meta.license || "-" },
  ]));

  appendToActiveModule(makeTagListCard("Meta 标签", meta.tags || []));

  const fileRows = files.map((f) => [f, f.endsWith(".md") ? "文档" : "文件"]);
  appendToActiveModule(makeSimpleTableCard("Skill 文件清单", ["路径", "类型"], fileRows));
  const skillDocBar = el("div", "toolbar");
  skillDocBar.append(
    makeButton("编辑 SKILL.md", () => openSkillMdModal(String(detail.slug || ""), String(detail.skill_md || ""), state.skillsAgent), "btn btn-primary"),
    makeButton("查看只读", () => {
      const doc = el("textarea", "textarea code-area code-area-lg");
      doc.readOnly = true;
      doc.value = String(detail.skill_md || "");
      openModal(`SKILL.md 预览 - ${String(detail.slug || "")}`, [doc]);
    })
  );
  addSection("SKILL.md", [el("div", "card-help", "SKILL.md 编辑已迁移到弹窗。"), skillDocBar]);
}
