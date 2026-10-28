# Configer Vue 重构 · 共享契约与文件所有权

> 本文件由编排者维护。Subagent 需要变更共享契约时，停止并向编排者报告 `CONTRACT_REQUESTS`，不要自行修改本文件或共享路径。

## 分支与命令

- 分支：`refactor/configer-vue`（从 `dev` 创建），禁止向 `main` 提交。
- 所有命令在 `frontend/` 下执行：
  - `npm run typecheck:configer`
  - `npm run test:configer`
  - `npm run build:configer`
  - `npm run check-js`
- 单测文件命名：`src/**/*.spec.ts`，Vitest + jsdom。
- 源码入口：`src/main.ts`；根组件：`src/App.vue`；模块注册表：`src/modules/registry.ts`。
- Vite 输出：`frontend/configer-dist/`，`base: './'`，Electron `file://` 可加载。

## 文件所有权

| 角色 | 独占路径 |
| --- | --- |
| 编排者 | `package.json`、`configer/vite.config.ts`、`configer/vitest.config.ts`、`configer/tsconfig.json`、`src/main.ts`、`src/App.vue`、`src/api/**`、`src/stores/{config,modules,ui}.ts`、`src/components/shell/**`、`src/styles/**`、`src/shims/**`、`src/onboarding/**`、`src/modules/registry.ts`、`src/deeplink.ts`、Electron/打包/文档/旧代码清理 |
| `shared-ui` | `src/config/**`、`src/components/config/**`、`src/components/ui/**`（`ModalHost.vue`/`DeeplinkModal.vue` 除外）、`src/utils/**` 及其测试 |
| `plugin-host` | `src/plugin/**`、`src/stores/plugins.ts` 及其测试 |
| `ai` | `src/modules/ai/**`、`src/stores/providers.ts`、AI 相关测试 |
| `model-speech` | `src/modules/model/**`、`src/modules/speech/**` 及其测试 |
| `simple-modules` | `src/modules/{overview,advanced,runtime,triggers,skills,mcp}/**` 及其 store/测试 |
| `memory` | `src/modules/memory/**`、`src/memory/**`、`src/stores/memory.ts` 及其测试 |
| `plugins-page` | `src/modules/plugins/**` 及其测试 |
| `agent-components-araya` | `src/modules/{agent,components,araya}/**`、对应 store 及其测试 |

禁止修改：`src/main.ts`、`src/App.vue`、`src/api/**`、`src/stores/{config,modules,ui}.ts`、`src/components/shell/**`、`src/modules/registry.ts`、`src/styles/**`、`src/shims/**`、`src/onboarding/**`、`frontend/config-window.css`、主窗口/直播窗口、`backend/**`、lockfile。

旧实现已删除（`frontend/config-window.html/js`、`frontend/libs/configer/**`、`libs/codemirror/**`、`libs/echarts/**`、`libs/onboarding.js`）。`frontend/config-window.css` 保留，Configer 与 `live-window.html` 共用；`frontend/libs/dom-utils.js` 保留给直播窗口。

## Store 契约

### `useConfigStore`（`src/stores/config.ts`）

状态（Pinia 已解包）：`publicConfig`、`privateConfig`、`originalPublic`、`originalPrivate`、`dirtyPublic: Map<string, unknown>`、`dirtyPrivate: Map`、`runtime`、`config`、`globalDirtyCount`、`dirtyCount`。

方法：

- `loadConfig(): Promise<void>`
- `loadRuntimeSummary(): Promise<void>`
- `updateValue(scope, key, value)`：密钥只有非空才 dirty；非密钥等于 original 时移出 dirty。
- `revertValue(scope, key)`、`isDirty(scope, key)`、`dirtyValue(scope, key)`
- `saveConfig()`、`applyRuntime()`、`reloadAll()`、`reloadFromDisk()`、`applyTtsReferToService()`
- `resetDirty()`

### `useProvidersStore`（`src/stores/providers.ts`，归 `ai`）

