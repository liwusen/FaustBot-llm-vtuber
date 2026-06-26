// 常量已抽取到 ./libs/configer/constants.js
if (typeof META === "undefined" || typeof FIELD_OPTIONS === "undefined" || typeof MODULES === "undefined") {
  console.error("配置常量未加载：请确认 frontend/libs/configer/constants.js 已在 HTML 中先加载。");
}

const state = {
  activeModule: "overview",
  config: { public: {}, private: {} },
  original: { public: {}, private: {} },
  dirty: { public: new Map(), private: new Map() },
  runtime: {},
  live2dModels: [],
  agents: [],
  selectedAgent: "",
  agentDetail: null,
  kbTree: null,
  kbScope: "",
  kbCurrentDir: "/",
  kbSelectedPath: "",
  kbSelectedContent: "",
  kbSelectedMeta: null,
  kbTasks: [],
  araya: null,
  services: [],
  selectedService: "",
  serviceDetail: null,
  recentErrors: [],
  triggers: [],
  selectedTriggerId: "",
  memoryView: "tree",  // "tree" | "graph"
  skills: [],
  selectedSkillSlug: "",
  skillsAgent: "",
  skillDetail: null,
  plugins: [],
  selectedPluginId: "",
  pluginConfigDraft: {},
  runtimeUpdate: null,
  graphEntities: [],
  graphRelations: [],
};

const els = {
  moduleNav: document.getElementById("moduleNav"),
  cardsRoot: document.getElementById("cardsRoot"),
  moduleTitle: document.getElementById("moduleTitle"),
  moduleDesc: document.getElementById("moduleDesc"),
  dirtyBadge: document.getElementById("dirtyBadge"),
  saveBtn: document.getElementById("saveBtn"),
  applyBtn: document.getElementById("applyBtn"),
  reloadBtn: document.getElementById("reloadBtn"),
  banner: document.getElementById("banner"),
};

function clearRoot() {
  if (els && els.cardsRoot) els.cardsRoot.innerHTML = "";
}

function setBusy(v) {
  if (!els) return;
  if (els.saveBtn) els.saveBtn.disabled = v;
  if (els.applyBtn) els.applyBtn.disabled = v;
  if (els.reloadBtn) els.reloadBtn.disabled = v;
}

function showBanner(type, text) {
  if (!els || !els.banner) return;
  els.banner.className = `banner ${type}`;
  els.banner.textContent = text;
  els.banner.classList.remove("hidden");
}

function hideBanner() {
  if (!els || !els.banner) return;
  els.banner.classList.add("hidden");
}

function cfgApi(method, path, payload, query) {
  if (window.api && typeof window.api.configRequest === "function") return window.api.configRequest(method, path, payload, query);
  return Promise.reject(new Error("window.api.configRequest 未实现"));
}

function apiLocal(method, url, payload) {
  if (window.api && typeof window.api.configHttpRequest === "function") return window.api.configHttpRequest(method, url, payload);
  return Promise.reject(new Error("window.api.configHttpRequest 未实现"));
}

function addSection(title, bodyNodes, full = true) {
  const section = el("section", `card section ${full ? "full-span" : ""}`.trim());
  const t = el("h3", "section-title", title);
  section.append(t);
  for (const node of bodyNodes) section.append(node);
  if (els && els.cardsRoot) els.cardsRoot.append(section);
}

// Modal helpers 已提取到 frontend/libs/configer/modal.js
if (typeof ensureModalRoot === "undefined" || typeof openModal === "undefined" || typeof closeModal === "undefined") {
  console.error("Modal helpers 未加载：请确认 frontend/libs/configer/modal.js 已在 HTML 中先加载。");
}

// Handle deeplink-config-faustcloud from main process
async function handleDeeplinkConfigFaustCloud(payload) {
  if (!payload || typeof payload !== 'object') return;
  const host = String(payload.host || '').trim();
  const key = String(payload.key || '').trim();
  if (!host || !key) return;

  const hostInput = el('input', 'input');
  hostInput.readOnly = true;
  hostInput.value = host;

  const keyInput = el('input', 'input');
  keyInput.readOnly = true;
  keyInput.value = key;

  const info = el('div', 'card-help');
  info.textContent = '系统检测到来自 FaustBot Cloud 的配置请求。确认后将把云地址和服务密钥写入配置，并将 TTS/ASR 模式切换为 FaustBot-cloud。';

  const actionBar = el('div', 'toolbar');
  const doConfirm = async () => {
    try {
      setBusy(true);
      const payloadToSave = {
        public: {
          FAUSTBOT_CLOUD_BASE_URL: host,
          TTS_MODE: 'faustbot-cloud',
          ASR_MODE: 'faustbot-cloud',
        },
        private: {
          FAUSTBOT_CLOUD_SERVICE_KEY: key,
        }
      };
      await cfgApi('POST', '/faust/admin/config', payloadToSave);
      // reload runtime to apply changes
      try { await cfgApi('POST', '/faust/admin/config/reload', { reset_dialog: false, no_initial_chat: true }); } catch (e) {}
      // reload UI config and runtime summary
      try { await reloadAll(); } catch (e) { console.warn('reloadAll failed after deeplink config', e); }
      showBanner('success', 'FaustBot Cloud 已配置。');
      closeModal();
    } catch (e) {
      console.error('Failed to apply FaustBot Cloud config', e);
      showBanner('error', '配置保存失败: ' + String(e));
    } finally {
      setBusy(false);
    }
  };

  actionBar.append(makeButton('确认并保存', doConfirm, 'btn btn-primary'), makeButton('取消', closeModal));

  const body = [info, el('label', '', 'FaustBot Cloud 地址'), hostInput, el('label', '', 'Service Key'), keyInput, actionBar];
  openModal('收到 FaustBot Cloud 配置', body);
}

if (window.deeplink && typeof window.deeplink.onConfigFaustCloud === 'function') {
  window.deeplink.onConfigFaustCloud((payload) => {
    try { handleDeeplinkConfigFaustCloud(payload); } catch (e) { console.error('deeplink handler failed', e); }
  });
}

// UI card builders 已提取到 frontend/libs/configer/ui-cards.js
if (typeof formatScalar === "undefined" || typeof makeDataView === "undefined" || typeof makeInfoCard === "undefined" || typeof makeTagListCard === "undefined" || typeof makeSimpleTableCard === "undefined") {
  console.error("UI card builders 未加载：请确认 frontend/libs/configer/ui-cards.js 已在 HTML 中先加载。");
}

// List helpers 已提取到 frontend/libs/configer/list-utils.js
if (typeof makeListBox === "undefined" || typeof makeListRow === "undefined" || typeof makeOpsToolbar === "undefined") {
  console.error("List helpers 未加载：请确认 frontend/libs/configer/list-utils.js 已在 HTML 中先加载。");
}

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

  const area = el("textarea", "textarea code-area code-area-lg");
  area.value = String(initialContent || "");

  const metaBox = el("div", "card-help");
  const refreshMetaText = () => {
    metaBox.textContent = `updated_at: ${formatScalar(meta.updated_at)} | declared_by: ${formatScalar(meta.declared_by)} | chunk_count: ${formatScalar(meta.chunk_count)} | indexed: ${formatScalar(meta.indexed)}`;
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

function openAgentFilesModal(agentName, files) {
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
      payload.files[filename] = areas.get(filename)?.value || "";
    }
    await cfgApi("PUT", `/faust/admin/agents/${encodeURIComponent(targetAgent)}/files`, payload);
    showBanner("success", `Agent 文件已保存: ${targetAgent}`);
    await ensureModuleData("agent");
    renderModule();
  }, "btn btn-primary");
  toolbar.append(openBtn, saveBtn, makeButton("关闭", closeModal));

  const blocks = [toolbar];
  for (const filename of AGENT_FILES) {
    const card = el("article", "card full-span");
    card.append(el("h3", "card-title", filename));
    const area = el("textarea", "textarea code-area code-area-lg");
    const fileObj = files && files[filename];
    const raw = (fileObj && typeof fileObj === "object") ? (fileObj.content || "") : String(fileObj || "");
    area.value = raw;
    areas.set(filename, area);
    card.append(area);
    const isReadonly = fileObj && fileObj.readonly;
    if (isReadonly) {
      area.disabled = true;
      area.style.opacity = "0.6";
      card.append(el("small", "hint", "（模板文件，不可编辑 — 修改请更新 agents_template/faust/ 源文件）"));
    }
    blocks.push(card);
  }
  openModal(`Agent 文件编辑 - ${targetAgent}`, blocks);
}

function createArayaTriggerSlider(onTrigger) {
  const wrap = el("div", "araya-trigger-wrap");
  const canvas = document.createElement("canvas");
  canvas.className = "araya-trigger-canvas";
  const hint = el("div", "araya-trigger-hint", "向右拖动触发 Araya");
  wrap.append(canvas, hint);

  const dpr = window.devicePixelRatio || 1;
  const W = 320;
  const H = 46;
  const pad = 10;
  const knobR = 14;
  let x = 0;
  let v = 0;
  let target = 0;
  let dragging = false;
  let draggingOffset = 0;
  let armed = false;
  let triggered = false;
  let raf = 0;

  const min = () => pad + knobR;
  const max = () => W - pad - knobR;
  const range = () => max() - min();
  const threshold = () => range() * 0.82;

  function setupCanvas() {
    canvas.width = Math.floor(W * dpr);
    canvas.height = Math.floor(H * dpr);
    canvas.style.width = `${W}px`;
    canvas.style.height = `${H}px`;
  }

  function roundRect(ctx, rx, ry, rw, rh, rr) {
    const r = Math.min(rr, rw / 2, rh / 2);
    ctx.beginPath();
    ctx.moveTo(rx + r, ry);
    ctx.arcTo(rx + rw, ry, rx + rw, ry + rh, r);
    ctx.arcTo(rx + rw, ry + rh, rx, ry + rh, r);
    ctx.arcTo(rx, ry + rh, rx, ry, r);
    ctx.arcTo(rx, ry, rx + rw, ry, r);
    ctx.closePath();
  }

  function draw() {
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const cy = H / 2;
    const trackY = cy - 12;
    const trackH = 24;
    const trackX = pad;
    const trackW = W - pad * 2;

    roundRect(ctx, trackX, trackY, trackW, trackH, 12);
    ctx.fillStyle = "#ecf2fb";
    ctx.fill();
    ctx.strokeStyle = "#cfd9e8";
    ctx.lineWidth = 1;
    ctx.stroke();

    const clampedX = Math.max(-28, Math.min(range(), x));
    const knobX = min() + clampedX;
    const progressW = Math.max(0, Math.min(trackW, knobX - trackX));

    if (progressW > 2) {
      const grad = ctx.createLinearGradient(trackX, 0, trackX + progressW, 0);
      grad.addColorStop(0, "#6f96ff");
      grad.addColorStop(1, "#3f6be8");
      roundRect(ctx, trackX, trackY, progressW, trackH, 12);
      ctx.fillStyle = grad;
      ctx.fill();
    }

    const tX = min() + threshold();
    ctx.beginPath();
    ctx.moveTo(tX, trackY + 4);
    ctx.lineTo(tX, trackY + trackH - 4);
    ctx.strokeStyle = "rgba(63,107,232,0.45)";
    ctx.lineWidth = 2;
    ctx.stroke();

    if (clampedX < 0) {
      ctx.beginPath();
      ctx.moveTo(min(), cy);
      ctx.quadraticCurveTo(min() + clampedX * 0.45, cy - 8, knobX, cy);
      ctx.strokeStyle = "rgba(229,68,71,0.45)";
      ctx.lineWidth = 3;
      ctx.stroke();
    }

    ctx.beginPath();
    ctx.arc(knobX, cy, knobR, 0, Math.PI * 2);
    ctx.fillStyle = triggered ? "#2c9158" : "#ffffff";
    ctx.fill();
    ctx.strokeStyle = triggered ? "#2c9158" : "#3f6be8";
    ctx.lineWidth = 2;
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(knobX - 4, cy - 5);
    ctx.lineTo(knobX + 3, cy);
    ctx.lineTo(knobX - 4, cy + 5);
    ctx.strokeStyle = triggered ? "#ffffff" : "#3f6be8";
    ctx.lineWidth = 2;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.stroke();
  }

  function tick() {
    if (!dragging) {
      const force = (target - x) * 0.18;
      v = v * 0.78 + force;
      x += v;
      if (Math.abs(target - x) < 0.03 && Math.abs(v) < 0.03) {
        x = target;
        v = 0;
      }
    }
    draw();
    raf = window.requestAnimationFrame(tick);
  }

  function pointX(evt) {
    const rect = canvas.getBoundingClientRect();
    return evt.clientX - rect.left;
  }

  function updateByPointer(px) {
    const dx = px - min() - draggingOffset;
    if (dx < 0) {
      x = dx * 0.35;
    } else {
      x = Math.min(range() + 8, dx);
    }
    armed = x >= threshold();
    if (!triggered) {
      hint.textContent = armed ? "松手触发 Araya" : "向右拖动触发 Araya";
    }
  }

  async function releaseHandle() {
    dragging = false;
    if (armed && !triggered) {
      triggered = true;
      target = range();
      hint.textContent = "触发中...";
      draw();
      try {
        await onTrigger();
        hint.textContent = "触发成功";
      } catch (_e) {
        hint.textContent = "触发失败，请重试";
      }
      window.setTimeout(() => {
        triggered = false;
        armed = false;
        target = 0;
        hint.textContent = "向右拖动触发 Araya";
      }, 800);
      return;
    }
    armed = false;
    target = 0;
    hint.textContent = "向右拖动触发 Araya";
  }

  canvas.addEventListener("pointerdown", (evt) => {
    if (triggered) return;
    canvas.setPointerCapture(evt.pointerId);
    dragging = true;
    target = x;
    const px = pointX(evt);
    draggingOffset = px - (min() + x);
  });

  canvas.addEventListener("pointermove", (evt) => {
    if (!dragging || triggered) return;
    updateByPointer(pointX(evt));
    draw();
  });

  const endDrag = async (evt) => {
    if (!dragging) return;
    if (evt && canvas.hasPointerCapture(evt.pointerId)) {
      canvas.releasePointerCapture(evt.pointerId);
    }
    await releaseHandle();
  };

  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);
  canvas.addEventListener("lostpointercapture", async () => {
    if (dragging) await releaseHandle();
  });

  setupCanvas();
  draw();
  raf = window.requestAnimationFrame(tick);

  wrap.addEventListener("DOMNodeRemoved", () => {
    if (raf) window.cancelAnimationFrame(raf);
  });

  return wrap;
}
// Araya slider 已提取到 frontend/libs/configer/araya-slider.js
if (typeof createArayaTriggerSlider === "undefined") {
  console.error("Araya slider 未加载：请确认 frontend/libs/configer/araya-slider.js 已在 HTML 中先加载。");
}
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

  // --- 折叠高级配置：渲染分隔条 + 隐藏的高级字段 ---
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
    
    // Edge TTS 语音选择器
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

