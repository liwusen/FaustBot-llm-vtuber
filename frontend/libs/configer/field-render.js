// Config field rendering utilities

function toText(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch (_e) {
    return String(v);
  }
}

function isMaskedSecret(value) {
  return typeof value === "string" && /^\*+$/.test(value);
}

function normalizeNumberInput(raw, fallback = 0) {
  if (raw === "" || raw === null || raw === undefined) return null;
  const num = Number(raw);
  return Number.isFinite(num) ? num : fallback;
}

function getMeta(key) {
  return META[key] || { label: key, help: "" };
}

function updateValue(scope, key, nextValue) {
  const original = state.original[scope][key];
  state.config[scope][key] = nextValue;
  const dirtyMap = state.dirty[scope];
  if (SECRET_KEYS.has(key)) {
    if (typeof nextValue === "string" && nextValue.length > 0) dirtyMap.set(key, nextValue);
    else dirtyMap.delete(key);
  } else if (nextValue === original) {
    dirtyMap.delete(key);
  } else {
    dirtyMap.set(key, nextValue);
  }
  refreshDirtyUI();
}

function refreshDirtyUI() {
  const count = state.dirty.public.size + state.dirty.private.size;
  if (count <= 0) {
    els.dirtyBadge.classList.add("hidden");
    els.dirtyBadge.textContent = "0 unsaved change";
  } else {
    els.dirtyBadge.classList.remove("hidden");
    els.dirtyBadge.textContent = `${count} unsaved change${count > 1 ? "s" : ""}`;
  }
}

function makeFieldCard(scope, key, value) {
  const meta = getMeta(key);
  const card = el("article", `card full-span ${state.dirty[scope].has(key) ? "dirty" : ""}`.trim());
  const title = el("h3", "card-title", meta.label);
  const code = el("div", "card-key", key);
  const help = el("p", "card-help", meta.help || "");
  const controlWrap = el("div", "field-wrap");
  const currentValue = state.config[scope][key];
  const options = FIELD_OPTIONS[key] || null;

  if (typeof value === "boolean") {
    const row = el("div", "switch-row");
    const txt = el("span", "switch-text", currentValue ? "已启用" : "已禁用");
    const label = el("label", "switch");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = Boolean(currentValue);
    const slider = el("span", "switch-slider");
    input.addEventListener("change", () => {
      const next = Boolean(input.checked);
      txt.textContent = next ? "已启用" : "已禁用";
      updateValue(scope, key, next);
      renderModule();
    });
    label.append(input, slider);
    row.append(txt, label);
    controlWrap.append(row);
  } else if (options && options.length) {
    const select = el("select", "select");
    for (const opt of options) {
      const item = document.createElement("option");
      item.value = opt;
      item.textContent = opt;
      if (String(currentValue || "") === String(opt)) item.selected = true;
      select.append(item);
    }
    select.addEventListener("change", () => {
      updateValue(scope, key, select.value);
      renderModule();
    });
    controlWrap.append(select);
  } else if (TEXTAREA_KEYS.has(key)) {
    const area = el("textarea", "textarea");
    area.value = String(currentValue ?? "");
    area.addEventListener("input", () => {
      updateValue(scope, key, area.value);
      card.classList.add("dirty");
    });
    controlWrap.append(area);
  } else if (SECRET_KEYS.has(key)) {
    const input = el("input", "input");
    input.type = "password";
    input.name = key;
    input.value = state.dirty[scope].get(key) || "";
    input.placeholder = isMaskedSecret(value) ? "已设置，保持不变" : "请输入密钥";
    input.autocomplete = "off";
    input.addEventListener("input", () => {
      updateValue(scope, key, input.value);
      card.classList.toggle("dirty", input.value.length > 0);
    });
    controlWrap.append(input);
  } else if (typeof value === "number") {
    const input = el("input", "number");
    input.type = "number";
    input.name = key;
    input.value = String(currentValue ?? 0);
    if (key === "TEXT_CHAT_BAR_Y_FACTOR") {
      input.step = "0.01";
      input.min = "0";
      input.max = "1";
    } else {
      input.step = Number.isInteger(value) ? "1" : "0.01";
    }
    input.addEventListener("input", () => {
      const parsed = normalizeNumberInput(input.value, value);
      updateValue(scope, key, parsed);
      card.classList.add("dirty");
    });
    controlWrap.append(input);
  } else if (key === "TTS_REFER_WAV_PATH") {
    const row = el("div", "toolbar");
    const input = el("input", "input");
    input.type = "text";
    input.value = currentValue === null || currentValue === undefined ? "" : String(currentValue);
    input.placeholder = "选择或输入参考音频文件路径";
    input.addEventListener("input", () => {
      updateValue(scope, key, input.value);
      card.classList.add("dirty");
    });
    const pickButton = makeButton("选择文件", async () => {
      const filePath = await window.api.configOpenFile({
        title: "选择 TTS 参考音频",
        filters: [
          { name: "Audio", extensions: ["wav", "mp3", "flac", "m4a", "ogg"] },
          { name: "All Files", extensions: ["*"] },
        ],
      });
      if (!filePath) return;
      input.value = filePath;
      updateValue(scope, key, filePath);
      card.classList.add("dirty");
    }, "btn btn-secondary");
    row.append(input, pickButton);
    controlWrap.append(row);
  } else {
    const input = el("input", "input");
    input.type = "text";
    input.name = key;
    input.value = currentValue === null || currentValue === undefined ? "" : String(currentValue);
    input.addEventListener("input", () => {
      updateValue(scope, key, input.value);
      card.classList.add("dirty");
    });
    controlWrap.append(input);
  }

  card.append(title, code, help, controlWrap);
  return card;
}

