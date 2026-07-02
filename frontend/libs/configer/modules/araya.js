// Araya module renderer

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

  const triggerSlider = createArayaTriggerSlider(async () => {
    if (state.arayaEventSource) {
      state.arayaEventSource.close();
      state.arayaEventSource = null;
    }

    const baseUrl = (window.api && window.api.backendBaseUrl) || "http://127.0.0.1:13900";
    const url = baseUrl + "/faust/araya/trigger-sse?reason=manual_from_configer";

    progressArea.style.display = "block";
    progressArea.textContent = "正在连接...";

    return new Promise((resolve, reject) => {
      const es = new EventSource(url);
      state.arayaEventSource = es;

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
        state.arayaEventSource = null;
        try {
          const data = JSON.parse(evt.data);
          progressArea.textContent += `\n\n\u2713 完成!（耗时 ${data.duration || "?"} 秒）`;
        } catch (e) {
          progressArea.textContent += "\n\n\u2713 完成!";
        }
        ensureModuleData("araya").then(() => {
          refreshModule();
          showBanner("success", "Araya 执行完成。");
        });
        resolve();
      });

      es.addEventListener("error", (evt) => {
        es.close();
        state.arayaEventSource = null;
        let msg = "未知错误";
        try {
          if (evt.data) {
            const data = JSON.parse(evt.data);
            msg = data.message || data.error || msg;
          }
        } catch (e) {}
        progressArea.textContent += `\n\n\u2717 错误: ${msg}`;
        ensureModuleData("araya").then(() => {
          refreshModule();
          showBanner("error", `Araya 错误: ${msg}`);
        });
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
      await ensureModuleData("araya");
      refreshModule();
      showBanner("success", "Araya 设置已保存。");
    }, "btn btn-primary"),
    makeButton("刷新状态", async () => {
      await ensureModuleData("araya");
      refreshModule();
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

  addSection("Araya 状态", [summaryCard, idleCard, logCard]);

  const msgs = Array.isArray(lastLog.messages) ? lastLog.messages : [];
  const lastMsgs = el("textarea", "textarea code-area");
  lastMsgs.readOnly = true;
  lastMsgs.value = msgs.map((x, i) => `#${i + 1} ${String(x || "")}`).join("\n\n") || "无消息片段";
  addSection("最近执行消息片段", [lastMsgs]);
}