// KB utilities 已提取到 frontend/libs/configer/kb-utils.js
if (typeof normalizeKbPath === "undefined" || typeof kbParentPath === "undefined" || typeof findKbNodeByPath === "undefined" || typeof getKbChildren === "undefined") {
  console.error("KB helpers 未加载：请确认 frontend/libs/configer/kb-utils.js 已在 HTML 中先加载。");
}

function openKbSearchModal() {
  const searchInput = el("input", "input");
  searchInput.placeholder = "输入关键词，在当前 scope 中搜索";
  const resultBox = el("div", "list-box");
  resultBox.style.maxHeight = "420px";

  const runSearch = async () => {
    const q = searchInput.value.trim();
    if (!q) {
      showBanner("info", "请输入搜索关键词。" );
      return;
    }
    try {
      resultBox.innerHTML = "";
      resultBox.append(el("div", "empty-state", "搜索中..."));
      const data = await cfgApi("POST", "/faust/memory/search", {
        query: q,
        scope: state.kbScope || null,
        top_k: 12,
        return: "snippets",
      });
      const items = data.items || [];
      resultBox.innerHTML = "";
      if (!items.length) {
        resultBox.append(el("div", "empty-state", "未找到匹配内容。"));
        return;
      }
      for (const it of items) {
        const row = el("div", "list-row clickable");
        const left = el("div", "field-wrap");
        left.append(
          el("div", "mono", `[FILE] ${it.path || "-"} | score=${it.score ?? "-"}`),
          el("div", "card-help", String(it.snippet || ""))
        );
        const ops = el("div", "toolbar compact");
        ops.addEventListener("click", (evt) => evt.stopPropagation());
        ops.append(makeButton("打开", async () => {
          state.kbSelectedPath = normalizeKbPath(String(it.path || ""));
          state.kbCurrentDir = kbParentPath(state.kbSelectedPath);
          const d = await cfgApi("GET", "/faust/memory/get", null, { path: state.kbSelectedPath });
          state.kbSelectedContent = String(d.content || "");
          await openKbEditorModal(state.kbSelectedPath, state.kbSelectedContent, d.meta || {});
          renderModule();
        }));
        row.append(left, ops);
        row.addEventListener("click", async () => {
          state.kbSelectedPath = normalizeKbPath(String(it.path || ""));
          state.kbCurrentDir = kbParentPath(state.kbSelectedPath);
          const d = await cfgApi("GET", "/faust/memory/get", null, { path: state.kbSelectedPath });
          state.kbSelectedContent = String(d.content || "");
          await openKbEditorModal(state.kbSelectedPath, state.kbSelectedContent, d.meta || {});
          renderModule();
        });
        resultBox.append(row);
      }
    } catch (err) {
      resultBox.innerHTML = "";
      resultBox.append(el("div", "empty-state", `搜索失败: ${String(err && err.message ? err.message : err)}`));
      showBanner("error", `KB 搜索失败: ${String(err && err.message ? err.message : err)}`);
    }
  };
  searchInput.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter") runSearch();
  });

  const actionBar = el("div", "toolbar");
  actionBar.append(
    searchInput,
    makeButton("搜索", runSearch, "btn btn-primary"),
    makeButton("关闭", closeModal)
  );
  openModal("KB 搜索", [actionBar, resultBox]);
}

// Edge TTS 语音管理Modal
async function openEdgeTTSVoiceModal() {
  const modalBody = el("div", "edge-tts-voice-modal");
  
  // 搜索栏
  const searchBar = el("div", "search-bar");
  const searchInput = el("input", "search-input");
  searchInput.type = "text";
  searchInput.placeholder = "搜索语音名称、ID或特征...";
  const searchBtn = makeButton("搜索", () => loadEdgeTTSVoices(searchInput.value), "btn btn-primary");
  const refreshBtn = makeButton("刷新", () => loadEdgeTTSVoices("", true), "btn btn-secondary");
  searchBar.append(searchInput, searchBtn, refreshBtn);
  
  // 筛选栏
  const filterBar = el("div", "filter-bar");
  const languageSelect = el("select", "filter-select");
  languageSelect.innerHTML = '<option value="">所有语言</option>';
  const genderSelect = el("select", "filter-select");
  genderSelect.innerHTML = '<option value="">所有性别</option>';
  
  // 语音列表容器
  const voiceList = el("div", "voice-list");
  voiceList.style.maxHeight = "400px";
  voiceList.style.overflowY = "auto";
  voiceList.style.border = "1px solid #ddd";
  voiceList.style.padding = "10px";
  
  // 选中信息
  const selectedInfo = el("div", "selected-info");
  selectedInfo.style.marginTop = "10px";
  selectedInfo.style.padding = "10px";
  selectedInfo.style.backgroundColor = "#f5f5f5";
  selectedInfo.style.borderRadius = "4px";
  
  // 加载语言和性别选项
  async function loadFilters() {
    try {
      const [languages, genders] = await Promise.all([
        cfgApi("GET", "/faust/edge-tts/languages"),
        cfgApi("GET", "/faust/edge-tts/genders")
      ]);
      
      languages.languages.forEach(lang => {
        const option = el("option");
        option.value = lang;
        option.textContent = lang;
        languageSelect.appendChild(option);
      });
      
      genders.genders.forEach(gender => {
        const option = el("option");
        option.value = gender;
        option.textContent = gender;
        genderSelect.appendChild(option);
      });
    } catch (error) {
      console.error('加载筛选器失败:', error);
    }
  }
  
  // 加载语音列表
  async function loadEdgeTTSVoices(searchQuery = "", forceRefresh = false) {
    try {
      voiceList.innerHTML = '<div style="text-align: center; padding: 20px;">加载中...</div>';

      const language = languageSelect.value;
      const gender = genderSelect.value;

      if (forceRefresh) {
        await cfgApi("POST", "/faust/edge-tts/cache/refresh", {});
      }

      const data = await cfgApi("GET", "/faust/edge-tts/voices/search", null, {
        q: searchQuery,
        language: language || null,
        gender: gender || null,
      });
      
      voiceList.innerHTML = '';
      
      if (data.voices.length === 0) {
        voiceList.innerHTML = '<div style="text-align: center; padding: 20px; color: #666;">未找到匹配的语音</div>';
        return;
      }
      
      data.voices.forEach(voice => {
        const voiceItem = el("div", "voice-item");
        voiceItem.style.padding = "10px";
        voiceItem.style.border = "1px solid #eee";
        voiceItem.style.marginBottom = "5px";
        voiceItem.style.cursor = "pointer";
        voiceItem.style.borderRadius = "4px";
        
        voiceItem.innerHTML = `
          <div style="font-weight: bold;">${voice.name}</div>
          <div style="color: #666; font-size: 0.9em;">ID: ${voice.voice_id}</div>
          <div style="color: #666; font-size: 0.9em;">语言: ${voice.language} | 性别: ${voice.gender}</div>
          <div style="color: #888; font-size: 0.8em;">特征: ${voice.voice_personalities}</div>
        `;
        
        voiceItem.addEventListener('click', () => selectVoice(voice, voiceItem));
        voiceList.appendChild(voiceItem);
      });
      
    } catch (error) {
      console.error('加载语音列表失败:', error);
      voiceList.innerHTML = '<div style="text-align: center; padding: 20px; color: red;">加载失败</div>';
    }
  }
  
  // 选择语音
  function selectVoice(voice, voiceItem) {
    // 移除之前的选中状态
    voiceList.querySelectorAll('.voice-item').forEach(item => {
      item.style.backgroundColor = '';
      item.style.border = '1px solid #eee';
    });
    
    // 添加选中状态
    voiceItem.style.backgroundColor = '#e3f2fd';
    voiceItem.style.border = '2px solid #2196f3';
    
    // 更新选中信息
    selectedInfo.innerHTML = `
      <div style="font-weight: bold;">已选择: ${voice.name}</div>
      <div>语音ID: ${voice.voice_id}</div>
      <div>语言: ${voice.language} | 性别: ${voice.gender}</div>
      <div>特征: ${voice.voice_personalities}</div>
      <button onclick="confirmEdgeTTSVoice('${voice.voice_id}', '${voice.name.replace(/'/g, "\\'")}')" 
              style="margin-top: 10px; padding: 5px 15px; background: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer;">
        确认选择
      </button>
    `;
  }
  
  // 确认选择
  window.confirmEdgeTTSVoice = function(voiceId, voiceName) {
    updateValue("public", "EDGE_TTS_VOICE", voiceId);
    const voiceField = document.querySelector('input[name="EDGE_TTS_VOICE"]');
    if (voiceField) {
      voiceField.value = voiceId;
      voiceField.dispatchEvent(new Event('input', { bubbles: true }));
      voiceField.dispatchEvent(new Event('change', { bubbles: true }));
    }
    
    // 关闭Modal
    closeModal();
    
    // 显示成功消息
    showBanner('success', `已选择语音: ${voiceName}`);
  };
  
  // 初始化
  modalBody.append(searchBar, filterBar, voiceList, selectedInfo);
  
  // 加载筛选器
  await loadFilters();
  
  // 加载语音列表
  await loadEdgeTTSVoices();
  
  openModal("Edge TTS 语音管理", [modalBody]);
}

