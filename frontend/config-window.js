const META = {
  GUI_OPERATOR_LLM_MODEL: { label: "GUI 操作模型", help: "用于 GUI 自动操作能力的模型名称。" },
  GUI_OPERATOR_LLM_BASE: { label: "GUI 操作接口地址", help: "GUI 自动操作模型使用的 API Base URL。" },
  CHAT_MODEL: { label: "主对话模型", help: "Faust 主聊天与推理使用的模型名称。" },
  CHAT_API_BASE: { label: "主对话接口地址", help: "主对话模型对应的 API Base URL。" },
  SECURITY_VERIFIER_API_ENDPOINT: { label: "安全校验接口地址", help: "安全审查模型使用的 API Base URL。" },
  SECURITY_VERIFIER_LLM_MODEL: { label: "安全校验模型", help: "用于高风险操作前校验的模型名称。" },
  SECURITY_SYS_ENABLED: { label: "启用安全系统", help: "开启后，部分高风险调用会先经过安全审查。" },
  KB_ENABLED: { label: "启用 KB", help: "开启后允许使用树形知识库与向量检索能力。" },
  KB_EMBED_MODEL: { label: "KB 向量模型", help: "知识库文本向量化使用的 embedding 模型名称。" },
  KB_ASYNC_INDEX_ON_WRITE: { label: "KB 异步索引", help: "开启后知识库写入会以后台任务方式异步索引。" },
  ARAYA_ENABLED: { label: "启用 Araya", help: "开启后允许独立记忆维护 Agent 自动触发。" },
  ARAYA_IDLE_MINUTES: { label: "Araya 空闲触发分钟", help: "主 Agent 连续空闲达到该值后允许自动运行。" },
  LIVE2D_MODEL_PATH: { label: "Live2D 模型路径", help: "前端加载的 Live2D 模型文件路径。" },
  LIVE2D_MODEL_SCALE: { label: "Live2D 缩放", help: "模型在前端画布中的整体缩放比例。" },
  LIVE2D_MODEL_X: { label: "Live2D 横向位置", help: "模型 X 坐标；留空时由前端自动决定。" },
  LIVE2D_MODEL_Y: { label: "Live2D 纵向位置", help: "模型 Y 坐标；留空时由前端自动决定。" },
  TEXT_CHAT_BAR_Y_FACTOR: { label: "文字对话框 Y 轴绑定", help: "控制文字对话框绑定在模型高度上的位置，范围 0 到 1。" },
  FRONTEND_QUICK_CONTROLLER_X_OFFSET: { label: "快捷控制栏 X 偏移", help: "控制快捷控制栏横向偏移，单位像素。" },
  FRONTEND_CLICK_THROUGH: { label: "前端点击穿透", help: "开启后桌宠窗口忽略鼠标点击。" },
  FRONTEND_DEFAULT_TTS_LANG: { label: "默认 TTS 语言", help: "前端发送 TTS 请求时默认使用的语言。" },
  TTS_MODE: { label: "TTS 模式", help: "选择本地 TTS 或 OpenAI 兼容 TTS。" },
  ASR_MODE: { label: "ASR 模式", help: "选择本地 ASR 或 OpenAI 兼容 ASR。" },
  OPENAI_TTS_BASE_URL: { label: "OpenAI TTS 接口地址", help: "OpenAI 兼容 TTS 服务的 API Base URL。" },
  OPENAI_TTS_MODEL: { label: "OpenAI TTS 模型", help: "OpenAI 兼容 TTS 所使用的模型名称。" },
  OPENAI_TTS_VOICE: { label: "OpenAI TTS 音色", help: "OpenAI 兼容 TTS 的 voice 参数。" },
  OPENAI_TTS_RESPONSE_FORMAT: { label: "OpenAI TTS 音频格式", help: "TTS 输出音频编码格式。" },
  OPENAI_TTS_SPEED: { label: "OpenAI TTS 语速", help: "TTS 合成语速倍率。" },
  OPENAI_TTS_INSTRUCTIONS: { label: "OpenAI TTS 附加指令", help: "传给 TTS 模型的补充语气说明。" },
  OPENAI_ASR_BASE_URL: { label: "OpenAI ASR 接口地址", help: "OpenAI 兼容 ASR 服务的 API Base URL。" },
  OPENAI_ASR_MODEL: { label: "OpenAI ASR 模型", help: "OpenAI 兼容 ASR 所使用的模型名称。" },
  OPENAI_ASR_LANGUAGE: { label: "OpenAI ASR 语言", help: "可选语言提示，留空则自动判断。" },
  OPENAI_ASR_PROMPT: { label: "OpenAI ASR 提示词", help: "传给识别模型的上下文提示。" },
  OPENAI_ASR_RESPONSE_FORMAT: { label: "OpenAI ASR 返回格式", help: "识别结果返回格式。" },
  OPENAI_ASR_TEMPERATURE: { label: "OpenAI ASR 温度", help: "识别采样温度。" },
  OPENAI_ASR_TIMESTAMP_GRANULARITIES: { label: "OpenAI ASR 时间戳粒度", help: "verbose_json 模式下的时间戳粒度。" },
  CHAT_API_KEY: { label: "主对话密钥", help: "主聊天模型使用的 API Key。" },
  SEARCH_API_KEY: { label: "搜索密钥", help: "联网搜索工具使用的 API Key。" },
  GUI_OPERATOR_LLM_KEY: { label: "GUI 操作密钥", help: "GUI 自动操作模型使用的 API Key。" },
  SECURITY_VERIFIER_LLM_KEY: { label: "安全校验密钥", help: "安全校验模型使用的 API Key。" },
  KB_OPENAI_API_KEY: { label: "KB 密钥", help: "知识库 embedding 使用的 API Key。" },
  OPENAI_TTS_API_KEY: { label: "OpenAI TTS 密钥", help: "OpenAI 兼容 TTS 服务使用的 API Key。" },
  OPENAI_ASR_API_KEY: { label: "OpenAI ASR 密钥", help: "OpenAI 兼容 ASR 服务使用的 API Key。" },
  AGENT_NAME: { label: "当前 Agent", help: "指定当前加载的角色目录名称。" },
  TTS_REFER_WAV_PATH: { label: "TTS 参考音频路径", help: "本地 TTS 的参考音频文件路径。" },
  TTS_PROMPT_TEXT: { label: "TTS 参考文本", help: "参考音频对应的文本内容。" },
  TTS_PROMPT_LANGUAGE: { label: "TTS 参考语言", help: "参考音频文本语言。" },
};

