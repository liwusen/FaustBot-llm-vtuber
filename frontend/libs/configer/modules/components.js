// 组件管理模块 — Phase E
// GPU 信息、FunASR、TTS、Minecraft 桥状态与启停

function renderComponentsModule() {
  const c = getModuleContainer("components");
  c.innerHTML = "";
  if (!state.componentStatus) return;
  renderGpuCard(c);
  renderAsrCards(c);
  renderTtsCard(c);
  renderMinecraftCard(c);
  renderProgressArea(c);
}

// ── 工具函数 ──

function _serviceStatusText(svc) {
  if (svc?.status === "starting") return "启动中";
  if (svc?.status === "stopping") return "关闭中";
  if (svc?.is_running) return "运行中";
  return "已停止";
}

function _serviceTone(svc) {
  if (svc?.status === "starting") return "is-warn";
  if (svc?.status === "stopping") return "is-busy";
  if (svc?.is_running) return "is-ok";
  return "is-stop";
}

function _cardTitle(icon, tone, text) {
  const dot = tone ? `<span class="cfg-dot ${tone}"></span>` : "";
  return `${dot}${FaustIcons.svg(icon, { size: 15 })}<span>${text}</span>`;
}

function _btn(label, onClick, opts) {
  const b = document.createElement("button");
  b.type = "button";
  b.onclick = onClick;
  b.className = `btn ${opts?.primary ? "btn-primary" : opts?.danger ? "btn-danger" : "btn-ghost"}`;
  if (opts?.icon) b.insertAdjacentHTML("afterbegin", FaustIcons.svg(opts.icon, { size: 13 }));
  b.append(document.createTextNode(label));
  b.disabled = !!opts?.disabled;
  return b;
}

function _card(titleHtml) {
  const card = el("div", "card");
  if (titleHtml) {
    const title = el("div", "card-title-line");
    title.innerHTML = titleHtml;
    card.append(title);
  }
  return card;
}

function _row(label, value) {
  const row = el("div", "service-row");
  row.append(el("span", "k", label), el("span", "v", value));
  return row;
}

function _select(options, selected) {
  const sel = document.createElement("select");
  sel.className = "select select-compact";
  options.forEach(o => {
    const opt = document.createElement("option");
    opt.value = o.value;
    opt.textContent = o.label;
    if (o.value === selected) opt.selected = true;
    sel.append(opt);
  });
  return sel;
}

function _toggle(label, checked, onChange) {
  const wrapper = document.createElement("label");
  wrapper.className = "check-inline";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = !!checked;
  cb.onchange = () => onChange(cb.checked);
  wrapper.append(cb, document.createTextNode(label));
  return wrapper;
}

function _hint(text) {
  return el("div", "card-help", text);
}
// admin API 使用实际 service key，与组件状态显示 key 可能不同
const _SERVICE_KEY_MAP = { minecraft: "mc_operator" };
function _adminKey(displayKey) {
  return _SERVICE_KEY_MAP[displayKey] || displayKey;
}


// ── 服务状态轮询 ──

let _servicePollTimers = {};

function _startServicePolling(serviceKey) {
  _clearServicePolling(serviceKey);
  const startTime = Date.now();
  const MAX_WAIT = 40000;
  const INTERVAL = 3000;

  const intervalId = setInterval(async () => {
    await refreshComponentStatus();
    const svc = state.componentStatus?.services?.[serviceKey];
    const elapsed = Date.now() - startTime;
    if (elapsed >= MAX_WAIT || !svc || (svc.status !== "starting" && svc.status !== "stopping")) {
      _clearServicePolling(serviceKey);
    }
  }, INTERVAL);

  _servicePollTimers[serviceKey] = intervalId;
  // 立即刷新一次
  refreshComponentStatus();
}

function _clearServicePolling(serviceKey) {
  if (_servicePollTimers[serviceKey] != null) {
    clearInterval(_servicePollTimers[serviceKey]);
    delete _servicePollTimers[serviceKey];
  }
}

// ── 服务日志 ──

