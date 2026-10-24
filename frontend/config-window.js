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
    btn.setAttribute("data-module", mod.id);
    btn.innerHTML = `<span>${mod.title}</span><small>${mod.nav || mod.desc}</small>`;
    btn.addEventListener("click", async () => {
      state.activeModule = mod.id;
      renderNav();
      await renderModule();
      if (els.cardsRoot) els.cardsRoot.scrollTop = 0;
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
    if (typeof window.pluginUI.resetPersistence === "function") window.pluginUI.resetPersistence();
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
    // 配置中心统一保存：插件注册的持久化钩子与未保存标记
    _persist: [],          // [{ plugin, module, save, apply, reload, _key }]
    _persistDirty: new Set(),  // 有未保存修改的插件 id
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

    // ── 配置中心统一保存（保存 / 应用 / 重新加载） ──
    //
    // 插件页面在 render() 里调用，把自己面板上的草稿交给右上角三个按钮：
    //   const persist = api.registerPersistence({
    //     plugin: 'my-plugin',        // 必需：插件 id（去重与报错用）
    //     module: 'my-plugin',        // 可选：缺省 = plugin；离开该页面时自动注销
    //     save:   async (ctx) => {...}, // 「保存」：把草稿落盘（ctx.dirty=false 时直接 return）
    //     apply:  async (ctx) => {...}, // 「应用」：让已保存内容立即生效（可选）
    //     reload: async (ctx) => {...}, // 「重新加载」：丢弃草稿并重新读取（可选）
    //   });
    //   persist.setDirty(true);      // 报告未保存修改（计入右上角 dirty 徽标）
    //
    // ctx = { phase, plugin, module, dirty }；未注册对应阶段的插件不会被该按钮触发。
    // 处理器抛错会按插件失败上报，不会静默吞掉，也不影响其它插件与全局配置的保存。
    registerPersistence(spec) {
      const plugin = String((spec && spec.plugin) || "").trim();
      if (!plugin) throw new Error("registerPersistence 缺少 plugin");
      const moduleId = String((spec && spec.module) || plugin).trim() || plugin;
      const entry = {
        plugin,
        module: moduleId,
        save: typeof spec.save === "function" ? spec.save : null,
        apply: typeof spec.apply === "function" ? spec.apply : null,
        reload: typeof spec.reload === "function" ? spec.reload : null,
        _key: `${plugin}::${moduleId}`,
      };
      // 同一插件同一页面重复注册视为覆盖：页面重渲染后旧处理器必须失效，
      // 否则会对着已销毁的 DOM 保存。
      const index = this._persist.findIndex((item) => item._key === entry._key);
      if (index >= 0) this._persist.splice(index, 1, entry);
      else this._persist.push(entry);
      // 重渲染/首次注册 = 面板按服务端状态重建，旧的未保存标记作废
      this._persistDirty.delete(plugin);
      refreshPluginDirtyUI();
      const self = this;
      return {
        plugin,
        module: moduleId,
        setDirty(value) { self.setPluginDirty(plugin, !!value); },
        dispose() { self.unregisterPersistence(plugin, moduleId); },
      };
    },

    unregisterPersistence(plugin, moduleId) {
      const key = `${plugin}::${moduleId || plugin}`;
      const before = this._persist.length;
      this._persist = this._persist.filter((item) => item._key !== key);
      if (this._persist.length !== before
        && !this._persist.some((item) => item.plugin === plugin)) {
        this._persistDirty.delete(plugin);
      }
      refreshPluginDirtyUI();
    },

    setPluginDirty(plugin, dirty) {
      const id = String(plugin || "").trim();
      if (!id) return;
      if (dirty) this._persistDirty.add(id);
      else this._persistDirty.delete(id);
      refreshPluginDirtyUI();
    },

    dirtyCount() {
      return this._persistDirty.size;
    },

    isPluginDirty(plugin) {
      return this._persistDirty.has(String(plugin || "").trim());
    },

    // 只清除保存成功的插件标记：失败的插件保持 dirty，用户可以重试
    clearDirty(exceptPlugins) {
      const failed = new Set(exceptPlugins || []);
      for (const id of Array.from(this._persistDirty)) {
        if (!failed.has(id)) this._persistDirty.delete(id);
      }
      refreshPluginDirtyUI();
    },

    resetPersistence() {
      this._persist = [];
      this._persistDirty = new Set();
      refreshPluginDirtyUI();
    },

    // 切换页面后注销不可见页面的钩子（插件页 DOM 已被销毁）
    dropPersistenceExcept(activeModuleId) {
      const keep = String(activeModuleId || "");
      const dropped = this._persist.filter((item) => item.module !== keep);
      if (!dropped.length) return;
      this._persist = this._persist.filter((item) => item.module === keep);
      for (const item of dropped) {
        if (!this._persist.some((entry) => entry.plugin === item.plugin)) {
          this._persistDirty.delete(item.plugin);
        }
      }
      refreshPluginDirtyUI();
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

// ── 插件持久化钩子的分发（配置中心统一保存/应用/重新加载） ──

// 只统计全局配置的未保存项（插件草稿由 pluginDirtyCount 单独统计）
function globalDirtyCount() {
  return state.dirty.public.size + state.dirty.private.size
    + (state.dirty.providers ? 1 : 0)
    + (state.dirty.mainModel ? 1 : 0)
    + (state.dirty.subagentModels ? 1 : 0);
}

function pluginDirtyCount() {
  return (window.pluginUI && typeof window.pluginUI.dirtyCount === "function")
    ? window.pluginUI.dirtyCount()
    : 0;
}

function refreshPluginDirtyUI() {
  if (typeof refreshDirtyUI === "function") refreshDirtyUI();
}

/// 依次执行插件注册的 save / apply / reload 钩子；返回失败插件列表（不抛错）
/// 钩子收到的 ctx: { phase, plugin, module, dirty }；dirty=false 时应直接返回，避免无谓写盘
async function runPluginPersistence(phase) {
  const api = window.pluginUI;
  if (!api || !Array.isArray(api._persist)) return [];
  const entries = api._persist.filter((item) => typeof item[phase] === "function");
  if (!entries.length) return [];
  const results = await Promise.all(entries.map(async (entry) => {
    try {
      await entry[phase]({
        phase,
        plugin: entry.plugin,
        module: entry.module,
        dirty: api.isPluginDirty(entry.plugin),
      });
      return null;
    } catch (err) {
      console.error(`[plugin] ${entry.plugin} 的 ${phase} 钩子失败`, err);
      return {
        plugin: entry.plugin,
        error: String((err && err.message) || err),
      };
    }
  }));
  return results.filter(Boolean);
}

function pluginFailuresText(failed) {
  return failed.map((item) => `${item.plugin}（${item.error}）`).join("、");
}

// 离开某个页面时，注销该页面注册的插件钩子（其 DOM 已被销毁）
if (typeof onModuleVisibilityChange === "function") {
  onModuleVisibilityChange((activeModuleId) => {
    if (window.pluginUI && typeof window.pluginUI.dropPersistenceExcept === "function") {
      window.pluginUI.dropPersistenceExcept(activeModuleId);
    }
  });
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
  const globalDirty = globalDirtyCount();
  const pluginDirty = pluginDirtyCount();
  if (globalDirty + pluginDirty <= 0) {
    showBanner("info", "当前没有未保存更改。");
    return;
  }
  setBusy(true);
  try {
    // 插件草稿先各自由插件落盘（互不影响；失败的插件保持 dirty 以便重试）
    const failures = await runPluginPersistence("save");
    if (globalDirty > 0) {
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
    }
    if (window.pluginUI && typeof window.pluginUI.clearDirty === "function") {
      window.pluginUI.clearDirty(failures.map((item) => item.plugin));
    }
    // 只有插件保存失败、又没有全局改动时保留现场：不重渲染，草稿留着让用户改完重试
    if (globalDirty > 0) {
      await loadRuntimeSummary();
      invalidateAllContainers();
      await renderModule(true);
    } else if (!failures.length) {
      invalidateAllContainers();
      await renderModule(true);
    }
    if (failures.length) {
      showBanner("error", `配置已保存，但以下插件保存失败：${pluginFailuresText(failures)}`);
    } else {
      showBanner("success", "配置已保存。" );
    }
  } catch (err) {
    showBanner("error", `保存失败: ${String(err && err.message ? err.message : err)}`);
  } finally {
    setBusy(false);
  }
}

async function applyRuntime() {
  setBusy(true);
  try {
    const failures = await runPluginPersistence("apply");
    await cfgApi("POST", "/faust/admin/config/reload", { reset_dialog: false, no_initial_chat: true });
    await loadRuntimeSummary();
    if (failures.length) {
      showBanner("error", `运行时已刷新，但以下插件应用失败：${pluginFailuresText(failures)}` );
    } else {
      showBanner("success", "运行时已刷新，配置已应用。" );
    }
  } catch (err) {
    showBanner("error", `应用失败: ${String(err && err.message ? err.message : err)}`);
  } finally {
    setBusy(false);
  }
}

async function reloadFromDisk() {
  const dirtyCount = globalDirtyCount() + pluginDirtyCount();
  if (dirtyCount > 0) {
    const ok = window.confirm("当前有未保存修改，继续重新加载会丢失这些修改。是否继续？");
    if (!ok) return;
  }
  const failures = await runPluginPersistence("reload");
  await reloadAll();
  if (failures.length) {
    showBanner("error", `已重新读取配置，但以下插件重新加载失败：${pluginFailuresText(failures)}` );
  } else {
    showBanner("info", "已重新读取配置。" );
  }
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

// ═══════════════════════════════════════════════════════════════════
// 交互式功能引导（首次进入配置中心自动播放；configAppUI.startConfigOnboarding() 可重放）
// ═══════════════════════════════════════════════════════════════════
var configOnboarding = null;

function ensureConfigOnboarding() {
  if (configOnboarding) return configOnboarding;
  configOnboarding = initOnboarding({
    onComplete: function () { saveConfigOnboardingFlag(true); }
  });
  return configOnboarding;
}

async function saveConfigOnboardingFlag(done) {
  try {
    const data = await cfgApi("GET", "/faust/ui-setting");
    const settings = (data && data.settings) || {};
    await cfgApi("POST", "/faust/ui-setting", Object.assign({}, settings, {
      widgets: (settings && settings.widgets) || {},
      onboarding: { done: !!done }
    }));
  } catch (e) {
    console.warn("saveConfigOnboardingFlag failed", e);
  }
}

function maybeStartConfigOnboarding() {
  cfgApi("GET", "/faust/ui-setting").then(function (data) {
    const done = !!(data && data.settings && data.settings.onboarding && data.settings.onboarding.done);
    if (done) return;
    setTimeout(function () { ensureConfigOnboarding().start(CONFIG_ONBOARDING_STEPS); }, 800);
  }).catch(function () {});
}

window.configAppUI = window.configAppUI || {};
window.configAppUI.startConfigOnboarding = function () {
  ensureConfigOnboarding().start(CONFIG_ONBOARDING_STEPS);
};

async function init() {
  renderNav();
  bindActions();
  await loadPluginAssets();
  await reloadAll();
  maybeStartConfigOnboarding();
}

init();