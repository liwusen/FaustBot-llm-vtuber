// Memory module renderer: tree, graph, search

function renderMemoryModule() {
  const currentDir = normalizeKbPath(state.kbCurrentDir || "/");
  const view = state.memoryView || "tree";

  // ── Tab bar ──
  const tabBar = el("div", "toolbar");
  const treeTab = makeButton("\u{1F4C1} 树状浏览", async () => {
    state.memoryView = "tree";
    refreshModule();
  }, view === "tree" ? "btn btn-primary" : "btn btn-ghost");
  const graphTab = makeButton("\u{1F517} 知识图谱", async () => {
    state.memoryView = "graph";
    await ensureModuleData("memory");
    refreshModule();
  }, view === "graph" ? "btn btn-primary" : "btn btn-ghost");
  const searchTab = makeButton("\u{1F50D} 统一搜索", async () => {
    state.memoryView = "search";
    refreshModule();
  }, view === "search" ? "btn btn-primary" : "btn btn-ghost");
  tabBar.append(treeTab, graphTab, searchTab);
  addSection("记忆", [tabBar]);

  if (view === "tree") {
    renderMemoryTree(currentDir);
  } else if (view === "graph") {
    renderMemoryGraph();
  } else if (view === "search") {
    renderMemorySearch();
  }
}