const FIELD_OPTIONS = {
  TTS_MODE: ["local", "openai"],
  ASR_MODE: ["local", "openai"],
  FRONTEND_DEFAULT_TTS_LANG: ["zh", "en", "ja", "ko", "yue"],
  OPENAI_TTS_VOICE: ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"],
  OPENAI_TTS_RESPONSE_FORMAT: ["mp3", "wav", "opus", "aac", "flac", "pcm"],
  OPENAI_ASR_RESPONSE_FORMAT: ["json", "text", "srt", "verbose_json", "vtt"],
  TTS_PROMPT_LANGUAGE: ["zh", "en", "ja", "ko", "yue", "中文", "英文", "日文", "韩文", "粤语"],
};

const AGENT_FILES = ["AGENT.md", "ROLE.md", "COREMEMORY.md", "TASK.md"];
const TEXTAREA_KEYS = new Set(["OPENAI_TTS_INSTRUCTIONS", "OPENAI_ASR_PROMPT", "TTS_PROMPT_TEXT"]);
const SECRET_KEYS = new Set(["CHAT_API_KEY", "SEARCH_API_KEY", "GUI_OPERATOR_LLM_KEY", "SECURITY_VERIFIER_LLM_KEY", "KB_OPENAI_API_KEY", "OPENAI_TTS_API_KEY", "OPENAI_ASR_API_KEY"]);