function pickModuleFields(moduleId) {
  const publicKeys = Object.keys(state.config.public || {}).sort((a, b) => a.localeCompare(b));
  const privateKeys = Object.keys(state.config.private || {}).sort((a, b) => a.localeCompare(b));

  if (moduleId === "ai") {
    return {
      publicKeys: AI_PUBLIC_KEYS.filter((k) => publicKeys.includes(k)),
      privateKeys: AI_PRIVATE_KEYS.filter((k) => privateKeys.includes(k)),
    };
  }
  if (moduleId === "live2d") {
    const modelType = String(state.config.public.MODEL_TYPE || "live2d").toLowerCase();
    if (modelType === "vrm") {
      const vrmOnly = LIVE2D_KEYS.filter((k) => {
        if (k === "LIVE2D_MODEL_PATH" || k === "LIVE2D_MODEL_X" || k === "LIVE2D_MODEL_Y") return false;
        return publicKeys.includes(k);
      });
      return { publicKeys: vrmOnly, privateKeys: [] };
    }
    return { publicKeys: LIVE2D_KEYS.filter((k) => publicKeys.includes(k)), privateKeys: [] };
  }
  if (moduleId === "speech") {
    const modeTts = String(state.config.public.TTS_MODE || "").toLowerCase();
    const modeAsr = String(state.config.public.ASR_MODE || "").toLowerCase();
    const isCloud = modeTts === "faustbot-cloud" || modeAsr === "faustbot-cloud";
    const isLocalTts = modeTts === "local";
    const isLocalAsr = modeAsr === "local";
    const pub = SPEECH_PUBLIC_KEYS.filter((k) => publicKeys.includes(k));
    const pri = SPEECH_PRIVATE_KEYS.filter((k) => privateKeys.includes(k));
    return {
      publicKeys: pub.filter((k) => {
        if (k.startsWith("OPENAI_TTS_") && modeTts !== "openai") return false;
        if (k.startsWith("OPENAI_ASR_") && modeAsr !== "openai") return false;
        if (k.startsWith("EDGE_TTS_") && modeTts !== "edge-tts") return false;
        if (k.startsWith("FAUSTBOT_CLOUD_") && !isCloud) return false;
        if ((k === "TTS_REFER_WAV_PATH" || k === "TTS_PROMPT_TEXT" || k === "TTS_PROMPT_LANGUAGE") && !isLocalTts) return false;
        return true;
      }),
      privateKeys: pri.filter((k) => {
        if (k.startsWith("OPENAI_TTS_") && modeTts !== "openai") return false;
        if (k.startsWith("OPENAI_ASR_") && modeAsr !== "openai") return false;
        if (k.startsWith("FAUSTBOT_CLOUD_") && !isCloud) return false;
        return true;
      }),
    };
  }
  const usedPublic = new Set([...AI_PUBLIC_KEYS, ...LIVE2D_KEYS, ...SPEECH_PUBLIC_KEYS]);
  const usedPrivate = new Set([...AI_PRIVATE_KEYS, ...SPEECH_PRIVATE_KEYS]);
  return {
    publicKeys: publicKeys.filter((k) => !usedPublic.has(k)),
    privateKeys: privateKeys.filter((k) => !usedPrivate.has(k)),
  };
}