状态：`providers`、`mainModel`、`subagentModels`、`resolvedFormats`、`providerCatalog`、`providersDirty`、`mainModelDirty`、`subagentModelsDirty`、`allModelEntries`、`loaded`。
方法：`loadProviders()`、`ensureProviders()`（仅首次加载，避免非持久 AI 页切回时冲掉草稿）、`setBundle(data)`、`findProvider(name)`、`markProvidersDirty()`、`markModelsDirty()`、`resetDirty()`、`anyDirty()`、`dirtyCount()`。

### `useModulesStore`（`src/stores/modules.ts`）

状态：`activeModule`、`allModules`、`currentModule`、`reloadEpoch`、`nonce`。
方法：`navigate(id)`、`switchModule(id)`、`refreshCurrent()`、`reloadAllModules()`、`isPersistent(id)`、`registerLeaveGuard(fn)`（返回 dispose）、`onVisibilityChange(fn)`（返回 dispose）、`ensureActiveValid()`。

离开守卫同步执行；返回 `false` 取消切换。成功切换后宿主自动调用 `dropPersistenceExcept(id)`。

### `usePluginsStore`（`src/stores/plugins.ts`，归 `plugin-host`）

状态：`pages`、`cards`、`persist`、`persistDirty: Set<string>`、`dirtyCount`。
方法：`addPage`、`addCard`、`modifyPage`、`registerPersistence`、`unregisterPersistence`、`setPluginDirty`、`isPluginDirty`、`clearDirty(except?)`、`resetPersistence`、`dropPersistenceExcept(id)`、`resetAll`、`runPersistence(phase)`、`pluginFailuresText(failures)`。

### `useUiStore`（`src/stores/ui.ts`）

状态：`bannerVisible`、`bannerType`、`bannerText`、`busy`、`modalOpen`、`modalComponent`、`modalProps`、`modalTitle`、`folds`、`tableUIState`。
方法：`showBanner(type, text)`、`hideBanner()`、`setBusy(bool)`、`openModal(component, props?, title?)`、`closeModal()`、`registerModalDisposer(fn)`、`foldState(key)`、`setFoldState(key, open)`、`tableState(key)`。

## API 契约

只允许使用 `src/api/ipc.ts` 与 `src/api/endpoints.ts`：

- `cfgApi<T>(method, path, payload?, query?, options?)`
- `apiLocal<T>(method, url, payload?)`
- `openFileDialog(options)`、`openDirectoryDialog({title})`、`openPath(path)`、`getFaustbotRoot()`、`openLiveWindow()`、`toggleLogPanel()`、`toggleWidgetEditMode()`、`ensureModelProfile(modelDir, force?)`、`saveModelProfile(modelDir, json)`、`isNestedLive2DModelDir(dir)`、`flattenNestedLive2DModelDir(dir)`、`ensureModel3Declarations(dir)`
- `formatApiError(err)`：统一 `response.detail / detail / message`。
- 端点统一从 `API` 取；不要手写路径。缺少端点时报告 `CONTRACT_REQUESTS`。

## 共享组件契约（`shared-ui` 交付）

所有组件使用现有 `config-window.css` 类名，不新增视觉体系、不使用 scoped 样式覆盖全局设计。