const AI_PUBLIC_KEYS = ["CHAT_MODEL", "CHAT_API_BASE", "GUI_OPERATOR_LLM_MODEL", "GUI_OPERATOR_LLM_BASE", "SECURITY_VERIFIER_API_ENDPOINT", "SECURITY_VERIFIER_LLM_MODEL", "SECURITY_SYS_ENABLED", "KB_ENABLED", "KB_EMBED_MODEL", "KB_ASYNC_INDEX_ON_WRITE", "AGENT_NAME", "ARAYA_ENABLED", "ARAYA_IDLE_MINUTES"];
const AI_PRIVATE_KEYS = ["CHAT_API_KEY", "SEARCH_API_KEY", "GUI_OPERATOR_LLM_KEY", "SECURITY_VERIFIER_LLM_KEY", "KB_OPENAI_API_KEY"];
const LIVE2D_KEYS = ["LIVE2D_MODEL_PATH", "LIVE2D_MODEL_SCALE", "LIVE2D_MODEL_X", "LIVE2D_MODEL_Y", "TEXT_CHAT_BAR_Y_FACTOR", "FRONTEND_QUICK_CONTROLLER_X_OFFSET", "FRONTEND_CLICK_THROUGH", "FRONTEND_DEFAULT_TTS_LANG"];
const SPEECH_PUBLIC_KEYS = ["TTS_MODE", "ASR_MODE", "OPENAI_TTS_BASE_URL", "OPENAI_TTS_MODEL", "OPENAI_TTS_VOICE", "OPENAI_TTS_RESPONSE_FORMAT", "OPENAI_TTS_SPEED", "OPENAI_TTS_INSTRUCTIONS", "OPENAI_ASR_BASE_URL", "OPENAI_ASR_MODEL", "OPENAI_ASR_LANGUAGE", "OPENAI_ASR_PROMPT", "OPENAI_ASR_RESPONSE_FORMAT", "OPENAI_ASR_TEMPERATURE", "OPENAI_ASR_TIMESTAMP_GRANULARITIES", "TTS_REFER_WAV_PATH", "TTS_PROMPT_TEXT", "TTS_PROMPT_LANGUAGE"];
const SPEECH_PRIVATE_KEYS = ["OPENAI_TTS_API_KEY", "OPENAI_ASR_API_KEY"];

const MODULES = [
  { id: "overview", title: "概览", desc: "当前 Agent、模型、运行时状态摘要。" },
  { id: "ai", title: "AI Provider", desc: "模型、接口地址与密钥配置。" },
  { id: "live2d", title: "Live2D", desc: "模型、位置、缩放与显示行为。" },
  { id: "speech", title: "语音", desc: "ASR/TTS 模式与参数配置。" },
  { id: "agent", title: "Agent", desc: "角色文件编辑、切换与创建。" },
  { id: "kb", title: "KB", desc: "知识库树、编辑、检索、索引管理。" },
  { id: "araya", title: "Araya", desc: "Araya 状态监控与触发。" },
  { id: "runtime", title: "Runtime", desc: "服务状态与运行时控制。" },
  { id: "triggers", title: "Triggers", desc: "计划任务列表与编辑。" },
  { id: "skills", title: "Skills", desc: "Skill 安装、启停、删除。" },
  { id: "plugins", title: "Plugins", desc: "插件启停、重载、配置。" },
  { id: "advanced", title: "高级", desc: "未归类字段与扩展配置。" },
];

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
  triggers: [],
  selectedTriggerId: "",
  skills: [],
  selectedSkillSlug: "",
  skillsAgent: "",
  skillDetail: null,
  plugins: [],
  selectedPluginId: "",
  pluginConfigDraft: {},
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

function cfgApi(method, path, payload, query) {
  return window.api.configRequest(method, path, payload, query);
}

function apiLocal(method, url, payload) {
  return window.api.configHttpRequest(method, url, payload);
}

function showBanner(type, text) {
  els.banner.className = `banner ${type}`;
  els.banner.textContent = text;
  els.banner.classList.remove("hidden");
}