function renderConfigModule(moduleId) {
  const fields = pickModuleFields(moduleId);
  if (!fields.publicKeys.length && !fields.privateKeys.length) {
    addSection("提示", [el("div", "empty-state", "当前模块暂无可编辑字段。")]);
    return;
  }
  // 分离基础配置和高级配置
  const basicPub = [], advancedPub = [];
  for (const key of fields.publicKeys) {
    if (ADVANCED_KEYS.has(key)) advancedPub.push(key);
    else basicPub.push(key);
  }
  const basicPri = [], advancedPri = [];
  for (const key of fields.privateKeys) {
    if (ADVANCED_KEYS.has(key)) advancedPri.push(key);
    else basicPri.push(key);
  }

  // 渲染基础配置
  for (const key of basicPub) {
    els.cardsRoot.append(makeFieldCard("public", key, state.config.public[key]));
  }
  for (const key of basicPri) {
    els.cardsRoot.append(makeFieldCard("private", key, state.config.private[key]));
  }

  // --- 折叠高级配置 ---
  const allAdvanced = [...advancedPub, ...advancedPri];
  if (allAdvanced.length > 0) {
    const divider = el("div", "advanced-divider");
    divider.innerHTML = '<span class="arrow">▶</span> 高级配置 <span class="badge-adv">' + allAdvanced.length + ' 项</span>';
    const body = el("div", "advanced-body");
    for (const key of advancedPub) {
      body.append(makeFieldCard("public", key, state.config.public[key]));
    }
    for (const key of advancedPri) {
      body.append(makeFieldCard("private", key, state.config.private[key]));
    }
    divider.addEventListener("click", () => {
      body.classList.toggle("open");
      divider.classList.toggle("open");
    });
    els.cardsRoot.append(divider);
    els.cardsRoot.append(body);
  }

  if (moduleId === "speech") {
    const ttsCard = el("article", "card");
    ttsCard.append(el("h3", "card-title", "TTS 服务即时应用"));
    ttsCard.append(el("p", "card-help", "local TTS 模式下可把参考音频参数即时同步到 5000 端口服务。"));
    ttsCard.append(makeButton("应用参考音频到 TTS 服务", applyTtsReferToService, "btn btn-secondary"));
    els.cardsRoot.append(ttsCard);

    const edgeTtsCard = el("article", "card");
    edgeTtsCard.append(el("h3", "card-title", "Edge TTS 语音选择器"));
    edgeTtsCard.append(el("p", "card-help", "点击打开语音选择器，浏览和选择可用的 Edge TTS 语音。"));
    edgeTtsCard.append(makeButton("选择 Edge TTS 语音", openEdgeTTSVoiceModal, "btn btn-primary"));
    els.cardsRoot.append(edgeTtsCard);
  }

  if (moduleId === "advanced") {
    const logCard = el("article", "card");
    logCard.append(el("h3", "card-title", "日志面板"));
    logCard.append(el("p", "card-help", "打开/切换主窗口的日志浮动面板，实时查看后端日志。"));
    logCard.append(makeButton("打开日志面板", async () => {
      try {
        await window.api.toggleLogPanel();
      } catch (e) {
        console.error("toggleLogPanel failed", e);
      }
    }, "btn btn-primary"));
    els.cardsRoot.append(logCard);
  }

  if (moduleId === "live2d") {
    const modelType = String(state.config.public.MODEL_TYPE || "live2d").toLowerCase();
    const m = el("article", "card full-span");
    m.append(el("h3", "card-title", "模型类型"));
    const typeRow = el("div", "list-row");
    typeRow.append(el("span", "", "当前模型类型: " + modelType.toUpperCase()));
    const switchType = modelType === "vrm" ? "live2d" : "vrm";
    typeRow.append(makeButton(`切换到 ${switchType.toUpperCase()}`, async () => {
      updateValue("public", "MODEL_TYPE", switchType);
      renderModule();
      await saveConfig();
    }, "btn btn-ghost"));
    m.append(typeRow);

    const m2 = el("article", "card full-span");
    m2.append(el("h3", "card-title", "可用模型"));
    const list = el("div", "list-box");
    const filtered = state.live2dModels.filter((item) => item.type === modelType);
    if (!filtered.length) {
      list.append(el("div", "empty-state", `暂无 ${modelType.toUpperCase()} 模型，可先点击右上角 Reload 或手动放置模型文件。`));
    } else {
      for (const item of filtered) {
        const row = el("div", "list-row");
        const path = String(item.path || "");
        row.append(el("span", "", `${item.label || "-"} | ${path}`));
        const configKey = modelType === "vrm" ? "VRM_MODEL_PATH" : "LIVE2D_MODEL_PATH";
        row.append(makeButton("使用", async () => {
          updateValue("public", configKey, path);
          updateValue("public", "MODEL_TYPE", modelType);
          renderModule();
          await saveConfig();
        }, "btn btn-ghost"));
        list.append(row);
      }
    }
    m2.append(list);
    els.cardsRoot.append(m);
    els.cardsRoot.append(m2);
  }
}
