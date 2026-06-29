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
  triggers: [],
  selectedTriggerId: "",
  memoryView: "tree",
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
  const section = el("section", `card section ${full ? "full-span" : ""}`.trim());
  const t = el("h3", "section-title", title);
  section.append(t);
  for (const node of bodyNodes) section.append(node);
  if (els && els.cardsRoot) els.cardsRoot.append(section);
}
