// 常量已抽取到 ./libs/configer/constants.js
if (typeof META === "undefined" || typeof FIELD_OPTIONS === "undefined" || typeof MODULES === "undefined") {
  console.error("配置常量未加载：请确认 frontend/libs/configer/constants.js 已在 HTML 中先加载。");
}

// State + core utilities 已抽取到 ./libs/configer/state-core.js
if (typeof state === "undefined" || typeof els === "undefined" || typeof cfgApi === "undefined" || typeof addSection === "undefined") {
  console.error("State/core 未加载：请确认 frontend/libs/configer/state-core.js 已在 HTML 中先加载。");
}

// DOM helpers 已抽取到 ./libs/configer/dom-utils.js
if (typeof el === "undefined" || typeof makeButton === "undefined") {
  console.error("DOM helpers 未加载：请确认 frontend/libs/configer/dom-utils.js 已在 HTML 中先加载。");
}

// Modal helpers 已抽取到 ./libs/configer/modal.js
if (typeof ensureModalRoot === "undefined" || typeof openModal === "undefined" || typeof closeModal === "undefined") {
  console.error("Modal helpers 未加载：请确认 frontend/libs/configer/modal.js 已在 HTML 中先加载。");
}

// Deep link 已抽取到 ./libs/configer/deeplink.js
if (typeof handleDeeplinkConfigFaustCloud === "undefined") {
  console.error("Deep link 未加载：请确认 frontend/libs/configer/deeplink.js 已在 HTML 中先加载。");
}

// UI card builders 已抽取到 ./libs/configer/ui-cards.js
if (typeof formatScalar === "undefined" || typeof makeDataView === "undefined" || typeof makeInfoCard === "undefined" || typeof makeTagListCard === "undefined" || typeof makeSimpleTableCard === "undefined") {
  console.error("UI card builders 未加载：请确认 frontend/libs/configer/ui-cards.js 已在 HTML 中先加载。");
}

// List helpers 已抽取到 ./libs/configer/list-utils.js
if (typeof makeListBox === "undefined" || typeof makeListRow === "undefined" || typeof makeOpsToolbar === "undefined") {
  console.error("List helpers 未加载：请确认 frontend/libs/configer/list-utils.js 已在 HTML 中先加载。");
}

// KB editor 已抽取到 ./libs/configer/kb-editor.js
if (typeof openKbEditorModal === "undefined" || typeof openAgentFilesModal === "undefined") {
  console.error("KB editor 未加载：请确认 frontend/libs/configer/kb-editor.js 已在 HTML 中先加载。");
}

// Field renderer 已抽取到 ./libs/configer/field-render.js
if (typeof getMeta === "undefined" || typeof updateValue === "undefined" || typeof makeFieldCard === "undefined" || typeof renderConfigModule === "undefined") {
  console.error("Field renderer 未加载：请确认 frontend/libs/configer/field-render.js 已在 HTML 中先加载。");
}

// KB utilities 已抽取到 ./libs/configer/kb-utils.js
if (typeof normalizeKbPath === "undefined" || typeof kbParentPath === "undefined" || typeof findKbNodeByPath === "undefined" || typeof getKbChildren === "undefined") {
  console.error("KB helpers 未加载：请确认 frontend/libs/configer/kb-utils.js 已在 HTML 中先加载。");
}

// KB search 已抽取到 ./libs/configer/kb-search.js
if (typeof openKbSearchModal === "undefined") {
  console.error("KB search 未加载：请确认 frontend/libs/configer/kb-search.js 已在 HTML 中先加载。");
}

// TTS voice 已抽取到 ./libs/configer/tts-voice.js
if (typeof openEdgeTTSVoiceModal === "undefined") {
  console.error("TTS voice 未加载：请确认 frontend/libs/configer/tts-voice.js 已在 HTML 中先加载。");
}

// Trigger editor 已抽取到 ./libs/configer/trigger-editor.js
if (typeof openTriggerEditorModal === "undefined") {
  console.error("Trigger editor 未加载：请确认 frontend/libs/configer/trigger-editor.js 已在 HTML 中先加载。");
}

