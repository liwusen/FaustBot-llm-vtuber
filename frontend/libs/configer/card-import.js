// SillyTavern 角色卡导入弹窗

const CARD_IMPORT_NAME_RE = /^[A-Za-z0-9_.-]+$/;
// 世界书逐条嵌入可能耗时数十秒到分钟级，放宽 IPC 默认 30s 超时
const CARD_IMPORT_TIMEOUT_MS = 10 * 60 * 1000;

function cardImportRow(label, value) {
  const row = el("div", "kv-row");
  row.append(el("span", "kv-key", label), el("span", "kv-inline-value", String(value || "-")));
  return row;
}

function cardImportSummary(preview) {
  const book = preview.lorebook || {};
  const wrap = el("div", "");
  wrap.append(
    cardImportRow("卡片名称", preview.name),
    cardImportRow("规格", preview.spec),
    cardImportRow("作者", preview.creator),
    cardImportRow("版本", preview.character_version),
    cardImportRow("标签", (preview.tags || []).join("、")),
    cardImportRow("世界书", book.total ? `${book.book}（启用 ${book.enabled} / 共 ${book.total}）` : "无"),
    cardImportRow("立绘", preview.has_avatar ? "有 → 写入记忆附件" : "无"),
    cardImportRow(
      "卡片附加约束",
      [
        preview.has_system_prompt ? "system_prompt" : "",
        preview.has_post_history_instructions ? "post_history_instructions" : "",
      ].filter(Boolean).join("、")
    ),
    cardImportRow("来源", `${preview.source_format} · ${preview.source_path}`)
  );
  if (preview.description_preview) {
    wrap.append(el("p", "card-help", `设定摘要：${preview.description_preview}`));
  }
  if (preview.first_mes_preview) {
    wrap.append(el("p", "card-help", `开场白摘要：${preview.first_mes_preview}`));
  }
  return wrap;
}

function cardImportErrorMessage(err) {
  const response = err && err.response;
  if (response && response.detail) return String(response.detail);
  return String((err && err.message) || err);
}

function openCardImportModal(preview, cardPath) {
  const nameInput = el("input", "input");
  nameInput.type = "text";
  nameInput.value = String(preview.suggested_name || "");
  nameInput.placeholder = "Agent 目录名";

  const nameField = el("div", "");
  nameField.append(
    el("label", "kv-key", "Agent 目录名"),
    nameInput,
    el("p", "card-help", "只能包含字母、数字、下划线、横线和点；创建为 ~/.faustbot/agents/<目录名>，AGENT.md 恒为 faust 模板。")
  );

  const switchLabel = el("label", "switch-row");
  const switchCheck = el("input", "");
  switchCheck.type = "checkbox";
  switchCheck.checked = false;
  switchLabel.append(
    el("span", "card-help", "创建后切换为当前角色（会重建运行时并重置当前对话）"),
    switchCheck
  );

  const status = el("p", "card-help", "");
  const importBtn = makeButton("导入", () => doImport(), "btn btn-primary");
  const cancelBtn = makeButton("取消", () => closeModal());

  const validate = () => {
    const ok = CARD_IMPORT_NAME_RE.test(String(nameInput.value || "").trim());
    importBtn.disabled = !ok;
    if (!ok) status.textContent = "目录名非法：只能包含字母、数字、下划线、横线和点。";
    else status.textContent = "";
    return ok;
  };

  const setFormBusy = (busy) => {
    importBtn.disabled = busy;
    cancelBtn.disabled = busy;
    nameInput.disabled = busy;
    switchCheck.disabled = busy;
  };

  async function doImport() {
    if (!validate()) return;
    setFormBusy(true);
    status.textContent = "正在解析卡片、写入角色文件与记忆库（世界书逐条嵌入，可能耗时数十秒）…";
    try {
      const result = await cfgApi(
        "POST",
        "/faust/admin/agents/import-card",
        { card_path: cardPath, agent_name: String(nameInput.value || "").trim() },
        null,
        { timeoutMs: CARD_IMPORT_TIMEOUT_MS }
      );
      const book = result.lorebook || {};
      const parts = [];
      if (book.enabled) parts.push(`世界书 ${book.imported}/${book.enabled}`);
      if (result.avatar) parts.push("立绘已存入记忆");
      closeModal();
      if (book.error_count) {
        const detail = (book.errors || []).slice(0, 3).join("；");
        showBanner("error", `已创建 Agent ${result.agent_name}，但世界书有 ${book.error_count} 条写入失败：${detail}`);
      } else {
        showBanner("success", `已从角色卡创建 Agent: ${result.agent_name}${parts.length ? `（${parts.join("，")}）` : ""}`);
      }
      if (switchCheck.checked) {
        await cfgApi("POST", "/faust/admin/agents/switch", { agent_name: result.agent_name });
        await reloadAll();
      } else {
        state.selectedAgent = result.agent_name;
        await ensureModuleData("agent");
        refreshModule();
      }
    } catch (err) {
      setBusy(false);
      status.textContent = `导入失败：${cardImportErrorMessage(err)}`;
    }
  }

  nameInput.addEventListener("input", validate);
  validate();
  const actions = el("div", "toolbar");
  actions.append(importBtn, cancelBtn);
  openModal("从角色卡导入", [cardImportSummary(preview), nameField, switchLabel, status, actions]);
}

async function pickAndImportCharacterCard() {
  const cardPath = await window.api.configOpenFile({
    title: "选择角色卡（SillyTavern PNG / JSON）",
    filters: [{ name: "角色卡", extensions: ["png", "json"] }],
  });
  if (!cardPath) return;
  let preview;
  try {
    preview = await cfgApi("POST", "/faust/admin/agents/parse-card", { card_path: cardPath });
  } catch (err) {
    showBanner("error", `角色卡解析失败：${cardImportErrorMessage(err)}`);
    return;
  }
  openCardImportModal(preview, cardPath);
}
