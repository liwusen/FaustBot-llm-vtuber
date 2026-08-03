// ═══════════════════════════════════════════════════════════════════
// App dispatcher — orchestration layer (kept in main file)
// ═══════════════════════════════════════════════════════════════════

async function renderModule(force = false) {
  const current = MODULES.find((m) => m.id === state.activeModule) || MODULES[0];
  els.moduleTitle.textContent = current.title;
  els.moduleDesc.textContent = current.desc;

  const container = getModuleContainer(current.id);
  switchModule(current.id);
  const boot = document.getElementById("bootPlaceholder");
  if (boot) boot.style.display = "none";


  // 持久模块（白名单）已渲染且非强制 → 只切换显示
  // 非持久模块始终重新渲染（容器已在 switchModule 离开时清空）
  if (!force && PERSISTENT_MODULES.includes(current.id) && state.moduleContainers[current.id] && state.moduleContainers[current.id].rendered) return;

  // 首次渲染或强制刷新：清空容器，设置激活容器，调用渲染函数
  container.innerHTML = "";
  setActiveContainer(container);

  try {
    await ensureModuleData(current.id);
    if (current.id === "overview") {
      renderOverviewModule();
    } else if (["ai", "live2d", "speech"].includes(current.id)) {
      renderConfigModule(current.id);
    } else if (current.id === "advanced") {
      renderConfigModule(current.id);
    } else if (current.id === "agent") {
      renderAgentModule();
    } else if (current.id === "memory") {
      renderMemoryModule();
    } else if (current.id === "araya") {
      renderArayaModule();
    } else if (current.id === "runtime") {
      renderRuntimeModule();
    } else if (current.id === "triggers") {
      renderTriggersModule();
    } else if (current.id === "skills") {
      renderSkillsModule();
    } else if (current.id === "plugins") {
      renderPluginsModule();
    } else if (current.id === "components") {
      renderComponentsModule();
    } else if (current.id === "mcp") {
      renderMcpModule();
    } else {
      // Plugin module: use page render function from addPage(), or cards from addCard()
      const pluginPage = window.pluginUI._pages.find(p => p.id === current.id);
      if (pluginPage && typeof pluginPage.render === 'function') {
        const container = getModuleContainer(current.id);
        setActiveContainer(container);
        pluginPage.render(container);
      } else {
        const pluginCards = window.pluginUI._cards.filter(c => c.moduleId === current.id);
        if (pluginCards.length > 0) {
          for (const card of pluginCards) {
            if (typeof card.render === 'function') {
              const cardContainer = el("div");
              card.render(cardContainer);
              addSection(card.title || "插件卡片", [cardContainer]);
            } else {
              addSection(card.title || "插件卡片", [el("div", "card-content", card.content || card.html || "")]);
            }
          }
        } else {
          renderSimpleJsonModule("数据", state);
        }
      }
    }
    state.moduleContainers[current.id].rendered = true;
  } catch (err) {
    addSection("错误", [el("div", "empty-state", `模块加载失败: ${String(err && err.message ? err.message : err)}`)]);
  }
}

function renderNav() {
  els.moduleNav.innerHTML = "";
  for (const mod of MODULES) {
    const btn = el("button", `nav-btn ${state.activeModule === mod.id ? "active" : ""}`.trim());
    btn.type = "button";
    btn.innerHTML = `<span>${mod.title}</span><small>${mod.desc}</small>`;
    btn.addEventListener("click", async () => {
      state.activeModule = mod.id;
      renderNav();
      await renderModule();
    });
    els.moduleNav.append(btn);
  }
}

function resetDirty() {
  state.dirty.public.clear();
  state.dirty.private.clear();
  state.dirty.providers = false;
  state.dirty.mainModel = false;
  state.dirty.subagentModels = false;
  refreshDirtyUI();
}

async function loadConfig() {
  const data = await cfgApi("GET", "/faust/admin/config");
  state.config = { public: { ...(data.public || {}) }, private: { ...(data.private || {}) } };
  state.original = { public: { ...(data.public || {}) }, private: { ...(data.private || {}) } };
  resetDirty();
}

async function loadRuntimeSummary() {
  const data = await cfgApi("GET", "/faust/admin/runtime");
  state.runtime = data.runtime || {};
}

// ── Plugin 前端资源加载 ──

const BASE_MODULE_IDS = new Set(MODULES.map((m) => m.id));

