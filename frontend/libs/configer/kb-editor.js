// Agent files modal（KB 文档编辑已改为记忆页内联编辑器）

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