| 组件 | Props | 说明 |
| --- | --- | --- |
| `components/ui/Icon.vue` | `name: string; size?: number; className?: string` | 输出 `window.FaustIcons.svg()`；缺失图标时留空 |
| `components/ui/SectionCard.vue` | `title?: string; fullSpan?: boolean; cardClass?: string` | 插槽 default/actions |
| `components/ui/InfoCard.vue` | `title: string; rows: Array<{label: string; value: unknown}>` | 复用 `simple-table info-table` |
| `components/ui/TagListCard.vue` | `title: string; tags: string[]` | 空态「无」 |
| `components/ui/DataView.vue` | `value: unknown` | 递归对象/数组展示，复用 `simple-table-compact` |
| `components/ui/SimpleTableCard.vue` | `title: string; columns: string[]; rows: unknown[][]; pageSize?: number; searchKey?: number; emptyText?: string` | 文本/数字单元格；复杂单元格由模块自绘 |
| `components/ui/EmptyState.vue` | `text: string` | `.empty-state` |
| `components/config/ConfigField.vue` | `scope: ConfigScope; fieldKey: string; rerender?: () => void; afterChange?: () => void` | 控件本体；secret/range/number/select/textarea/bool/TTS 文件选择语义与旧 `buildFieldControl` 一致 |
| `components/config/FieldCard.vue` | `scope; fieldKey; rerender?` | 字段卡（`.card.field-card`，dirty 类） |
| `components/config/FieldRow.vue` | `scope; fieldKey; rerender?; readonly?` | AI 字段行：复制键名、已设置/已修改、撤销 |
| `components/config/FieldGroup.vue` | `id?; title; desc?; meta?; folded?; foldKey?; foldLabel?; foot?` | 插槽 `meta`、`actions`、default；`folded` 时内部用 FieldFold |
| `components/config/FieldFold.vue` | `title; stateKey; forceOpen?; defaultOpen?` | 展开状态走 `ui.folds`；重渲染保留 |
| `components/ui/CodeMirrorHost.vue` | `modelValue; language?; readOnly?; height?` | emits `update:modelValue`；expose `getValue/setValue/view/measure`；卸载销毁 |
| `components/ui/EChart.vue` | `option; height?` | 使用 `window.echarts`；resize + dispose；无 echarts 时降级文案 |
| `components/ui/SwitchField.vue` | `modelValue: boolean; label?` | 复用 `.switch` 标记，emit update |
| `components/ui/SliderField.vue` | `modelValue: number; min; max; step; unit?; preview?` | 复用 `.slider-field` |

`components/config` 组件内部直接读写 `useConfigStore`；模块不要复制 `updateValue` 逻辑。

## 插件宿主契约

`window.pluginUI` 必须在插件脚本加载前安装，字段/方法逐项兼容旧 `config-window.js`：

- `_pages`、`_cards`、`_persist`、`_persistDirty: Set`、`backendBaseUrl`。
- `addPage`：`id/label` 必填；`plugin:id` 去重；导航响应式追加。
- `addCard`：`plugin:moduleId:title` 去重；保留旧行为（内置模块不渲染卡片；仅无页面的未知模块回退）。
- `modifyPage`：找不到模块时把 `undefined` 传给回调。
- `registerPersistence`：缺 `plugin` 抛错；`plugin::module` 覆盖；注册时清该插件 dirty；返回 `{setDirty,dispose}`；ctx `{phase,plugin,module,dirty}`。
- `unregisterPersistence`、`setPluginDirty`、`dirtyCount`、`isPluginDirty`、`clearDirty(except)`、`resetPersistence`、`dropPersistenceExcept(active)` 语义照旧。
- `communicate` / `communicateSSE` 返回 `Promise` / 原生 `EventSource`，URL 与旧实现一致。
- 插件 `render` 抛错时内容区显示 `模块加载失败: …`，不阻断其它模块。
- 切换模块成功后注销不可见插件页的钩子；插件页卸载销毁容器。

## 测试与验收

- 每个实现 subagent 至少覆盖本模块最容易回归的纯逻辑（payload、dirty、路径、过滤、解析）。
- 不 mock 掉被测逻辑；ECharts/CodeMirror 可 mock。
- 报告格式：

```text
STATUS: done | blocked
FILES: ...
TESTED: 命令 + 结果
GAPS: 未覆盖项/风险
CONTRACT_REQUESTS: 无 | ...
```

## 禁止事项

- 不运行 `npm install`、`git add/commit/checkout/stash`、`vite build`、全仓格式化。
- 不修改其它 subagent 路径；不新增依赖。旧实现删除由编排者在收尾阶段统一执行，subagent 不参与。
- 不改变后端接口、配置键、文案、视觉、插件 API。