// Trigger utils 已抽取到 ./libs/configer/trigger-utils.js
if (typeof buildTriggerUpdatePayload === "undefined") {
  console.error("Trigger utils 未加载：请确认 frontend/libs/configer/trigger-utils.js 已在 HTML 中先加载。");
}

// Araya slider 已抽取到 ./libs/configer/araya-slider.js
if (typeof createArayaTriggerSlider === "undefined") {
  console.error("Araya slider 未加载：请确认 frontend/libs/configer/araya-slider.js 已在 HTML 中先加载。");
}

// Skill helpers 已抽取到 ./libs/configer/skill-utils.js
if (typeof openSkillMdModal === "undefined") {
  console.error("Skill helpers 未加载：请确认 frontend/libs/configer/skill-utils.js 已在 HTML 中先加载。");
}

// Graph renderer 已抽取到 ./libs/configer/graph-renderer.js
if (typeof GraphCanvas === "undefined") {
  console.error("Graph renderer 未加载：请确认 frontend/libs/configer/graph-renderer.js 已在 HTML 中先加载。");
}

// Module renderers 已抽取到 ./libs/configer/modules/
if (typeof ensureModuleData === "undefined" || typeof renderAgentModule === "undefined") {
  console.error("Module agent 未加载：请确认 frontend/libs/configer/modules/agent.js 已在 HTML 中先加载。");
}
if (typeof renderMemoryModule === "undefined") {
  console.error("Module memory 未加载：请确认 frontend/libs/configer/modules/memory.js 已在 HTML 中先加载。");
}
if (typeof renderArayaModule === "undefined") {
  console.error("Module araya 未加载：请确认 frontend/libs/configer/modules/araya.js 已在 HTML 中先加载。");
}
if (typeof renderRuntimeModule === "undefined") {
  console.error("Module runtime 未加载：请确认 frontend/libs/configer/modules/runtime.js 已在 HTML 中先加载。");
}
if (typeof renderTriggersModule === "undefined") {
  console.error("Module triggers 未加载：请确认 frontend/libs/configer/modules/triggers.js 已在 HTML 中先加载。");
}
if (typeof renderSkillsModule === "undefined") {
  console.error("Module skills 未加载：请确认 frontend/libs/configer/modules/skills.js 已在 HTML 中先加载。");
}
if (typeof renderPluginsModule === "undefined") {
  console.error("Module plugins 未加载：请确认 frontend/libs/configer/modules/plugins.js 已在 HTML 中先加载。");
}
if (typeof renderOverviewModule === "undefined") {
  console.error("Module overview 未加载：请确认 frontend/libs/configer/modules/overview.js 已在 HTML 中先加载。");
}

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
    } else if (["ai", "live2d", "speech", "advanced"].includes(current.id)) {
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
    } else {
      renderSimpleJsonModule("数据", state);
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
  const dirtyCount = state.dirty.public.size + state.dirty.private.size;
  if (dirtyCount <= 0) {
    showBanner("info", "当前没有未保存更改。");
    return;
  }
  setBusy(true);
  try {
    const payload = {
      public: Object.fromEntries(state.dirty.public.entries()),
      private: Object.fromEntries(state.dirty.private.entries()),
    };
    await cfgApi("POST", "/faust/admin/config", payload);
    const hasLive2D = LIVE2D_KEYS.some((k) => state.dirty.public.has(k));
    if (hasLive2D) {
      await cfgApi("POST", "/faust/admin/live2d/apply", {
        public: Object.fromEntries([...state.dirty.public.entries()].filter(([k]) => LIVE2D_KEYS.includes(k))),
      });
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
  const dirtyCount = state.dirty.public.size + state.dirty.private.size;
  if (dirtyCount > 0) {
    const ok = window.confirm("当前有未保存修改，继续重新加载会丢失这些修改。是否继续？");
    if (!ok) return;
  }
  await reloadAll();
  showBanner("info", "已重新读取配置。" );
}

async function applyTtsReferToService() {
  try {
    const mode = String(state.config.public.TTS_MODE || "local").toLowerCase();
    if (mode !== "local") {
      showBanner("info", "当前 TTS 模式不是 local，无法应用参考音频到本地 TTS 服务。" );
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
  await reloadAll();
}

init();