function _showServiceLog(serviceKey) {
  const overlay = el("div", "cfg-modal-overlay");

  const modal = el("div", "cfg-modal");
  modal.style.width = "min(820px, 80vw)";
  modal.style.minWidth = "400px";

  const header = el("div", "cfg-modal-head");
  header.innerHTML = `<strong class="cfg-modal-title">${serviceKey.toUpperCase()} 日志</strong>`;

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "btn btn-ghost modal-close";
  closeBtn.setAttribute("aria-label", "关闭日志");
  closeBtn.innerHTML = FaustIcons.svg("close", { size: 15 });
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);

  const textarea = document.createElement("textarea");
  textarea.readOnly = true;
  textarea.className = "textarea service-log-area";
  textarea.value = "加载中...";

  modal.append(header, textarea);
  overlay.append(modal);
  document.body.append(overlay);

  // 异步获取日志
  (async () => {
    try {
      const res = await cfgApi("GET", `/faust/admin/services/${_adminKey(serviceKey)}?include_log=true`);
      const log = res?.item?.log_tail;
      if (Array.isArray(log)) {
        textarea.value = log.join("\n");
      } else if (typeof log === "string") {
        textarea.value = log;
      } else {
        textarea.value = "（无日志内容）";
      }
    } catch (e) {
      textarea.value = "获取日志失败: " + (e.message || "未知错误");
    }
  })();

  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.remove(); });
}

// ── GPU 信息 ──

function renderGpuCard(container) {
  const data = state.componentStatus?.gpu;
  if (!data) return;

  const card = _card(_cardTitle("server", "", "GPU 信息"));
  if (!data.has_nvidia || data.gpus.length === 0) {
    card.append(_row("状态", "未检测到 NVIDIA GPU"));
  } else {
    data.gpus.forEach((gpu, i) => {
      card.append(_row(`GPU ${i + 1}`, gpu.name || "未知"));
      if (gpu.cuda_version) card.append(_row("CUDA 版本", gpu.cuda_version));
      if (gpu.driver_version) card.append(_row("Driver 版本", gpu.driver_version));
    });
  }
  container.append(card);
}

// ── ASR 卡片（服务 + FunASR/Torch 依赖，分开标注）──

function renderAsrCards(container) {
  renderAsrServiceCard(container);
  renderFunasrDepsCard(container);
}

// 本地 ASR 服务：Whisper 与 FunASR 两种模式共用
function renderAsrServiceCard(container) {
  const svc = state.componentStatus?.services?.asr;
  const svcStatus = _serviceStatusText(svc);
  const svcPort = svc?.port ? `端口 ${svc.port}` : "";
  const svcTone = _serviceTone(svc);

  const card = _card(_cardTitle("mic", svcTone, "ASR 服务"));
  card.append(_hint("本地语音识别服务，Whisper 模式与 FunASR 模式共用。无论选择哪种 ASR 模式，都需要先启动此服务；切换模式无需重装，只需重启服务。"));
  card.append(_row("服务", `${svcStatus} ${svcPort}`));

  const actions = document.createElement("div");
  actions.className = "card-actions";
  actions.append(_btn("启动服务", () => startService("asr"), { primary: true, icon: "play" }));
  actions.append(_btn("停止服务", () => stopService("asr"), { danger: true, icon: "stop" }));
  actions.append(_btn("日志", () => _showServiceLog("asr"), { icon: "doc" }));
  card.append(actions);
  container.append(card);
}

// FunASR / Torch 依赖：仅 FunASR 模式需要下载安装
function renderFunasrDepsCard(container) {
  const data = state.componentStatus?.components?.funasr;
  if (!data) return;

  const installed = data.installed;
  const statusText = installed ? `已安装 (funasr ${data.version || ""})` : "未安装";
  const torchInfo = data.torch_version ? `${data.torch_version} (${data.torch_variant || "?"})` : "未检测";

  const card = _card(_cardTitle("package", installed ? "is-ok" : "is-stop", "FunASR / Torch 依赖"));
  card.append(_hint("仅「FunASR 模式」需要：下载安装 PyTorch 与 FunASR 模型（体积较大）。若使用「Whisper 模式」则无需安装此依赖，只需启动上方的 ASR 服务即可。"));
  card.append(_row("安装状态", statusText));
  card.append(_row("PyTorch 环境", torchInfo));

  const actions = document.createElement("div");
  actions.className = "card-actions";

  const variantSelect = _select(
    [{ value: "cu128", label: "cu128 (CUDA 12.8)" },
     { value: "cu121", label: "cu121 (CUDA 12.1)" },
     { value: "cu130", label: "cu130 (CUDA 13.0)" },
     { value: "cpu", label: "cpu (无 GPU)" }],
    data.torch_variant || "cpu"
  );
  variantSelect.id = "funasrVariantSelect";
  actions.append(variantSelect);

  const mirrorToggle = _toggle("使用阿里云镜像", false, () => {});
  mirrorToggle.querySelector("input").id = "funasrMirrorCheck";
  actions.append(mirrorToggle);

  actions.append(_btn("安装/重装", () => startComponentInstall("funasr"), { primary: true, icon: "refresh" }));

  card.append(actions);
  container.append(card);
}

