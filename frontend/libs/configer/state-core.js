// State, DOM refs, and core utility helpers for configer

const state = {
  activeModule: "overview",
  config: { public: {}, private: {} },
  original: { public: {}, private: {} },
  dirty: { public: new Map(), private: new Map() },
  runtime: {},
  live2dModels: [],
  agents: [],
  selectedAgent: "",
  agentDetail: null,
  kbTree: null,
  kbScope: "",
  kbCurrentDir: "/",
  kbSelectedPath: "",
  kbSelectedContent: "",
  kbSelectedMeta: null,
  kbTasks: [],
  araya: null,
  services: [],
  selectedService: "",
  serviceDetail: null,
  recentErrors: [],
  mcpServers: [],
  selectedMcpId: "",
  triggers: [],
  selectedTriggerId: "",
  memoryView: "tree",
  kbGraphSelectedEntityId: "",
  skills: [],
  selectedSkillSlug: "",
  skillsAgent: "",
  skillDetail: null,
  plugins: [],
  selectedPluginId: "",
  pluginConfigDraft: {},
  runtimeUpdate: null,
  graphEntities: [],
  graphRelations: [],
  moduleContainers: {},  // { [moduleId]: { div: HTMLElement, rendered: boolean } }
  _activeContainer: null,
  arayaEventSource: null,  // Araya SSE 连接（页面切换后持续存活）
};

const els = {
  moduleNav: document.getElementById("moduleNav"),
  cardsRoot: document.getElementById("cardsRoot"),
  moduleTitle: document.getElementById("moduleTitle"),
  moduleDesc: document.getElementById("moduleDesc"),
  dirtyBadge: document.getElementById("dirtyBadge"),
  saveBtn: document.getElementById("saveBtn"),
  applyBtn: document.getElementById("applyBtn"),
  reloadBtn: document.getElementById("reloadBtn"),
  banner: document.getElementById("banner"),
};

function clearRoot() {
  invalidateAllContainers();
  if (els && els.cardsRoot) els.cardsRoot.innerHTML = "";
}

function setBusy(v) {
  if (!els) return;
  if (els.saveBtn) els.saveBtn.disabled = v;
  if (els.applyBtn) els.applyBtn.disabled = v;
  if (els.reloadBtn) els.reloadBtn.disabled = v;
}

function showBanner(type, text) {
  if (!els || !els.banner) return;
  els.banner.className = `banner ${type}`;
  els.banner.textContent = text;
  els.banner.classList.remove("hidden");
}

function hideBanner() {
  if (!els || !els.banner) return;
  els.banner.classList.add("hidden");
}

function cfgApi(method, path, payload, query) {
  if (window.api && typeof window.api.configRequest === "function") return window.api.configRequest(method, path, payload, query);
  return Promise.reject(new Error("window.api.configRequest 未实现"));
}

function apiLocal(method, url, payload) {
  if (window.api && typeof window.api.configHttpRequest === "function") return window.api.configHttpRequest(method, url, payload);
  return Promise.reject(new Error("window.api.configHttpRequest 未实现"));
}

function addSection(title, bodyNodes, full = true) {
  // If caller passed a single node that's already a `.card`, append it directly
  if (Array.isArray(bodyNodes) && bodyNodes.length === 1 && bodyNodes[0] instanceof HTMLElement && bodyNodes[0].classList.contains('card')) {
    const target = (state && state._activeContainer) || (els && els.cardsRoot);
    if (target) target.append(bodyNodes[0]);
    return;
  }

  const section = el("section", `card section ${full ? "full-span" : ""}`.trim());
  const t = el("h3", "section-title", title);
  section.append(t);
  for (const node of bodyNodes) section.append(node);
  const target = (state && state._activeContainer) || (els && els.cardsRoot);
  if (target) target.append(section);
}

function getModuleContainer(moduleId) {
  if (!state.moduleContainers[moduleId]) {
    const div = document.createElement("div");
    div.className = "module-container";
    div.style.display = "none";
    if (els && els.cardsRoot) els.cardsRoot.append(div);
    state.moduleContainers[moduleId] = { div: div, rendered: false };
  }
  return state.moduleContainers[moduleId].div;
}

// 白名单：离开这些页面时保留 DOM（SSE 连接、实时进度、图谱状态）
var PERSISTENT_MODULES = ["overview", "memory", "araya"];

function switchModule(moduleId) {
  for (const [id, entry] of Object.entries(state.moduleContainers)) {
    if (id === moduleId) {
      entry.div.style.display = "";
    } else {
      entry.div.style.display = "none";
      // 离开非持久页面时释放其 DOM
      if (!PERSISTENT_MODULES.includes(id)) {
        entry.div.innerHTML = "";
        entry.rendered = false;
      }
    }
  }
}

function setActiveContainer(container) {
  state._activeContainer = container;
}

function invalidateAllContainers() {
  for (const entry of Object.values(state.moduleContainers)) {
    entry.rendered = false;
  }
}

// 统一刷新 API：模块内操作始终调用此函数，框架自动决定 show-cached 或 re-render
function refreshModule() {
  const cid = state.activeModule;
  if (state.moduleContainers[cid]) state.moduleContainers[cid].rendered = false;
  renderModule();
}

function appendToActiveModule(...nodes) {
  const target = (state && state._activeContainer) || (els && els.cardsRoot);
  if (target) target.append(...nodes);
}
