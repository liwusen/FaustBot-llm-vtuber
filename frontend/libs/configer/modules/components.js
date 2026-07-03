// 组件管理模块 — Phase E
// GPU 信息、FunASR、TTS、Minecraft 桥状态与启停

function renderComponentsModule() {
  const c = getModuleContainer("components");
  c.innerHTML = "";
  if (!state.componentStatus) return;
  renderGpuCard(c);
  renderFunasrCard(c);
  renderTtsCard(c);
  renderMinecraftCard(c);
  renderProgressArea(c);
}

// ── 工具函数 ──

function _statusBadge(isRunning) {
  const span = document.createElement("span");
  span.style.cssText = `display:inline-block;width:10px;height:10px;border-radius:50%;
    background:${isRunning ? "#4caf50" : "#f44336"};margin-right:6px;vertical-align:middle`;
  return span;
}

function _btn(label, onClick, opts) {
  const b = document.createElement("button");
  b.textContent = label;
  b.onclick = onClick;
  b.style.cssText = `margin:4px;padding:6px 14px;border:1px solid var(--border);border-radius:6px;
    background:var(--bg1);color:var(--fg);cursor:pointer;font-size:13px;
    ${opts?.danger ? "border-color:#f44336;color:#f44336;" : ""}
    ${opts?.primary ? "border-color:var(--accent);background:var(--accent);color:#fff;" : ""}
    ${opts?.disabled ? "opacity:0.5;cursor:not-allowed;" : ""}`;
  b.disabled = !!opts?.disabled;
  return b;
}

function _card(titleHtml) {
  const card = document.createElement("div");
  card.style.cssText = "background:var(--bg1);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:12px";
  if (titleHtml) {
    const title = document.createElement("div");
    title.style.cssText = "font-weight:600;font-size:14px;margin-bottom:10px";
    title.innerHTML = titleHtml;
    card.append(title);
  }
  return card;
}

function _row(label, value) {
  const row = document.createElement("div");
  row.style.cssText = "display:flex;justify-content:space-between;padding:4px 0;font-size:13px";
  const lbl = document.createElement("span");
  lbl.style.cssText = "color:var(--muted)";
  lbl.textContent = label;
  const val = document.createElement("span");
  val.textContent = value;
  row.append(lbl, val);
  return row;
}

function _select(options, selected) {
  const sel = document.createElement("select");
  sel.style.cssText = "padding:4px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg2);color:var(--fg);font-size:13px";
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
  wrapper.style.cssText = "display:inline-flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;margin-right:12px";
  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = !!checked;
  cb.onchange = () => onChange(cb.checked);
  wrapper.append(cb, label);
  return wrapper;
}