// ── TTS 卡片 ──

function renderTtsCard(container) {
  const data = state.componentStatus?.components?.tts;
  const svc = state.componentStatus?.services?.tts;
  if (!data) return;

  const installed = data.installed;
  const statusText = installed ? `已安装 (${data.path || ""})` : "未安装";
  const variantText = data.variant || "standard";
  const svcStatus = _serviceStatusText(svc);
  const svcPort = svc?.port ? `端口 ${svc.port}` : "";

  const card = _card(_cardTitle("speaker", installed ? "is-ok" : "is-stop", "TTS"));
  card.append(_row("状态", statusText));
  card.append(_row("TTS GPU 类型", variantText));
  card.append(_row("服务", `${svcStatus} ${svcPort}`));

  const actions = document.createElement("div");
  actions.className = "card-actions";

  const ttsVariant = _select(
    [{ value: "standard", label: "Standard" },
     { value: "nvidia50", label: "NVIDIA 50 系列" }],
    data.variant || "standard"
  );
  ttsVariant.id = "ttsVariantSelect";
  actions.append(ttsVariant);

  actions.append(_btn("重新下载", () => startComponentInstall("tts"), { primary: true, icon: "refresh" }));
  actions.append(_btn("启动服务", () => startService("tts"), { icon: "play" }));
  actions.append(_btn("停止服务", () => stopService("tts"), { danger: true, icon: "stop" }));
  actions.append(_btn("日志", () => _showServiceLog("tts"), { icon: "doc" }));

  card.append(actions);
  container.append(card);
}

// ── Minecraft 桥卡片 ──

function renderMinecraftCard(container) {
  const data = state.componentStatus?.components?.minecraft_bridge;
  const svc = state.componentStatus?.services?.minecraft;
  if (!data) return;

  const svcText = _serviceStatusText(svc);
  const portText = svc?.port ? `端口 ${svc.port}` : "";

  const card = _card(_cardTitle("gamepad", _serviceTone(svc), "Minecraft 操作桥"));
  card.append(_row("状态", `${svcText} ${portText}`));

  const actions = document.createElement("div");
  actions.className = "card-actions";

  actions.append(_btn("启动服务", () => startService("minecraft"), { icon: "play" }));
  actions.append(_btn("停止服务", () => stopService("minecraft"), { danger: true, icon: "stop" }));
  actions.append(_btn("日志", () => _showServiceLog("minecraft"), { icon: "doc" }));

  const note = el("div", "service-note", "后端启动时自动运行，无需下载");
  card.append(actions, note);
  container.append(card);
}

// ── 安装进度区 ──

let _installTaskId = null;
let _installEventSource = null;

function _setCancelButtonVisible(visible) {
  const btn = document.getElementById("componentCancelBtn");
  if (!btn) return;
  btn.style.display = visible ? "inline-block" : "none";
  btn.disabled = false;
}

function renderProgressArea(container) {
  const area = document.createElement("div");
  area.id = "componentProgressArea";
  area.style.cssText = "display:none;margin-top:8px";

  const progressBar = document.createElement("div");
  progressBar.className = "progress-track";
  const progressFill = document.createElement("div");
  progressFill.id = "componentProgressFill";
  progressFill.className = "progress-fill";
  progressBar.append(progressFill);

  const label = document.createElement("div");
  label.id = "componentProgressLabel";
  label.className = "progress-note";
  label.textContent = "";

  const cancelBtn = document.createElement("button");
  cancelBtn.id = "componentCancelBtn";
  cancelBtn.className = "btn btn-danger";
  cancelBtn.textContent = "终止";
  cancelBtn.style.display = "none";
  cancelBtn.style.marginTop = "8px";
  cancelBtn.onclick = () => cancelComponentInstall();

  const logArea = document.createElement("div");
  logArea.id = "componentLogArea";
  logArea.className = "install-log";

  area.append(progressBar, label, cancelBtn, logArea);
  container.append(area);
}

