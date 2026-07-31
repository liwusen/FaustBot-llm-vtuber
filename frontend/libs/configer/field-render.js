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

function getNumberFieldSpec(key, value) {
  const numericValue = Number(value);
  
  if (SCALE_PRESETS[key]) return SCALE_PRESETS[key];
  if (Number.isFinite(numericValue) && numericValue >= 0 && numericValue <= 1) {
    return { type: "range", min: 0, max: 1, step: 0.01, unit: "" };
  }
  return {
    type: "number",
    step: Number.isInteger(value) ? 1 : 0.01,
    unit: "",
  };
}

function createFieldHeader(meta, key) {
  const header = el("div", "field-card-head");
  const titleWrap = el("div", "field-card-title-wrap");
  titleWrap.append(el("h3", "card-title", meta.label));
  titleWrap.append(el("div", "card-key", key));
  header.append(titleWrap);
  return header;
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
    els.dirtyBadge.textContent = "0 项配置已修改";
  } else {
    els.dirtyBadge.classList.remove("hidden");
    els.dirtyBadge.textContent = `${count} 项配置已修改`;
  }
}

function makeFieldCard(scope, key, value) {
  const meta = getMeta(key);
  const card = el("article", `card full-span ${state.dirty[scope].has(key) ? "dirty" : ""}`.trim());
  card.classList.add("field-card");
  const header = createFieldHeader(meta, key);
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
    const spec = getNumberFieldSpec(key, currentValue ?? value);
    if (spec.type === "range") {
      const sliderWrap = el("div", "slider-field");
      const input = el("input", "range-input");
      input.type = "range";
      input.name = key;
      input.min = String(spec.min);
      input.max = String(spec.max);
      input.step = String(spec.step);
      input.value = String(currentValue ?? value ?? spec.min);

      const meter = el("div", "slider-meter");
      const valueText = el("strong", "slider-value", "");
      const hintText = el("span", "slider-hint", `${spec.min} - ${spec.max}${spec.unit || ""}`);
      meter.append(valueText, hintText);

      const syncSlider = () => {
        const parsed = normalizeNumberInput(input.value, value);
        valueText.textContent = `${Number(parsed ?? 0).toFixed(spec.step < 1 ? 2 : 0)}${spec.unit || ""}`;
        const ratio = ((Number(input.value) - spec.min) / (spec.max - spec.min || 1)) * 100;
        input.style.setProperty("--range-fill", `${Math.max(0, Math.min(100, ratio))}%`);
      };
      syncSlider();
      input.addEventListener("input", () => {
        const parsed = normalizeNumberInput(input.value, value);
        syncSlider();
        updateValue(scope, key, parsed);
        card.classList.add("dirty");
      });
      sliderWrap.append(input, meter);
      controlWrap.append(sliderWrap);
    } else {
      const input = el("input", "number");
      input.type = "number";
      input.name = key;
      input.value = String(currentValue ?? 0);
      input.step = String(spec.step);
      input.addEventListener("input", () => {
        const parsed = normalizeNumberInput(input.value, value);
        updateValue(scope, key, parsed);
        card.classList.add("dirty");
      });
      controlWrap.append(input);
    }
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
          { name: "音频文件", extensions: ["wav", "mp3", "flac", "m4a", "ogg"] },
          { name: "所有文件", extensions: ["*"] },
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

  card.append(header, help, controlWrap);
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
        if (k === "LIVE2D_MODEL_PATH" || k === "LIVE2D_MODEL_X" || k === "LIVE2D_MODEL_Y" || k === "IMAGE_MODEL_CONFIG") return false;
        return publicKeys.includes(k);
      });
      return { publicKeys: vrmOnly, privateKeys: [] };
    }
    if (modelType === "images") {
      const imageOnly = LIVE2D_KEYS.filter((k) => {
        if (k === "LIVE2D_MODEL_PATH" || k === "VRM_MODEL_PATH" || k === "IMAGE_MODEL_CONFIG") return false;
        return publicKeys.includes(k);
      });
      return { publicKeys: imageOnly, privateKeys: [] };
    }
    return { publicKeys: LIVE2D_KEYS.filter((k) => publicKeys.includes(k) && k !== "IMAGE_MODEL_CONFIG"), privateKeys: [] };
  }
  if (moduleId === "speech") {
    const modeTts = String(state.config.public.TTS_MODE || "").toLowerCase();
    const modeAsr = String(state.config.public.ASR_MODE || "").toLowerCase();
    const isCloud = modeTts === "faustbot-cloud" || modeAsr === "faustbot-cloud";
    const isLocalTts = modeTts === "gpt-sovits";
    const isWhisperAsr = modeAsr === "whisper";
    const pub = SPEECH_PUBLIC_KEYS.filter((k) => publicKeys.includes(k));
    const pri = SPEECH_PRIVATE_KEYS.filter((k) => privateKeys.includes(k));
    return {
      publicKeys: pub.filter((k) => {
        if (k.startsWith("OPENAI_TTS_") && modeTts !== "openai") return false;
        if (k.startsWith("OPENAI_ASR_") && modeAsr !== "openai") return false;
        if (k.startsWith("WHISPER_") && !isWhisperAsr) return false;
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
  const moduleManagedPublic = new Set(["MC_OPERATOR_URL", "MC_EVENT_TRIGGER_ENABLED", "MC_BRIDGE_ENABLED", "mcp_servers"]);
  const usedPublic = new Set([...AI_PUBLIC_KEYS, ...LIVE2D_KEYS, ...SPEECH_PUBLIC_KEYS, ...moduleManagedPublic]);
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
    appendToActiveModule(makeFieldCard("public", key, state.config.public[key]));
  }
  for (const key of basicPri) {
    appendToActiveModule(makeFieldCard("private", key, state.config.private[key]));
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
    appendToActiveModule(divider);
    appendToActiveModule(body);
  }

  if (moduleId === "speech") {
    const ttsCard = el("article", "card");
    ttsCard.append(el("h3", "card-title", "TTS 服务即时应用"));
    ttsCard.append(el("p", "card-help", "local TTS 模式下可把参考音频参数即时同步到 5000 端口服务。"));
    ttsCard.append(makeButton("应用参考音频到 TTS 服务", applyTtsReferToService, "btn btn-secondary"));
    appendToActiveModule(ttsCard);

    const edgeTtsCard = el("article", "card");
    edgeTtsCard.append(el("h3", "card-title", "Edge TTS 语音选择器"));
    edgeTtsCard.append(el("p", "card-help", "点击打开语音选择器，浏览和选择可用的 Edge TTS 语音。"));
    edgeTtsCard.append(makeButton("选择 Edge TTS 语音", openEdgeTTSVoiceModal, "btn btn-primary"));
    appendToActiveModule(edgeTtsCard);
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
    appendToActiveModule(logCard);
  }

  if (moduleId === "live2d") {
    const modelType = String(state.config.public.MODEL_TYPE || "live2d").toLowerCase();
    const m = el("article", "card full-span");
    m.append(el("h3", "card-title", "模型类型"));
    const typeRow = el("div", "list-row");
    typeRow.append(el("span", "", "当前模型类型: " + modelType.toUpperCase()));
    for (const switchType of ["live2d", "vrm", "images"]) {
      if (switchType === modelType) continue;
      typeRow.append(makeButton(`切换到 ${switchType.toUpperCase()}`, async () => {
        updateValue("public", "MODEL_TYPE", switchType);
        renderModule();
        await saveConfig();
      }, "btn btn-ghost"));
    }
    m.append(typeRow);

    const m2 = el("article", "card full-span");
    m2.append(el("h3", "card-title", "可用模型"));
    const list = el("div", "list-box");
    if (modelType === "images") {
      const cfg = state.config.public.IMAGE_MODEL_CONFIG || {};
      const baseCount = Array.isArray(cfg.baseImages) ? cfg.baseImages.length : 0;
      const emoCount = Array.isArray(cfg.emotions) ? cfg.emotions.length : 0;
      const tapCount = Array.isArray(cfg.tapImages) ? cfg.tapImages.length : 0;
      const mouthCount = Array.isArray(cfg.mouthShapes) ? cfg.mouthShapes.length : 0;
      const summary = el("div", "card-help", `默认图 ${baseCount} 张 | 情绪 ${emoCount} 组 | Tap 图 ${tapCount} 张 | 嘴型图 ${mouthCount} 张`);
      const editorBar = el("div", "toolbar");
      editorBar.append(makeButton("编辑 Images 模型", async () => {
        const clone = JSON.parse(JSON.stringify(state.config.public.IMAGE_MODEL_CONFIG || { baseImages: [], emotions: [], tapImages: [], mouthShapes: [], motionDurationMs: 3000, tapDurationMs: 700 }));

        function buildListSection(title, getItems, setItems, isMouthShape = false, hintText = "") {
          const wrap = el("div", "card full-span");
          wrap.append(el("h3", "card-title", title));
          if (hintText) wrap.append(el("p", "form-hint", hintText));
          const body = el("div");
          body.style.display = "flex";
          body.style.flexDirection = "column";
          body.style.gap = "8px";

          const renderRows = () => {
            body.innerHTML = "";
            const items = getItems();
            if (!items.length) body.append(el("div", "empty-state", "暂无项目"));
            items.forEach((item, idx) => {
              const row = el("div", "toolbar");
              row.style.alignItems = "center";
              if (isMouthShape) {
                const pathInput = el("input", "input");
                pathInput.value = item.path || "";
                pathInput.placeholder = "图片路径（支持任意磁盘位置的绝对路径）";
                pathInput.addEventListener("input", () => { item.path = pathInput.value; });
                const opennessInput = el("input", "number");
                opennessInput.type = "number";
                opennessInput.min = "0";
                opennessInput.max = "1";
                opennessInput.step = "0.05";
                opennessInput.title = "嘴巴张开程度 0~1";
                opennessInput.value = String(item.openness ?? 0);
                opennessInput.addEventListener("input", () => { item.openness = Number(opennessInput.value || 0); });
                const pickBtn = makeButton("选择图片", async () => {
                  const filePath = await window.api.configOpenFile({ title: "选择图片", filters: [{ name: "图片", extensions: ["png", "jpg", "jpeg", "webp", "gif", "bmp"] }] });
                  if (!filePath) return;
                  pathInput.value = filePath;
                  item.path = filePath;
                }, "btn btn-ghost");
                row.append(pathInput, opennessInput, pickBtn);
              } else {
                const input = el("input", "input");
                input.value = String(item || "");
                input.placeholder = "图片路径（支持任意磁盘位置的绝对路径）";
                input.addEventListener("input", () => { items[idx] = input.value; setItems(items); });
                const pickBtn = makeButton("选择图片", async () => {
                  const filePath = await window.api.configOpenFile({ title: "选择图片", filters: [{ name: "图片", extensions: ["png", "jpg", "jpeg", "webp", "gif", "bmp"] }] });
                  if (!filePath) return;
                  input.value = filePath;
                  items[idx] = filePath;
                  setItems(items);
                }, "btn btn-ghost");
                row.append(input, pickBtn);
              }
              row.append(makeButton("删除", () => {
                const next = getItems().slice();
                next.splice(idx, 1);
                setItems(next);
                renderRows();
              }, "btn btn-ghost"));
              body.append(row);
            });
          };

          const addBtn = makeButton("添加", () => {
            const next = getItems().slice();
            next.push(isMouthShape ? { path: "", openness: 0 } : "");
            setItems(next);
            renderRows();
          }, "btn btn-secondary");
          wrap.append(addBtn, body);
          renderRows();
          return wrap;
        }

        const baseSection = buildListSection("默认图片", () => clone.baseImages || [], (next) => { clone.baseImages = next; }, false, "角色的常态/待机图片；若配置多张会随机或轮换显示，作为无特殊情绪时的默认立绘。");
        const tapSection = buildListSection("Tap 图片", () => clone.tapImages || [], (next) => { clone.tapImages = next; }, false, "被点击（Tap）时短暂切换显示的图片，用于点击反馈；持续时间由下方“Tap 持续”控制。");
        const mouthSection = buildListSection("嘴型图片", () => clone.mouthShapes || [], (next) => { clone.mouthShapes = next; }, true, "口型同步用图片，每张对应一个张嘴程度 openness（0=闭合，1=最大张开）；说话时按音量匹配显示。");

        const emotionsCard = el("article", "card full-span");
        emotionsCard.append(el("h3", "card-title", "情绪变体"));
        emotionsCard.append(el("p", "form-hint", "按情绪分组配置的立绘。每组填写情绪名称（如 happy、angry）与对应图片；当 Agent 表达该情绪时会切换到本组图片，持续时间由下方“情绪持续”控制。"));
        const emotionBody = el("div");
        emotionBody.style.display = "flex";
        emotionBody.style.flexDirection = "column";
        emotionBody.style.gap = "10px";

        const renderEmotions = () => {
          emotionBody.innerHTML = "";
          const items = Array.isArray(clone.emotions) ? clone.emotions : [];
          if (!items.length) emotionBody.append(el("div", "empty-state", "暂无情绪分组"));
          items.forEach((emotion, idx) => {
            const box = el("div", "card");
            const nameRow = el("div", "toolbar");
            const nameInput = el("input", "input");
            nameInput.value = emotion.name || "";
            nameInput.placeholder = "情绪名称，如 happy";
            nameInput.addEventListener("input", () => { emotion.name = nameInput.value; });
            nameRow.append(nameInput, makeButton("删除分组", () => {
              clone.emotions.splice(idx, 1);
              renderEmotions();
            }, "btn btn-ghost"));
            box.append(nameRow);
            const imagesSection = buildListSection("该情绪图片", () => emotion.images || [], (next) => { emotion.images = next; }, false, "该情绪触发时显示的图片，可配置多张。");
            box.append(imagesSection);
            emotionBody.append(box);
          });
        };

        emotionsCard.append(makeButton("添加情绪分组", () => {
          clone.emotions = Array.isArray(clone.emotions) ? clone.emotions : [];
          clone.emotions.push({ name: "", images: [] });
          renderEmotions();
        }, "btn btn-secondary"), emotionBody);
        renderEmotions();

        // 顶部通用设置：现代表单 + 逐项描述
        const settingsCard = el("article", "card full-span");
        settingsCard.append(el("h3", "card-title", "通用设置"));
        const settingsGrid = el("div", "form-grid");
        const makeSettingField = (labelText, control, hintText) => {
          const field = el("div", "form-field");
          field.append(el("label", "form-field-label", labelText));
          if (hintText) field.append(el("p", "form-hint", hintText));
          const ctrlWrap = el("div", "form-field-control");
          ctrlWrap.append(control);
          field.append(ctrlWrap);
          return field;
        };
        const scaleInput = el("input", "number");
        scaleInput.type = "number";
        scaleInput.step = "0.05";
        scaleInput.min = "0.1";
        scaleInput.max = "4";
        scaleInput.value = String(clone.scale || 1.0);
        scaleInput.addEventListener("input", () => { clone.scale = Number(scaleInput.value || 1.0); });
        const motionInput = el("input", "number");
        motionInput.type = "number";
        motionInput.value = String(clone.motionDurationMs || 3000);
        motionInput.addEventListener("input", () => { clone.motionDurationMs = Number(motionInput.value || 3000); });
        const tapInput = el("input", "number");
        tapInput.type = "number";
        tapInput.value = String(clone.tapDurationMs || 700);
        tapInput.addEventListener("input", () => { clone.tapDurationMs = Number(tapInput.value || 700); });
        settingsGrid.append(
          makeSettingField("图片缩放", scaleInput, "整体显示缩放倍数（0.1~4），1 为原始尺寸，用于适配窗口大小。"),
          makeSettingField("情绪持续 (ms)", motionInput, "切换到情绪立绘后保持的毫秒数，到时后回到默认图片。"),
          makeSettingField("Tap 持续 (ms)", tapInput, "点击后显示 Tap 图片的毫秒数，到时后恢复。")
        );
        settingsCard.append(settingsGrid);

        const actions = el("div", "toolbar");
        actions.append(
          makeButton("保存 Images 模型", async () => {
            updateValue("public", "IMAGE_MODEL_CONFIG", clone);
            updateValue("public", "MODEL_TYPE", "images");
            closeModal();
            renderModule();
            await saveConfig();
          }, "btn btn-primary"),
          makeButton("关闭", closeModal)
        );
        openModal("编辑 Images 模型", [settingsCard, baseSection, emotionsCard, tapSection, mouthSection, actions]);
      }, "btn btn-primary"));
      list.append(summary, editorBar);
    } else {
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
    }
    m2.append(list);
    appendToActiveModule(m);
    appendToActiveModule(m2);
  }
}