function renderMemoryTree(currentDir) {
  const doRefresh = async () => {
    await ensureModuleData("memory");
    refreshModule();
  };

  const doNewFile = async () => {
    const defaultPath = `${currentDir === "/" ? "" : currentDir.slice(1) + "/"}new.md`;
    const nameInput = el("input", "input");
    nameInput.value = defaultPath;
    const save = async () => {
      const p = String(nameInput.value || "").trim();
      if (!p) return showBanner("error", "请输入文件路径");
      const target = normalizeKbPath(p);
      closeModal();
      await openKbEditorModal(target, "", { path: target, declared_by: "config-center", indexed: true, tags: [] });
    };
    const tb = el("div", "toolbar");
    tb.append(makeButton("创建", save, "btn btn-primary"), makeButton("取消", closeModal));
    openModal("新建文件", [nameInput, tb]);
  };

  const doNewFolder = async () => {
    const defaultPath = `${currentDir === "/" ? "" : currentDir.slice(1) + "/"}new-folder`;
    const nameInput = el("input", "input");
    nameInput.value = defaultPath;
    const save = async () => {
      const p = String(nameInput.value || "").trim();
      if (!p) return showBanner("error", "请输入文件夹路径");
      await cfgApi("POST", "/faust/memory/mkdir", { path: p });
      state.kbCurrentDir = normalizeKbPath(p);
      await ensureModuleData("memory");
      closeModal();
      refreshModule();
    };
    const tb = el("div", "toolbar");
    tb.append(makeButton("创建", save, "btn btn-primary"), makeButton("取消", closeModal));
    openModal("新建文件夹", [nameInput, tb]);
  };

  const doDelete = async () => {
    const p = (state.kbSelectedPath || "").trim();
    if (!p) { showBanner("info", "请先选中文件或目录。"); return; }
    if (!window.confirm(`确定删除 ${p} ?`)) return;
    await cfgApi("DELETE", "/faust/memory/delete", null, { path: p });
    state.kbSelectedPath = "";
    state.kbSelectedContent = "";
    state.kbCurrentDir = kbParentPath(p);
    await ensureModuleData("memory");
    refreshModule();
  };

  // ── Build node count summary ──
  function countTreeStats(treeRoot) {
    function countRec(nodes, typeFilter) {
      let count = 0;
      for (const n of nodes) {
        if (n.type === typeFilter) count++;
        if (n.children) count += countRec(n.children, typeFilter);
      }
      return count;
    }
    const children = treeRoot && treeRoot.children ? treeRoot.children : [];
    return {
      dirs: countRec(children, "dir"),
      files: countRec(children, "file"),
    };
  }

  function getDirMeta(rowNode) {
    if (!rowNode) return {};
    if (rowNode.type === "dir") {
      const sub = (rowNode.children || []).reduce(function (acc, c) {
        if (c.type === "dir") acc.dirCt++;
        else if (c.type === "file") acc.fileCt++;
        return acc;
      }, { dirCt: 0, fileCt: 0 });
      return {
        metaText: [sub.dirCt && `${sub.dirCt}目录`, sub.fileCt && `${sub.fileCt}文件`].filter(Boolean).join(" | ") || "空",
        description: String(rowNode.description || ""),
        tags: rowNode.tags || [],
      };
    }
    if (rowNode.type === "file") {
      return {
        metaText: String(rowNode.description || "").slice(0, 120),
        description: String(rowNode.description || ""),
        tags: rowNode.tags || [],
      };
    }
    return { metaText: "", description: "", tags: [] };
  }

  // ── Actions ──
  const actionBar = el("div", "toolbar");
  actionBar.append(
    makeButton("刷新", doRefresh),
    makeButton("新建文件", doNewFile, "btn btn-primary"),
    makeButton("新建文件夹", doNewFolder),
    makeButton("删除", doDelete),
  );
  addSection("目录操作", [actionBar]);

  // ── Stats badge ──
  const treeStats = countTreeStats(state.kbTree);
  const statsLine = el("div", "card-help");
  statsLine.style.padding = "4px 0";
  statsLine.style.lineHeight = "1.6";
  statsLine.innerHTML = [
    "<b>总计:</b> " + [
      treeStats.dirs && `${treeStats.dirs} 目录`,
      treeStats.files && `${treeStats.files} 文件`,
    ].filter(Boolean).join(", ") +
    (state.graphEntities && state.graphEntities.length ? " | " + state.graphEntities.length + " 实体" : "") +
    (state.graphRelations && state.graphRelations.length ? " | " + state.graphRelations.length + " 关系" : ""),
    "<b>当前:</b> " + currentDir,
    (state.kbTasks && state.kbTasks.length ? "<br><b>待处理任务:</b> " + state.kbTasks.length + " 个" : ""),
  ].filter(Boolean).join("<br>");

  // ── Dir list (Windows Explorer 风格表格) ──
  const table = el("table", "explorer-table");
  const thead = document.createElement("thead");
  thead.innerHTML = "<tr><th>名称</th><th class=col-type>类型</th><th class=col-desc>描述</th></tr>";
  table.append(thead);
  const tbody = document.createElement("tbody");
  table.append(tbody);
  const addRow = (icon, name, typeText, descText, cls, onclick, oncontext) => {
    const tr = document.createElement("tr");
    tr.className = cls || "";
    tr.innerHTML = `<td><span class="col-name mono">${icon} ${name}</span></td><td class=col-type>${typeText}</td><td class=col-desc>${descText}</td>`;
    if (onclick) tr.addEventListener("click", onclick);
    if (oncontext) tr.addEventListener("contextmenu", (evt) => { evt.preventDefault(); oncontext(evt); });
    tbody.append(tr);
  };
  addRow("\u{1F4C1}", "/ (根目录)", "目录", "", currentDir === "/" ? "selected" : "",
    () => { state.kbCurrentDir = "/"; state.kbSelectedPath = ""; refreshModule(); });
  const parentPath2 = kbParentPath(currentDir);
  if (parentPath2 !== currentDir) {
    addRow("\u{1F4C2}", ".. (上一级)", "目录", "", "",
      () => { state.kbCurrentDir = parentPath2; state.kbSelectedPath = ""; refreshModule(); });
  }
  const nodes = getKbChildren(state.kbTree, currentDir);
  if (!nodes.length) {
    tbody.innerHTML = "<tr><td colspan=3 class=empty-state>当前目录为空。</td></tr>";
  }
  for (const node of nodes) {
    if (node.type === "entity") continue;
    const iconText = node.type === "file" ? "\u{1F4C4}" : "\u{1F4C1}";
    const isImage = node.type === "file" && /\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(node.name);
    const displayIcon = isImage ? "\u{1F5BC}" : iconText;
    const typeText = node.type === "file" ? "文件" : "目录";
    const desc = String(node.description || "");
    const itemCount = node.type !== "file" ? (node.children || []).length : 0;
    const descText = node.type === "file"
      ? (desc ? desc.slice(0, 80) + (desc.length > 80 ? "\u2026" : "") : "")
      : (itemCount > 0 ? itemCount + " 项" + (desc ? " | " + desc.slice(0, 40) : "") : (desc.slice(0, 40) || "空"));
    const sel = state.kbSelectedPath === node.path ? "selected" : "";
    addRow(displayIcon, node.name, typeText, descText, sel,
      () => {
        if (node.type === "file") {
          state.kbSelectedPath = node.path;
          state.kbSelectedMeta = null;
          state.kbGraphSelectedEntityId = "";
        } else {
          state.kbCurrentDir = node.path;
          state.kbSelectedPath = "";
          state.kbSelectedMeta = null;
        }
        refreshModule();
      },
      (evt) => {
        state.kbSelectedPath = node.path;
        renderContextMenu(evt.clientX, evt.clientY, node);
      }
    );
  }
  addSection("目录: " + currentDir + " (" + nodes.length + " 项)", [table]);
  // ── Detail section follows ──

  // ── Detail panel + entity children + metadata for selected file ──
  (async function renderDetailAndEntityChildren() {
    const selPath = state.kbSelectedPath || "";
    if (!selPath) return;

    const metaTable = el("div", "info-grid");
    metaTable.style.marginTop = "6px";
    const addMetaRow = (label, value) => {
      if (!value && value !== 0) return;
      const row = el("div", "info-item");
      row.innerHTML = `<span class="info-key">${label}</span><span class="info-value">${value}</span>`;
      metaTable.append(row);
    };
    const meta = state.kbSelectedMeta || {};
    const timeStr = meta.updated_at ? new Date(meta.updated_at + (meta.updated_at.endsWith("Z") ? "" : "Z")).toLocaleString() : "-";
    const contentLen = (state.kbSelectedContent || "").length;
    const sizeStr = meta.content_type && meta.content_type.startsWith("image/") ? "\u{1F5BC} 图片" : (contentLen > 0 ? contentLen + " 字符" : "");
    addMetaRow("路径", "<b>" + selPath + "</b>");
    addMetaRow("创建者", meta.declared_by || "-");
    addMetaRow("更新时间", timeStr);
    addMetaRow("大小", sizeStr || "-");
    addMetaRow("索引块", meta.chunk_count != null ? String(meta.chunk_count) : "0");
    addMetaRow("权重", meta.score_patch != null ? String(meta.score_patch) : "0");
    addMetaRow("索引状态", meta.indexed ? "\u2705 已索引" : "\u274C 未索引");
    if (meta.content_type) addMetaRow("类型", meta.content_type);
    if (meta.managed_by) addMetaRow("管理者", meta.managed_by);
    if (meta.description) addMetaRow("描述", meta.description);

    // ── Tags: editable chip list ──
    const tagsWrap = el("div", "tag-list");
    tagsWrap.style.marginTop = "8px";
    tagsWrap.style.display = "flex";
    tagsWrap.style.flexWrap = "wrap";
    tagsWrap.style.alignItems = "center";
    tagsWrap.style.gap = "4px";
    tagsWrap.append(el("span", "", "标签: "));

    let currentTags = Array.isArray(meta.tags) ? [...meta.tags] : [];

    const refreshTagChips = () => {
      while (tagsWrap.children.length > 1) tagsWrap.removeChild(tagsWrap.lastChild);
      for (const tag of currentTags) {
        const chip = el("span", "tag-chip", tag);
        const removeBtn = el("span", "", " \u00D7");
        removeBtn.style.cursor = "pointer";
        removeBtn.style.marginLeft = "4px";
        removeBtn.style.fontWeight = "bold";
        removeBtn.style.color = "#c00";
        removeBtn.title = "删除标签";
        removeBtn.addEventListener("click", async (e) => {
          e.stopPropagation();
          currentTags = currentTags.filter(t => t !== tag);
          try {
            await cfgApi("POST", "/faust/memory/tags", { path: selPath, tags: currentTags });
          } catch (err) {
            console.warn("failed to remove tag", err);
          }
          refreshTagChips();
        });
        chip.append(removeBtn);
        tagsWrap.append(chip);
      }
      const tagInput = el("input", "input");
      tagInput.placeholder = "新标签";
      tagInput.style.width = "80px";
      tagInput.style.height = "24px";
      tagInput.style.fontSize = "12px";
      tagInput.style.padding = "0 4px";
      const addTagBtn = makeButton("+", async () => {
        const newTag = tagInput.value.trim();
        if (!newTag || currentTags.includes(newTag)) return;
        currentTags.push(newTag);
        tagInput.value = "";
        try {
          await cfgApi("POST", "/faust/memory/tags", { path: selPath, tags: currentTags });
        } catch (err) {
          console.warn("failed to add tag", err);
        }
        refreshTagChips();
      }, "btn btn-ghost");
      addTagBtn.style.height = "24px";
      addTagBtn.style.padding = "0 8px";
      addTagBtn.style.fontSize = "12px";
      tagInput.addEventListener("keydown", (evt) => {
        if (evt.key === "Enter") addTagBtn.click();
      });
      tagsWrap.append(tagInput, addTagBtn);
    };
    refreshTagChips();

    const detailBox = el("div", "card-help");
    detailBox.style.padding = "8px";
    detailBox.style.marginTop = "4px";
    detailBox.style.background = "var(--bg-secondary, #f5f5f5)";
    detailBox.style.borderRadius = "4px";
    detailBox.append(metaTable, tagsWrap);
    // Edit button for files
    if (selPath) {
      const editBar = el("div", "toolbar");
      editBar.style.marginTop = "4px";
      editBar.style.gap = "8px";
      editBar.append(makeButton("编辑文件", async () => {
        await openKbEditorModal(selPath, state.kbSelectedContent || "", meta);
      }, "btn btn-primary"));
      // Image display
      if (meta.content_type && String(meta.content_type).startsWith("image/")) {
        try {
          const imgResp = await cfgApi("GET", "/faust/memory/attachment", null, { path: selPath });
          if (imgResp && imgResp.content_base64) {
            const imgWrap = el("div");
            imgWrap.style.marginTop = "4px";
            const img = el("img");
            img.src = "data:" + imgResp.content_type + ";base64," + imgResp.content_base64;
            img.style.maxWidth = "100%";
            img.style.maxHeight = "300px";
            img.style.borderRadius = "4px";
            img.style.cursor = "pointer";
            img.addEventListener("click", () => {
              const full = el("img");
              full.src = img.src;
              full.style.maxWidth = "100%";
              full.style.maxHeight = "80vh";
              openModal("图片预览", [full]);
            });
            imgWrap.append(img);
            editBar.append(imgWrap);
          }
        } catch (_) {}
      } else if (/\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(selPath)) {
        try {
          const imgResp = await cfgApi("GET", "/faust/memory/attachment", null, { path: selPath });
          if (imgResp && imgResp.content_base64) {
            const imgWrap = el("div");
            imgWrap.style.marginTop = "4px";
            const img = el("img");
            img.src = "data:" + (imgResp.content_type || "image/png") + ";base64," + imgResp.content_base64;
            img.style.maxWidth = "100%";
            img.style.maxHeight = "300px";
            imgWrap.append(img);
            editBar.append(imgWrap);
          }
        } catch (_) {}
      }
      detailBox.append(editBar);
    }

    function findInTree(tree, target) {
      if (!tree || typeof tree !== "object") return null;
      if (tree.path === target || tree.id === target) return tree;
      for (const c of tree.children || []) {
        const found = findInTree(c, target);
        if (found) return found;
      }
      return null;
    }
    addSection("详情", [detailBox]);

    // Entity children - only for file nodes
    const found = findInTree(state.kbTree, selPath);
    if (found && found.type === "file") {
      var entChildren = [];
      try {
        const resp = await cfgApi("GET", "/faust/memory/graph/entity-children", null, { path: selPath });
        entChildren = resp.items || [];
      } catch (_) {}
      if (entChildren.length) {
        const entBox = el("div", "list-box");
        entBox.style.maxHeight = "200px";
        entBox.style.marginTop = "4px";
        for (const ent of entChildren) {
          const row = el("div", "list-row entity-row");
          row.style.paddingLeft = "1.5em";
          const label = el("div");
          const icon = el("span", "mono");
          icon.textContent = "\u{1F464}[ENT:" + ent.entity_type + "] " + ent.name;
          label.append(icon);
          if (ent.description) {
            const desc = el("div", "card-help");
            desc.style.marginLeft = "16px";
            desc.textContent = ent.description.length > 80 ? ent.description.slice(0, 80) + "\u2026" : ent.description;
            label.append(desc);
          }
          row.style.cursor = "pointer";
          row.title = "点击查看实体图谱";
          row.addEventListener("click", () => {
            state.kbGraphSelectedEntityId = ent.id;
            state.memoryView = "graph";
            refreshModule();
          });
          row.append(label);
          entBox.append(row);
        }
        addSection("文档实体 (" + entChildren.length + ")", [entBox]);
      }
    }
  })();

  // ── Right-click context menu ──
  function renderContextMenu(x, y, node) {
    const old = document.getElementById("kbContextMenu");
    if (old) old.remove();

    const menu = el("div", "context-menu");
    menu.id = "kbContextMenu";
    menu.style.position = "fixed";
    menu.style.left = x + "px";
    menu.style.top = y + "px";
    menu.style.zIndex = "9999";
    menu.style.background = "var(--bg-primary, #fff)";
    menu.style.border = "1px solid var(--border-color, #ccc)";
    menu.style.borderRadius = "6px";
    menu.style.boxShadow = "0 4px 12px rgba(0,0,0,0.15)";
    menu.style.padding = "4px 0";
    menu.style.minWidth = "140px";
    const items = [];
    if (node.type === "file") {
      items.push({ label: "编辑", icon: "\u270F", action: async () => {
        const d = await cfgApi("GET", "/faust/memory/get", null, { path: node.path });
        await openKbEditorModal(node.path, String(d.content || ""), d.meta || {});
      }});
    }
    items.push({ label: "新建文件", icon: "\u{1F4C4}", action: async () => {
      const target = normalizeKbPath((node.path || currentDir) + "/new.md");
      await openKbEditorModal(target, "", { path: target, declared_by: "config-center", indexed: true, tags: [] });
    }});
    items.push(null); // separator
    items.push({ label: "重命名", icon: "\u{1F3AB}", action: async () => {
      const newName = window.prompt("新名称:", node.name);
      if (!newName || newName === node.name) return;
      try {
        await cfgApi("POST", "/faust/memory/rename", { path: node.path, new_name: newName });
        state.kbCurrentDir = kbParentPath(kbParentPath(node.path));
        await ensureModuleData("memory");
        showBanner("success", "已重命名为 " + newName);
      } catch (err) { showBanner("error", "重命名失败: " + String(err)); }
    }});
    if (node.type === "file") {
      items.push({ label: "复制", icon: "\u{1F4CB}", action: async () => {
        state._kbClipboard = { mode: "copy", path: node.path, name: node.name };
        showBanner("info", "已复制: " + node.name);
      }});
      items.push({ label: "剪切", icon: "\u2702", action: async () => {
        state._kbClipboard = { mode: "cut", path: node.path, name: node.name };
        showBanner("info", "已剪切: " + node.name);
      }});
    }
    items.push({ label: "粘贴", icon: "\u{1F4CC}", action: async () => {
      if (!state._kbClipboard) { showBanner("info", "剪贴板为空"); return; }
      const clip = state._kbClipboard;
      const targetDir = node.type === "dir" ? node.path : kbParentPath(node.path);
      try {
        if (clip.mode === "copy") {
          const destPath = normalizeKbPath(targetDir + "/" + clip.name);
          await cfgApi("POST", "/faust/memory/copy", { path: clip.path, dest: destPath });
          showBanner("success", "已粘贴: " + clip.name);
        } else if (clip.mode === "cut") {
          await cfgApi("POST", "/faust/memory/move", { path: clip.path, dest_dir: targetDir });
          state._kbClipboard = null;
          showBanner("success", "已移动: " + clip.name);
        }
        await ensureModuleData("memory");
      } catch (err) { showBanner("error", "粘贴失败: " + String(err)); }
    }, disabled: !state._kbClipboard});
    items.push(null); // separator
    items.push({ label: node.type === "file" ? "删除文件" : "删除目录", icon: "\u{1F5D1}", action: async () => {
      if (!window.confirm("确定删除 " + node.path + " ?")) return;
      await cfgApi("DELETE", "/faust/memory/delete", null, { path: node.path });
      state.kbSelectedPath = "";
      state.kbCurrentDir = kbParentPath(node.path);
      await ensureModuleData("memory");
      refreshModule();
    }});

    for (const it of items) {
      if (it === null) {
        const sep = document.createElement("div");
        sep.style.cssText = "border-top:1px solid var(--border-color,#ddd);margin:4px 8px";
        menu.append(sep);
        continue;
      }
      const item = el("div", "context-menu-item");
      item.style.padding = "6px 16px";
      item.style.cursor = it.disabled ? "default" : "pointer";
      item.style.display = "flex";
      item.style.alignItems = "center";
      item.style.gap = "8px";
      item.style.fontSize = "13px";
      if (it.disabled) { item.style.opacity = "0.4"; }
      item.innerHTML = (it.icon || "") + " " + it.label;
      item.addEventListener("mouseenter", () => { if (!it.disabled) item.style.background = "var(--bg-secondary, #f0f0f0)"; });
      item.addEventListener("mouseleave", () => { if (!it.disabled) item.style.background = "transparent"; });
      if (!it.disabled) {
        item.addEventListener("click", async () => {
          menu.remove();
          await it.action();
          refreshModule();
        });
      }
      menu.append(item);
    }

    document.body.append(menu);
    const close = (evt) => {
      if (!menu.contains(evt.target)) {
        menu.remove();
        document.removeEventListener("click", close);
      }
    };
    document.addEventListener("click", close);
  }

  // ── Keyboard shortcuts ──
  const selNode = state.kbSelectedPath ? (() => {
    const nodes = getKbChildren(state.kbTree, state.kbCurrentDir);
    return nodes.find(n => n.path === state.kbSelectedPath) || null;
  })() : null;
  const kbHandler = (evt) => {
    const isFile = selNode && selNode.type === "file";
    if (evt.key === "F2" && selNode) {
      evt.preventDefault();
      const newName = window.prompt("新名称:", selNode.name);
      if (!newName || newName === selNode.name) return;
      cfgApi("POST", "/faust/memory/rename", { path: selNode.path, new_name: newName })
        .then(() => { state.kbCurrentDir = kbParentPath(kbParentPath(selNode.path)); return ensureModuleData("memory"); })
        .then(() => { refreshModule(); showBanner("success", "已重命名为 " + newName); })
        .catch(err => showBanner("error", "重命名失败: " + String(err)));
    } else if (evt.key === "Delete" && selNode) {
      evt.preventDefault();
      if (!window.confirm("确定删除 " + selNode.path + " ?")) return;
      cfgApi("DELETE", "/faust/memory/delete", null, { path: selNode.path })
        .then(() => { state.kbSelectedPath = ""; state.kbCurrentDir = kbParentPath(selNode.path); return ensureModuleData("memory"); })
        .then(() => refreshModule());
    } else if (evt.ctrlKey && evt.key === "c" && isFile) {
      state._kbClipboard = { mode: "copy", path: selNode.path, name: selNode.name };
      showBanner("info", "已复制: " + selNode.name);
    } else if (evt.ctrlKey && evt.key === "x" && isFile) {
      state._kbClipboard = { mode: "cut", path: selNode.path, name: selNode.name };
      showBanner("info", "已剪切: " + selNode.name);
    } else if (evt.ctrlKey && evt.key === "v" && state._kbClipboard) {
      evt.preventDefault();
      const clip = state._kbClipboard;
      const targetDir = currentDir;
      if (clip.mode === "copy") {
        const destPath = normalizeKbPath(targetDir + "/" + clip.name);
        cfgApi("POST", "/faust/memory/copy", { path: clip.path, dest: destPath })
          .then(() => { showBanner("success", "已粘贴: " + clip.name); return ensureModuleData("memory"); })
          .then(() => refreshModule())
          .catch(err => showBanner("error", "粘贴失败: " + String(err)));
      } else if (clip.mode === "cut") {
        cfgApi("POST", "/faust/memory/move", { path: clip.path, dest_dir: targetDir })
          .then(() => { state._kbClipboard = null; showBanner("success", "已移动: " + clip.name); return ensureModuleData("memory"); })
          .then(() => refreshModule())
          .catch(err => showBanner("error", "移动失败: " + String(err)));
      }
    } else if (evt.ctrlKey && evt.shiftKey && evt.key === "N") {
      evt.preventDefault();
      doNewFolder();
    } else if (evt.ctrlKey && evt.key === "n") {
      evt.preventDefault();
      const defaultPath = (currentDir === "/" ? "" : currentDir.slice(1) + "/") + "new.md";
      closeModal();
      openKbEditorModal(normalizeKbPath(defaultPath), "", { path: normalizeKbPath(defaultPath), declared_by: "config-center", indexed: true, tags: [] });
    }
  };
  if (state._kbHandler) document.removeEventListener("keydown", state._kbHandler);
  state._kbHandler = kbHandler;
  document.addEventListener("keydown", kbHandler);

  // ── Pending tasks display ──
  if (state.kbTasks && state.kbTasks.length > 0) {
    const taskBox = el("div", "list-box");
    taskBox.style.maxHeight = "150px";
    for (const t of state.kbTasks) {
      const row = el("div", "list-row");
      row.style.fontSize = "12px";
      row.style.padding = "4px 8px";
      const type = String(t.type || "?").slice(0, 20);
      const target = String(t.target || "-").slice(0, 40);
      const status = String(t.status || "waiting");
      const created = t.created_at ? new Date(t.created_at).toLocaleString() : "";
      const statusColor = status === "done" ? "#2c9158" : status === "running" ? "#3f6be8" : status === "error" ? "#c00" : "#888";
      row.innerHTML = `<span style="color:${statusColor};font-weight:bold">${status}</span> ${type} ${created ? "| " + created : ""}<br><span class="mono">${target}</span>`;
      taskBox.append(row);
    }
    addSection("待处理任务 (" + state.kbTasks.length + ")", [taskBox]);
  }
  // ── Import ──
  const importBar = el("div", "toolbar");
  importBar.append(makeButton("导入外部文件", async () => {
    const filePath = await window.api.configOpenFile({ title: "选择外部文件" });
    if (!filePath) return;
    const kbPath = window.prompt("KB 路径（可留空自动）") || "";
    await cfgApi("POST", "/faust/memory/declare-update", { file_path: filePath, kb_path: kbPath.trim() || null });
    await ensureModuleData("memory");
    showBanner("success", "文件已导入。");
    refreshModule();
  }));
  addSection("导入", [importBar]);
}

