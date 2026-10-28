# Configer（Vue 3）

FaustBot 配置中心的前端实现。Electron 配置窗口加载 `frontend/configer-dist/index.html`，源码位于本目录。

## 开发

```powershell
cd frontend
npm run dev:configer
```

开发服务器只监听 `127.0.0.1:5174`。Electron 通过 `FAUST_CONFIGER_DEV_URL` 加载它（仅允许 localhost/127.0.0.1）：

```powershell
$env:FAUST_CONFIGER_DEV_URL = "http://127.0.0.1:5174"
npm start
```

配置中心依赖 `preload.js` 暴露的 `window.api`（IPC）。直接在现代浏览器打开 Vite 页面会明确提示 `window.api 不可用`，不会静默空转。

## 构建与测试

```powershell
cd frontend
npm run typecheck:configer
npm run test:configer
npm run build:configer
```

构建产物在 `frontend/configer-dist/`，`base: './'` 以支持 Electron `file://`。

## 结构

- `src/main.ts`：样式、全局 shim、插件宿主安装、App 挂载。
- `src/App.vue`：侧边栏、顶栏、banner、模块宿主、弹窗与引导。
- `src/api/`：`window.api` 类型化封装与全部端点。
- `src/stores/`：配置、provider、模块、插件、UI 等 Pinia store。
- `src/plugin/`：`window.pluginUI` 兼容宿主与插件资源加载。
- `src/components/`：shell、通用 UI、配置字段组件。
- `src/modules/`：13 个内置模块与 `registry.ts`。
- `src/memory/`：知识库图谱/可视化引擎。
- `src/styles/config-window.css`：旧视觉系统原样复用，插件 CSS 兼容依赖它。

## 约定

- 不使用 vue-router；模块切换由 `useModulesStore().activeModule` 驱动。
- `overview`、`memory`、`araya` 使用 KeepAlive；其它模块每次进入重新挂载。
- `window.pluginUI` 的字段/方法必须保持旧契约；插件脚本在配置加载前注入。
- 所有后端请求经 `src/api/ipc.ts`，端点集中在 `src/api/endpoints.ts`。
- 旧经典入口 `frontend/config-window.html/js`、`frontend/libs/configer/**`、`libs/codemirror/**`、`libs/echarts/**`、`libs/onboarding.js` 已删除。
- `frontend/config-window.css` 仍保留：`live-window.html` 与 Configer 的视觉系统共用它；`frontend/libs/dom-utils.js` 也保留给直播窗口使用。
- 共享契约与文件所有权见 `CONTRACTS.md`。
