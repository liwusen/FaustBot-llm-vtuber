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

  const editorContainer = el("div");
  editorContainer.style.height = "min(65vh, 600px)";
  editorContainer.style.border = "1px solid var(--line)";
  editorContainer.style.borderRadius = "10px";
  editorContainer.style.overflow = "hidden";
  let _editor = null;

  const metaBox = el("div", "card-help");
  const refreshMetaText = () => {
    metaBox.innerHTML = [
      `<div>更新时间：${formatScalar(meta.updated_at, "更新时间")}</div>`,
      `<div>声明者：${formatScalar(meta.declared_by, "声明者")}</div>`,
      `<div>分块数：${formatScalar(meta.chunk_count, "分块数")}</div>`,
      `<div>已加入索引：${formatScalar(meta.indexed, "状态")}</div>`,
    ].join("");
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
      content: _editor ? _editor.getValue() : "",
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
    state.kbSelectedContent = _editor ? _editor.getValue() : "";
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

  openModal("KB 文档编辑", [headerBar, settingBar, metaBox, editorContainer, actionBar]);
  _editor = await createCodeMirrorEditor(editorContainer, String(initialContent || ""), {
    language: guessEditorLanguage(path),
    readOnly: false,
  });
}

async function openAgentFilesModal(agentName, files) {
  const targetAgent = String(agentName || "").trim();
  if (!targetAgent) return;

  const editors = new Map();
  let activeTab = null;

  // toolbar
  const toolbar = el("div", "toolbar");
  const openBtn = makeButton("打开 Agent 目录", async () => {
    const root = await window.api.getFaustbotRoot();
    const dir = `${root}/agents/${targetAgent}`;
    await window.api.configOpenPath(dir);
  });
  const saveBtn = makeButton("保存全部文件", async () => {
    const payload = { files: {} };
    for (const filename of AGENT_FILES) {
      const editor = editors.get(filename);
      payload.files[filename] = editor ? editor.getValue() : "";
    }
    await cfgApi("PUT", `/faust/admin/agents/${encodeURIComponent(targetAgent)}/files`, payload);
    showBanner("success", `Agent 文件已保存: ${targetAgent}`);
    await ensureModuleData("agent");
    renderModule();
  }, "btn btn-primary");
  toolbar.append(openBtn, saveBtn, makeButton("关闭", closeModal));

  // tab bar
  const tabBar = el("div", "editor-tab-bar");
  const tabButtons = {};
  for (const filename of AGENT_FILES) {
    const btn = el("button", "editor-tab-btn", filename);
    const fileObj = files && files[filename];
    if (fileObj && fileObj.readonly) btn.classList.add("readonly");
    btn.onclick = () => switchTab(filename);
    tabBar.append(btn);
    tabButtons[filename] = btn;
  }

  // editor area — 每个文件一个 pane，创建时逐一亮起确保 CodeMirror 正确测量
  const editorArea = el("div", "editor-area");
  const areaContainers = {};
  const editorTargets = [];
  for (const filename of AGENT_FILES) {
    const pane = el("div", "editor-pane");
    pane.style.display = "none";
    pane.style.height = "min(65vh, 600px)";
    pane.style.border = "1px solid var(--line)";
    pane.style.borderRadius = "10px";
    pane.style.overflow = "hidden";
    const fileObj = files && files[filename];
    const raw = (fileObj && typeof fileObj === "object") ? (fileObj.content || "") : String(fileObj || "");
    const isReadonly = !!(fileObj && fileObj.readonly);
    if (isReadonly) {
      const hint = el("small", "hint", "（模板文件，不可编辑 — 修改请更新 agents_template/faust/ 源文件）");
      pane.after(hint);
      pane.dataset.readonly = "1";
    }
    editorArea.append(pane);
    areaContainers[filename] = pane;
    editorTargets.push({ filename, area: pane, raw, isReadonly });
  }

  function switchTab(filename) {
    if (activeTab === filename) return;
    if (activeTab && tabButtons[activeTab]) {
      tabButtons[activeTab].classList.remove("active");
    }
    tabButtons[filename].classList.add("active");
    activeTab = filename;

    for (const [f, pane] of Object.entries(areaContainers)) {
      pane.style.display = f === filename ? "block" : "none";
    }
    // 更新只读提示
    for (const [f, pane] of Object.entries(areaContainers)) {
      const hint = pane.nextElementSibling;
      if (hint && hint.tagName === "SMALL" && hint.classList.contains("hint")) {
        hint.style.display = f === filename && pane.dataset.readonly === "1" ? "block" : "none";
      }
    }
  }

  openModal(`Agent 文件编辑 - ${targetAgent}`, [toolbar, tabBar, editorArea]);
  // 逐个创建编辑器，创建时显示对应 pane，确保 CodeMirror 能测量尺寸
  for (const item of editorTargets) {
    item.area.style.display = "block";
    try {
      const editor = await createCodeMirrorEditor(item.area, item.raw, {
        language: guessEditorLanguage(item.filename),
        readOnly: item.isReadonly,
      });
      editors.set(item.filename, editor);
    } catch (err) {
      const errMsg = err && (err.message || String(err));
      console.error(`[AgentFiles] 编辑器创建失败 (${item.filename}):`, err);
      item.area.textContent = "";
      const errBox = el("div", "editor-error");
      errBox.textContent = `编辑器加载失败: ${errMsg || "未知错误"}。请检查网络连接后刷新页面重试。`;
      errBox.style.padding = "20px";
      errBox.style.color = "#c00";
      errBox.style.fontSize = "14px";
      item.area.append(errBox);
    }
    item.area.style.display = "none";
  }
  switchTab(AGENT_FILES[0]);
}

