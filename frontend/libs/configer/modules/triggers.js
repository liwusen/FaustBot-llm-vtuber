// Triggers module renderer

function renderTriggersModule() {
  const bar = el("div", "toolbar");
  bar.append(
    makeButton("刷新", async () => { await ensureModuleData("triggers"); refreshModule(); }),
    makeButton("新建", async () => {
      openTriggerEditorModal(null, async (payload) => {
        await cfgApi("POST", "/faust/admin/triggers", payload);
        await ensureModuleData("triggers");
        refreshModule();
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
        refreshModule();
      });
    }),
    makeButton("删除", async () => {
      if (!state.selectedTriggerId) return;
      if (!window.confirm(`确定删除 Trigger ${state.selectedTriggerId} ?`)) return;
      await cfgApi("DELETE", `/faust/admin/triggers/${encodeURIComponent(state.selectedTriggerId)}`);
      state.selectedTriggerId = "";
      await ensureModuleData("triggers");
      refreshModule();
    })
  );
  addSection("触发器操作", [bar]);

  const tableCard = el("article", "card full-span");
  tableCard.append(el("h3", "card-title", "触发器列表"));
  const list = el("table", "simple-table");
  list.innerHTML = "<thead><tr><th>触发器 ID</th><th>类型</th><th>有效期</th><th>说明</th><th>操作</th></tr></thead>";
  const tbody = el("tbody", "");
  for (const trig of state.triggers) {
    const tid = String(trig.id || "");
    const row = el("tr", state.selectedTriggerId === tid ? "selected" : "");
    row.append(
      el("td", "cell-primary", tid),
      el("td", "", trig.type || "-"),
      el("td", "", trig.lifespan == null ? "未设置" : String(trig.lifespan)),
      el("td", "", trig.recall_description || "-"),
    );
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
          refreshModule();
        });
      }),
      makeButton("删除", async () => {
        if (!window.confirm(`确定删除 Trigger ${tid} ?`)) return;
        await cfgApi("DELETE", `/faust/admin/triggers/${encodeURIComponent(tid)}`);
        if (state.selectedTriggerId === tid) state.selectedTriggerId = "";
        await ensureModuleData("triggers");
        refreshModule();
      })
    );
    const opsCell = el("td", "");
    opsCell.append(ops);
    row.append(opsCell);
    row.addEventListener("click", () => {
      state.selectedTriggerId = tid;
      refreshModule();
    });
    tbody.append(row);
  }
  if (!state.triggers.length) {
    const row = el("tr", "");
    const empty = el("td", "table-empty", "当前没有 Trigger。 ");
    empty.colSpan = 5;
    row.append(empty);
    tbody.append(row);
  }
  list.append(tbody);
  tableCard.append(list);
  appendToActiveModule(tableCard);
}