function buildTriggerUpdatePayload(source) {
  const base = {
    id: String(source.id || "").trim(),
    type: String(source.type || "").trim(),
    recall_description: String(source.recall_description || ""),
  };
  if (source.lifespan !== null && source.lifespan !== undefined && String(source.lifespan).trim() !== "") {
    base.lifespan = Number(source.lifespan);
  }
  if (base.type === "interval") {
    base.interval_seconds = Number(source.interval_seconds || 60);
  } else if (base.type === "datetime") {
    base.target = String(source.target || "").trim();
  } else if (base.type === "py-eval") {
    base.eval_code = String(source.eval_code || "");
  }
  return base;
}

function openSkillMdModal(slug, content, agentName) {
}
// Skill helpers 已提取到 frontend/libs/configer/skill-utils.js
if (typeof openSkillMdModal === "undefined") {
  console.error("Skill helpers 未加载：请确认 frontend/libs/configer/skill-utils.js 已在 HTML 中先加载。");
}

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
}

function renderAgentModule() {
  const actions = el("div", "toolbar");
  actions.append(
    makeButton("刷新", async () => { await ensureModuleData("agent"); renderModule(); }),
    makeButton("新建", async () => {
      const name = window.prompt("请输入 Agent 名称");
      if (!name || !name.trim()) return;
      await cfgApi("POST", "/faust/admin/agents", { agent_name: name.trim() });
      await ensureModuleData("agent");
      showBanner("success", `已创建 Agent: ${name.trim()}`);
      renderModule();
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
      renderModule();
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

  const list = el("div", "list-box");
  for (const item of state.agents) {
    const row = el("div", `list-row clickable ${state.selectedAgent === item.name ? "selected" : ""}`.trim());
    row.append(el("span", "mono", `[${item.is_current ? "CURRENT" : "AGENT"}] ${item.name}`));
    const ops = el("div", "toolbar compact");
    ops.addEventListener("click", (evt) => evt.stopPropagation());
    ops.append(makeButton("编辑文件", async () => {
      state.selectedAgent = String(item.name || "");
      await ensureModuleData("agent");
      openAgentFilesModal(state.selectedAgent, (state.agentDetail && state.agentDetail.files) || {});
    }));
    row.append(ops);
    row.addEventListener("click", async () => {
      state.selectedAgent = String(item.name || "");
      await ensureModuleData("agent");
      renderModule();
    });
    list.append(row);
  }
  addSection("Agent 列表", [list]);

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
  addSection("Agent 文件操作", [controls, el("div", "card-help", "文件编辑已迁移到弹窗。")]);
}

function renderMemoryModule() {
  const currentDir = normalizeKbPath(state.kbCurrentDir || "/");
  const view = state.memoryView || "tree";

  // ── Tab bar ──
  const tabBar = el("div", "toolbar");
  const treeTab = makeButton("📁 树状浏览", async () => {
    state.memoryView = "tree";
    renderModule();
  }, view === "tree" ? "btn btn-primary" : "btn btn-ghost");
  const graphTab = makeButton("🔗 知识图谱", async () => {
    state.memoryView = "graph";
    await ensureModuleData("memory");
    renderModule();
  }, view === "graph" ? "btn btn-primary" : "btn btn-ghost");
  const searchTab = makeButton("🔍 统一搜索", async () => {
    state.memoryView = "search";
    renderModule();
  }, view === "search" ? "btn btn-primary" : "btn btn-ghost");
  tabBar.append(treeTab, graphTab, searchTab);
  addSection("记忆", [tabBar]);

  if (view === "tree") {
    renderMemoryTree(currentDir);
  } else if (view === "graph") {
    renderMemoryGraph();
  } else if (view === "search") {
    renderMemorySearch();
  }
}

function renderMemoryTree(currentDir) {
  const doRefresh = async () => {
    await ensureModuleData("memory");
    renderModule();
  };

  const doNewFile = async () => {
    const defaultPath = `${currentDir === "/" ? "" : currentDir.slice(1) + "/"}new.md`;
    const nameInput = el("input", "input");
    nameInput.value = defaultPath;
    const save = async () => {
      const p = String(nameInput.value || "").trim();
      if (!p) return showBanner("error", "请输入文件路径");
      const target = normalizeKbPath(p);
      closeModal();
      await openKbEditorModal(target, "", { path: target, declared_by: "config-center", indexed: true, tags: [] });
    };
    const tb = el("div", "toolbar");
    tb.append(makeButton("创建", save, "btn btn-primary"), makeButton("取消", closeModal));
    openModal("新建文件", [nameInput, tb]);
  };

  const doNewFolder = async () => {
    const defaultPath = `${currentDir === "/" ? "" : currentDir.slice(1) + "/"}new-folder`;
    const nameInput = el("input", "input");
    nameInput.value = defaultPath;
    const save = async () => {
      const p = String(nameInput.value || "").trim();
      if (!p) return showBanner("error", "请输入文件夹路径");
      await cfgApi("POST", "/faust/memory/mkdir", { path: p });
      state.kbCurrentDir = normalizeKbPath(p);
      await ensureModuleData("memory");
      closeModal();
      renderModule();
    };
    const tb = el("div", "toolbar");
    tb.append(makeButton("创建", save, "btn btn-primary"), makeButton("取消", closeModal));
    openModal("新建文件夹", [nameInput, tb]);
  };

  const doDelete = async () => {
    const p = (state.kbSelectedPath || "").trim();
    if (!p) { showBanner("info", "请先选中文件或目录。"); return; }
    if (!window.confirm(`确定删除 ${p} ?`)) return;
    await cfgApi("DELETE", "/faust/memory/delete", null, { path: p });
    state.kbSelectedPath = "";
    state.kbSelectedContent = "";
    state.kbCurrentDir = kbParentPath(p);
    await ensureModuleData("memory");
    renderModule();
  };

  // ── Build node count summary ──
  function countTreeStats(treeRoot) {
    function countRec(nodes, typeFilter) {
      let count = 0;
      for (const n of nodes) {
        if (n.type === typeFilter) count++;
        if (n.children) count += countRec(n.children, typeFilter);
      }
      return count;
    }
    const children = treeRoot && treeRoot.children ? treeRoot.children : [];
    return {
      dirs: countRec(children, "dir"),
      files: countRec(children, "file"),
    };
  }

  function getDirMeta(rowNode) {
    if (!rowNode) return {};
    if (rowNode.type === "dir") {
      const sub = (rowNode.children || []).reduce(function (acc, c) {
        if (c.type === "dir") acc.dirCt++;
        else if (c.type === "file") acc.fileCt++;
        return acc;
      }, { dirCt: 0, fileCt: 0 });
      return {
        metaText: [sub.dirCt && `${sub.dirCt}目录`, sub.fileCt && `${sub.fileCt}文件`].filter(Boolean).join(" | ") || "空",
        description: String(rowNode.description || ""),
      };
    }
    if (rowNode.type === "file") {
      return {
        metaText: String(rowNode.description || "").slice(0, 120),
        description: String(rowNode.description || ""),
      };
    }
    return { metaText: "", description: "" };
  }

  // ── Actions ──
  const actionBar = el("div", "toolbar");
  actionBar.append(
    makeButton("刷新", doRefresh),
    makeButton("新建文件", doNewFile, "btn btn-primary"),
    makeButton("新建文件夹", doNewFolder),
    makeButton("删除", doDelete),
  );
  addSection("目录操作", [actionBar]);

  // ── Stats badge ──
  const treeStats = countTreeStats(state.kbTree);
  const statsLine = el("div", "card-help");
  statsLine.style.padding = "4px 0";
  statsLine.style.lineHeight = "1.6";
  statsLine.innerHTML = [
    "<b>总计:</b> " + [
      treeStats.dirs && `${treeStats.dirs} 目录`,
      treeStats.files && `${treeStats.files} 文件`,
    ].filter(Boolean).join(", ") +
    (state.graphEntities && state.graphEntities.length ? " | " + state.graphEntities.length + " 实体" : "") +
    (state.graphRelations && state.graphRelations.length ? " | " + state.graphRelations.length + " 关系" : ""),
    "<b>当前:</b> " + currentDir,
  ].join("<br>");
  addSection("统计", [statsLine]);

  // ── Dir list ──
  const list = el("div", "list-box");
  list.style.maxHeight = "350px";

  const rootRow = el("div", `list-row clickable ${currentDir === "/" ? "selected" : ""}`.trim());
  rootRow.append(el("span", "mono", "\u{1F4C1} / (根目录)"));
  rootRow.addEventListener("click", () => { state.kbCurrentDir = "/"; state.kbSelectedPath = ""; renderModule(); });
  list.append(rootRow);

  const parentPath = kbParentPath(currentDir);
  if (parentPath !== currentDir) {
    const upRow = el("div", "list-row clickable");
    upRow.append(el("span", "mono", "\u{1F4C2} .. (上一级: " + parentPath + ")"));
    upRow.addEventListener("click", () => { state.kbCurrentDir = parentPath; state.kbSelectedPath = ""; renderModule(); });
    list.append(upRow);
  }

    const nodes = getKbChildren(state.kbTree, currentDir);
  if (!nodes.length) {
    list.append(el("div", "empty-state", "当前目录为空。"));
  }
  for (const node of nodes) {
    if (node.type === "entity") continue;
    const row = el("div", `list-row clickable ${state.kbSelectedPath === node.path ? "selected" : ""}`.trim());
    const label = el("div");
    const iconText = node.type === "file" ? "\u{1F4C4}[FILE]" : "\u{1F4C1}[DIR]";
    // 图片文件显示特殊图标
    const isImage = node.type === "file" && /\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(node.name);
    const displayIcon = isImage ? "\u{1F5BC}[IMG]" : iconText;
    const titleSpan = el("span", "mono");
    titleSpan.textContent = displayIcon + " " + node.name;
    label.append(titleSpan);

    const meta = getDirMeta(node);
    if (meta.description) {
      const descLine = el("div", "card-help");
      descLine.style.marginLeft = "16px";
      descLine.style.fontSize = "0.85em";
      descLine.textContent = meta.description.length > 100
        ? meta.description.slice(0, 100) + "\u2026"
        : meta.description;
      label.append(descLine);
    }
    if (meta.metaText) {
      const badgeline = el("div", "card-help");
      badgeline.style.marginLeft = "16px";
      badgeline.style.fontSize = "0.8em";
      badgeline.style.color = "#888";
      badgeline.textContent = meta.metaText;
      label.append(badgeline);
    }

    row.append(label);
    row.addEventListener("click", () => {
      if (node.type === "file") {
        state.kbSelectedPath = node.path;
        state.kbSelectedMeta = null;
      } else {
        state.kbCurrentDir = node.path;
        state.kbSelectedPath = "";
        state.kbSelectedMeta = null;
      }
      renderModule();
    });
    row.addEventListener("contextmenu", (evt) => {
      evt.preventDefault();
      state.kbSelectedPath = node.path;
      renderContextMenu(evt.clientX, evt.clientY, node);
    });
    list.append(row);
  }
  addSection("目录: " + currentDir, [list]);

  // ── Detail panel + entity children + metadata for selected file ──
  (async function renderDetailAndEntityChildren() {
    const selPath = state.kbSelectedPath || "";
    if (!selPath) return;

    const detailBox = el("div", "card-help");
    detailBox.style.padding = "8px";
    detailBox.style.marginTop = "4px";
    detailBox.style.background = "var(--bg-secondary, #f5f5f5)";
    detailBox.style.borderRadius = "4px";

    // Detailed metadata
    const meta = state.kbSelectedMeta || {};
    const timeStr = meta.updated_at ? new Date(meta.updated_at + (meta.updated_at.endsWith("Z") ? "" : "Z")).toLocaleString() : "-";
    const contentLen = (state.kbSelectedContent || "").length;
    const sizeStr = meta.content_type && meta.content_type.startsWith("image/") ? "\u{1F5BC} 图片" : (contentLen > 0 ? contentLen + " 字符" : "");

    detailBox.innerHTML = [
      "<b>" + selPath + "</b>",
      "<div style='margin-top:2px;color:#555;font-size:12px'>",
      meta.declared_by ? "创建: " + meta.declared_by + " | " : "",
      meta.updated_at ? "更新: " + timeStr + " | " : "",
      sizeStr ? sizeStr + " | " : "",
      meta.chunk_count ? meta.chunk_count + " 索引块 | " : "",
      meta.score_patch ? "权重 " + meta.score_patch : "",
      "</div>",
      meta.description ? "<div style='margin-top:4px;color:#333'>" + meta.description.slice(0, 200) + "</div>" : "",
      meta.tags && meta.tags.length ? "<div style='margin-top:4px'>标签: " + meta.tags.join(", ") + "</div>" : "",
    ].filter(Boolean).join("");

    // Edit button for files
    if (selPath) {
      const editBar = el("div", "toolbar");
      editBar.style.marginTop = "4px";
      editBar.style.gap = "8px";
      editBar.append(makeButton("编辑文件", async () => {
        await openKbEditorModal(selPath, state.kbSelectedContent || "", meta);
      }, "btn btn-primary"));
      // Image display
      if (meta.content_type && String(meta.content_type).startsWith("image/")) {
        try {
          const imgResp = await cfgApi("GET", "/faust/memory/attachment", null, { path: selPath });
          if (imgResp && imgResp.content_base64) {
            const imgWrap = el("div");
            imgWrap.style.marginTop = "4px";
            const img = el("img");
            img.src = "data:" + imgResp.content_type + ";base64," + imgResp.content_base64;
            img.style.maxWidth = "100%";
            img.style.maxHeight = "300px";
            img.style.borderRadius = "4px";
            img.style.cursor = "pointer";
            img.addEventListener("click", () => {
              const full = el("img");
              full.src = img.src;
              full.style.maxWidth = "100%";
              full.style.maxHeight = "80vh";
              openModal("图片预览", [full]);
            });
            imgWrap.append(img);
            editBar.append(imgWrap);
          }
        } catch (_) {}
      } else if (/\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(selPath)) {
        // Try loading as image even without content_type in meta
        try {
          const imgResp = await cfgApi("GET", "/faust/memory/attachment", null, { path: selPath });
          if (imgResp && imgResp.content_base64) {
            const imgWrap = el("div");
            imgWrap.style.marginTop = "4px";
            const img = el("img");
            img.src = "data:" + (imgResp.content_type || "image/png") + ";base64," + imgResp.content_base64;
            img.style.maxWidth = "100%";
            img.style.maxHeight = "300px";
            imgWrap.append(img);
            editBar.append(imgWrap);
          }
        } catch (_) {}
      }
      detailBox.append(editBar);
    }

    function findInTree(tree, target) {
      if (!tree || typeof tree !== "object") return null;
      if (tree.path === target || tree.id === target) return tree;
      for (const c of tree.children || []) {
        const found = findInTree(c, target);
        if (found) return found;
      }
      return null;
    }
    addSection("详情", [detailBox]);

    // Entity children - only for file nodes
    const found = findInTree(state.kbTree, selPath);
    if (found && found.type === "file") {
      var entChildren = [];
      try {
        const resp = await cfgApi("GET", "/faust/memory/graph/entity-children", null, { path: selPath });
        entChildren = resp.items || [];
      } catch (_) {}
      if (entChildren.length) {
        const entBox = el("div", "list-box");
        entBox.style.maxHeight = "200px";
        entBox.style.marginTop = "4px";
        for (const ent of entChildren) {
          const row = el("div", "list-row entity-row");
          row.style.paddingLeft = "1.5em";
          const label = el("div");
          const icon = el("span", "mono");
          icon.textContent = "\u{1F464}[ENT:" + ent.entity_type + "] " + ent.name;
          label.append(icon);
          if (ent.description) {
            const desc = el("div", "card-help");
            desc.style.marginLeft = "16px";
            desc.textContent = ent.description.length > 80 ? ent.description.slice(0, 80) + "\u2026" : ent.description;
            label.append(desc);
          }
          row.append(label);
          entBox.append(row);
        }
        addSection("文档实体 (" + entChildren.length + ")", [entBox]);
      }
    }
  })();

  // ── Right-click context menu ──
  function renderContextMenu(x, y, node) {
    const old = document.getElementById("kbContextMenu");
    if (old) old.remove();

    const menu = el("div", "context-menu");
    menu.id = "kbContextMenu";
    menu.style.position = "fixed";
    menu.style.left = x + "px";
    menu.style.top = y + "px";
    menu.style.zIndex = "9999";
    menu.style.background = "var(--bg-primary, #fff)";
    menu.style.border = "1px solid var(--border-color, #ccc)";
    menu.style.borderRadius = "6px";
    menu.style.boxShadow = "0 4px 12px rgba(0,0,0,0.15)";
    menu.style.padding = "4px 0";
    menu.style.minWidth = "140px";

    const items = [];
    if (node.type === "file") {
      items.push({ label: "编辑", icon: "\u270F", action: async () => {
        const d = await cfgApi("GET", "/faust/memory/get", null, { path: node.path });
        await openKbEditorModal(node.path, String(d.content || ""), d.meta || {});
      }});
    }
    items.push({ label: "新建文件", icon: "\u{1F4C4}", action: async () => {
      const target = normalizeKbPath((node.path || currentDir) + "/new.md");
      await openKbEditorModal(target, "", { path: target, declared_by: "config-center", indexed: true, tags: [] });
    }});
    items.push({ label: node.type === "file" ? "删除文件" : "删除目录", icon: "\u{1F5D1}", action: async () => {
      if (!window.confirm("确定删除 " + node.path + " ?")) return;
      await cfgApi("DELETE", "/faust/memory/delete", null, { path: node.path });
      state.kbSelectedPath = "";
      state.kbCurrentDir = kbParentPath(node.path);
      await ensureModuleData("memory");
      renderModule();
    }});

    for (const it of items) {
      const item = el("div", "context-menu-item");
      item.style.padding = "6px 16px";
      item.style.cursor = "pointer";
      item.style.display = "flex";
      item.style.alignItems = "center";
      item.style.gap = "8px";
      item.style.fontSize = "13px";
      item.innerHTML = (it.icon || "") + " " + it.label;
      item.addEventListener("mouseenter", () => { item.style.background = "var(--bg-secondary, #f0f0f0)"; });
      item.addEventListener("mouseleave", () => { item.style.background = "transparent"; });
      item.addEventListener("click", async () => {
        menu.remove();
        await it.action();
        renderModule();
      });
      menu.append(item);
    }

    document.body.append(menu);
    const close = (evt) => {
      if (!menu.contains(evt.target)) {
        menu.remove();
        document.removeEventListener("click", close);
      }
    };
    document.addEventListener("click", close);
  }

  // ── Import ──
  const importBar = el("div", "toolbar");
  importBar.append(makeButton("导入外部文件", async () => {
    const filePath = await window.api.configOpenFile({ title: "选择外部文件" });
    if (!filePath) return;
    const kbPath = window.prompt("KB 路径（可留空自动）") || "";
    await cfgApi("POST", "/faust/memory/declare-update", { file_path: filePath, kb_path: kbPath.trim() || null });
    await ensureModuleData("memory");
    showBanner("success", "文件已导入。");
    renderModule();
  }));
  addSection("导入", [importBar]);
}

function renderMemoryGraph() {
  const wrap = el("div", "graph-canvas-wrap");
  wrap.id = "graphCanvasWrap";
  addSection("", [wrap]);

  // ── Toolbar ──
  const searchInput = el("input", "input");
  searchInput.placeholder = "搜索实体名称";
  searchInput.style.maxWidth = "200px";
  const statusText = el("span", "card-help", "加载中...");

  const tb = el("div", "graph-toolbar");
  tb.append(searchInput, makeButton("搜索", doSearch, "btn btn-primary"), statusText,
    makeButton("＋放大", () => { if (gc) { gc._viewScale *= 1.2; gc.render(); } }, "btn btn-ghost"),
    makeButton("−缩小", () => { if (gc) { gc._viewScale /= 1.2; gc.render(); } }, "btn btn-ghost"),
    makeButton("适应", () => { if (gc) gc.fitToScreen(); }, "btn btn-ghost"),
  );
  addSection("操作", [tb]);

  // ── Legend ──
  const legend = el("div", "graph-legend");
  legend.append(el("span", "", "图例: "));
  for (const [k, v] of Object.entries(GRAPH_COLORS)) {
    const dot = el("span", "graph-legend-dot");
    dot.style.background = v;
    const item = el("span", "graph-legend-item");
    item.append(dot, document.createTextNode(" " + k));
    legend.append(item);
  }
  addSection("图例", [legend]);

  // ── Context hint ──
  const ctxHint = el("div", "card-help");
  ctxHint.textContent = state.kbSelectedPath
    ? "基于选中文件 \"" + state.kbSelectedPath + "\" 的实体图谱（2跳展开）"
    : "在树上选中一个文件查看其关联实体图谱";
  addSection("", [ctxHint]);

  // ── Init graph ──
  let gc = null;
  let allEntities = [];
  let allRelations = [];

  async function initGraph() {
    try {
      statusText.textContent = "请求数据中...";
      let nodes = [];
      let edges = [];
      const selPath = state.kbSelectedPath || "";

      if (selPath) {
        // Load entity children of selected file, then expand each
        const [entResp] = await Promise.all([
          cfgApi("GET", "/faust/memory/graph/entity-children", null, { path: selPath })
        ]);
        const entChildren = entResp.items || [];
        const seenIds = new Set();
        const seenEdgeKeys = new Set();
        for (const ent of entChildren) {
          if (seenIds.has(ent.id)) continue;
          seenIds.add(ent.id);
          nodes.push({ id: ent.id, name: ent.name, entity_type: ent.entity_type, type: "entity", description: ent.description });
          // Expand each entity to find neighbors and all relations
          try {
            const expResp = await cfgApi("GET", "/faust/memory/graph/expand", null, { entity_id: ent.id, depth: 2 });
            for (const n of expResp.items || []) {
              if (!seenIds.has(n.id)) {
                seenIds.add(n.id);
                nodes.push({ id: n.id, name: n.name, entity_type: n.entity_type || n.type, type: "entity", description: n.description });
              }
            }
            for (const e of expResp.edges || []) {
              const ek = e.key || e.source + "->" + e.target;
              if (!seenEdgeKeys.has(ek)) {
                seenEdgeKeys.add(ek);
                edges.push({ source: e.source, target: e.target, type: e.type, key: ek });
              }
            }
          } catch (_) {}
        }
        // Also fetch all relations that connect any of these nodes
        try {
          const relResp = await cfgApi("GET", "/faust/memory/graph/relations");
          for (const r of relResp.items || []) {
            if ((seenIds.has(r.source) || seenIds.has(r.target))) {
              const ek = r.key || r.source + "->" + r.target;
              if (!seenEdgeKeys.has(ek)) {
                seenEdgeKeys.add(ek);
                edges.push({ source: r.source, target: r.target, type: r.type, key: ek });
                // Also add neighbor nodes not yet in set
                for (const side of [r.source, r.target]) {
                  if (!seenIds.has(side)) {
                    seenIds.add(side);
                    // fetch node details
                    try {
                      const nbResp = await cfgApi("GET", "/faust/memory/graph/neighbors", null, { entity_id: side, depth: 0 });
                      const nb = nbResp.items || [];
                      for (const n of nb) {
                        if (!seenIds.has(n.id)) {
                          seenIds.add(n.id);
                          nodes.push({ id: n.id, name: n.name, entity_type: n.entity_type || "custom", type: "entity", description: n.description });
                        }
                      }
                    } catch (_) {}
                  }
                }
              }
            }
          }
        } catch (_) {}
      } else {
        const [fullData] = await Promise.all([
          cfgApi("GET", "/faust/memory/graph/full"),
        ]);
        const allEntities = fullData.entities || [];
        const allRelations = fullData.relations || [];
        nodes = allEntities.map(function (e) {
          return { id: e.id, name: e.name, entity_type: e.entity_type || e.type, type: "entity" };
        });
        edges = allRelations.map(function (r) {
          return { source: r.source, target: r.target, type: r.type, key: r.key };
        });
      }

      if (!gc) {
        gc = new GraphCanvas(wrap);
      }
      gc.setData(nodes, edges);
      statusText.textContent = "实体: " + nodes.length + " | 关系: " + edges.length;

      gc.onNodeClick(function (node) {
        gc._selectedNode = node;
        gc.render();
        if (gc._expanded[node.id]) return;
        gc._expanded[node.id] = true;
        statusText.textContent = "展开 " + node.name + "...";
        cfgApi("GET", "/faust/memory/graph/expand", null, { entity_id: node.id, depth: GRAPH_EXPAND_DEPTH || 1 }).then(function (data) {
          const items = data.items || [];
          const newEdges2 = data.edges || [];
          const newNodes = items.filter(function (it) {
            return !gc.simulation.nodes.some(function (n) { return n.id === it.id; });
          }).map(function (it) {
            return { id: it.id, name: it.name, entity_type: it.entity_type || it.type, type: "entity" };
          });
          if (newNodes.length) {
            gc.addNodes(newNodes, newEdges2);
          }
          statusText.textContent = "实体: " + gc.simulation.nodes.length + " | 关系: " + gc.simulation.edges.length;
        }).catch(function () { statusText.textContent = "展开失败"; });
      });

      gc.fitToScreen();
    } catch (e) {
      statusText.textContent = "加载失败: " + (e.message || e);
    }
  }

  // ── Search ──
  async function doSearch() {
    const q = searchInput.value.trim();
    if (!q || !gc) return;
    statusText.textContent = "搜索中...";
    try {
      const data = await cfgApi("GET", "/faust/memory/graph/search", null, { query: q, top_k: 20 });
      const items = data.items || [];
      const ids = items.map(function (it) { return it.id; });
      gc.highlightIds(ids);
      if (items.length) gc.focusNode(items[0].id);
      statusText.textContent = "找到 " + items.length + " 个匹配实体";
    } catch (e) {
      statusText.textContent = "搜索失败";
    }
  }
  searchInput.addEventListener("keydown", function (evt) { if (evt.key === "Enter") doSearch(); });

  initGraph();
}

function renderMemorySearch() {
  const searchInput = el("input", "input");
  searchInput.placeholder = "输入关键词搜索";
  const resultBox = el("div", "list-box");
  resultBox.style.maxHeight = "500px";

  const iconByExt = (p) => /\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(p) ? "\u{1F5BC}" : "\u{1F4C4}";

  const openResult = async (path) => {
    state.kbCurrentDir = kbParentPath(normalizeKbPath(path));
    state.kbSelectedPath = normalizeKbPath(path);
    state.memoryView = "tree";
    const d = await cfgApi("GET", "/faust/memory/get", null, { path: state.kbSelectedPath });
    state.kbSelectedContent = String(d.content || "");
    state.kbSelectedMeta = d.meta || {};
    renderModule();
  };

  const doSearch = async () => {
    const q = searchInput.value.trim();
    if (!q) { showBanner("info", "请输入关键词。"); return; }
    resultBox.innerHTML = `<div class="empty-state">搜索中...</div>`;
    try {
      const data = await cfgApi("POST", "/faust/memory/search-compact", { query: q, top_k: 5 });
      resultBox.innerHTML = "";
      const items = data.items || [];
      if (!items.length) { resultBox.append(el("div", "empty-state", "未找到匹配内容。")); return; }
      for (const it of items) {
        const row = el("div", "list-row clickable");
        row.style.padding = "10px 12px";
        const left = el("div", "field-wrap");
        const scoreStr = it.score != null && it.score > 0 ? " score=" + it.score.toFixed(2) : "";
        const lcStr = it.line_count > 0 ? it.line_count + " 行" : (it.line_count === 0 && it.description ? "（关联文件）" : "");
        left.append(
          el("div", "mono", iconByExt(it.path) + " " + it.path),
          el("div", "card-help", [
            lcStr && lcStr,
            scoreStr && scoreStr,
          ].filter(Boolean).join(" | ") || ""),
          el("div", "card-help", String(it.description || "").slice(0, 200))
        );
        row.append(left);
        row.addEventListener("click", () => openResult(it.path));
        resultBox.append(row);
      }
    } catch (err) { resultBox.innerHTML = `<div class="empty-state">搜索失败: ${err}</div>`; }
  };
  searchInput.addEventListener("keydown", (evt) => { if (evt.key === "Enter") doSearch(); });

  const bar = el("div", "toolbar");
  bar.append(searchInput, makeButton("搜索", doSearch, "btn btn-primary"));
  addSection("统一搜索", [bar, resultBox]);
}

function renderArayaModule() {
  const status = state.araya || {};
  const enabled = document.createElement("input");
  enabled.type = "checkbox";
  enabled.checked = Boolean(status.enabled);
  const idle = el("input", "number");
  idle.type = "number";
  idle.value = String(status.idle_minutes || 30);

  const bar = el("div", "toolbar");
  const progressArea = el("div", "araya-progress", "");
  progressArea.style.cssText = "margin:8px 0;padding:8px;background:#f5f7fa;border-radius:6px;font-size:13px;color:#555;white-space:pre-wrap;min-height:20px;display:none";

  let activeEventSource = null;

  const triggerSlider = createArayaTriggerSlider(async () => {
    if (activeEventSource) {
      activeEventSource.close();
      activeEventSource = null;
    }

    const baseUrl = (window.api && window.api.backendBaseUrl) || "http://127.0.0.1:13900";
    const url = baseUrl + "/faust/araya/trigger-sse?reason=manual_from_configer";

    progressArea.style.display = "block";
    progressArea.textContent = "正在连接...";

    return new Promise((resolve, reject) => {
      const es = new EventSource(url);
      activeEventSource = es;

      es.addEventListener("step", (evt) => {
        try {
          const data = JSON.parse(evt.data);
          switch (data.type) {
            case "start":
              progressArea.textContent = `目标 Agent: ${data.target_agent || "-"}\n原因: ${data.reason || "-"}\n正在启动...`;
              break;
            case "llm_start":
              progressArea.textContent += "\n→ 正在调用 LLM...";
              break;
            case "llm_chunk":
              break;
            case "tool_start":
              progressArea.textContent += `\n→ 工具调用: ${data.tool || "?"} args=${JSON.stringify(data.args)}`;
              break;
            case "tool_end":
              progressArea.textContent += `\n→ 工具完成: ${data.tool || "?"}`;
              break;
            default:
              break;
          }
        } catch (e) {
          console.warn("SSE step parse error", e);
        }
      });

      es.addEventListener("done", (evt) => {
        es.close();
        activeEventSource = null;
        try {
          const data = JSON.parse(evt.data);
          progressArea.textContent += `\n\n✓ 完成! (耗时 ${data.duration || "?"}s)`;
        } catch (e) {
          progressArea.textContent += "\n\n✓ 完成!";
        }
        ensureModuleData("araya");
        renderModule();
        showBanner("success", "Araya 执行完成。");
        resolve();
      });

      es.addEventListener("error", (evt) => {
        es.close();
        activeEventSource = null;
        let msg = "未知错误";
        try {
          if (evt.data) {
            const data = JSON.parse(evt.data);
            msg = data.message || data.error || msg;
          }
        } catch (e) {}
        progressArea.textContent += `\n\n✗ 错误: ${msg}`;
        ensureModuleData("araya");
        renderModule();
        showBanner("error", `Araya 错误: ${msg}`);
        reject(new Error(msg));
      });
    });
  });

  bar.append(
    enabled,
    el("span", "switch-text", "启用 Araya 自动维护"),
    el("span", "switch-text", "空闲分钟"),
    idle,
    makeButton("保存设置", async () => {
      await cfgApi("POST", "/faust/araya/settings", {
        enabled: enabled.checked,
        idle_minutes: Number(idle.value || 30),
      });
      showBanner("success", "Araya 设置已保存。");
    }, "btn btn-primary"),
    makeButton("刷新状态", async () => {
      await ensureModuleData("araya");
      renderModule();
    }),
    triggerSlider
  );
  addSection("Araya 控制", [bar, progressArea]);

  const idleMinutes = Number(status.idle_minutes || 0);
  const idleSeconds = Number(status.idle_seconds || 0);
  const thresholdSeconds = idleMinutes > 0 ? idleMinutes * 60 : 0;
  const remainSeconds = Math.max(0, thresholdSeconds - idleSeconds);

  const summaryCard = makeInfoCard("运行状态", [
    { label: "目标 Agent", value: status.target_agent },
    { label: "运行线程", value: status.running },
    { label: "执行中", value: status.run_in_progress },
    { label: "配置启用", value: status.enabled_by_config },
    { label: "运行启用", value: status.enabled },
  ]);

  const idleCard = makeInfoCard("空闲触发", [
    { label: "空闲阈值(分钟)", value: idleMinutes || "-" },
    { label: "当前空闲(秒)", value: Math.floor(idleSeconds) },
    { label: "预计剩余(秒)", value: Math.floor(remainSeconds) },
    { label: "最近主 Agent 活跃", value: status.last_main_activity_at },
    { label: "最后更新时间", value: status.updated_at },
  ]);

  const lastLog = status.last_log && typeof status.last_log === "object" ? status.last_log : {};
  const logCard = makeInfoCard("最近一次执行", [
    { label: "触发原因", value: lastLog.reason },
    { label: "结果", value: lastLog.status || lastLog.result || "-" },
    { label: "开始时间", value: lastLog.started_at },
    { label: "结束时间", value: lastLog.finished_at },
    { label: "错误", value: lastLog.error },
  ]);

  els.cardsRoot.append(summaryCard, idleCard, logCard);

  const msgs = Array.isArray(lastLog.messages) ? lastLog.messages : [];
  const lastMsgs = el("textarea", "textarea code-area");
  lastMsgs.readOnly = true;
  lastMsgs.value = msgs.map((x, i) => `#${i + 1} ${String(x || "")}`).join("\n\n") || "无消息片段";
  addSection("最近执行消息片段", [lastMsgs]);
}

function renderRuntimeModule() {
  const bar = el("div", "toolbar");
  bar.append(
    makeButton("重建 Agent Runtime", async () => {
      await cfgApi("POST", "/faust/admin/runtime/reload-agent", {});
      await reloadAll();
      showBanner("success", "Agent Runtime 已重建。" );
    }, "btn btn-secondary"),
    makeButton("重载配置并重建 Runtime", async () => {
      await cfgApi("POST", "/faust/admin/runtime/reload-all", {});
      await reloadAll();
      showBanner("success", "运行时已重载。" );
    }, "btn btn-primary"),
    makeButton("刷新服务列表", async () => {
      await ensureModuleData("runtime");
      renderModule();
    })
  );
  addSection("运行时控制", [bar]);

  const list = el("div", "list-box");
  for (const svc of state.services) {
    const key = String(svc.key || "");
    const row = el("div", `list-row clickable ${state.selectedService === key ? "selected" : ""}`.trim());
    row.append(el("span", "mono", `[SERVICE] ${key} | ${svc.name || "-"} | ${svc.is_running ? "运行中" : "未运行"} | 端口 ${svc.port || "-"}`));
    const ops = el("div", "toolbar compact");
    ops.addEventListener("click", (evt) => evt.stopPropagation());
    ops.append(
      makeButton("查看", async () => {
        state.selectedService = key;
        await ensureModuleData("runtime");
        renderModule();
      }),
      makeButton("启动", async () => { await cfgApi("POST", `/faust/admin/services/${encodeURIComponent(key)}/start`, {}); await ensureModuleData("runtime"); renderModule(); }),
      makeButton("停止", async () => { await cfgApi("POST", `/faust/admin/services/${encodeURIComponent(key)}/stop`, {}); await ensureModuleData("runtime"); renderModule(); }),
      makeButton("重启", async () => { await cfgApi("POST", `/faust/admin/services/${encodeURIComponent(key)}/restart`, {}); await ensureModuleData("runtime"); renderModule(); })
    );
    row.append(ops);
    row.addEventListener("click", async () => {
      state.selectedService = key;
      await ensureModuleData("runtime");
      renderModule();
    });
    list.append(row);
  }
  addSection("服务列表", [list]);

  const log = el("textarea", "textarea code-area");
  log.readOnly = true;
  log.value = String((state.serviceDetail && state.serviceDetail.log_tail) || "暂无日志");
  log.classList.add("code-area-lg");
  addSection("服务日志", [log]);
}

function openTriggerEditorModal(initialTrigger, onSubmit) {
  const source = initialTrigger || {
    id: "",
    type: "interval",
    interval_seconds: 60,
    target: "",
    eval_code: "",
    recall_description: "",
    lifespan: "",
  };

  const idInput = el("input", "input");
  idInput.placeholder = "Trigger ID";
  idInput.value = String(source.id || "");

  const typeSelect = el("select", "select");
  for (const t of ["interval", "datetime", "py-eval"]) {
    const opt = document.createElement("option");
    opt.value = t;
    opt.textContent = t;
    if (String(source.type || "interval") === t) opt.selected = true;
    typeSelect.append(opt);
  }

  const recallInput = el("input", "input");
  recallInput.placeholder = "描述（可选）";
  recallInput.value = String(source.recall_description || "");

  const lifespanInput = el("input", "number");
  lifespanInput.type = "number";
  lifespanInput.placeholder = "lifespan 秒（可选）";
  lifespanInput.value = source.lifespan === null || source.lifespan === undefined ? "" : String(source.lifespan);

  const dynamicWrap = el("div", "field-wrap");

  const intervalInput = el("input", "number");
  intervalInput.type = "number";
  intervalInput.min = "1";
  intervalInput.value = String(source.interval_seconds || 60);

  const targetInput = el("input", "input");
  targetInput.placeholder = "YYYY-MM-DD HH:mm:ss";
  targetInput.value = String(source.target || "");

  const evalArea = el("textarea", "textarea code-area");
  evalArea.value = String(source.eval_code || "");

  const renderDynamic = () => {
    dynamicWrap.innerHTML = "";
    const t = typeSelect.value;
    if (t === "interval") {
      dynamicWrap.append(el("div", "card-help", "interval_seconds"), intervalInput);
    } else if (t === "datetime") {
      dynamicWrap.append(el("div", "card-help", "target"), targetInput);
    } else {
      dynamicWrap.append(el("div", "card-help", "eval_code"), evalArea);
    }
  };

  typeSelect.addEventListener("change", renderDynamic);
  renderDynamic();

  const submitBtn = makeButton("保存", async () => {
    const payload = {
      id: idInput.value.trim(),
      type: typeSelect.value,
      recall_description: recallInput.value.trim(),
    };
    const lifespanRaw = lifespanInput.value.trim();
    if (lifespanRaw) payload.lifespan = Number(lifespanRaw);

    if (!payload.id) {
      showBanner("error", "Trigger ID 不能为空。");
      return;
    }
    if (payload.type === "interval") {
      payload.interval_seconds = Number(intervalInput.value || "60");
    } else if (payload.type === "datetime") {
      payload.target = targetInput.value.trim();
    } else {
      payload.eval_code = evalArea.value;
    }

    await onSubmit(payload);
    closeModal();
  }, "btn btn-primary");

  const bar = el("div", "toolbar");
  bar.append(submitBtn, makeButton("关闭", closeModal));

  const body = [
    el("div", "card-help", "编辑 Trigger（已适配 Electron，无需 prompt）"),
    idInput,
    typeSelect,
    recallInput,
    lifespanInput,
    dynamicWrap,
    bar,
  ];
  openModal(source.id ? `编辑 Trigger: ${source.id}` : "新建 Trigger", body);
}

function renderTriggersModule() {
  const bar = el("div", "toolbar");
  bar.append(
    makeButton("刷新", async () => { await ensureModuleData("triggers"); renderModule(); }),
    makeButton("新建", async () => {
      openTriggerEditorModal(null, async (payload) => {
        await cfgApi("POST", "/faust/admin/triggers", payload);
        await ensureModuleData("triggers");
        renderModule();
      });
    }, "btn btn-primary"),
    makeButton("编辑", async () => {
      if (!state.selectedTriggerId) return;
      const source = state.triggers.find((x) => String(x.id) === String(state.selectedTriggerId));
      if (!source) return;
      const base = buildTriggerUpdatePayload(source);
      openTriggerEditorModal(base, async (payload) => {
        await cfgApi("PUT", `/faust/admin/triggers/${encodeURIComponent(state.selectedTriggerId)}`, payload);
        await ensureModuleData("triggers");
        renderModule();
      });
    }),
    makeButton("删除", async () => {
      if (!state.selectedTriggerId) return;
      if (!window.confirm(`确定删除 Trigger ${state.selectedTriggerId} ?`)) return;
      await cfgApi("DELETE", `/faust/admin/triggers/${encodeURIComponent(state.selectedTriggerId)}`);
      state.selectedTriggerId = "";
      await ensureModuleData("triggers");
      renderModule();
    })
  );
  addSection("Trigger 操作", [bar]);

  const list = el("div", "list-box");
  for (const trig of state.triggers) {
    const tid = String(trig.id || "");
    const row = el("div", `list-row clickable ${state.selectedTriggerId === tid ? "selected" : ""}`.trim());
    row.append(el("span", "mono", `[TRIGGER] ${tid} | ${trig.type} | lifespan=${trig.lifespan ?? "-"} | ${trig.recall_description || ""}`));
    const ops = el("div", "toolbar compact");
    ops.addEventListener("click", (evt) => evt.stopPropagation());
    ops.append(
      makeButton("编辑", async () => {
        state.selectedTriggerId = tid;
        const source = state.triggers.find((x) => String(x.id) === tid);
        if (!source) return;
        const base = buildTriggerUpdatePayload(source);
        openTriggerEditorModal(base, async (payload) => {
          await cfgApi("PUT", `/faust/admin/triggers/${encodeURIComponent(tid)}`, payload);
          await ensureModuleData("triggers");
          renderModule();
        });
      }),
      makeButton("删除", async () => {
        if (!window.confirm(`确定删除 Trigger ${tid} ?`)) return;
        await cfgApi("DELETE", `/faust/admin/triggers/${encodeURIComponent(tid)}`);
        if (state.selectedTriggerId === tid) state.selectedTriggerId = "";
        await ensureModuleData("triggers");
        renderModule();
      })
    );
    row.append(ops);
    row.addEventListener("click", () => {
      state.selectedTriggerId = tid;
      renderModule();
    });
    list.append(row);
  }
  addSection("Trigger 列表", [list]);
}

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
      renderModule();
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
      renderModule();
    }, "btn btn-primary"),
    makeButton("从 ZIP 安装", async () => {
      const agentName = (agentInput.value || "").trim() || state.skillsAgent || state.runtime.current_agent || state.config.public.AGENT_NAME || "";
      if (!agentName) {
        showBanner("error", "请先填写 Agent 名称。");
        return;
      }
      const zipPath = await window.api.configOpenFile({ title: "选择 Skill ZIP", filters: [{ name: "ZIP", extensions: ["zip"] }] });
      if (!zipPath) return;
      const overwrite = window.confirm("若已存在是否覆盖安装?");
      await cfgApi("POST", "/faust/admin/skills/install-zip", { zip_path: zipPath, agent_name: agentName, overwrite });
      state.skillsAgent = agentName;
      await ensureModuleData("skills");
      showBanner("success", "Skill ZIP 安装完成。" );
      renderModule();
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
  addSection("Skill 操作", [top]);

  const list = el("div", "list-box");
  for (const sk of state.skills) {
    const slug = String(sk.slug || "");
    const row = el("div", `list-row clickable ${state.selectedSkillSlug === slug ? "selected" : ""}`.trim());
    const prefix = sk.missing ? "MISSING" : (sk.enabled ? "ON" : "OFF");
    row.append(el("span", "mono", `[${prefix}] ${slug} v${sk.version || "-"}`));
    row.addEventListener("click", async () => {
      state.selectedSkillSlug = slug;
      await ensureModuleData("skills");
      renderModule();
    });
    const ops = el("div", "toolbar compact");
    ops.append(
      makeButton("启用", async () => { await cfgApi("POST", `/faust/admin/skills/${encodeURIComponent(slug)}/enable`, { agent_name: state.skillsAgent }); await ensureModuleData("skills"); renderModule(); }),
      makeButton("禁用", async () => { await cfgApi("POST", `/faust/admin/skills/${encodeURIComponent(slug)}/disable`, { agent_name: state.skillsAgent }); await ensureModuleData("skills"); renderModule(); }),
      makeButton("删除", async () => {
        if (!window.confirm(`确定删除 Skill ${slug} ?`)) return;
        await cfgApi("DELETE", `/faust/admin/skills/${encodeURIComponent(slug)}`, null, { agent_name: state.skillsAgent });
        await ensureModuleData("skills");
        renderModule();
      })
    );
    row.append(ops);
    list.append(row);
  }
  addSection("Skill 列表", [list]);
  if (!state.skillDetail) {
    addSection("Skill 详情", [el("div", "empty-state", "请选择 Skill 查看详情。")]);
    return;
  }

  const detail = state.skillDetail || {};
  const meta = detail.meta && typeof detail.meta === "object" ? detail.meta : {};
  const files = Array.isArray(detail.files) ? detail.files : [];

  els.cardsRoot.append(
    makeInfoCard("Skill 基本信息", [
      { label: "Slug", value: detail.slug },
      { label: "版本", value: meta.version || "-" },
      { label: "启用状态", value: detail.enabled },
      { label: "安装时间", value: detail.installed_at },
      { label: "来源", value: detail.source },
      { label: "路径", value: detail.path },
    ]),
    makeInfoCard("Meta 字段", [
      { label: "名称", value: meta.name || meta.title || "-" },
      { label: "作者", value: meta.author || "-" },
      { label: "描述", value: meta.description || "-" },
      { label: "仓库", value: meta.repo || meta.homepage || "-" },
      { label: "入口", value: meta.entry || "-" },
      { label: "许可证", value: meta.license || "-" },
    ])
  );

  els.cardsRoot.append(makeTagListCard("Meta 标签", meta.tags || []));

  const fileRows = files.map((f) => [f, f.endsWith(".md") ? "文档" : "文件"]);
  els.cardsRoot.append(makeSimpleTableCard("Skill 文件清单", ["路径", "类型"], fileRows));
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

function parsePluginFieldValue(fieldType, rawValue) {
  const t = String(fieldType || "str").toLowerCase();
  if (t === "bool") return Boolean(rawValue);
  if (t === "int") return Number.parseInt(String(rawValue || "0"), 10) || 0;
  if (t === "float") return Number.parseFloat(String(rawValue || "0")) || 0;
  if (t === "json") {
    if (!String(rawValue || "").trim()) return null;
    return JSON.parse(String(rawValue));
  }
  return rawValue;
}

function renderPluginsModule() {
  const top = el("div", "toolbar");
  top.append(
    makeButton("刷新", async () => { await ensureModuleData("plugins"); renderModule(); }),
    makeButton("重载插件", async () => { await cfgApi("POST", "/faust/admin/plugins/reload", { apply_runtime: true, no_initial_chat: true }); await ensureModuleData("plugins"); renderModule(); }, "btn btn-secondary"),
    makeButton("从 ZIP 安装", async () => {
      const zipPath = await window.api.configOpenFile({ title: "选择插件 ZIP", filters: [{ name: "ZIP", extensions: ["zip"] }] });
      if (!zipPath) return;
      const overwrite = window.confirm("若插件已存在是否覆盖安装?");
      await cfgApi("POST", "/faust/admin/plugins/install-zip", { zip_path: zipPath, overwrite, apply_runtime: true, no_initial_chat: true, reset_dialog: false });
      await ensureModuleData("plugins");
      renderModule();
    }),
    makeButton("打包为 ZIP", async () => {
      if (!state.selectedPluginId) return;
      const outputDir = await window.api.configOpenDirectory({ title: "选择 ZIP 输出目录" });
      const zipName = window.prompt("ZIP 文件名（可留空）") || "";
      const payload = { plugin_id: state.selectedPluginId };
      if (outputDir) payload.output_dir = outputDir;
      if (zipName.trim()) payload.zip_name = zipName.trim();
      const data = await cfgApi("POST", "/faust/admin/plugins/package-zip", payload);
      showBanner("success", `插件已打包: ${(data.package || {}).zip_path || "-"}`);
    })
  );
  addSection("插件操作", [top]);

  const list = el("div", "list-box");
  for (const p of state.plugins) {
    const pid = String(p.id || "");
    const row = el("div", `list-row clickable ${state.selectedPluginId === pid ? "selected" : ""}`.trim());
    row.append(el("span", "mono", `[${p.enabled ? "ON" : "OFF"}] ${pid} | ${p.version || "-"}`));
    row.addEventListener("click", async () => {
      state.selectedPluginId = pid;
      await ensureModuleData("plugins");
      renderModule();
    });
    const ops = el("div", "toolbar compact");
    ops.append(
      makeButton("启用", async () => { await cfgApi("POST", `/faust/admin/plugins/${encodeURIComponent(pid)}/enable`, { apply_runtime: true, no_initial_chat: true, reset_dialog: true }); await ensureModuleData("plugins"); renderModule(); }),
      makeButton("禁用", async () => { await cfgApi("POST", `/faust/admin/plugins/${encodeURIComponent(pid)}/disable`, { apply_runtime: true, no_initial_chat: true, reset_dialog: true }); await ensureModuleData("plugins"); renderModule(); }),
      makeButton("删除", async () => {
        if (!window.confirm(`确定删除插件 ${pid} ?`)) return;
        await cfgApi("DELETE", `/faust/admin/plugins/${encodeURIComponent(pid)}`, null, { apply_runtime: "true", reset_dialog: "false", no_initial_chat: "true" });
        await ensureModuleData("plugins");
        renderModule();
      })
    );
    row.append(ops);
    list.append(row);
  }
  addSection("插件列表", [list]);

  const selected = state.plugins.find((x) => String(x.id) === String(state.selectedPluginId));
  if (!selected) {
    addSection("插件详情", [el("div", "empty-state", "请选择插件。")]);
    return;
  }

  const health = selected.health && typeof selected.health === "object" ? selected.health : {};
  const triggerControl = selected.trigger_control && typeof selected.trigger_control === "object" ? selected.trigger_control : {};
  els.cardsRoot.append(
    makeInfoCard("插件基本信息", [
      { label: "ID", value: selected.id },
      { label: "名称", value: selected.name },
      { label: "版本", value: selected.version },
      { label: "作者", value: selected.author },
      { label: "主页", value: selected.homepage },
      { label: "启用", value: selected.enabled },
      { label: "优先级", value: selected.priority },
      { label: "描述", value: selected.description },
    ]),
    makeInfoCard("健康状态", [
      { label: "status", value: health.status || "unknown" },
      { label: "error", value: health.error || "-" },
      { label: "append filter", value: triggerControl.supports_append_filter },
      { label: "fire filter", value: triggerControl.supports_fire_filter },
    ])
  );
  els.cardsRoot.append(makeTagListCard("权限", selected.permissions || []));

  const toolRows = (selected.tools || []).map((x) => [x.name, x.enabled, x.description || "-"]);
  els.cardsRoot.append(makeSimpleTableCard("工具注册", ["名称", "启用", "描述"], toolRows));

  const middlewareRows = (selected.middlewares || []).map((x) => [x.name, x.priority, x.enabled, x.description || "-"]);
  els.cardsRoot.append(makeSimpleTableCard("中间件注册", ["名称", "优先级", "启用", "描述"], middlewareRows));

  const schema = ((selected.config || {}).schema) || [];
  if (!schema.length) {
    addSection("插件配置", [el("div", "empty-state", "该插件未注册配置项。")]);
  } else {
    const form = el("div", "plugin-form");
    for (const item of schema) {
      const key = String(item.key || "");
      if (!key) continue;
      const type = String(item.type || "str");
      const wrap = el("div", "plugin-field");
      wrap.append(el("label", "card-key", `${item.label || key} (${key})`));
      let input;
      const val = state.pluginConfigDraft[key];
      if (type === "bool") {
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = Boolean(val);
      } else if (type === "json") {
        input = el("textarea", "textarea");
        input.value = toText(val);
      } else if (type === "int" || type === "float") {
        input = el("input", "number");
        input.type = "number";
        input.value = String(val ?? "");
      } else {
        input = el("input", "input");
        input.type = SECRET_KEYS.has(key) ? "password" : "text";
        input.value = val === null || val === undefined ? "" : String(val);
      }
      if (item.description) {
        input.title = String(item.description);
      }
      input.addEventListener("input", () => {
        if (type === "bool") state.pluginConfigDraft[key] = Boolean(input.checked);
        else state.pluginConfigDraft[key] = input.value;
      });
      input.addEventListener("change", () => {
        if (type === "bool") state.pluginConfigDraft[key] = Boolean(input.checked);
      });
      wrap.append(input);
      form.append(wrap);
    }

    const saveBtn = makeButton("保存插件配置并重载", async () => {
      const values = {};
      for (const item of schema) {
        const key = String(item.key || "");
        if (!key) continue;
        values[key] = parsePluginFieldValue(item.type || "str", state.pluginConfigDraft[key]);
      }
      await cfgApi("POST", `/faust/admin/plugins/${encodeURIComponent(selected.id)}/config`, {
        values,
        apply_runtime: true,
        reset_dialog: false,
        no_initial_chat: true,
      });
      await ensureModuleData("plugins");
      showBanner("success", `插件 ${selected.id} 配置已保存并重载。`);
      renderModule();
    }, "btn btn-primary");

    addSection("插件配置", [form, saveBtn]);
  }
}

function renderSimpleJsonModule(title, data) {
  const area = el("textarea", "textarea code-area");
  area.readOnly = true;
  area.value = toText(data);
  addSection(title, [area]);
}

function renderOverviewModule() {
  const agent = state.runtime.current_agent || state.config.public.AGENT_NAME || "-";
  const model = state.config.public.CHAT_MODEL || "-";
  const tts = state.config.public.TTS_MODE || "-";
  const asr = state.config.public.ASR_MODE || "-";
  const modelType = String(state.config.public.MODEL_TYPE || "live2d").toLowerCase();
  const modelPath = modelType === "vrm"
    ? (state.config.public.VRM_MODEL_PATH || "-")
    : (state.config.public.LIVE2D_MODEL_PATH || "-");

  const plugins = state.plugins || [];
  const pluginsEnabled = plugins.filter((p) => p.enabled).length;
  const pluginsTotal = plugins.length;

  const services = state.services || [];
  const servicesRunning = services.filter((s) => s.running).length;
  const servicesTotal = services.length;

  // ── 基础信息 ──
  const summaryCard = el("article", "card full-span");
  summaryCard.append(el("h3", "card-title", "运行概览"));
  const summaryGrid = el("div", "info-grid");
  const summaryRows = [
    { label: "当前 Agent", value: agent },
    { label: "主模型", value: model },
    { label: "TTS 模式", value: tts },
    { label: "ASR 模式", value: asr },
    { label: "模型类型", value: modelType.toUpperCase() },
    { label: "模型路径", value: modelPath },
  ];
  for (const row of summaryRows) {
    const item = el("div", "info-item");
    item.append(el("div", "info-key", row.label));
    item.append(el("div", "info-value", row.value));
    summaryGrid.append(item);
  }
  summaryCard.append(summaryGrid);
  els.cardsRoot.append(summaryCard);

  // ── 统计卡片 ──
  const statData = [
    { label: "插件", icon: "🧩", value: `${pluginsEnabled}/${pluginsTotal}`, desc: "已启用/总数" },
    { label: "服务", icon: "⚙️", value: `${servicesRunning}/${servicesTotal}`, desc: "运行中/总数" },
    { label: modelType === "vrm" ? "VRM" : "Live2D", icon: modelType === "vrm" ? "🧊" : "🖼", value: modelPath === "-" ? "未配置" : modelPath.split("/").pop(), desc: "当前模型" },
    { label: "Agent", icon: "🤖", value: agent, desc: "当前角色" },
  ];
  for (const s of statData) {
    const card = el("article", "card");
    card.style.textAlign = "center";
    card.style.justifyContent = "center";
    card.innerHTML = `<div style="font-size:28px;line-height:1.2">${s.icon}</div><div style="font-size:22px;font-weight:700;margin:4px 0">${s.value}</div><div style="font-size:12px;color:var(--muted)">${s.desc}</div>`;
    card.querySelector("div:last-child").style.marginTop = "2px";
    els.cardsRoot.append(card);
  }

  // ── 直播控制台 ──
  const liveCard = el("article", "card full-span");
  liveCard.append(el("h3", "card-title", "直播模式"));
  const liveHelp = el("p", "card-help", "打开直播控制台，可以配置 B站弹幕监听、管理弹幕黑名单、查看实时弹幕和 Trigger 队列。");
  const liveBtn = makeButton("打开直播控制台", async () => {
    if (window.api && typeof window.api.openLiveWindow === "function") {
      await window.api.openLiveWindow();
    } else {
      window.open("live-window.html", "_blank", "width=900,height=700");
    }
  }, "btn btn-primary");
  liveCard.append(liveHelp, liveBtn);
  els.cardsRoot.append(liveCard);

  // ── 更新 ──
  const updateCard = el("article", "card full-span");
  updateCard.append(el("h3", "card-title", "版本更新"));

  const curTag = state.runtimeUpdate?.current_tag || "-";
  const updateInfo = el("p", "card-help", `当前版本: ${curTag}`);
  const updateResult = el("div");
  updateResult.style.marginTop = "8px";

  const progressContainer = el("div");
  progressContainer.style.display = "none";
  progressContainer.style.marginTop = "8px";
  const progressBar = el("div");
  progressBar.style.cssText = "height:8px;background:var(--bg2);border-radius:4px;overflow:hidden";
  const progressFill = el("div");
  progressFill.style.cssText = "height:100%;width:0%;background:var(--accent);transition:width .3s";
  progressBar.append(progressFill);
  const progressLabel = el("div");
  progressLabel.style.cssText = "font-size:11px;color:var(--muted);margin-top:4px";
  progressContainer.append(progressBar, progressLabel);

  function sseDownload(tag, assetName, useProxy) {
    return new Promise((resolve, reject) => {
      progressContainer.style.display = "";
      progressFill.style.width = "0%";
      progressLabel.textContent = "准备下载...";

      window.api.configRequest("POST", "/faust/update/start-download", { tag, asset_name: assetName, use_proxy: useProxy })
        .then((res) => {
          if (res.status !== "started") {
            reject(new Error(res.error || "启动下载失败"));
            return;
          }
          const base = window.api.backendBaseUrl || "http://127.0.0.1:13900";
          const es = new EventSource(`${base}/faust/update/download/${res.download_id}/events`);
          let closed = false;
          function done(data) {
            if (closed) return;
            closed = true;
            es.close();
            resolve(data);
          }
          function fail(msg) {
            if (closed) return;
            closed = true;
            es.close();
            reject(new Error(msg));
          }
          es.addEventListener("progress", (ev) => {
            try {
              const d = JSON.parse(ev.data);
              if (d.done) { done(d); return; }
              progressFill.style.width = d.progress + "%";
              progressLabel.textContent = `${d.downloaded_mb}MB / ${d.total_mb}MB  (${d.speed_mbps}MB/s)`;
            } catch (e) { /* ignore */ }
          });
          es.addEventListener("complete", (ev) => {
            try { done(JSON.parse(ev.data)); } catch (e) { fail("解析完成事件失败"); }
          });
          es.addEventListener("error", (ev) => {
            try {
              const d = JSON.parse(ev.data);
              if (d && d.error) { fail(d.error); return; }
            } catch (e) { /* ignore */ }
            fail("下载连接中断");
          });
        })
        .catch(reject);
    });
  }

  const checkBtn = makeButton("检查更新", async () => {
    checkBtn.disabled = true;
    checkBtn.textContent = "检查中...";
    updateResult.innerHTML = "";
    progressContainer.style.display = "none";
    try {
      const resp = await window.api.configRequest("POST", "/faust/update/check", {});
      const data = resp || {};
      if (data.status === "error") {
        updateResult.innerHTML = `<span style="color:var(--danger)">${data.error || "检查失败"}</span>`;
        return;
      }
      state.runtimeUpdate = data;
      if (data.has_update) {
        updateResult.innerHTML = `
          <div style="color:var(--accent);font-weight:600;margin-bottom:6px">
            新版本可用: ${data.latest_tag} (${data.latest_version})
          </div>
          <div style="font-size:12px;margin-bottom:8px;max-height:80px;overflow:auto;background:var(--bg2);padding:6px;border-radius:4px">
            ${(data.release_body || "暂无发布说明").substring(0, 500)}
          </div>
        `;
        const btnRow = el("div", "toolbar");
        const applyBtn = makeButton("开始更新", async () => {
          applyBtn.disabled = true;
          applyBtn.textContent = "下载中...";
          try {
            await sseDownload(data.latest_tag, data.asset_name, proxyChk.checked);
            const ar = await window.api.configRequest("POST", "/faust/update/apply", {
              tag: data.latest_tag,
              asset_name: data.asset_name,
            });
            if (ar.status === "update_prepared") {
              updateResult.innerHTML = `<div style="color:var(--accent);font-weight:600">
                更新已准备就绪。请关闭所有窗口后重新启动 Faust 以完成更新。
              </div>`;
              progressContainer.style.display = "none";
            } else {
              showBanner("error", `更新失败: ${ar.error || "未知错误"}`);
            }
          } catch (e) {
            showBanner("error", `更新异常: ${e}`);
          }
          applyBtn.disabled = false;
          applyBtn.textContent = "开始更新";
        });
        btnRow.append(applyBtn);
        updateResult.append(btnRow);
        updateResult.append(progressContainer);
      } else {
        updateResult.innerHTML = `<span style="color:var(--muted)">已是最新版本</span>`;
      }
    } catch (e) {
      updateResult.innerHTML = `<span style="color:var(--danger)">检查更新失败: ${e}</span>`;
    }
    checkBtn.disabled = false;
    checkBtn.textContent = "检查更新";
  });

  // ── 镜像切换 ──
  const proxyRow = el("div");
  proxyRow.style.cssText = "margin-top:8px;display:flex;align-items:center;gap:8px";
  const proxyChk = el("input");
  proxyChk.type = "checkbox";
  proxyChk.id = "update-proxy-chk";
  proxyChk.checked = true;
  const proxyLbl = el("label", "", "使用镜像加速下载 (gh-proxy.com)");
  proxyLbl.htmlFor = "update-proxy-chk";
  proxyRow.append(proxyChk, proxyLbl);
  updateCard.append(proxyRow);

  updateCard.append(updateInfo, checkBtn, updateResult);
  els.cardsRoot.append(updateCard);

  // ── 数据目录 ──
  const dataCard = el("article", "card full-span");
  dataCard.append(el("h3", "card-title", "数据目录"));
  const dataHelp = el("p", "card-help", "打开 FaustBot 用户数据目录（Agent 文件、日志、配置、插件等）。");
  const dataBtn = makeButton("打开数据目录", async () => {
    const root = await window.api.getFaustbotRoot();
    await window.api.configOpenPath(root);
  }, "btn btn-primary");
  dataCard.append(dataHelp, dataBtn);
  els.cardsRoot.append(dataCard);

  // ── 最近 ERROR 日志 ──
  const errors = state.recentErrors || [];
  const errCard = el("article", "card full-span");
  errCard.append(el("h3", "card-title", "最近错误日志"));
  if (!errors.length) {
    errCard.append(el("p", "card-help", "暂无 ERROR 级别日志。"));
  } else {
    for (const item of errors) {
      const ts = item.timestamp || "";
      const msg = item.message || "";
      const name = item.name || "";
      const row = el("div", "list-row");
      row.style.fontSize = "12px";
      row.style.fontFamily = "monospace";
      row.style.color = "#c43";
      row.style.borderBottom = "1px solid var(--line)";
      row.style.padding = "4px 0";
      row.textContent = `${ts} [${name}] ${msg}`;
      errCard.append(row);
    }
    const help = el("p", "card-help");
    help.textContent = `仅显示最近 ${errors.length} 条 ERROR 日志。`;
    errCard.append(help);
  }
  els.cardsRoot.append(errCard);
}




async function renderModule() {
  const current = MODULES.find((m) => m.id === state.activeModule) || MODULES[0];
  els.moduleTitle.textContent = current.title;
  els.moduleDesc.textContent = current.desc;
  clearRoot();
  try {
    await ensureModuleData(current.id);
    if (current.id === "overview") {
      renderOverviewModule();
    } else if (["ai", "live2d", "speech", "advanced"].includes(current.id)) {
      renderConfigModule(current.id);
    } else if (current.id === "agent") {
      renderAgentModule();
    } else if (current.id === "memory") {
      renderMemoryModule();
    } else if (current.id === "araya") {
      renderArayaModule();
    } else if (current.id === "runtime") {
      renderRuntimeModule();
    } else if (current.id === "triggers") {
      renderTriggersModule();
    } else if (current.id === "skills") {
      renderSkillsModule();
    } else if (current.id === "plugins") {
      renderPluginsModule();
    } else {
      renderSimpleJsonModule("数据", state);
    }
  } catch (err) {
    addSection("错误", [el("div", "empty-state", `模块加载失败: ${String(err && err.message ? err.message : err)}`)]);
  }
}

function renderNav() {
  els.moduleNav.innerHTML = "";
  for (const mod of MODULES) {
    const btn = el("button", `nav-btn ${state.activeModule === mod.id ? "active" : ""}`.trim());
    btn.type = "button";
    btn.innerHTML = `<span>${mod.title}</span><small>${mod.desc}</small>`;
    btn.addEventListener("click", async () => {
      state.activeModule = mod.id;
      renderNav();
      await renderModule();
    });
    els.moduleNav.append(btn);
  }
}

function resetDirty() {
  state.dirty.public.clear();
  state.dirty.private.clear();
  refreshDirtyUI();
}

async function loadConfig() {
  const data = await cfgApi("GET", "/faust/admin/config");
  state.config = { public: { ...(data.public || {}) }, private: { ...(data.private || {}) } };
  state.original = { public: { ...(data.public || {}) }, private: { ...(data.private || {}) } };
  resetDirty();
}

async function loadRuntimeSummary() {
  const data = await cfgApi("GET", "/faust/admin/runtime");
  state.runtime = data.runtime || {};
}

async function reloadAll() {
  setBusy(true);
  try {
    await loadConfig();
    await loadRuntimeSummary();
    hideBanner();
    await renderModule();
  } catch (err) {
    showBanner("error", `刷新失败: ${String(err && err.message ? err.message : err)}`);
  } finally {
    setBusy(false);
  }
}

async function saveConfig() {
  const dirtyCount = state.dirty.public.size + state.dirty.private.size;
  if (dirtyCount <= 0) {
    showBanner("info", "当前没有未保存更改。");
    return;
  }
  setBusy(true);
  try {
    const payload = {
      public: Object.fromEntries(state.dirty.public.entries()),
      private: Object.fromEntries(state.dirty.private.entries()),
    };
    await cfgApi("POST", "/faust/admin/config", payload);
    const hasLive2D = LIVE2D_KEYS.some((k) => state.dirty.public.has(k));
    if (hasLive2D) {
      await cfgApi("POST", "/faust/admin/live2d/apply", {
        public: Object.fromEntries([...state.dirty.public.entries()].filter(([k]) => LIVE2D_KEYS.includes(k))),
      });
    }
    await loadConfig();
    await loadRuntimeSummary();
    await renderModule();
    showBanner("success", "配置已保存。" );
  } catch (err) {
    showBanner("error", `保存失败: ${String(err && err.message ? err.message : err)}`);
  } finally {
    setBusy(false);
  }
}

async function applyRuntime() {
  setBusy(true);
  try {
    await cfgApi("POST", "/faust/admin/config/reload", { reset_dialog: false, no_initial_chat: true });
    await loadRuntimeSummary();
    showBanner("success", "运行时已刷新，配置已应用。" );
  } catch (err) {
    showBanner("error", `应用失败: ${String(err && err.message ? err.message : err)}`);
  } finally {
    setBusy(false);
  }
}

async function reloadFromDisk() {
  const dirtyCount = state.dirty.public.size + state.dirty.private.size;
  if (dirtyCount > 0) {
    const ok = window.confirm("当前有未保存修改，继续 Reload 会丢失这些修改。是否继续？");
    if (!ok) return;
  }
  await reloadAll();
  showBanner("info", "已重新读取配置。" );
}

async function applyTtsReferToService() {
  try {
    const mode = String(state.config.public.TTS_MODE || "local").toLowerCase();
    if (mode !== "local") {
      showBanner("info", "当前 TTS 模式不是 local，无法应用参考音频到本地 TTS 服务。" );
      return;
    }
    await apiLocal("POST", "http://127.0.0.1:5000/change_refer", {
      refer_wav_path: state.config.public.TTS_REFER_WAV_PATH,
      prompt_text: state.config.public.TTS_PROMPT_TEXT,
      prompt_language: state.config.public.TTS_PROMPT_LANGUAGE,
    });
    showBanner("success", "TTS 参考音频已同步到本地服务。" );
  } catch (err) {
    showBanner("error", `同步 TTS 服务失败: ${String(err && err.message ? err.message : err)}`);
  }
}

function bindActions() {
  els.saveBtn.addEventListener("click", saveConfig);
  els.applyBtn.addEventListener("click", applyRuntime);
  els.reloadBtn.addEventListener("click", reloadFromDisk);
  window.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape") closeModal();
  });
}

async function init() {
  renderNav();
  bindActions();
  await reloadAll();
}

init();