function showProgress(stage, percent, message) {
  const area = document.getElementById("componentProgressArea");
  const fill = document.getElementById("componentProgressFill");
  const label = document.getElementById("componentProgressLabel");
  const logArea = document.getElementById("componentLogArea");
  if (!area || !fill || !label) return;

  area.style.display = "";
  if (typeof percent === "number") {
    fill.style.width = percent + "%";
  }
  if (message) {
    label.textContent = message;
  }
  if (stage === "pip_output" && logArea) {
    logArea.style.display = "block";
    logArea.append(message + "\n");
    logArea.scrollTop = logArea.scrollHeight;
  }
}

function connectSse(taskId) {
  _installTaskId = taskId;
  _setCancelButtonVisible(true);
  const base = window.api?.backendBaseUrl || "http://127.0.0.1:13900";
  if (_installEventSource) _installEventSource.close();
  _installEventSource = new EventSource(`${base}/faust/components/tasks/${taskId}/events`);

  _installEventSource.addEventListener("progress", (ev) => {
    try {
      const d = JSON.parse(ev.data);
      showProgress(d.stage, d.progress_percent, d.log_lines?.[d.log_lines.length - 1] || d.stage);
    } catch (e) { /* ignore */ }
  });

  _installEventSource.addEventListener("complete", (ev) => {
    _installEventSource?.close();
    _installEventSource = null;
    _installTaskId = null;
    _setCancelButtonVisible(false);
    showProgress("complete", 100, "安装完成");
    // 刷新组件状态
    refreshComponentStatus();
  });

  _installEventSource.addEventListener("cancelled", (ev) => {
    _installEventSource?.close();
    _installEventSource = null;
    _installTaskId = null;
    _setCancelButtonVisible(false);
    showProgress("cancelled", null, "任务已终止");
    refreshComponentStatus();
  });

  _installEventSource.addEventListener("error", (ev) => {
    _installEventSource?.close();
    _installEventSource = null;
    _installTaskId = null;
    _setCancelButtonVisible(false);
    try {
      const d = JSON.parse(ev.data);
      showProgress("error", null, "错误: " + (d.error || "未知"));
    } catch (e) {
      showProgress("error", null, "连接错误");
    }
  });
}

// ── API 操作 ──

async function startComponentInstall(component) {
  const body = { component };
  if (component === "funasr") {
    const select = document.getElementById("funasrVariantSelect");
    if (select) body.torch_variant = select.value;
    const mirror = document.getElementById("funasrMirrorCheck");
    if (mirror) body.use_aliyun_mirror = mirror.checked;
  }
  if (component === "tts") {
    const select = document.getElementById("ttsVariantSelect");
    if (select) body.tts_variant = select.value;
  }

  try {
    const res = await cfgApi("POST", "/faust/components/install", body);
    if (res.task_id) {
      showProgress("preparing", 0, `开始安装 ${component}...`);
      connectSse(res.task_id);
    }
  } catch (e) {
    showProgress("error", null, "启动安装失败: " + e.message);
  }
}

async function cancelComponentInstall() {
  if (!_installTaskId) return;
  const btn = document.getElementById("componentCancelBtn");
  if (btn) btn.disabled = true;
  try {
    await cfgApi("POST", `/faust/components/tasks/${_installTaskId}/cancel`, {});
    showProgress("cancel_requested", null, "正在终止任务...");
  } catch (e) {
    showProgress("error", null, "终止失败: " + e.message);
    if (btn) btn.disabled = false;
  }
}

async function startService(serviceKey) {
  try {
    await cfgApi("POST", `/faust/admin/services/${_adminKey(serviceKey)}/start`);
    _startServicePolling(serviceKey);
  } catch (e) {
    console.warn("启动服务失败", e);
  }
}

async function stopService(serviceKey) {
  try {
    await cfgApi("POST", `/faust/admin/services/${_adminKey(serviceKey)}/stop`);
    _startServicePolling(serviceKey);
  } catch (e) {
    console.warn("停止服务失败", e);
  }
}


async function refreshComponentStatus() {
  try {
    const data = await cfgApi("GET", "/faust/components/status");
    state.componentStatus = data;
    // 重新渲染
    const c = getModuleContainer("components");
    c.innerHTML = "";
    renderGpuCard(c);
    renderAsrCards(c);
    renderTtsCard(c);
    renderMinecraftCard(c);
    renderProgressArea(c);
  } catch (e) {
    console.warn("刷新组件状态失败", e);
  }
}
