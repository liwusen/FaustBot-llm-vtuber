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
