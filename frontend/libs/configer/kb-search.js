// KB search modal

function openKbSearchModal() {
  const searchInput = el("input", "input");
  searchInput.placeholder = "输入关键词，在当前范围内搜索";
  const resultBox = el("div", "list-box");
  resultBox.style.maxHeight = "420px";

  const runSearch = async () => {
    const q = searchInput.value.trim();
    if (!q) {
      showBanner("info", "请输入搜索关键词。" );
      return;
    }
    try {
      resultBox.innerHTML = "";
      resultBox.append(el("div", "empty-state", "搜索中..."));
      const data = await cfgApi("POST", "/faust/memory/search", {
        query: q,
        scope: state.kbScope || null,
        top_k: 12,
        return: "snippets",
      });
      const items = data.items || [];
      resultBox.innerHTML = "";
      if (!items.length) {
        resultBox.append(el("div", "empty-state", "未找到匹配内容。"));
        return;
      }
      for (const it of items) {
        const row = el("div", "list-row clickable");
        const left = el("div", "field-wrap");
        left.append(
          el("div", "mono", `[FILE] ${it.path || "-"} | score=${it.score ?? "-"}`),
          el("div", "card-help", String(it.snippet || ""))
        );
        const ops = el("div", "toolbar compact");
        ops.addEventListener("click", (evt) => evt.stopPropagation());
        ops.append(makeButton("打开", async () => {
          state.kbSelectedPath = normalizeKbPath(String(it.path || ""));
          state.kbCurrentDir = kbParentPath(state.kbSelectedPath);
          const d = await cfgApi("GET", "/faust/memory/get", null, { path: state.kbSelectedPath });
          state.kbSelectedContent = String(d.content || "");
          await openKbEditorModal(state.kbSelectedPath, state.kbSelectedContent, d.meta || {});
          renderModule();
        }));
        row.append(left, ops);
        row.addEventListener("click", async () => {
          state.kbSelectedPath = normalizeKbPath(String(it.path || ""));
          state.kbCurrentDir = kbParentPath(state.kbSelectedPath);
          const d = await cfgApi("GET", "/faust/memory/get", null, { path: state.kbSelectedPath });
          state.kbSelectedContent = String(d.content || "");
          await openKbEditorModal(state.kbSelectedPath, state.kbSelectedContent, d.meta || {});
          renderModule();
        });
        resultBox.append(row);
      }
    } catch (err) {
      resultBox.innerHTML = "";
      resultBox.append(el("div", "empty-state", `搜索失败: ${String(err && err.message ? err.message : err)}`));
      showBanner("error", `KB 搜索失败: ${String(err && err.message ? err.message : err)}`);
    }
  };
  searchInput.addEventListener("keydown", (evt) => {
    if (evt.key === "Enter") runSearch();
  });

  const actionBar = el("div", "toolbar");
  actionBar.append(
    searchInput,
    makeButton("搜索", runSearch, "btn btn-primary"),
    makeButton("关闭", closeModal)
  );
  openModal("KB 搜索", [actionBar, resultBox]);
}
