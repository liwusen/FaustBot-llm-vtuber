function cfgApi(method, path, payload, query) {
  if (window.api && typeof window.api.configRequest === "function") return window.api.configRequest(method, path, payload, query);
  return fetch("http://127.0.0.1:13900" + path, {
    method: method,
    headers: { "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : undefined,
  }).then(r => r.json());
}

const els = {
  liveStatusBadge: document.getElementById("liveStatusBadge"),
  toggleLiveBtn: document.getElementById("toggleLiveBtn"),
  refreshBtn: document.getElementById("refreshBtn"),
  danmakuCount: document.getElementById("danmakuCount"),
  roomIdInput: document.getElementById("roomIdInput"),
  sessdataInput: document.getElementById("sessdataInput"),
  bliveEnabled: document.getElementById("bliveEnabled"),
  saveBliveSettingsBtn: document.getElementById("saveBliveSettingsBtn"),
  bliveStatusText: document.getElementById("bliveStatusText"),
  danmakuBlacklistInput: document.getElementById("danmakuBlacklistInput"),
  ttsBlacklistInput: document.getElementById("ttsBlacklistInput"),
  saveBlacklistBtn: document.getElementById("saveBlacklistBtn"),
  danmakuFeed: document.getElementById("danmakuFeed"),
  triggerList: document.getElementById("triggerList"),
  triggerCount: document.getElementById("triggerCount"),
};

let state = {
  liveMode: false,
  blive: { started: false, room_id: 0, enabled: false, has_sessdata: false },
  danmakuBlacklist: [],
  ttsBlacklist: [],
  triggers: [],
  danmakuEntries: [],
};

let pollTimer = null;

async function loadStatus() {
  try {
    const data = await cfgApi("GET", "/faust/live/status");
    state.liveMode = Boolean(data.live_mode);
    state.blive = data.blive || state.blive;
    state.danmakuBlacklist = data.danmaku_blacklist || [];
    state.ttsBlacklist = data.tts_blacklist || [];
    renderStatus();
  } catch (e) {
    console.warn("加载直播状态失败", e);
  }
}

async function loadTriggers() {
  try {
    const data = await cfgApi("GET", "/faust/live/triggers");
    state.triggers = data.triggers || [];
    renderTriggers();
  } catch (e) {
    console.warn("加载 Trigger 失败", e);
  }
}

function renderStatus() {
  els.liveStatusBadge.textContent = state.liveMode ? "直播中" : "未开启";
  els.liveStatusBadge.className = `badge ${state.liveMode ? "badge-on" : "badge-off"}`;
  els.toggleLiveBtn.textContent = state.liveMode ? "关闭直播模式" : "开启直播模式";
  els.toggleLiveBtn.className = state.liveMode ? "btn btn-secondary" : "btn btn-primary";
  els.bliveStatusText.textContent = state.blive.started ? `已连接 (room ${state.blive.room_id})` : "未连接";
  els.roomIdInput.value = state.blive.room_id || "";
  els.bliveEnabled.checked = Boolean(state.blive.enabled);
  if (!state.blive.has_sessdata) {
    els.sessdataInput.placeholder = "未设置 SESSDATA（用户名会匿名）";
  }
  els.danmakuBlacklistInput.value = (state.danmakuBlacklist || []).join("\n");
  els.ttsBlacklistInput.value = (state.ttsBlacklist || []).join("\n");
  els.danmakuCount.textContent = `弹幕: ${state.danmakuEntries.length}`;
}

function renderTriggers() {
  const list = els.triggerList;
  const count = state.triggers.length;
  els.triggerCount.textContent = `(${count})`;
  if (count === 0) {
    list.innerHTML = '<div style="color:var(--muted);font-size:12px;">暂无弹幕 Trigger</div>';
    return;
  }
  list.innerHTML = "";
  for (const t of state.triggers) {
    const payload = t.payload || {};
    const uname = payload.uname || "?";
    const msg = (payload.msg || "").slice(0, 60);
    const item = document.createElement("div");
    item.className = "trigger-item";
    item.innerHTML = `<span class="uname">${escapeHtml(uname)}</span><span class="msg">${escapeHtml(msg)}</span>`;
    const delBtn = document.createElement("span");
    delBtn.className = "del-btn";
    delBtn.textContent = "×";
    delBtn.title = "删除此 Trigger";
    const uid = String(payload.uid || "");
    delBtn.addEventListener("click", async () => {
      try {
        await cfgApi("DELETE", `/faust/live/triggers/${encodeURIComponent(uid)}`);
        await loadTriggers();
      } catch (e) {
        console.warn("删除 Trigger 失败", e);
      }
    });
    item.append(delBtn);
    list.append(item);
  }
}

function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = String(s);
  return d.innerHTML;
}

