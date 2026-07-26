// Runtime module renderer

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
      refreshModule();
    })
  );
  addSection("运行时控制", [bar]);

  const tableCard = el("article", "card full-span");
  tableCard.append(el("h3", "card-title", "服务列表"));
  const list = el("table", "simple-table");
  list.innerHTML = "<thead><tr><th>服务</th><th>名称</th><th>状态</th><th>端口</th><th>操作</th></tr></thead>";
  const tbody = el("tbody", "");
  for (const svc of state.services) {
    const key = String(svc.key || "");
    const row = el("tr", state.selectedService === key ? "selected" : "");
    row.append(
      el("td", "cell-primary", key),
      el("td", "", svc.name || "-"),
      el("td", "", svc.is_running ? "运行中" : "未运行"),
      el("td", "", svc.port ? String(svc.port) : "-"),
    );
    const ops = el("div", "toolbar compact");
    ops.addEventListener("click", (evt) => evt.stopPropagation());
    ops.append(
      makeButton("查看", async () => {
        state.selectedService = key;
        await ensureModuleData("runtime");
        refreshModule();
      }),
      makeButton("启动", async () => { await cfgApi("POST", `/faust/admin/services/${encodeURIComponent(key)}/start`, {}); await ensureModuleData("runtime"); refreshModule(); }),
      makeButton("停止", async () => { await cfgApi("POST", `/faust/admin/services/${encodeURIComponent(key)}/stop`, {}); await ensureModuleData("runtime"); refreshModule(); }),
      makeButton("重启", async () => { await cfgApi("POST", `/faust/admin/services/${encodeURIComponent(key)}/restart`, {}); await ensureModuleData("runtime"); refreshModule(); })
    );
    const opsCell = el("td", "");
    opsCell.append(ops);
    row.append(opsCell);
    row.addEventListener("click", async () => {
      state.selectedService = key;
      await ensureModuleData("runtime");
      refreshModule();
    });
    tbody.append(row);
  }
  if (!state.services.length) {
    const row = el("tr", "");
    const empty = el("td", "table-empty", "当前没有服务信息。");
    empty.colSpan = 5;
    row.append(empty);
    tbody.append(row);
  }
  list.append(tbody);
  tableCard.append(list);
  appendToActiveModule(tableCard);

  const log = el("textarea", "textarea code-area");
  log.readOnly = true;
  log.value = String((state.serviceDetail && state.serviceDetail.log_tail) || "暂无日志");
  log.classList.add("code-area-lg");
  addSection("服务日志", [log]);
}