function resetPluginUiState() {
  document.querySelectorAll('script[data-plugin], link[data-plugin]').forEach((node) => node.remove());
  if (window.pluginUI) {
    window.pluginUI._pages = [];
    window.pluginUI._cards = [];
  }
  for (let index = MODULES.length - 1; index >= 0; index--) {
    if (!BASE_MODULE_IDS.has(MODULES[index].id)) MODULES.splice(index, 1);
  }
  for (const [moduleId, entry] of Object.entries(state.moduleContainers || {})) {
    if (!BASE_MODULE_IDS.has(moduleId)) {
      try { entry.div.remove(); } catch (e) {}
      delete state.moduleContainers[moduleId];
    }
  }
}

async function loadPluginAssets(forceReload = false) {
  try {
    if (forceReload) resetPluginUiState();
    if (!window.api || typeof window.api.configRequest !== "function") {
      console.warn("[loadPluginAssets] window.api.configRequest not available");
      return;
    }
    const data = await window.api.configRequest("GET", "/faust/admin/plugins/assets");
    if (!data) return;
    const assets = data.assets || [];
    const baseUrl = window.api.backendBaseUrl || "http://127.0.0.1:13900";
    // Expose backend base URL to plugins so they can make API calls
    if (window.pluginUI) window.pluginUI.backendBaseUrl = baseUrl;
    const loadPromises = [];
    const cacheBust = "v=" + Date.now();
    for (const a of assets) {
      if (a.type === "js" && a.path) {
        const s = document.createElement("script");
        s.src = baseUrl + a.path + (a.path.includes("?") ? "&" : "?") + cacheBust;
        s.setAttribute("data-plugin", a.plugin_id || "");
        const p = new Promise((resolve, reject) => {
          s.onload = () => { console.log("[plugin] loaded JS:", a.path); resolve(); };
          s.onerror = () => { console.warn("[plugin] failed to load JS:", a.path); resolve(); };
        });
        loadPromises.push(p);
        document.head.appendChild(s);
      } else if (a.type === "css" && a.path) {
        const l = document.createElement("link");
        l.rel = "stylesheet";
        l.href = baseUrl + a.path + (a.path.includes("?") ? "&" : "?") + cacheBust;
        l.setAttribute("data-plugin", a.plugin_id || "");
        document.head.appendChild(l);
      }
    }
    // Wait for all plugin scripts to load before allowing renderModule to run
    if (loadPromises.length > 0) {
      await Promise.all(loadPromises);
      console.log("[plugin] all JS assets loaded");
    }
  } catch (e) {
    console.warn("[loadPluginAssets] Error:", e);
  }
}

// ── pluginUI API（插件注入前台页面/卡片用） ──

if (!window.pluginUI) {
  window.pluginUI = {
    _pages: [],
    _cards: [],
    backendBaseUrl: (window.api && window.api.backendBaseUrl) || "http://127.0.0.1:13900",

    addPage(spec) {
      if (!spec || !spec.id || !spec.label) return;
      // 去重：同名插件同 id 不重复注册
      const key = `${spec.plugin || ""}:${spec.id}`;
      if (this._pages.find((p) => p._key === key)) return;
      this._pages.push({ ...spec, _key: key });
      // 动态注入 MODULES 列表
      if (typeof MODULES !== "undefined" && !MODULES.find((m) => m.id === spec.id)) {
        MODULES.push({ id: spec.id, title: spec.label, desc: spec.desc || "" });
        renderNav();
      }
    },

    addCard(moduleId, spec) {
      if (!moduleId || !spec || !spec.title) return;
      const key = `${spec.plugin || ""}:${moduleId}:${spec.title}`;
      if (this._cards.find((c) => c._key === key)) return;
      this._cards.push({ ...spec, moduleId, _key: key });
    },

    modifyPage(moduleId, fn) {
      if (typeof fn !== "function") return;
      fn(MODULES.find((m) => m.id === moduleId));
    },

    communicate(pluginId, payload) {
      if (!window.api || typeof window.api.configRequest !== "function") {
        return Promise.reject(new Error("window.api.configRequest 未实现"));
      }
      return window.api.configRequest(
        "POST",
        `/faust/plugins/${encodeURIComponent(String(pluginId || ""))}/communicate`,
        payload ?? {}
      );
    },

    communicateSSE(pluginId, params) {
      const base = (window.api && window.api.backendBaseUrl) || "http://127.0.0.1:13900";
      const query = new URLSearchParams(params || {}).toString();
      const url = `${base}/faust/plugins/${encodeURIComponent(String(pluginId || ""))}/sse-communicate${query ? "?" + query : ""}`;
      return new EventSource(url);
    },
  };
}

