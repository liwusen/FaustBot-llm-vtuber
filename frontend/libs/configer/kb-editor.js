// KB editor modal + Agent files modal

async function openKbEditorModal(path, initialContent = "", initialMeta = null) {
  const pathInput = el("input", "input");
  pathInput.value = String(path || "");
  pathInput.placeholder = "输入 KB 路径，例如 reactor/core/doc.md";

  const tagsInput = el("input", "input");
  tagsInput.placeholder = "标签（逗号分隔）";
  const meta = initialMeta && typeof initialMeta === "object" ? initialMeta : {};
  const currentTags = Array.isArray(meta.tags) ? meta.tags : [];
  tagsInput.value = currentTags.join(", ");

  const indexChk = document.createElement("input");
  indexChk.type = "checkbox";
  indexChk.checked = true;
  const indexLbl = el("label", "switch-text", "保存后加入索引");

  const area = el("textarea", "textarea code-area code-area-xl");
  area.value = String(initialContent || "");
  area.style.height = "65vh";
  area.style.minHeight = "300px";

  const metaBox = el("div", "card-help");
  const refreshMetaText = () => {
    metaBox.textContent = `更新时间： ${formatScalar(meta.updated_at)} | 声明者： ${formatScalar(meta.declared_by)} | 分块数： ${formatScalar(meta.chunk_count)} | 已索引： ${formatScalar(meta.indexed)}`;
  };
  refreshMetaText();

  const saveAction = async () => {
    const targetPath = normalizeKbPath(pathInput.value.trim());
    if (!targetPath || targetPath === "/") {
      showBanner("error", "请输入有效的 KB 文件路径。");
      return;
    }
    const tags = tagsInput.value
      .split(",")
      .map((x) => x.trim())
      .filter(Boolean);
    const data = await cfgApi("POST", "/faust/memory/save", {
      path: targetPath,
      content: area.value,
      declared_by: "config-center",
      index: indexChk.checked,
      tags,
    });
    const savedMeta = data.meta || {};
    meta.updated_at = savedMeta.updated_at;
    meta.declared_by = savedMeta.declared_by;
    meta.chunk_count = savedMeta.chunk_count;
    meta.indexed = savedMeta.indexed;
    meta.tags = savedMeta.tags || tags;
    refreshMetaText();
    state.kbSelectedPath = targetPath;
    state.kbSelectedContent = area.value;
    state.kbCurrentDir = kbParentPath(targetPath);
    await ensureModuleData("memory");
    showBanner("success", `KB 已保存: ${targetPath}`);
    renderModule();
  };

  const deleteAction = async () => {
    const targetPath = normalizeKbPath(pathInput.value.trim());
    if (!targetPath || targetPath === "/") return;
    if (!window.confirm(`确定删除 ${targetPath} ?`)) return;
    await cfgApi("POST", "/faust/memory/delete", { path: targetPath });
    state.kbSelectedPath = "";
    state.kbSelectedContent = "";
    state.kbCurrentDir = kbParentPath(targetPath);
    await ensureModuleData("memory");
    closeModal();
    showBanner("success", `KB 已删除: ${targetPath}`);
    renderModule();
  };

  const headerBar = el("div", "toolbar");
  headerBar.append(pathInput);
  const settingBar = el("div", "toolbar");
  settingBar.append(tagsInput, indexChk, indexLbl);
  const actionBar = el("div", "toolbar");
  actionBar.append(
    makeButton("保存", saveAction, "btn btn-primary"),
    makeButton("删除", deleteAction),
    makeButton("关闭", closeModal)
  );

  openModal("KB 文档编辑", [headerBar, settingBar, metaBox, area, actionBar]);
}

async function openAgentFilesModal(agentName, files) {
  const targetAgent = String(agentName || "").trim();
  if (!targetAgent) return;

  const areas = new Map();
  const toolbar = el("div", "toolbar");
  const openBtn = makeButton("打开 Agent 目录", async () => {
    const root = await window.api.getFaustbotRoot();
    const dir = `${root}/agents/${targetAgent}`;
    await window.api.configOpenPath(dir);
  });
  const saveBtn = makeButton("保存全部文件", async () => {
    const payload = { files: {} };
    for (const filename of AGENT_FILES) {
      const editor = areas.get(filename);
      payload.files[filename] = editor ? editor.getValue() : "";
    }
    await cfgApi("PUT", `/faust/admin/agents/${encodeURIComponent(targetAgent)}/files`, payload);
    showBanner("success", `Agent 文件已保存: ${targetAgent}`);
    await ensureModuleData("agent");
    renderModule();
  }, "btn btn-primary");
  toolbar.append(openBtn, saveBtn, makeButton("关闭", closeModal));

  const blocks = [toolbar];
  const editorTargets = [];
  for (const filename of AGENT_FILES) {
    const card = el("article", "card full-span");
    card.append(el("h3", "card-title", filename));
    const area = el("div");
    area.style.height = "260px";
    area.style.border = "1px solid var(--line)";
    area.style.borderRadius = "10px";
    area.style.overflow = "hidden";
    const fileObj = files && files[filename];
    const raw = (fileObj && typeof fileObj === "object") ? (fileObj.content || "") : String(fileObj || "");
    card.append(area);
    const isReadonly = fileObj && fileObj.readonly;
    if (isReadonly) {
      card.append(el("small", "hint", "（模板文件，不可编辑 — 修改请更新 agents_template/faust/ 源文件）"));
    }
    editorTargets.push({ filename, area, raw, isReadonly: !!isReadonly });
    blocks.push(card);
  }
  openModal(`Agent 文件编辑 - ${targetAgent}`, blocks);
  for (const item of editorTargets) {
    const editor = await createMonacoEditorHost(item.area, item.raw, {
      language: guessMonacoLanguage(item.filename),
      readOnly: item.isReadonly,
    });
    areas.set(item.filename, editor);
  }
}