function _serviceStatusText(svc) {
  if (svc?.status === "starting") return "🟡 启动中";
  if (svc?.status === "stopping") return "🟠 关闭中";
  if (svc?.is_running) return "🟢 运行中";
  return "🔴 已停止";
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
  const overlay = document.createElement("div");
  overlay.style.cssText = "position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.5);z-index:1000;display:flex;align-items:center;justify-content:center";

  const modal = document.createElement("div");
  modal.style.cssText = "background:var(--bg1);border:1px solid var(--border);border-radius:8px;padding:20px;max-width:80vw;max-height:80vh;min-width:400px;display:flex;flex-direction:column";

  const header = document.createElement("div");
  header.style.cssText = "display:flex;justify-content:space-between;align-items:center;margin-bottom:12px";
  header.innerHTML = `<strong>${serviceKey.toUpperCase()} 日志</strong>`;

  const closeBtn = document.createElement("button");
  closeBtn.textContent = "✕";
  closeBtn.style.cssText = "border:none;background:none;cursor:pointer;font-size:16px;color:var(--fg);padding:4px 8px";
  closeBtn.onclick = () => overlay.remove();
  header.append(closeBtn);

  const textarea = document.createElement("textarea");
  textarea.readOnly = true;
  textarea.style.cssText = "width:100%;height:400px;background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:8px;font-size:12px;font-family:monospace;color:var(--fg);resize:vertical;box-sizing:border-box;white-space:pre;overflow:auto;tab-size:2";
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

  const card = _card("🖥 GPU 信息");
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

// ── FunASR 卡片 ──

function renderFunasrCard(container) {
  const data = state.componentStatus?.components?.funasr;
  const svc = state.componentStatus?.services?.asr;
  if (!data) return;

  const installed = data.installed;
  const statusIcon = installed ? "✅" : "🔴";
  const statusText = installed ? `已安装 (funasr ${data.version || ""})` : "未安装";
  const torchInfo = data.torch_version ? `${data.torch_version} (${data.torch_variant || "?"})` : "未检测";
  const svcStatus = _serviceStatusText(svc);
  const svcPort = svc?.port ? `端口 ${svc.port}` : "";

  const card = _card(`${statusIcon} FunASR (ASR)`);
  card.append(_row("状态", statusText));
  card.append(_row("PyTorch", torchInfo));
  card.append(_row("服务", `${svcStatus} ${svcPort}`));

  // 操作区
  const actions = document.createElement("div");
  actions.style.cssText = "margin-top:10px;display:flex;flex-wrap:wrap;align-items:center;gap:6px";

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

  actions.append(_btn("🔄 安装/重装", () => startComponentInstall("funasr"), { primary: true }));
  actions.append(_btn("▶ 启动服务", () => startService("asr")));
  actions.append(_btn("⏹ 停止服务", () => stopService("asr"), { danger: true }));
  actions.append(_btn("📋 日志", () => _showServiceLog("asr")));

  card.append(actions);
  container.append(card);
}

// ── TTS 卡片 ──

function renderTtsCard(container) {
  const data = state.componentStatus?.components?.tts;
  const svc = state.componentStatus?.services?.tts;
  if (!data) return;

  const installed = data.installed;
  const statusIcon = installed ? "✅" : "🔴";
  const statusText = installed ? `已安装 (${data.path || ""})` : "未安装";
  const variantText = data.variant || "standard";
  const svcStatus = _serviceStatusText(svc);
  const svcPort = svc?.port ? `端口 ${svc.port}` : "";

  const card = _card(`${statusIcon} TTS`);
  card.append(_row("状态", statusText));
  card.append(_row("Variant", variantText));
  card.append(_row("服务", `${svcStatus} ${svcPort}`));

  const actions = document.createElement("div");
  actions.style.cssText = "margin-top:10px;display:flex;flex-wrap:wrap;align-items:center;gap:6px";

  const ttsVariant = _select(
    [{ value: "standard", label: "Standard" },
     { value: "nvidia50", label: "NVIDIA 50 系列" }],
    data.variant || "standard"
  );
  ttsVariant.id = "ttsVariantSelect";
  actions.append(ttsVariant);

  actions.append(_btn("🔄 重新下载", () => startComponentInstall("tts"), { primary: true }));
  actions.append(_btn("▶ 启动服务", () => startService("tts")));
  actions.append(_btn("⏹ 停止服务", () => stopService("tts"), { danger: true }));
  actions.append(_btn("📋 日志", () => _showServiceLog("tts")));

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
  const enabled = data.enabled || false;

  const card = _card("🎮 Minecraft 操作桥");
  card.append(_row("状态", `${svcText} ${portText}`));

  const actions = document.createElement("div");
  actions.style.cssText = "margin-top:10px;display:flex;flex-wrap:wrap;align-items:center;gap:6px";

  actions.append(_toggle("启用 Minecraft 桥", enabled, (val) => toggleMcBridge(val)));

  actions.append(_btn("▶ 启动服务", () => startService("minecraft")));
  actions.append(_btn("⏹ 停止服务", () => stopService("minecraft"), { danger: true }));
  actions.append(_btn("📋 日志", () => _showServiceLog("minecraft")));

  const note = document.createElement("div");
  note.style.cssText = "font-size:11px;color:var(--muted);margin-top:6px";
  note.textContent = "无需下载，启用后自动启停";
  card.append(actions, note);
  container.append(card);
}

// ── 安装进度区 ──

let _installTaskId = null;
let _installEventSource = null;

function renderProgressArea(container) {
  const area = document.createElement("div");
  area.id = "componentProgressArea";
  area.style.cssText = "display:none;margin-top:8px";

  const progressBar = document.createElement("div");
  progressBar.style.cssText = "height:8px;background:var(--bg2);border-radius:4px;overflow:hidden";
  const progressFill = document.createElement("div");
  progressFill.id = "componentProgressFill";
  progressFill.style.cssText = "height:100%;width:0%;background:var(--accent);transition:width .3s";
  progressBar.append(progressFill);

  const label = document.createElement("div");
  label.id = "componentProgressLabel";
  label.style.cssText = "font-size:11px;color:var(--muted);margin-top:4px";
  label.textContent = "";

  const logArea = document.createElement("div");
  logArea.id = "componentLogArea";
  logArea.style.cssText = "margin-top:8px;max-height:200px;overflow-y:auto;background:var(--bg2);border-radius:4px;padding:8px;font-size:12px;font-family:monospace;white-space:pre-wrap;display:none";

  area.append(progressBar, label, logArea);
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
    logArea.style.display = "";
    logArea.append(message + "\n");
    logArea.scrollTop = logArea.scrollHeight;
  }
}

function connectSse(taskId) {
  _installTaskId = taskId;
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
    showProgress("complete", 100, "安装完成");
    // 刷新组件状态
    refreshComponentStatus();
  });

  _installEventSource.addEventListener("error", (ev) => {
    _installEventSource?.close();
    _installEventSource = null;
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


async function toggleMcBridge(enabled) {
  try {
    // 保存配置
    const config = await cfgApi("GET", "/faust/admin/config");
    const publicCfg = config?.public || {};
    publicCfg.MC_BRIDGE_ENABLED = enabled;
    await cfgApi("POST", "/faust/admin/config", { public: publicCfg });
    // 重载配置触发服务启停
    await cfgApi("POST", "/faust/admin/config/reload", {});
    await refreshComponentStatus();
  } catch (e) {
    console.warn("切换 Minecraft 桥状态失败", e);
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
    renderFunasrCard(c);
    renderTtsCard(c);
    renderMinecraftCard(c);
    renderProgressArea(c);
  } catch (e) {
    console.warn("刷新组件状态失败", e);
  }
}
