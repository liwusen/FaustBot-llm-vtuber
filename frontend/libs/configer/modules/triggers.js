// Triggers module renderer

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