function renderMemoryGraph() {
  const wrap = el("div", "graph-canvas-wrap");
  wrap.id = "graphCanvasWrap";
  addSection("", [wrap]);

  // ── Toolbar ──
  const searchInput = el("input", "input");
  searchInput.placeholder = "搜索实体名称";
  searchInput.style.maxWidth = "200px";
  let statusText = el("span", "card-help", "加载中...");
  let searchResultBox = null;
  const log = function(msg) { console.log("[Graph] " + msg); };

  const tb = el("div", "graph-toolbar");
  tb.append(searchInput, makeButton("搜索", doSearch, "btn btn-primary"), statusText,
    makeButton("\uFF0B放大", () => { if (gc) { gc._viewScale *= 1.2; gc.render(); } }, "btn btn-ghost"),
    makeButton("\u2212缩小", () => { if (gc) { gc._viewScale /= 1.2; gc.render(); } }, "btn btn-ghost"),
    makeButton("适应", () => { if (gc) gc.fitToScreen(); }, "btn btn-ghost"),
    makeButton("显示全部", () => { state.kbGraphSelectedEntityId = ""; state.kbSelectedPath = ""; initGraph(); }, "btn btn-ghost"),
  );
  // ── Depth slider ──
  const depthSlider = document.createElement("input");
  depthSlider.type = "range";
  depthSlider.min = "1";
  depthSlider.max = "10";
  depthSlider.value = "3";
  depthSlider.style.width = "80px";
  depthSlider.style.verticalAlign = "middle";
  const depthLabel = el("span", "card-help", "深度: 3");
  depthSlider.addEventListener("input", () => {
    depthLabel.textContent = "深度: " + depthSlider.value;
  });
  depthSlider.addEventListener("change", () => {
    depthLabel.textContent = "深度: " + depthSlider.value;
    const centerId = state.kbGraphSelectedEntityId || (gc && gc._selectedNode && gc._selectedNode.id) || "";
    log("[depthSlider] centerId=" + centerId + " selPath=" + (state.kbSelectedPath||"") + " value=" + depthSlider.value);
    if (!centerId && !state.kbSelectedPath) {
      statusText.textContent = "请先在树上选中文件或在图谱上点击实体";
      return;
    }
    statusText.textContent = "重新 BFS 深度 " + depthSlider.value + "...";
    if (centerId) {
      doBfsExpand(centerId, (gc._selectedNode && gc._selectedNode.name) || centerId, (gc._selectedNode && gc._selectedNode.entity_type) || "entity");
    } else {
      initGraph();
    }
  });
  tb.append(document.createTextNode(" | "), depthLabel, depthSlider);
  addSection("", [tb]);

  // ── Legend ──
  const legend = el("div", "graph-legend");
  legend.append(el("span", "", "图例: "));
  for (const [k, v] of Object.entries(GRAPH_COLORS)) {
    const dot = el("span", "graph-legend-dot");
    dot.style.background = v;
    const item = el("span", "graph-legend-item");
    item.append(dot, document.createTextNode(" " + k));
    legend.append(item);
  }
  addSection("图例", [legend]);

  // ── Context hint ──
  const ctxHint = el("div", "card-help");
  ctxHint.textContent = state.kbSelectedPath
    ? "基于选中文件 \"" + state.kbSelectedPath + "\" 的实体图谱（2跳展开）"
    : "在树上选中一个文件查看其关联实体图谱";
  addSection("", [ctxHint]);
  // ── Search result list (below graph) ──
  {
    const srWrap = el("div", "");
    srWrap.style.marginTop = "8px";
    searchResultBox = el("div", "list-box");
    searchResultBox.id = "graphSearchResultBox";
    searchResultBox.style.display = "none";
    searchResultBox.style.maxHeight = "280px";
    srWrap.append(searchResultBox);
    addSection("搜索结果", [srWrap]);
  }

  // ── Init graph ──
  let gc = null;
  let allRelations = [];

  async function initGraph() {
    try {
      log("[initGraph] selEntity=" + (state.kbGraphSelectedEntityId||"") + " selPath=" + (state.kbSelectedPath||"") + " depth=" + depthSlider.value);
      statusText.textContent = "请求数据中...";
      let nodes = [];
      let edges = [];
      const selEntity = state.kbGraphSelectedEntityId || "";
      const selPath = state.kbSelectedPath || "";
      // When selEntity is a file path node and selPath is also available,
      // prefer selPath branch for richer entity-children display
      const effectiveEntity = (selEntity && !selEntity.startsWith("path:")) ? selEntity : "";

      if (effectiveEntity) {
        // ── BFS from selected entity ──
        const depth = parseInt(depthSlider.value) || 3;
        log("[initGraph] selEntity BFS: id=" + selEntity + " depth=" + depth);
        const expResp = await cfgApi("GET", "/faust/memory/graph/expand", null, { entity_id: selEntity, depth: depth });
        log("[initGraph] expand returned items=" + (expResp.items||[]).length + " edges=" + (expResp.edges||[]).length);
        const items = expResp.items || [];
        const expEdges = expResp.edges || [];
        nodes.push({ id: selEntity, name: "(中心)", entity_type: "selected", type: "entity" });
        try {
          const nbResp = await cfgApi("GET", "/faust/memory/graph/neighbors", null, { entity_id: selEntity, depth: 0 });
          log("[initGraph] neighbors depth=0 items=" + (nbResp.items||[]).length);
          for (const n of (nbResp.items || [])) {
            if (n.id === selEntity) { nodes[0].name = n.name || "(中心)"; nodes[0].entity_type = n.entity_type || n.type || "selected"; nodes[0].description = n.description; break; }
          }
        } catch (_) {}
        const seenIds = new Set([selEntity]);
        for (const n of items) {
          if (!seenIds.has(n.id)) {
            seenIds.add(n.id);
            nodes.push({ id: n.id, name: n.name, entity_type: n.entity_type || n.type, type: "entity", description: n.description });
          }
        }
        for (const e of expEdges) {
          edges.push({ source: e.source, target: e.target, type: e.type, key: e.key || e.source + "->" + e.target });
        }
      } else if (selPath) {
        log("[initGraph] selPath branch: " + selPath);
        const [entResp] = await Promise.all([
          cfgApi("GET", "/faust/memory/graph/entity-children", null, { path: selPath })
        ]);
        const entChildren = entResp.items || [];
        log("[initGraph] entity-children count=" + entChildren.length);
        if (entChildren.length === 0) {
          // 无实体子节点：回退到显示文件节点本身的 BFS 邻域
          const fileNid = "path:/" + selPath.replace(/^\//, "");
          const depth = parseInt(depthSlider.value) || 3;
          log("[initGraph] fallback: expanding file node " + fileNid + " depth=" + depth);
          try {
            const expResp = await cfgApi("GET", "/faust/memory/graph/expand", null, { entity_id: fileNid, depth: depth });
            log("[initGraph] fallback expand items=" + (expResp.items||[]).length + " edges=" + (expResp.edges||[]).length);
            const items = expResp.items || [];
            const expEdges = expResp.edges || [];
            const fileName = selPath.split("/").pop() || selPath;
            const seenIds = new Set([fileNid]);
            nodes.push({ id: fileNid, name: fileName, entity_type: "file", type: "entity" });
            for (const n of items) {
              if (!seenIds.has(n.id)) {
                seenIds.add(n.id);
                nodes.push({ id: n.id, name: n.name, entity_type: n.entity_type || n.type, type: "entity", description: n.description });
              }
            }
            const seenEdgeKeys = new Set();
            for (const e of expEdges) {
              const ek = e.key || e.source + "->" + e.target;
              if (!seenEdgeKeys.has(ek)) {
                seenEdgeKeys.add(ek);
                edges.push({ source: e.source, target: e.target, type: e.type, key: ek });
              }
            }
          } catch (_) {}
        } else {
        const [entResp] = await Promise.all([
          cfgApi("GET", "/faust/memory/graph/entity-children", null, { path: selPath })
        ]);
        const entChildren = entResp.items || [];
        const seenIds = new Set();
        const seenEdgeKeys = new Set();
        for (const ent of entChildren) {
          if (seenIds.has(ent.id)) continue;
          seenIds.add(ent.id);
          nodes.push({ id: ent.id, name: ent.name, entity_type: ent.entity_type, type: "entity", description: ent.description });
          try {
            const expResp = await cfgApi("GET", "/faust/memory/graph/expand", null, { entity_id: ent.id, depth: parseInt(depthSlider.value) || 3 });
            for (const n of expResp.items || []) {
              if (!seenIds.has(n.id)) {
                seenIds.add(n.id);
                nodes.push({ id: n.id, name: n.name, entity_type: n.entity_type || n.type, type: "entity", description: n.description });
              }
            }
            for (const e of expResp.edges || []) {
              const ek = e.key || e.source + "->" + e.target;
              if (!seenEdgeKeys.has(ek)) {
                seenEdgeKeys.add(ek);
                edges.push({ source: e.source, target: e.target, type: e.type, key: ek });
              }
            }
          } catch (_) {}
        }
        try {
          const relResp = await cfgApi("GET", "/faust/memory/graph/relations");
          for (const r of relResp.items || []) {
            if ((seenIds.has(r.source) || seenIds.has(r.target))) {
              const ek = r.key || r.source + "->" + r.target;
              if (!seenEdgeKeys.has(ek)) {
                seenEdgeKeys.add(ek);
                edges.push({ source: r.source, target: r.target, type: r.type, key: ek });
                for (const side of [r.source, r.target]) {
                  if (!seenIds.has(side)) {
                    seenIds.add(side);
                    try {
                      const nbResp = await cfgApi("GET", "/faust/memory/graph/neighbors", null, { entity_id: side, depth: 0 });
                      const nb = nbResp.items || [];
                      for (const n of nb) {
                        if (!seenIds.has(n.id)) {
                          seenIds.add(n.id);
                          nodes.push({ id: n.id, name: n.name, entity_type: n.entity_type || "custom", type: "entity", description: n.description });
                        }
                      }
                    } catch (_) {}
                  }
                }
              }
            }
          }
        } catch (_) {}
        }
      } else {
        const [fullData] = await Promise.all([
          cfgApi("GET", "/faust/memory/graph/full"),
        ]);
        const allEntities = fullData.entities || [];
        const allRelations = fullData.relations || [];
        nodes = allEntities.map(function (e) {
          return { id: e.id, name: e.name, entity_type: e.entity_type || e.type, type: "entity" };
        });
        edges = allRelations.map(function (r) {
          return { source: r.source, target: r.target, type: r.type, key: r.key };
        });
      }

      if (!gc) {
        gc = new GraphCanvas(wrap);
      }
      gc.setData(nodes, edges);
      statusText.textContent = "实体: " + nodes.length + " | 关系: " + edges.length;

      gc.onNodeClick(function (node) {
        gc._selectedNode = node;
        gc.render();
        log("[onNodeClick] id=" + node.id + " name=" + node.name + " depth=" + depthSlider.value);
        state.kbGraphSelectedEntityId = node.id;
        statusText.textContent = "BFS展开 " + node.name + "...";
        doBfsExpand(node.id, node.name, node.entity_type || "entity");
      });

      if (selEntity) {
        gc.focusNode(selEntity);
      }
      gc.fitToScreen();
    } catch (e) {
      statusText.textContent = "加载失败: " + (e.message || e);
    }
  }

  // ── BFS expand helper (shared by node click, depth slider, search) ──
  async function doBfsExpand(centerId, centerName, centerType) {
    const depth = parseInt(depthSlider.value) || 3;
    log("[doBfsExpand] center=" + centerId + " depth=" + depth);
    try {
      const expResp = await cfgApi("GET", "/faust/memory/graph/expand", null, { entity_id: centerId, depth: depth });
      log("[doBfsExpand] items=" + (expResp.items||[]).length + " edges=" + (expResp.edges||[]).length);
      const items = expResp.items || [];
      const rawEdges = expResp.edges || [];
      const allNodes = [{ id: centerId, name: centerName, entity_type: centerType || "entity", type: "entity" }];
      const seenIds = new Set([centerId]);
      for (const n of items) {
        if (!seenIds.has(n.id)) {
          seenIds.add(n.id);
          allNodes.push({ id: n.id, name: n.name, entity_type: n.entity_type || n.type, type: "entity", description: n.description });
        }
      }
      const cleanEdges = [];
      const seenEdgeKeys = new Set();
      for (const e of rawEdges) {
        const ek = e.key || e.source + "->" + e.target;
        if (!seenEdgeKeys.has(ek)) {
          seenEdgeKeys.add(ek);
          cleanEdges.push({ source: e.source, target: e.target, type: e.type, key: ek });
        }
      }
      gc.clearExpanded();
      gc.setData(allNodes, cleanEdges);
      gc.focusNode(centerId);
      statusText.textContent = "实体: " + allNodes.length + " | 关系: " + cleanEdges.length + " (深度: " + depth + ")";
    } catch (_) { statusText.textContent = "BFS失败"; }
  }

  // ── Graph entity search ──
  async function doSearch() {
    const q = searchInput.value.trim();
    if (!q || !gc) return;
    log("[doSearch] query=" + q);
    statusText.textContent = "搜索中...";
    searchResultBox.innerHTML = "";
    try {
      const data = await cfgApi("GET", "/faust/memory/graph/search", null, { query: q, top_k: 20 });
      const items = data.items || [];
      log("[doSearch] results=" + items.length);
      const ids = items.map(function (it) { return it.id; });
      gc.highlightIds(ids);
      if (items.length) {
        searchResultBox.style.display = "block";
        for (const it of items) {
          const row = el("div", "list-row clickable");
          row.style.display = "flex";
          row.style.alignItems = "center";
          row.style.padding = "6px 10px";
          const left = el("div", "field-wrap");
          left.style.flex = "1";
          const entIcon = GRAPH_COLORS[it.entity_type] ? ("<span style=\"display:inline-block;width:10px;height:10px;border-radius:50%;background:" + GRAPH_COLORS[it.entity_type] + ";margin-right:4px\"></span>") : "";
          left.innerHTML = [
            "<div class=\"mono\">" + entIcon + "[ENT:" + (it.entity_type || "entity") + "] " + it.name + "</div>",
            it.description ? "<div class=\"card-help\">" + it.description.slice(0, 120) + "</div>" : "",
          ].join("");
          const detailBtn = makeButton("详情", async (evt) => {
            evt.stopPropagation();
            openEntityDetailModal(it.id);
          }, "btn btn-ghost");
          detailBtn.style.fontSize = "11px";
          detailBtn.style.padding = "2px 8px";
          detailBtn.style.flexShrink = "0";
          row.append(left, detailBtn);
          row.addEventListener("click", () => {
            state.kbGraphSelectedEntityId = it.id;
            statusText.textContent = "BFS展开 " + it.name + "...";
            doBfsExpand(it.id, it.name, it.entity_type || "entity");
          });
          searchResultBox.append(row);
        }
        // Focus first result
        const first = items[0];
        state.kbGraphSelectedEntityId = first.id;
        gc.focusNode(first.id);
        doBfsExpand(first.id, first.name, first.entity_type || "entity");
      } else {
        searchResultBox.style.display = "none";
        statusText.textContent = "未找到匹配实体";
      }
    } catch (e) {
      statusText.textContent = "搜索失败";
    }
  }
  searchInput.addEventListener("keydown", function (evt) { if (evt.key === "Enter") doSearch(); });

  initGraph();
}

function renderMemorySearch() {
  const searchInput = el("input", "input");
  searchInput.placeholder = "输入关键词（可选）";
  searchInput.style.flex = "1";
  const resultBox = el("div", "list-box");
  resultBox.style.maxHeight = "500px";

  const iconByExt = (p) => /\.(png|jpg|jpeg|gif|webp|bmp)$/i.test(p) ? "\u{1F5BC}" : "\u{1F4C4}";

  const openResult = async (path) => {
    state.kbCurrentDir = kbParentPath(normalizeKbPath(path));
    state.kbSelectedPath = normalizeKbPath(path);
    state.memoryView = "tree";
    const d = await cfgApi("GET", "/faust/memory/get", null, { path: state.kbSelectedPath });
    state.kbSelectedContent = String(d.content || "");
    state.kbSelectedMeta = d.meta || {};
    refreshModule();
  };

  const filterWrap = el("div");
  filterWrap.style.marginTop = "8px";
  filterWrap.style.padding = "8px";
  filterWrap.style.background = "var(--bg-secondary, #f5f7fa)";
  filterWrap.style.borderRadius = "6px";
  const filterToggle = makeButton("\u25BC 展开筛选", () => {
    const hidden = filterBody.style.display === "none";
    filterBody.style.display = hidden ? "flex" : "none";
    filterToggle.textContent = hidden ? "\u25B2 收起筛选" : "\u25BC 展开筛选";
  }, "btn btn-ghost");
  filterToggle.style.fontSize = "12px";
  const filterBody = el("div");
  filterBody.style.display = "none";
  filterBody.style.flexWrap = "wrap";
  filterBody.style.gap = "8px";
  filterBody.style.marginTop = "6px";
  filterBody.style.alignItems = "end";

  const mkField = (label, inputEl) => {
    const wrap = el("div");
    wrap.style.display = "flex";
    wrap.style.flexDirection = "column";
    wrap.style.gap = "2px";
    const lbl = el("label", "", label);
    lbl.style.fontSize = "11px";
    lbl.style.color = "#666";
    wrap.append(lbl, inputEl);
    return wrap;
  };
  const tagInput = el("input", "input");
  tagInput.placeholder = "标签（逗号分隔）";
  tagInput.style.width = "120px";
  const scopeInput = el("input", "input");
  scopeInput.placeholder = "目录路径";
  scopeInput.style.width = "100px";
  scopeInput.value = state.kbCurrentDir && state.kbCurrentDir !== "/" ? state.kbCurrentDir : "";
  const dateFrom = document.createElement("input");
  dateFrom.type = "date";
  dateFrom.style.width = "130px";
  const dateTo = document.createElement("input");
  dateTo.type = "date";
  dateTo.style.width = "130px";
  const dateWrap = el("div");
  dateWrap.style.display = "flex";
  dateWrap.style.alignItems = "center";
  dateWrap.style.gap = "4px";
  dateWrap.append(dateFrom, el("span", "", "~"), dateTo);
  const byInput = el("input", "input");
  byInput.placeholder = "创建者";
  byInput.style.width = "100px";
  const sortSelect = el("select", "select");
  sortSelect.style.width = "100px";
  for (const [v, l] of [["relevance", "相关性"], ["updated_at", "更新时间"], ["created_at", "创建时间"]]) {
    const opt = document.createElement("option");
    opt.value = v; opt.textContent = l; sortSelect.append(opt);
  }
  const sortOrderSelect = el("select", "select");
  sortOrderSelect.style.width = "80px";
  for (const [v, l] of [["desc", "降序"], ["asc", "升序"]]) {
    const opt = document.createElement("option");
    opt.value = v; opt.textContent = l; sortOrderSelect.append(opt);
  }
  const tagLogicSelect = el("select", "select");
  tagLogicSelect.style.width = "60px";
  for (const [v, l] of [["AND", "AND"], ["OR", "OR"]]) {
    const opt = document.createElement("option");
    opt.value = v; opt.textContent = l; tagLogicSelect.append(opt);
  }
  filterBody.append(
    mkField("标签", tagInput), mkField("逻辑", tagLogicSelect),
    mkField("目录", scopeInput), mkField("日期范围", dateWrap),
    mkField("创建者", byInput), mkField("排序", sortSelect),
    mkField("顺序", sortOrderSelect),
  );
  filterWrap.append(filterToggle, filterBody);

  const highlightInText = (text, query) => {
    if (!text || !query) return text || "";
    const q = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    return String(text).replace(new RegExp(`(${q})`, 'gi'), '<mark>$1</mark>');
  };

  const doSearch = async () => {
    const q = searchInput.value.trim() || null;
    const tagsStr = tagInput.value.trim();
    const tags = tagsStr ? tagsStr.split(",").map(t => t.trim()).filter(Boolean) : null;
    const scope = scopeInput.value.trim() || null;
    const dateFromVal = dateFrom.value || null;
    const dateToVal = dateTo.value || null;
    const sortBy = sortSelect.value;
    const sortOrder = sortOrderSelect.value;
    const tagLogic = tagLogicSelect.value;
    resultBox.innerHTML = `<div class="empty-state">搜索中...</div>`;
    try {
      const declaredByVal = byInput.value.trim() || null;
      const payload = { query: q, tags, scope, date_from: dateFromVal, date_to: dateToVal, declared_by: declaredByVal, sort_by: sortBy, sort_order: sortOrder, tag_logic: tagLogic, top_k: 20 };
      const data = await cfgApi("POST", "/faust/memory/advanced-search", payload);
      resultBox.innerHTML = "";
      const items = data.items || [];
      if (!items.length) {
        const emptyMsg = el("div", "empty-state", "未找到匹配内容。");
        // 尝试获取KB文件总数作为诊断
        (async () => {
          try {
            const treeData = await cfgApi("GET", "/faust/memory/tree");
            if (treeData && treeData.tree) {
              const countFiles = (nodes) => {
                let n = 0;
                for (const c of nodes || []) {
                  if (c.type === "file") n++;
                  if (c.children) n += countFiles(c.children);
                }
                return n;
              };
              const total = countFiles([treeData.tree]);
              const hint = el("div", "card-help", `KB 中共有 ${total} 个文件。`);
              hint.style.marginTop = "8px";
              resultBox.append(hint);
            }
          } catch (_) {}
        })();
        resultBox.append(emptyMsg);
        return;
      }
      for (const it of items) {
        const row = el("div", "list-row clickable");
        row.style.padding = "10px 12px";
        const left = el("div", "field-wrap");
        const scoreStr = it.score != null && it.score > 0 ? " score=" + it.score.toFixed(2) : "";
        const lcStr = it.line_count > 0 ? it.line_count + " 行" : "";
        const pathHtml = highlightInText(it.path, q);
        const descHtml = highlightInText(it.description, q);
        const tagsHtml = (it.tags || []).map(t => `<span class="tag-chip" style="font-size:10px;padding:1px 6px">${t}</span>`).join(" ");
        left.innerHTML = [
          `<div class="mono">${iconByExt(it.path)} ${pathHtml}</div>`,
          `<div class="card-help" style="font-size:11px">${[lcStr, scoreStr, it.updated_at ? new Date(it.updated_at).toLocaleDateString() : "", it.declared_by ? "by " + it.declared_by : ""].filter(Boolean).join(" | ")}</div>`,
          descHtml ? `<div class="card-help">${descHtml.slice(0, 200)}</div>` : "",
          tagsHtml ? `<div style="margin-top:2px">${tagsHtml}</div>` : "",
        ].join("");
        row.append(left);
        const graphBtn = makeButton("🔗 实体", async () => {
          state.kbSelectedPath = normalizeKbPath(it.path);
          state.memoryView = "graph";
          try {
            const entResp = await cfgApi("GET", "/faust/memory/graph/entity-children", null, { path: state.kbSelectedPath });
            const entities = entResp.items || [];
            state.kbGraphSelectedEntityId = entities.length > 0 ? entities[0].id : "";
          } catch (_) { state.kbGraphSelectedEntityId = ""; }
          refreshModule();
        }, "btn btn-ghost");
        graphBtn.style.marginLeft = "auto";
        graphBtn.style.fontSize = "11px";
        graphBtn.style.padding = "2px 8px";
        graphBtn.style.flexShrink = "0";
        row.style.display = "flex";
        row.style.alignItems = "center";
        row.append(graphBtn);
        row.addEventListener("click", () => openResult(it.path));
        resultBox.append(row);
      }
    } catch (err) { resultBox.innerHTML = `<div class="empty-state">搜索失败: ${err}</div>`; }
  };
  searchInput.addEventListener("keydown", (evt) => { if (evt.key === "Enter") doSearch(); });

  const clearFilters = () => {
    searchInput.value = "";
    tagInput.value = "";
    scopeInput.value = "";
    dateFrom.value = ""; dateTo.value = "";
    byInput.value = "";
    sortSelect.value = "relevance";
    sortOrderSelect.value = "desc";
    tagLogicSelect.value = "AND";
    resultBox.innerHTML = "";
  };

  const bar = el("div", "toolbar");
  bar.append(searchInput, makeButton("搜索", doSearch, "btn btn-primary"), makeButton("清除", clearFilters, "btn btn-ghost"));
  addSection("多条件搜索", [bar, filterWrap, resultBox]);
}

// ── Entity detail modal (shared by graph search + tree view) ──
async function openEntityDetailModal(entityId) {
  try {
    const resp = await cfgApi("GET", "/faust/memory/graph/entity-detail", null, { entity_id: entityId });
    const detail = (resp && resp.detail) ? resp.detail : null;
    if (!detail) { showBanner("error", "无法加载实体详情"); return; }
    const content = el("div");
    content.style.maxHeight = "60vh";
    content.style.overflowY = "auto";
    // Basic info table
    const infoGrid = el("div", "info-grid");
    infoGrid.style.marginBottom = "12px";
    const addRow = (label, value) => {
      if (!value && value !== 0) return;
      const row = el("div", "info-item");
      row.innerHTML = "<span class=\"info-key\">" + label + "</span><span class=\"info-value\">" + value + "</span>";
      infoGrid.append(row);
    };
    const typeColor = GRAPH_COLORS[detail.entity_type] || "#888";
    const typeBadge = "<span style=\"display:inline-block;width:12px;height:12px;border-radius:50%;background:" + typeColor + ";vertical-align:middle;margin-right:4px\"></span>";
    addRow("名称", "<b>" + typeBadge + " " + (detail.name || "(未命名)") + "</b>");
    addRow("类型", detail.entity_type || "custom");
    addRow("关系数", String(detail.relations_count || 0));
    addRow("创建时间", detail.created_at || "-");
    if (detail.description) {
      const descWrap = el("div");
      descWrap.style.marginTop = "8px";
      descWrap.innerHTML = "<div class=\"card-help\">" + detail.description + "</div>";
      content.append(infoGrid, descWrap);
    } else {
      content.append(infoGrid);
    }
    // Properties
    const props = detail.properties || {};
    const propKeys = Object.keys(props);
    if (propKeys.length) {
      const propTitle = el("h4", "", "属性");
      propTitle.style.margin = "12px 0 4px";
      const propGrid = el("div", "info-grid");
      for (const k of propKeys) {
        const row = el("div", "info-item");
        row.innerHTML = "<span class=\"info-key\">" + k + "</span><span class=\"info-value\">" + props[k] + "</span>";
        propGrid.append(row);
      }
      content.append(propTitle, propGrid);
    }
    // Linked files
    const files = detail.linked_files || [];
    if (files.length) {
      const fileTitle = el("h4", "", "关联文件 (" + files.length + ")");
      fileTitle.style.margin = "12px 0 4px";
      const fileList = el("div", "list-box");
      fileList.style.maxHeight = "160px";
      for (const f of files) {
        const frow = el("div", "list-row clickable");
        frow.textContent = f;
        frow.style.padding = "4px 8px";
        frow.style.fontSize = "12px";
        frow.addEventListener("click", () => {
          closeModal();
          state.kbSelectedPath = normalizeKbPath(f);
          state.kbCurrentDir = kbParentPath(state.kbSelectedPath);
          state.memoryView = "tree";
          refreshModule();
        });
        fileList.append(frow);
      }
      content.append(fileTitle, fileList);
    }
    // Actions
    const actionBar = el("div", "toolbar");
    actionBar.style.marginTop = "12px";
    actionBar.append(
      makeButton("在图中查看", () => {
        closeModal();
        state.kbGraphSelectedEntityId = entityId;
        state.memoryView = "graph";
        refreshModule();
      }, "btn btn-primary"),
      makeButton("关闭", closeModal),
    );
    content.append(actionBar);
    openModal("实体详情: " + (detail.name || entityId), [content]);
  } catch (err) {
    showBanner("error", "加载实体详情失败: " + (err.message || err));
  }
}
