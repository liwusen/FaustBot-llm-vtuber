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
  kbTasks: [],
  araya: null,
  services: [],
  selectedService: "",
  serviceDetail: null,
  recentErrors: [],
  triggers: [],
  selectedTriggerId: "",
  skills: [],
  selectedSkillSlug: "",
  skillsAgent: "",
  skillDetail: null,
  plugins: [],
  selectedPluginId: "",
  pluginConfigDraft: {},
  runtimeUpdate: null,
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
    const data = await cfgApi("POST", "/faust/kb/save", {
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
    await ensureModuleData("kb");
    showBanner("success", `KB 已保存: ${targetPath}`);
    renderModule();
  };

  const deleteAction = async () => {
    const targetPath = normalizeKbPath(pathInput.value.trim());
    if (!targetPath || targetPath === "/") return;
    if (!window.confirm(`确定删除 ${targetPath} ?`)) return;
    await cfgApi("POST", "/faust/kb/delete", { path: targetPath });
    state.kbSelectedPath = "";
    state.kbSelectedContent = "";
    state.kbCurrentDir = kbParentPath(targetPath);
    await ensureModuleData("kb");
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
    area.value = String((files && files[filename]) || "");
    areas.set(filename, area);
    card.append(area);
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
    return { publicKeys: LIVE2D_KEYS.filter((k) => publicKeys.includes(k)), privateKeys: [] };
  }
  if (moduleId === "speech") {
    const modeTts = String(state.config.public.TTS_MODE || "").toLowerCase();
    const modeAsr = String(state.config.public.ASR_MODE || "").toLowerCase();
    const isCloud = modeTts === "faustbot-cloud" || modeAsr === "faustbot-cloud";
    const isLocalTts = modeTts === "local";
    const isWhisper = modeAsr === "whisper" || modeAsr === "local";
    const pub = SPEECH_PUBLIC_KEYS.filter((k) => publicKeys.includes(k));
    const pri = SPEECH_PRIVATE_KEYS.filter((k) => privateKeys.includes(k));
    return {
      publicKeys: pub.filter((k) => {
        if (k.startsWith("OPENAI_TTS_") && modeTts !== "openai") return false;
        if (k.startsWith("OPENAI_ASR_") && modeAsr !== "openai") return false;
        if (k.startsWith("EDGE_TTS_") && modeTts !== "edge-tts") return false;
        if (k.startsWith("FAUSTBOT_CLOUD_") && !isCloud) return false;
        if (k.startsWith("WHISPER_") && !isWhisper) return false;
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
    const m = el("article", "card full-span");
    m.append(el("h3", "card-title", "可用模型"));
    const list = el("div", "list-box");
    if (!state.live2dModels.length) {
      list.append(el("div", "empty-state", "暂无模型列表，可先点击右上角 Reload。"));
    } else {
      for (const item of state.live2dModels) {
        const row = el("div", "list-row");
        const path = String(item.path || "");
        row.append(el("span", "", `${item.label || "-"} | ${path}`));
        row.append(makeButton("使用", async () => {
          updateValue("public", "LIVE2D_MODEL_PATH", path);
          renderModule();
          await saveConfig();
        }, "btn btn-ghost"));
        list.append(row);
      }
    }
    m.append(list);
    els.cardsRoot.append(m);
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
      const data = await cfgApi("POST", "/faust/kb/search", {
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
          const d = await cfgApi("GET", "/faust/kb/get", null, { path: state.kbSelectedPath });
          state.kbSelectedContent = String(d.content || "");
          await openKbEditorModal(state.kbSelectedPath, state.kbSelectedContent, d.meta || {});
          renderModule();
        }));
        row.append(left, ops);
        row.addEventListener("click", async () => {
          state.kbSelectedPath = normalizeKbPath(String(it.path || ""));
          state.kbCurrentDir = kbParentPath(state.kbSelectedPath);
          const d = await cfgApi("GET", "/faust/kb/get", null, { path: state.kbSelectedPath });
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
  if (moduleId === "kb") {
    const treeRes = await cfgApi("GET", "/faust/kb/tree", null, { scope: state.kbScope || null });
    state.kbTree = treeRes.tree || null;
    state.kbCurrentDir = normalizeKbPath(state.kbCurrentDir || "/");
    if (!findKbNodeByPath(state.kbTree, state.kbCurrentDir)) {
      state.kbCurrentDir = "/";
    }
    if (state.kbSelectedPath && !findKbNodeByPath(state.kbTree, state.kbSelectedPath)) {
      state.kbSelectedPath = "";
      state.kbSelectedContent = "";
    }
    const taskRes = await cfgApi("GET", "/faust/kb/tasks");
    state.kbTasks = taskRes.items || [];
    if (state.kbSelectedPath) {
      try {
        const nodeRes = await cfgApi("GET", "/faust/kb/get", null, { path: state.kbSelectedPath });
        state.kbSelectedContent = String(nodeRes.content || "");
      } catch (_e) {
        state.kbSelectedContent = "";
      }
    } else {
      state.kbSelectedContent = "";
    }
  }
  if (moduleId === "araya") {
    const ar = await cfgApi("GET", "/faust/araya/status");
    state.araya = ar.araya || null;
  }
  if (moduleId === "runtime") {
    const sv = await cfgApi("GET", "/faust/admin/services");
    state.services = sv.items || [];
    if (!state.selectedService && state.services.length) {
      state.selectedService = String(state.services[0].key || "");
    }
    if (state.selectedService) {
      const detail = await cfgApi("GET", `/faust/admin/services/${encodeURIComponent(state.selectedService)}`, null, { include_log: "true" });
      state.serviceDetail = detail.item || null;
    }
  }
  if (moduleId === "triggers") {
    const tr = await cfgApi("GET", "/faust/admin/triggers");
    state.triggers = (tr.items || []).filter((x) => ["interval", "datetime", "py-eval"].includes(String(x.type || "")));
    if (!state.selectedTriggerId && state.triggers.length) {
      state.selectedTriggerId = String(state.triggers[0].id || "");
    }
  }
  if (moduleId === "skills") {
    const agentName = state.skillsAgent || state.runtime.current_agent || state.config.public.AGENT_NAME;
    state.skillsAgent = agentName || "";
    if (agentName) {
      const sk = await cfgApi("GET", "/faust/admin/skills", null, { agent_name: agentName });
      state.skills = sk.items || [];
      if (!state.selectedSkillSlug && state.skills.length) {
        state.selectedSkillSlug = String(state.skills[0].slug || "");
      }
      if (state.selectedSkillSlug) {
        const d = await cfgApi("GET", `/faust/admin/skills/${encodeURIComponent(state.selectedSkillSlug)}`, null, { agent_name: agentName });
        state.skillDetail = d.detail || null;
      }
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

function renderKbModule() {
  const doKbRefresh = async () => {
    await ensureModuleData("kb");
    renderModule();
  };

  const doKbNewFile = async () => {
    const defaultPath = `${state.kbCurrentDir === "/" ? "" : state.kbCurrentDir.slice(1) + "/"}new.md`;
    const nameInput = el("input", "input");
    nameInput.value = defaultPath;
    const save = async () => {
      const p = String(nameInput.value || "").trim();
      if (!p) return showBanner("error", "请输入文件路径");
      const target = normalizeKbPath(p);
      // 先关闭当前的新建对话框，再打开编辑器对话框
      closeModal();
      await openKbEditorModal(target, "", {
        path: target,
        declared_by: "config-center",
        indexed: true,
        tags: [],
      });
    };
    const kbFileToolbar = el("div", "toolbar");
    kbFileToolbar.append(makeButton("创建", save, "btn btn-primary"), makeButton("取消", closeModal));
    openModal("新建 KB 文件", [nameInput, kbFileToolbar]);
  };

  const doKbNewFolder = async () => {
    const defaultPath = `${state.kbCurrentDir === "/" ? "" : state.kbCurrentDir.slice(1) + "/"}new-folder`;
    const nameInput = el("input", "input");
    nameInput.value = defaultPath;
    const save = async () => {
      const p = String(nameInput.value || "").trim();
      if (!p) return showBanner("error", "请输入文件夹路径");
      await cfgApi("POST", "/faust/kb/mkdir", { path: p });
      state.kbCurrentDir = normalizeKbPath(p);
      await ensureModuleData("kb");
      closeModal();
      renderModule();
    };
    const kbFolderToolbar = el("div", "toolbar");
    kbFolderToolbar.append(makeButton("创建", save, "btn btn-primary"), makeButton("取消", closeModal));
    openModal("新建 KB 文件夹", [nameInput, kbFolderToolbar]);
  };

  const doKbDelete = async () => {
    const p = (state.kbSelectedPath || "").trim();
    if (!p) {
      showBanner("info", "请先在列表中选中要删除的文件或目录。");
      return;
    }
    if (!window.confirm(`确定删除 ${p} ?`)) return;
    await cfgApi("POST", "/faust/kb/delete", { path: p });
    state.kbSelectedPath = "";
    state.kbSelectedContent = "";
    state.kbCurrentDir = kbParentPath(p);
    await ensureModuleData("kb");
    renderModule();
  };

  const closeKbContextMenu = () => {
    const menu = document.getElementById("kbContextMenu");
    if (menu) menu.remove();
  };

  const openKbContextMenu = (clientX, clientY) => {
    closeKbContextMenu();
    const menu = el("div", "kb-context-menu");
    menu.id = "kbContextMenu";
    const mk = (label, handler) => {
      const item = el("button", "kb-context-item", label);
      item.type = "button";
      item.addEventListener("click", async () => {
        closeKbContextMenu();
        await handler();
      });
      return item;
    };
    menu.append(
      mk("刷新", doKbRefresh),
      mk("新建文件", doKbNewFile),
      mk("新建文件夹", doKbNewFolder),
      mk("删除", doKbDelete)
    );
    menu.style.left = `${clientX}px`;
    menu.style.top = `${clientY}px`;
    document.body.append(menu);
      const close = (evt) => {
        // 如果点击在菜单内部，则不关闭
        if (evt && evt.target && menu.contains(evt.target)) return;
        closeKbContextMenu();
        window.removeEventListener("click", close, true);
        window.removeEventListener("contextmenu", close, true);
        window.removeEventListener("keydown", onKey, true);
      };
      const onKey = (evt) => {
        if (evt.key === "Escape") close(evt);
      };
      window.addEventListener("click", close, true);
      window.addEventListener("contextmenu", close, true);
      window.addEventListener("keydown", onKey, true);
  };

  const currentDir = normalizeKbPath(state.kbCurrentDir || "/");

  const searchInput = el("input", "input");
  searchInput.placeholder = `搜索当前目录: ${currentDir}`;
  const searchBtn = makeButton("搜索", async () => {
    const q = searchInput.value.trim();
    if (!q) {
      showBanner("info", "请输入搜索关键词。" );
      return;
    }
    const boundScope = currentDir === "/" ? null : currentDir;
    const data = await cfgApi("POST", "/faust/kb/search", {
      query: q,
      scope: boundScope,
      top_k: 12,
      return: "snippets",
    });
    const listBox = el("div", "list-box");
    listBox.style.maxHeight = "420px";
    const items = data.items || [];
    if (!items.length) {
      listBox.append(el("div", "empty-state", "当前目录下未找到匹配内容。"));
    } else {
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
          const d = await cfgApi("GET", "/faust/kb/get", null, { path: state.kbSelectedPath });
          state.kbSelectedContent = String(d.content || "");
          await openKbEditorModal(state.kbSelectedPath, state.kbSelectedContent, d.meta || {});
          renderModule();
        }));
        row.append(left, ops);
        row.addEventListener("click", async () => {
          state.kbSelectedPath = normalizeKbPath(String(it.path || ""));
          state.kbCurrentDir = kbParentPath(state.kbSelectedPath);
          const d = await cfgApi("GET", "/faust/kb/get", null, { path: state.kbSelectedPath });
          state.kbSelectedContent = String(d.content || "");
          await openKbEditorModal(state.kbSelectedPath, state.kbSelectedContent, d.meta || {});
          renderModule();
        });
        listBox.append(row);
      }
    }
    openModal(`KB 搜索结果 (${boundScope || "/"})`, [listBox]);
  }, "btn btn-secondary");
  searchInput.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter") searchBtn.click();
  });
  const reindexBtn = makeButton("全量重建索引", async () => {
    await cfgApi("POST", "/faust/kb/reindex", {});
    await ensureModuleData("kb");
    renderModule();
  }, "btn btn-secondary");

  const list = el("div", "list-box");
  list.addEventListener("contextmenu", (evt) => {
    evt.preventDefault();
    openKbContextMenu(evt.clientX, evt.clientY);
  });
  const rootRow = el("div", `list-row clickable ${currentDir === "/" ? "selected" : ""}`.trim());
  rootRow.append(el("span", "mono", `[DIR] /  (根目录)`));
  const rootOps = el("div", "toolbar compact");
  rootOps.addEventListener("click", (evt) => evt.stopPropagation());
  rootOps.append(makeButton("进入", () => {
    state.kbCurrentDir = "/";
    state.kbSelectedPath = "";
    renderModule();
  }));
  rootRow.append(rootOps);
  rootRow.addEventListener("click", () => {
    state.kbCurrentDir = "/";
    state.kbSelectedPath = "";
    renderModule();
  });
  list.append(rootRow);

  const parentPath = kbParentPath(currentDir);
  const upRow = el("div", "list-row clickable");
  upRow.append(el("span", "mono", `[DIR] ..  (上一级: ${parentPath})`));
  const upOps = el("div", "toolbar compact");
  upOps.addEventListener("click", (evt) => evt.stopPropagation());
  upOps.append(makeButton("进入", () => {
    state.kbCurrentDir = parentPath;
    state.kbSelectedPath = "";
    renderModule();
  }));
  upRow.append(upOps);
  upRow.addEventListener("click", () => {
    state.kbCurrentDir = parentPath;
    state.kbSelectedPath = "";
    renderModule();
  });
  list.append(upRow);

  const nodes = getKbChildren(state.kbTree, currentDir);
  if (!nodes.length) {
    list.append(el("div", "empty-state", "当前目录为空。"));
  }
  for (const node of nodes) {
    const row = el("div", `list-row clickable ${state.kbSelectedPath === node.path ? "selected" : ""}`.trim());
    row.append(el("span", "mono", `${node.type === "file" ? "[FILE]" : "[DIR]"} ${node.name} | ${node.path}`));
    const ops = el("div", "toolbar compact");
    ops.addEventListener("click", (evt) => evt.stopPropagation());
    ops.append(
      makeButton(node.type === "file" ? "编辑" : "进入", async () => {
        state.kbSelectedPath = node.path;
        if (node.type === "file") {
          const d = await cfgApi("GET", "/faust/kb/get", null, { path: node.path });
          state.kbSelectedContent = String(d.content || "");
          await openKbEditorModal(node.path, state.kbSelectedContent, d.meta || {});
        } else {
          state.kbCurrentDir = node.path;
          state.kbSelectedPath = "";
          state.kbSelectedContent = "";
        }
        renderModule();
      })
    );
    row.append(ops);
    row.addEventListener("click", async () => {
      state.kbSelectedPath = node.path;
      if (node.type === "file") {
        const d = await cfgApi("GET", "/faust/kb/get", null, { path: node.path });
        state.kbSelectedContent = String(d.content || "");
        await openKbEditorModal(node.path, state.kbSelectedContent, d.meta || {});
      } else {
        state.kbCurrentDir = node.path;
        state.kbSelectedPath = "";
        state.kbSelectedContent = "";
      }
      renderModule();
    });
    list.append(row);
  }
  const searchBar = el("div", "toolbar");
  searchBar.append(el("span", "mono", `当前目录: ${currentDir}`), searchInput, searchBtn, reindexBtn);
  addSection("KB 当前目录内容", [searchBar, list]);

  const importBar = el("div", "toolbar");
  importBar.append(makeButton("Declare Update 导入外部文件", async () => {
    const filePath = await window.api.configOpenFile({ title: "选择外部文件" });
    if (!filePath) return;
    const kbPath = window.prompt("输入导入后的 KB 路径（可留空自动）") || "";
    await cfgApi("POST", "/faust/kb/declare-update", { file_path: filePath, kb_path: kbPath.trim() || null });
    await ensureModuleData("kb");
    showBanner("success", "外部文件已导入 KB。" );
    renderModule();
  }));

  const taskView = el("textarea", "textarea code-area");
  taskView.readOnly = true;
  taskView.value = (state.kbTasks || []).slice(0, 30).map((x) => `[${x.status}] ${x.type} | ${x.task_id} | ${x.updated_at}${x.error ? ` | ${x.error}` : ""}`).join("\n");

  addSection("KB 文档", [el("div", "card-help", "文档编辑已迁移到弹窗：点击文件行即可打开编辑器。"), importBar]);
  addSection("KB 后台任务", [taskView]);
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
  const live2d = state.config.public.LIVE2D_MODEL_PATH || "-";

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
    { label: "Live2D 模型", value: live2d },
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
    { label: "Live2D", icon: "🖼", value: live2d === "-" ? "未配置" : live2d.split("/").pop(), desc: "当前模型" },
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
        const dryBtn = makeButton("预览变更", async () => {
          dryBtn.disabled = true;
          dryBtn.textContent = "下载中...";
          try {
            await sseDownload(data.latest_tag, data.asset_name, proxyChk.checked);
            const dr = await window.api.configRequest("POST", "/faust/update/dry-run", {
              tag: data.latest_tag,
              asset_name: data.asset_name,
            });
            if (dr.status === "ok") {
              const lines = [];
              if (dr.new_files?.length) lines.push(`新增文件: ${dr.new_files.length}`);
              if (dr.overwritten?.length) lines.push(`将更新: ${dr.overwritten.length}`);
              if (dr.preserved?.length) lines.push(`将保留: ${dr.preserved.length}`);
              showBanner("info", `Dry-Run: ${lines.join(" | ")}`);
              console.log("[dry-run]", JSON.stringify(dr, null, 2));
            } else {
              showBanner("error", `分析失败: ${dr.error}`);
            }
          } catch (e) {
            showBanner("error", `预览异常: ${e}`);
          }
          dryBtn.disabled = false;
          dryBtn.textContent = "预览变更";
          progressContainer.style.display = "none";
        });
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
        btnRow.append(dryBtn, applyBtn);
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
    } else if (current.id === "kb") {
      renderKbModule();
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