function addDanmakuEntry(uname, msg) {
  state.danmakuEntries.push({ uname, msg, ts: Date.now() });
  if (state.danmakuEntries.length > 200) {
    state.danmakuEntries = state.danmakuEntries.slice(-200);
  }
  renderDanmakuFeed();
}

function renderDanmakuFeed() {
  const feed = els.danmakuFeed;
  if (state.danmakuEntries.length === 0) {
    feed.innerHTML = '<div style="color:var(--muted);font-size:13px;">等待直播开始...</div>';
    return;
  }
  feed.innerHTML = "";
  const entries = state.danmakuEntries.slice(-50);
  for (const e of entries) {
    const row = document.createElement("div");
    row.className = "entry";
    row.innerHTML = `<span class="uname">${escapeHtml(e.uname)}</span><span class="msg">${escapeHtml(e.msg)}</span>`;
    feed.append(row);
  }
  feed.scrollTop = feed.scrollHeight;
}

async function toggleLive() {
  try {
    const endpoint = state.liveMode ? "/faust/live/stop" : "/faust/live/start";
    const result = await cfgApi("POST", endpoint);
    if (result.status === "ok" || result.status === "already_in_live_mode" || result.status === "not_in_live_mode") {
      await loadStatus();
    } else {
      alert("切换直播模式失败: " + (result.error || "未知错误"));
    }
  } catch (e) {
    alert("切换直播模式出错: " + e);
  }
}

async function saveBliveSettings() {
  const room_id = parseInt(els.roomIdInput.value) || 0;
  const sessdata = els.sessdataInput.value;
  const enabled = els.bliveEnabled.checked;
  try {
    const result = await cfgApi("POST", "/faust/live/blive/settings", { room_id, sessdata, enabled });
    if (result.status === "ok") {
      els.sessdataInput.value = "";
      await loadStatus();
    }
  } catch (e) {
    alert("保存 B站设置失败: " + e);
  }
}

async function saveBlacklist() {
  const dWords = els.danmakuBlacklistInput.value.split("\n").map(s => s.trim()).filter(Boolean);
  const tWords = els.ttsBlacklistInput.value.split("\n").map(s => s.trim()).filter(Boolean);
  try {
    await cfgApi("POST", "/faust/live/blacklist/danmaku", { words: dWords });
    await cfgApi("POST", "/faust/live/blacklist/tts", { words: tWords });
    await loadStatus();
  } catch (e) {
    alert("保存黑名单失败: " + e);
  }
}

async function poll() {
  if (state.liveMode) {
    await loadTriggers();
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(poll, 2000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

els.toggleLiveBtn.addEventListener("click", toggleLive);
els.refreshBtn.addEventListener("click", async () => {
  await loadStatus();
  await loadTriggers();
});
els.saveBliveSettingsBtn.addEventListener("click", saveBliveSettings);
els.saveBlacklistBtn.addEventListener("click", saveBlacklist);

async function init() {
  await loadStatus();
  await loadTriggers();
  startPolling();
  window.__LIVE_WINDOW_READY__ = true;
}

init();

window.addEventListener("beforeunload", async () => {
  stopPolling();
  if (state.liveMode) {
    try {
      await cfgApi("POST", "/faust/live/stop");
    } catch (e) {
      console.warn("退出时停止直播模式失败", e);
    }
  }
});
