// Overview module renderer

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
  const servicesRunning = services.filter((s) => s.is_running).length;
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
    { label: "插件", icon: "\u{1F9E9}", value: `${pluginsEnabled}/${pluginsTotal}`, desc: "已启用/总数" },
    { label: "服务", icon: "\u2699\uFE0F", value: `${servicesRunning}/${servicesTotal}`, desc: "运行中/总数" },
    { label: modelType === "vrm" ? "VRM" : "Live2D", icon: modelType === "vrm" ? "\u{1F9CA}" : "\u{1F5BC}", value: modelPath === "-" ? "未配置" : modelPath.split("/").pop(), desc: "当前模型" },
    { label: "Agent", icon: "\u{1F916}", value: agent, desc: "当前角色" },
  ];
  for (const s of statData) {
    const card = el("article", "card");
    card.style.textAlign = "center";
    card.style.justifyContent = "center";
    card.innerHTML = `<div style="font-size:28px;line-height:1.2">${s.icon}</div><div style="font-size:22px;font-weight:700;margin:4px 0">${s.value}</div><div style="font-size:12px;color:var(--muted)">${s.desc}</div>`;
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
