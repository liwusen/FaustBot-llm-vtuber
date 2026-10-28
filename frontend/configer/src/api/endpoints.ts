const enc = encodeURIComponent;

/**
 * 配置中心用到的全部后端端点。模块与组件不要自行拼 URL；
 * 需要新增端点时由编排者修改本文件并广播契约。
 */
export const API = {
  config: '/faust/admin/config',
  configReload: '/faust/admin/config/reload',
  runtime: '/faust/admin/runtime',
  runtimeReloadAgent: '/faust/admin/runtime/reload-agent',
  runtimeReloadAll: '/faust/admin/runtime/reload-all',
  live2dApply: '/faust/admin/live2d/apply',
  live2dModels: '/faust/admin/live2d/models',
  uiSetting: '/faust/ui-setting',

  plugins: '/faust/admin/plugins',
  pluginAssets: '/faust/admin/plugins/assets',
  pluginsReload: '/faust/admin/plugins/reload',
  pluginsInstallZip: '/faust/admin/plugins/install-zip',
  pluginPackageZip: '/faust/admin/plugins/package-zip',
  pluginMarketCheckUpdates: '/faust/admin/plugin-market/check-updates',
  pluginMarketSync: '/faust/admin/plugin-market/sync',
  pluginConfig: (id: string) => `/faust/admin/plugins/${enc(id)}/config`,
  pluginEnable: (id: string) => `/faust/admin/plugins/${enc(id)}/enable`,
  pluginDisable: (id: string) => `/faust/admin/plugins/${enc(id)}/disable`,
  pluginDelete: (id: string) => `/faust/admin/plugins/${enc(id)}`,
  pluginCommunicate: (id: string) => `/faust/plugins/${enc(id)}/communicate`,
  pluginCommunicateSSE: (id: string) => `/faust/plugins/${enc(id)}/sse-communicate`,

  providers: '/faust/admin/providers',
  providerLoadModels: (name: string) => `/faust/admin/providers/${enc(name)}/load-models`,
  providerCatalogMatch: (name: string) => `/faust/admin/providers/${enc(name)}/catalog-match`,
  modelsDevRefresh: '/faust/admin/models-dev/refresh',

  services: '/faust/admin/services',
  service: (key: string, includeLog = true) =>
    `/faust/admin/services/${enc(key)}${includeLog ? '?include_log=true' : ''}`,
  serviceStart: (key: string) => `/faust/admin/services/${enc(key)}/start`,
  serviceStop: (key: string) => `/faust/admin/services/${enc(key)}/stop`,
  serviceRestart: (key: string) => `/faust/admin/services/${enc(key)}/restart`,
  recentErrors: '/faust/admin/log/recent-errors',

  updateCheck: '/faust/update/check',
  updateDownload: '/faust/update/start-download',
  updateDownloadEvents: (downloadId: string) =>
    `/faust/update/download/${enc(downloadId)}/events`,
  updateApply: '/faust/update/apply',
  updateManual: '/faust/update/manual',

  agents: '/faust/admin/agents',
  agent: (name: string) => `/faust/admin/agents/${enc(name)}`,
  agentSwitch: '/faust/admin/agents/switch',
  agentCheckpoint: (name: string) => `/faust/admin/agents/${enc(name)}/checkpoint`,
  agentFiles: (name: string) => `/faust/admin/agents/${enc(name)}/files`,
  agentParseCard: '/faust/admin/agents/parse-card',
  agentImportCard: '/faust/admin/agents/import-card',

  memoryTree: '/faust/memory/tree',
  memoryTasks: '/faust/memory/tasks',
  memoryGet: '/faust/memory/get',
  memorySave: '/faust/memory/save',
  memoryDelete: '/faust/memory/delete',
  memoryMkdir: '/faust/memory/mkdir',
  memoryRename: '/faust/memory/rename',
  memoryCopy: '/faust/memory/copy',
  memoryMove: '/faust/memory/move',
  memoryTags: '/faust/memory/tags',
  memoryDeclareUpdate: '/faust/memory/declare-update',
  memoryAdvancedSearch: '/faust/memory/advanced-search',
  memoryAttachment: '/faust/memory/attachment',
  memoryGraphSearch: '/faust/memory/graph/search',
  memoryGraphExpand: '/faust/memory/graph/expand',
  memoryGraphEntityDetail: '/faust/memory/graph/entity-detail',
  memoryGraphEntityChildren: '/faust/memory/graph/entity-children',

  arayaStatus: '/faust/araya/status',
  arayaTrace: '/faust/araya/trace',
  arayaSettings: '/faust/araya/settings',
  arayaTriggerSSE: '/faust/araya/trigger-sse',

  componentsStatus: '/faust/components/status',
  componentsInstall: '/faust/components/install',
  componentTaskCancel: (taskId: string) => `/faust/components/tasks/${enc(taskId)}/cancel`,
  componentTaskEvents: (taskId: string) => `/faust/components/tasks/${enc(taskId)}/events`,

  mcpServers: '/faust/admin/mcp/servers',
  mcpServer: (id: string) => `/faust/admin/mcp/${enc(id)}`,
  mcpStart: (id: string) => `/faust/admin/mcp/${enc(id)}/start`,
  mcpStop: (id: string) => `/faust/admin/mcp/${enc(id)}/stop`,

  triggers: '/faust/admin/triggers',
  trigger: (id: string) => `/faust/admin/triggers/${enc(id)}`,

  skills: '/faust/admin/skills',
  skill: (slug: string, query = '') => `/faust/admin/skills/${enc(slug)}${query}`,
  skillMd: (slug: string) => `/faust/admin/skills/${enc(slug)}/skill-md`,
  skillInstall: '/faust/admin/skills/install',
  skillInstallZip: '/faust/admin/skills/install-zip',

  edgeTtsLanguages: '/faust/edge-tts/languages',
  edgeTtsGenders: '/faust/edge-tts/genders',
  edgeTtsVoicesSearch: '/faust/edge-tts/voices/search',
  edgeTtsCacheRefresh: '/faust/edge-tts/cache/refresh',
} as const;