async function reloadAll() {
  setBusy(true);
  try {
    await loadConfig();
    await loadRuntimeSummary();
    hideBanner();
    invalidateAllContainers();
    await renderModule(true);
  } catch (err) {
    showBanner("error", `刷新失败: ${String(err && err.message ? err.message : err)}`);
  } finally {
    setBusy(false);
  }
}

async function saveConfig() {
  const dirtyCount = state.dirty.public.size + state.dirty.private.size
    + (state.dirty.providers ? 1 : 0)
    + (state.dirty.mainModel ? 1 : 0)
    + (state.dirty.subagentModels ? 1 : 0);
  if (dirtyCount <= 0) {
    showBanner("info", "当前没有未保存更改。");
    return;
  }
  setBusy(true);
  try {
    const mdBlockChanged = state.dirty.public.has("MD_BLOCK_ENABLED");
    const payload = {
      public: Object.fromEntries(state.dirty.public.entries()),
      private: Object.fromEntries(state.dirty.private.entries()),
    };
    // [统一保存] provider 配置随公共配置一起提交
    if (state.dirty.providers || state.dirty.mainModel || state.dirty.subagentModels) {
      payload.providers = state.providers;
      payload.main_model = state.mainModel;
      payload.subagent_models = state.subagentModels;
    }
    await cfgApi("POST", "/faust/admin/config", payload);
    const hasLive2D = LIVE2D_KEYS.some((k) => state.dirty.public.has(k));
    if (hasLive2D) {
      await cfgApi("POST", "/faust/admin/live2d/apply", {
        public: Object.fromEntries([...state.dirty.public.entries()].filter(([k]) => LIVE2D_KEYS.includes(k))),
      });
    }
    if (mdBlockChanged) {
      await cfgApi("POST", "/faust/admin/config/reload", { reset_dialog: false, no_initial_chat: true });
    }
    await loadConfig();
    await loadRuntimeSummary();
    invalidateAllContainers();
    await renderModule(true);
    showBanner("success", "配置已保存。" );
  } catch (err) {
    showBanner("error", `保存失败: ${String(err && err.message ? err.message : err)}`);
  } finally {
    setBusy(false);
  }
}

async function applyRuntime() {
  setBusy(true);
  try {
    await cfgApi("POST", "/faust/admin/config/reload", { reset_dialog: false, no_initial_chat: true });
    await loadRuntimeSummary();
    showBanner("success", "运行时已刷新，配置已应用。" );
  } catch (err) {
    showBanner("error", `应用失败: ${String(err && err.message ? err.message : err)}`);
  } finally {
    setBusy(false);
  }
}

async function reloadFromDisk() {
  const dirtyCount = state.dirty.public.size + state.dirty.private.size
    + (state.dirty.providers ? 1 : 0)
    + (state.dirty.mainModel ? 1 : 0)
    + (state.dirty.subagentModels ? 1 : 0);
  if (dirtyCount > 0) {
    const ok = window.confirm("当前有未保存修改，继续重新加载会丢失这些修改。是否继续？");
    if (!ok) return;
  }
  await reloadAll();
  showBanner("info", "已重新读取配置。" );
}

async function applyTtsReferToService() {
  try {
    const mode = String(state.config.public.TTS_MODE || "gpt-sovits").toLowerCase();
    if (mode !== "gpt-sovits") {
      showBanner("info", "当前 TTS 模式不是 gpt-sovits，无法应用参考音频到本地 TTS 服务。" );
      return;
    }
    await apiLocal("POST", "http://127.0.0.1:5000/change_refer", {
      refer_wav_path: state.config.public.TTS_REFER_WAV_PATH,
      prompt_text: state.config.public.TTS_PROMPT_TEXT,
      prompt_language: state.config.public.TTS_PROMPT_LANGUAGE,
    });
    showBanner("success", "TTS 参考音频已同步到本地服务。" );
  } catch (err) {
    showBanner("error", `同步 TTS 服务失败: ${String(err && err.message ? err.message : err)}`);
  }
}

function bindActions() {
  els.saveBtn.addEventListener("click", saveConfig);
  els.applyBtn.addEventListener("click", applyRuntime);
  els.reloadBtn.addEventListener("click", reloadFromDisk);
  window.addEventListener("keydown", (evt) => {
    if (evt.key === "Escape") closeModal();
  });
}

async function init() {
  renderNav();
  bindActions();
  await loadPluginAssets();
  await reloadAll();
}

init();