function hideBanner() {
  els.banner.classList.add("hidden");
}

function setBusy(v) {
  els.saveBtn.disabled = v;
  els.applyBtn.disabled = v;
  els.reloadBtn.disabled = v;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function makeButton(text, onClick, className = "btn btn-ghost") {
  const btn = el("button", className, text);
  btn.type = "button";
  btn.addEventListener("click", onClick);
  return btn;
}

function clearRoot() {
  els.cardsRoot.innerHTML = "";
}

function addSection(title, bodyNodes, full = true) {
  const section = el("section", `card section ${full ? "full-span" : ""}`.trim());
  const t = el("h3", "section-title", title);
  section.append(t);
  for (const node of bodyNodes) {
    section.append(node);
  }
  els.cardsRoot.append(section);
}

function formatScalar(v) {
  if (v === null || v === undefined) return "-";
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function makeDataView(value, depth = 0) {
  if (value === null || value === undefined || typeof value !== "object") {
    return el("span", "mono", formatScalar(value));
  }

  if (Array.isArray(value)) {
    const wrap = el("div", "kv-group");
    if (!value.length) {
      wrap.append(el("div", "empty-state", "空数组"));
      return wrap;
    }
    for (const item of value) {
      const row = el("div", "kv-row");
      row.append(makeDataView(item, depth + 1));
      wrap.append(row);
    }
    return wrap;
  }

  const entries = Object.entries(value);
  const grid = el("div", "kv-grid");
  if (!entries.length) {
    grid.append(el("div", "empty-state", "空对象"));
    return grid;
  }
  for (const [k, v] of entries) {
    const item = el("div", "kv-item");
    item.append(el("div", "kv-key", k));
    if (v !== null && typeof v === "object") {
      const details = el("details", "kv-details");
      const summary = el("summary", "kv-summary", Array.isArray(v) ? `数组 (${v.length})` : "对象");
      details.append(summary, makeDataView(v, depth + 1));
      item.append(details);
    } else {
      item.append(el("div", "kv-value", formatScalar(v)));
    }
    grid.append(item);
  }
  return grid;
}

function ensureModalRoot() {
  let overlay = document.getElementById("cfgModalOverlay");
  if (overlay) return overlay;

  overlay = el("div", "cfg-modal-overlay hidden");
  overlay.id = "cfgModalOverlay";
  const dialog = el("div", "cfg-modal");
  const header = el("div", "cfg-modal-head");
  const title = el("h3", "cfg-modal-title", "弹窗");
  title.id = "cfgModalTitle";
  const closeBtn = makeButton("关闭", () => closeModal());
  closeBtn.className = "btn btn-ghost";
  header.append(title, closeBtn);
  const body = el("div", "cfg-modal-body");
  body.id = "cfgModalBody";
  dialog.append(header, body);
  overlay.append(dialog);
  overlay.addEventListener("click", (evt) => {
    if (evt.target === overlay) closeModal();
  });
  document.body.append(overlay);
  return overlay;
}

function openModal(title, bodyNodes) {
  const overlay = ensureModalRoot();
  const titleEl = document.getElementById("cfgModalTitle");
  const bodyEl = document.getElementById("cfgModalBody");
  titleEl.textContent = title;
  bodyEl.innerHTML = "";
  for (const n of bodyNodes) bodyEl.append(n);
  overlay.classList.remove("hidden");
}

function closeModal() {
  const overlay = document.getElementById("cfgModalOverlay");
  if (overlay) overlay.classList.add("hidden");
}

function makeInfoCard(title, rows) {
  const card = el("article", "card");
  card.append(el("h3", "card-title", title));
  const grid = el("div", "info-grid");
  for (const row of rows) {
    const item = el("div", "info-item");
    item.append(el("div", "info-key", String(row.label || "-")));
    item.append(el("div", "info-value", formatScalar(row.value)));
    grid.append(item);
  }
  card.append(grid);
  return card;
}

function makeTagListCard(title, tags) {
  const card = el("article", "card");
  card.append(el("h3", "card-title", title));
  const wrap = el("div", "tag-list");
  const arr = Array.isArray(tags) ? tags : [];
  if (!arr.length) {
    wrap.append(el("div", "empty-state", "无"));
  } else {
    for (const t of arr) wrap.append(el("span", "tag-chip", String(t)));
  }
  card.append(wrap);
  return card;
}

function makeSimpleTableCard(title, columns, rows) {
  const card = el("article", "card full-span");
  card.append(el("h3", "card-title", title));
  const table = el("table", "simple-table");
  const thead = el("thead", "");
  const htr = el("tr", "");
  for (const col of columns) htr.append(el("th", "", col));
  thead.append(htr);
  table.append(thead);
  const tbody = el("tbody", "");
  if (!rows.length) {
    const tr = el("tr", "");
    const td = el("td", "", "无数据");
    td.colSpan = columns.length;
    tr.append(td);
    tbody.append(tr);
  } else {
    for (const row of rows) {
      const tr = el("tr", "");
      for (const c of row) tr.append(el("td", "", formatScalar(c)));
      tbody.append(tr);
    }
  }
  table.append(tbody);
  card.append(table);
  return card;
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
    const dir = `d:/dev/faustbot/faust/backend/agents/${targetAgent}`;
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
  } else {
    const input = el("input", "input");
    input.type = "text";
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
    const pub = SPEECH_PUBLIC_KEYS.filter((k) => publicKeys.includes(k));
    const pri = SPEECH_PRIVATE_KEYS.filter((k) => privateKeys.includes(k));
    return {
      publicKeys: pub.filter((k) => {
        if (k.startsWith("OPENAI_TTS_") && modeTts !== "openai") return false;
        if (k.startsWith("OPENAI_ASR_") && modeAsr !== "openai") return false;
        return true;
      }),
      privateKeys: pri.filter((k) => {
        if (k.startsWith("OPENAI_TTS_") && modeTts !== "openai") return false;
        if (k.startsWith("OPENAI_ASR_") && modeAsr !== "openai") return false;
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
  for (const key of fields.publicKeys) {
    els.cardsRoot.append(makeFieldCard("public", key, state.config.public[key]));
  }
  for (const key of fields.privateKeys) {
    els.cardsRoot.append(makeFieldCard("private", key, state.config.private[key]));
  }

  if (moduleId === "speech") {
    const ttsCard = el("article", "card");
    ttsCard.append(el("h3", "card-title", "TTS 服务即时应用"));
    ttsCard.append(el("p", "card-help", "local TTS 模式下可把参考音频参数即时同步到 5000 端口服务。"));
    ttsCard.append(makeButton("应用参考音频到 TTS 服务", applyTtsReferToService, "btn btn-secondary"));
    els.cardsRoot.append(ttsCard);
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

function normalizeKbPath(raw) {
  const input = String(raw || "").replace(/\\/g, "/").trim();
  if (!input || input === "/") return "/";
  const withLeading = input.startsWith("/") ? input : `/${input}`;
  const compact = withLeading.replace(/\/{2,}/g, "/");
  return compact.endsWith("/") ? compact.slice(0, -1) : compact;
}

function kbParentPath(path) {
  const p = normalizeKbPath(path);
  if (p === "/") return "/";
  const idx = p.lastIndexOf("/");
  return idx <= 0 ? "/" : p.slice(0, idx);
}

function findKbNodeByPath(root, path) {
  if (!root || typeof root !== "object") return null;
  const target = normalizeKbPath(path);
  const stack = [root];
  while (stack.length) {
    const cur = stack.pop();
    const curPath = normalizeKbPath(cur.path || "/");
    if (curPath === target) return cur;
    for (const child of cur.children || []) stack.push(child);
  }
  return null;
}

function getKbChildren(root, dirPath) {
  const dirNode = findKbNodeByPath(root, dirPath);
  if (!dirNode || String(dirNode.type || "dir") === "file") return [];
  const rows = (dirNode.children || []).map((child) => ({
    path: normalizeKbPath(child.path || "/"),
    type: String(child.type || "dir"),
    name: String(child.name || child.path || "") || "/",
  }));
  rows.sort((a, b) => {
    if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  return rows;
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
  const area = el("textarea", "textarea code-area code-area-lg");
  area.value = String(content || "");
  const info = el("div", "card-help", `Skill: ${slug} | Agent: ${agentName || "-"}`);
  const bar = el("div", "toolbar");
  bar.append(
    makeButton("保存 SKILL.md", async () => {
      const useAgent = String(agentName || state.skillsAgent || "").trim();
      await cfgApi("PUT", `/faust/admin/skills/${encodeURIComponent(slug)}/skill-md`, {
        agent_name: useAgent || null,
        content: area.value,
      });
      if (state.skillDetail && String(state.skillDetail.slug || "") === String(slug)) {
        state.skillDetail.skill_md = area.value;
      }
      showBanner("success", `SKILL.md 已保存: ${slug}`);
      closeModal();
      await ensureModuleData("skills");
      renderModule();
    }, "btn btn-primary"),
    makeButton("关闭", closeModal)
  );
  openModal(`编辑 SKILL.md - ${slug}`, [info, area, bar]);
}

async function ensureModuleData(moduleId) {
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
      const dir = `d:/dev/faustbot/faust/backend/agents/${state.selectedAgent}`;
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
    const p = window.prompt("请输入文件路径，例如 reactor/core/control.md", defaultPath);
    if (!p) return;
    const target = normalizeKbPath(p.trim());
    await openKbEditorModal(target, "", {
      path: target,
      declared_by: "config-center",
      indexed: true,
      tags: [],
    });
  };

  const doKbNewFolder = async () => {
    const defaultPath = `${state.kbCurrentDir === "/" ? "" : state.kbCurrentDir.slice(1) + "/"}new-folder`;
    const p = window.prompt("请输入文件夹路径，例如 reactor/core", defaultPath);
    if (!p) return;
    await cfgApi("POST", "/faust/kb/mkdir", { path: p.trim() });
    state.kbCurrentDir = normalizeKbPath(p.trim());
    await ensureModuleData("kb");
    renderModule();
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
    const close = () => {
      closeKbContextMenu();
      window.removeEventListener("click", close, true);
      window.removeEventListener("contextmenu", close, true);
      window.removeEventListener("keydown", onKey, true);
    };
    const onKey = (evt) => {
      if (evt.key === "Escape") close();
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
  const triggerSlider = createArayaTriggerSlider(async () => {
    await cfgApi("POST", "/faust/araya/trigger", { reason: "manual_from_configer" });
    await ensureModuleData("araya");
    showBanner("success", "Araya 已触发。" );
    renderModule();
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
      await ensureModuleData("araya");
      showBanner("success", "Araya 设置已保存。");
      renderModule();
    }, "btn btn-primary"),
    makeButton("刷新状态", async () => {
      await ensureModuleData("araya");
      renderModule();
    }),
    triggerSlider
  );
  addSection("Araya 控制", [bar]);

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
      const basePath = `d:/dev/faustbot/faust/backend/agents/${agentName}/skill.d/${state.selectedSkillSlug || ""}`;
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
  const overview = {
    current_agent: state.runtime.current_agent || state.config.public.AGENT_NAME || "-",
    chat_model: state.config.public.CHAT_MODEL || "-",
    chat_provider: state.config.public.CHAT_PROVIDER || "-",
    live2d_model: state.config.public.LIVE2D_MODEL_PATH || "-",
    tts_mode: state.config.public.TTS_MODE || "-",
    asr_mode: state.config.public.ASR_MODE || "-",
    services: (state.services || []).length,
    triggers: (state.triggers || []).length,
    skills: (state.skills || []).length,
    plugins: (state.plugins || []).length,
  };

  addSection("运行概览", [makeDataView(overview)]);
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