// Araya module renderer

// 实时运行态：SSE 连接存在 state 里跨页面存活，渲染层状态也必须放在闭包外，
// 否则每次 re-render（保存设置 / 刷新状态）都会把流式更新写进被丢弃的节点。
const arayaLive = {
  trace: null,     // 本次运行的 trace，结构同后端 /faust/araya/trace（messages: user|assistant|tool|tool_result）
  timeline: null,  // 当前挂在页面上的轨迹渲染器，re-render 时指向新节点
};

function arayaReasonLabel(reason) {
  const map = {
    idle: "空闲自动触发",
    manual: "手动触发",
    manual_from_configer: "配置中心手动触发",
  };
  const key = String(reason || "");
  return map[key] || (key || "-");
}

function arayaStatusLabel(status) {
  const map = { ok: "成功", running: "运行中", error: "失败", idle: "未运行" };
  const key = String(status || "");
  return map[key] || (key || "-");
}

function arayaDuration(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s < 0) return "-";
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} 秒`;
  const m = Math.floor(s / 60);
  const rest = Math.round(s - m * 60);
  return rest ? `${m} 分 ${rest} 秒` : `${m} 分钟`;
}

function arayaRelative(iso) {
  const t = Date.parse(String(iso || ""));
  if (Number.isNaN(t)) return "-";
  const diff = Math.max(0, (Date.now() - t) / 1000);
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

function arayaText(value) {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch (_e) {
    return String(value);
  }
}

// 轨迹时间线：全量渲染与流式追加共用同一组原语，避免两条渲染路径漂移。
function createArayaTimeline(onToolAdded) {
  const list = el("div", "araya-timeline");
  const placeholder = el("div", "empty-state", "等待 Araya 输出…");
  const toolRows = new Map();
  let lastAssistant = null;
  let steps = 0;
  reset();

  function syncPlaceholder() {
    placeholder.classList.toggle("hidden", steps > 0);
  }

  function reset() {
    list.innerHTML = "";
    list.append(placeholder);
    toolRows.clear();
    lastAssistant = null;
    steps = 0;
    syncPlaceholder();
  }

  function step(kind) {
    steps += 1;
    syncPlaceholder();
    const row = el("div", `araya-step araya-step-${kind}`);
    row.append(el("span", "araya-step-dot"));
    const body = el("div", "araya-step-body");
    row.append(body);
    list.append(row);
    return body;
  }

  function addAssistant(text) {
    const chunk = String(text ?? "");
    if (!chunk) return;
    if (!lastAssistant) {
      const body = step("assistant");
      body.append(el("div", "araya-step-label", "Araya 输出"));
      lastAssistant = el("div", "araya-step-text", "");
      body.append(lastAssistant);
    }
    lastAssistant.textContent += chunk;
  }

  function addTool(callId, name, args) {
    lastAssistant = null;
    const body = step("tool");
    const details = document.createElement("details");
    details.className = "araya-tool";
    details.open = true;
    const summary = document.createElement("summary");
    summary.append(el("span", "araya-tool-caret", "▸"));
    summary.append(el("span", "araya-tool-name", String(name || "工具调用")));
    const timer = el("span", "araya-tool-time", "运行中…");
    summary.append(timer);
    const argsPre = el("pre", "araya-tool-pre", arayaText(args));
    const resultPre = el("pre", "araya-tool-pre araya-tool-result", "");
    details.append(summary, argsPre, resultPre);
    body.append(details);
    toolRows.set(String(callId || name || ""), { details, timer, resultPre });
    if (onToolAdded) onToolAdded();
  }

  function endTool(callId, name, result, duration) {
    const key = String(callId || "");
    const row = toolRows.get(key) || toolRows.get(String(name || ""));
    const seconds = Number(duration);
    if (!row) {
      // 只有结果没有调用（截断的历史 trace）：单独成行，别丢信息
      const body = step("result");
      body.append(el("div", "araya-step-label", `${String(name || "工具")} 结果`));
      body.append(el("pre", "araya-tool-pre", arayaText(result)));
      return;
    }
    row.timer.textContent = Number.isFinite(seconds) ? `${seconds.toFixed(2)}s` : "完成";
    row.resultPre.textContent = arayaText(result);
    row.details.open = false;
  }

  function render(items) {
    reset();
    for (const item of Array.isArray(items) ? items : []) {
      if (!item || typeof item !== "object") continue;
      if (item.role === "user") continue;  // 内部指令，不是给用户看的输出
      if (item.role === "assistant") {
        addAssistant(item.content);
      } else if (item.role === "tool") {
        addTool(item.call_id, item.tool_name, item.args);
      } else if (item.role === "tool_result") {
        endTool(item.call_id, item.tool_name, item.result, item.duration_seconds);
      }
    }
  }

  return { list, reset, addAssistant, addTool, endTool, render };
}

// 把一份 trace 填进 host（同步调用；host 被丢弃后继续写入不会污染新页面）
function mountArayaTrace(host, trace, onToolCount, keepEmpty) {
  host.innerHTML = "";
  const messages = trace && Array.isArray(trace.messages) ? trace.messages : [];
  if (!messages.length && !keepEmpty) {
    host.append(el("div", "empty-state", "还没有运行记录。触发一次后，这里会按顺序显示 Araya 的输出与每次工具调用。"));
    return null;
  }
  let toolCount = 0;
  const timeline = createArayaTimeline(() => {
    toolCount += 1;
    if (onToolCount) onToolCount(toolCount);
  });
  host.append(timeline.list);
  timeline.render(messages);
  if (onToolCount) onToolCount(toolCount);
  return timeline;
}

function renderArayaOverviewCard(status, lastLog, running) {
  const card = el("article", "card full-span");
  const head = el("div", "araya-card-head");
  let badgeText = "等待触发";
  let badgeClass = "badge badge-valid";
  if (running) {
    badgeText = "运行中";
    badgeClass = "badge badge-run";
  } else if (status.last_error) {
    badgeText = "上次运行异常";
    badgeClass = "badge badge-dirty";
  } else if (status.enabled === false) {
    badgeText = "自动触发已停用";
    badgeClass = "badge badge-muted";
  }
  head.append(el("h3", "card-title", "运行概览"), el("span", badgeClass, badgeText));
  card.append(head);

  const idleMinutes = Number(status.idle_minutes || 0);
  const idleSeconds = Math.max(0, Number(status.idle_seconds || 0));
  const remainSeconds = Math.max(0, idleMinutes * 60 - idleSeconds);
  const stats = el("div", "araya-stat-grid");
  const cell = (value, label) => {
    const box = el("div", "araya-stat");
    box.append(el("div", "araya-stat-value", value), el("div", "araya-stat-label", label));
    return box;
  };
  stats.append(
    cell(String(status.target_agent || "-"), "目标 Agent"),
    cell(arayaDuration(idleSeconds), `主 Agent 空闲（阈值 ${idleMinutes || "-"} 分钟）`),
    cell(status.enabled === false ? "已停用" : arayaDuration(remainSeconds), "距下次自动触发"),
    cell(lastLog && lastLog.finished_at_iso ? arayaRelative(lastLog.finished_at_iso) : "无记录", "上次运行")
  );
  card.append(stats);
  card.append(el("p", "card-help",
    `主 Agent 最近活跃 ${status.last_main_activity_at || "-"} · 状态更新 ${status.updated_at || "-"}`));
  appendToActiveModule(card);
}

function renderArayaSettingsCard(status) {
  const card = el("article", "card full-span");
  card.append(el("h3", "card-title", "自动维护设置"));

  const enabled = document.createElement("input");
  enabled.type = "checkbox";
  enabled.checked = status.enabled !== false;
  const enableLabel = el("label", "araya-check");
  enableLabel.append(enabled, el("span", "", "启用自动维护"));

  const idle = document.createElement("input");
  idle.type = "number";
  idle.className = "araya-number";
  idle.min = "1";
  idle.step = "1";
  idle.value = String(status.idle_minutes || 30);
  const idleLabel = el("label", "araya-check");
  idleLabel.append(idle, el("span", "", "空闲分钟"));

  const bar = el("div", "toolbar");
  bar.append(
    enableLabel,
    idleLabel,
    makeButton("保存设置", async () => {
      try {
        const data = await cfgApi("POST", "/faust/araya/settings", {
          enabled: enabled.checked,
          idle_minutes: Number(idle.value || 30),
        });
        state.araya = data && data.araya ? data.araya : state.araya;
        refreshModule();
        showBanner("success", "Araya 设置已保存。");
      } catch (e) {
        showBanner("error", "保存失败: " + (e.message || String(e)));
      }
    }, "btn btn-primary"),
    makeButton("刷新状态", async () => {
      await ensureModuleData("araya");
      refreshModule();
    })
  );
  card.append(bar);
  if (status.enabled_by_config === false) {
    card.append(el("p", "card-help", "配置文件里 ARAYA_ENABLED 为 false，这里的开关不会生效。"));
  }
  appendToActiveModule(card);
}

function renderArayaTriggerCard(running) {
  const card = el("article", "card full-span");
  const busyHint = "Araya 正在运行，请等这次跑完";
  card.append(el("h3", "card-title", "手动触发"));
  card.append(createArayaTriggerSlider(
    running ? async () => busyHint : arayaStartRun,
    running ? busyHint : undefined
  ));
  appendToActiveModule(card);
}

function renderArayaRunCard(lastLog, status) {
  const hasLog = Boolean(lastLog && Object.keys(lastLog).length);
  if (!hasLog) {
    const card = el("article", "card full-span");
    card.append(el("h3", "card-title", "最近一次运行"));
    card.append(el("p", "card-help", status.last_error
      ? `上次运行异常：${status.last_error}`
      : "还没有运行记录。等空闲自动触发，或拖动「手动触发」里的滑块跑一次。"));
    appendToActiveModule(card);
    return;
  }

  const rows = [
    { label: "触发原因", value: arayaReasonLabel(lastLog.reason) },
    { label: "结果", value: arayaStatusLabel(lastLog.status) },
    { label: "耗时", value: arayaDuration(lastLog.duration_seconds) },
    { label: "开始时间", value: lastLog.started_at_iso },
    { label: "结束时间", value: lastLog.finished_at_iso },
  ];
  if (lastLog.error) rows.push({ label: "错误", value: lastLog.error });

  const card = makeInfoCard("最近一次运行", rows);
  card.classList.add("full-span");
  const output = el("div", "araya-output");
  output.append(el("div", "araya-output-label", "Araya 输出"));
  output.append(el("div", "araya-output-text", String(lastLog.response || "（本次没有文本输出）")));
  card.append(output);
  appendToActiveModule(card);
}

function renderArayaTraceCard() {
  const card = el("article", "card full-span");
  const head = el("div", "araya-card-head");
  const host = el("div", "araya-trace-host");
  card.append(head, host);
  appendToActiveModule(card);

  const live = arayaLive.trace;
  const title = live ? "本次运行轨迹" : "上次运行轨迹";
  head.append(el("h3", "card-title", title));
  const meta = el("span", "araya-card-meta", "");
  head.append(meta);
  // trace 与 last_run.json 可能来自不同的两次运行，标上它自己的时间，避免两张卡看起来互相矛盾
  const setMeta = (trace, count) => {
    const when = arayaRelative(trace && trace.started_at_iso);
    meta.textContent = when && when !== "-" ? `${count} 次工具调用 · ${when}` : `${count} 次工具调用`;
  };

  if (live) {
    arayaLive.timeline = mountArayaTrace(host, live, (n) => setMeta(live, n), true);
    return;
  }

  // 空闲态读后端最后一份 trace；只填自己这张卡的 host，晚到的响应不会重复挂卡片
  cfgApi("GET", "/faust/araya/trace").then((data) => {
    const trace = data && data.trace ? data.trace : null;
    if (!trace) {
      host.append(el("div", "empty-state", "还没有运行记录。触发一次后，这里会按顺序显示 Araya 的输出与每次工具调用。"));
      return;
    }
    mountArayaTrace(host, trace, (n) => setMeta(trace, n));
  }).catch((e) => {
    host.innerHTML = "";
    host.append(el("div", "empty-state", `读取轨迹失败: ${e.message || String(e)}`));
  });
}

function renderArayaModule() {
  const status = state.araya || {};
  // 运行中 = 本页发起的 SSE，或后端报告的进行中运行（空闲自动触发 / 其他客户端触发）
  const running = Boolean(state.arayaEventSource) || status.run_in_progress === true;
  const lastLog = status.last_log && typeof status.last_log === "object" ? status.last_log : null;

  renderArayaOverviewCard(status, lastLog, running);
  renderArayaSettingsCard(status);
  renderArayaTriggerCard(running);
  renderArayaRunCard(lastLog, status);
  renderArayaTraceCard();
}

// 手动触发一次：SSE 全程只写 arayaLive，re-render 后由 renderArayaTraceCard 重新挂载
function arayaInvalidate() {
  // 运行结束时若用户停在别的模块，也要让 Araya 页失效，下次切回来才是新数据
  const entry = state.moduleContainers && state.moduleContainers.araya;
  if (entry) entry.rendered = false;
}

function arayaStartRun() {
  if (state.arayaEventSource) return Promise.resolve("Araya 正在运行");
  const baseUrl = (window.api && window.api.backendBaseUrl) || "http://127.0.0.1:13900";
  const url = baseUrl + "/faust/araya/trigger-sse?reason=manual_from_configer";

  return new Promise((resolve, reject) => {
    const es = new EventSource(url);
    state.arayaEventSource = es;

    const finish = () => {
      es.close();
      state.arayaEventSource = null;
      arayaLive.trace = null;
      arayaLive.timeline = null;
    };

    es.addEventListener("step", (evt) => {
      let data = null;
      try {
        data = JSON.parse(evt.data);
      } catch (e) {
        console.warn("SSE step parse error", e);
        return;
      }
      const timeline = arayaLive.timeline;
      switch (data.type) {
        case "start":
          arayaLive.trace = {
            conversation_id: `araya-live-${Date.now()}`,
            reason: data.reason || "-",
            status: "running",
            started_at_iso: new Date().toISOString(),
            messages: [],
            tool_calls: [],
          };
          // 先把页面切到「运行中 + 本次运行轨迹」，流式事件才有正确的落点
          arayaInvalidate();
          refreshModule();
          break;
        case "llm_chunk":
          if (!arayaLive.trace) return;
          arayaLive.trace.messages.push({ role: "assistant", content: data.content || "" });
          if (timeline) timeline.addAssistant(data.content || "");
          break;
        case "tool_start":
          if (!arayaLive.trace) return;
          arayaLive.trace.messages.push({ role: "tool", tool_name: data.tool || "tool", call_id: data.call_id || "", args: data.args || {} });
          if (timeline) timeline.addTool(data.call_id, data.tool, data.args);
          break;
        case "tool_end":
          if (!arayaLive.trace) return;
          arayaLive.trace.messages.push({ role: "tool_result", tool_name: data.tool || "tool", call_id: data.call_id || "", result: data.result ?? "", duration_seconds: data.duration ?? null });
          if (timeline) timeline.endTool(data.call_id, data.tool, data.result, data.duration);
          break;
        default:
          break;
      }
    });

    es.addEventListener("done", (evt) => {
      finish();
      let duration = null;
      try {
        const data = JSON.parse(evt.data);
        duration = data.duration ?? null;
      } catch (e) {
        console.warn("SSE done parse error", e);
      }
      arayaInvalidate();
      ensureModuleData("araya").then(() => {
        refreshModule();
        showBanner("success", `Araya 执行完成${duration ? `（${arayaDuration(duration)}）` : ""}。`);
      });
      resolve();
    });

    es.addEventListener("error", (evt) => {
      let msg = "连接中断";
      if (evt && evt.data) {
        try {
          const data = JSON.parse(evt.data);
          msg = data.message || data.error || msg;
        } catch (e) {
          console.warn("SSE error parse error", e);
        }
      }
      finish();
      arayaInvalidate();
      ensureModuleData("araya").then(() => {
        refreshModule();
        showBanner("error", `Araya 错误: ${msg}`);
      });
      reject(new Error(msg));
    });
  });
}
