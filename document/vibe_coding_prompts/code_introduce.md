# Faust 项目代码功能介绍

本文档用于快速帮助AI Agent理解 `faust/` 目录下各主要代码文件与模块的职责分工。内容以当前仓库结构为准，重点介绍**主入口、后端服务、前端界面、配置文件、工具模块与运行脚本**。

---

## 一、项目整体结构概览

`Faust` 项目可以大致分为五层：

1. **后端主服务层**：负责创建 Agent、管理上下文、处理聊天接口、触发器与前端交互。
2. **工具能力层**：负责给 Agent 提供文件操作、系统执行、搜索、RAG、灵动交互等工具。
3. **前端展示层**：基于 Electron + HTML/CSS/JS，负责 Live2D 展示、文字聊天、ASR/TTS 面板和灵动窗口渲染。
4. **配置控制层**：负责统一管理配置文件、Agent 核心 Prompt 文件，以及运行时动态重载。
5. **配置与脚本层**：负责环境配置、批处理启动、模型/角色 Prompt、依赖与辅助服务管理。

---

## 二、根目录文件说明

### `README.md`
项目主说明文档。介绍项目目标、功能列表、长期规划和技术栈。适合快速了解项目定位。

### `requirements.txt`
Python 依赖列表。主要对应后端运行所需的库。

### `LICENSE`
项目许可证文件。

### `document/`
项目文档目录。

#### `document/vibe_coding_prompts/`
用于存放开发提示词、功能改造说明、结构说明等辅助文档。

- `ui.md`：UI 相关说明文档。
- `code_introduce.md`：当前这份代码结构说明文档。

#### `document/plugin.md`
插件系统完整编写指南，包含插件目录规范、Tool/Middleware 注册、热重载、Heartbeat、Trigger CRUD 与管理 API。

---

## 三、后端目录 `backend/` 说明

`backend/` 是 Faust 项目的主要后端目录，负责 Agent 主服务、工具模块、ASR/TTS 相关服务管理与配置。

### 1. 主入口文件

#### `backend/backend-main.py`
这是 **Faust 后端主服务入口**，是当前项目最核心的后端程序。

主要职责：

- 创建 FastAPI 应用
- 初始化 Agent Prompt（从 `agents/<agent_name>/` 目录拼装 AGENT.md / ROLE.md / COREMEMORY.md / TASK.md）
- 初始化 LangGraph/LangChain Agent
- 初始化 checkpoint / store（内存或 sqlite）
- 提供聊天接口：
  - `POST /faust/chat`：兼容性 HTTP 聊天接口
  - `WebSocket /faust/chat`：主流式聊天接口
- 提供前端命令接口：
  - `WebSocket /faust/command`
  - `/faust/command/forward`
  - `/faust/command/feedback`
- 提供 HIL（Human In Loop）与 Nimble 灵动交互相关接口
- 提供服务状态、优雅关停等控制接口
- 提供配置中心 / 管理后台接口：
  - `GET/POST /faust/admin/config`
  - `POST /faust/admin/config/reload`
  - `GET /faust/admin/runtime`
  - `POST /faust/admin/runtime/reload-agent`
  - `POST /faust/admin/runtime/reload-all`
  - `GET/POST/DELETE /faust/admin/agents*`
  - `GET /faust/admin/live2d/models`
- 提供插件系统管理接口：
  - `GET /faust/admin/plugins`
  - `POST /faust/admin/plugins/reload`
  - `POST /faust/admin/plugins/{plugin_id}/enable|disable`
  - `POST /faust/admin/plugins/{plugin_id}/tools/{tool_name}/enable|disable`
  - `POST /faust/admin/plugins/{plugin_id}/middlewares/{middleware_name}/enable|disable`
  - `POST /faust/admin/plugins/{plugin_id}/trigger-control/enable|disable`
  - `GET /faust/admin/plugins/hot-reload`
  - `POST /faust/admin/plugins/hot-reload/start|stop`
  - `POST /faust/admin/plugins/heartbeat`
- 支持在**不重启 FastAPI 进程**时重新加载配置并重建 Agent runtime
- 启动触发器 watchdog 线程
- 启动插件热重载与插件心跳调度（默认 10 秒调用一次插件心跳）

可以把它理解成：

> **Faust 的大脑入口 + API 总线 + 前后端桥梁 + 配置中心后端入口**

---

#### `backend/backend_manager.py`
用于管理后端服务进程/子服务启动逻辑的辅助入口，通常与批处理脚本配合使用。

#### `backend/faust_backend_manager.py`
与主后端相关的辅助管理脚本，职责上类似服务控制器。

#### `backend/asr_api.py`
ASR 相关接口封装或独立接口入口，和语音识别服务联动。

---

### 2. 后端配置文件

#### `backend/faust.config.json`
主配置文件，用于定义后端运行所需的公共参数。

现在除了基础模型配置外，也可承载：

- 当前 Agent 名称
- RAG 服务地址、RAG 模型 Base URL、聊天模型、向量模型、向量维度等配置
- 是否自动把聊天记录写入 `agents/<agent>/record/YYYYMMDD.md` 并触发后台索引
- Live2D 默认模型路径 / 缩放 / 坐标
- Minecraft bridge 设置
- 前端默认行为（如默认点击穿透、默认 TTS 语言）

#### `backend/faust.config.private.example`
私密配置示例文件，通常用于放 API Key 或本地开发特定配置的模板。

#### `backend/faust.config.private.json`
实际私密配置文件。一般用于保存密钥与敏感配置。

> 这类文件通常通过 `faust_backend/config_loader.py` 读取。

---

### 3. 批处理 / 启动脚本

#### `backend/MAIN.bat`
后端主服务启动脚本。

#### `backend/ASR.bat`
ASR 服务启动脚本。

#### `backend/TTS.bat`
TTS 服务启动脚本。

#### `backend/RAG.bat`
LightRAG 服务启动脚本。现在它和 ASR/TTS 一样，被统一纳入后端服务管理器，可在配置中心中直接启动、停止、重启和查看日志。

#### `backend/OCR.bat`
OCR 服务启动脚本。

#### `backend/clearCheckPoint.bat`
用于清理 checkpoint 数据，适合调试或重置 Agent 上下文。

---

### 4. Agent Prompt 与角色目录

#### `backend/agents/`
每个 Agent 的角色、记忆和任务定义目录。

典型结构通常包含：

- `AGENT.md`
- `ROLE.md`
- `COREMEMORY.md`
- `TASK.md`

这些文件会在 `backend-main.py` 中被加载并拼接成初始系统 Prompt。

在配置中心中，用户可以：

- 列出所有 Agent
- 新建 Agent（可选复制模板）
- 删除 Agent（受保护策略限制）
- 编辑四个核心 Prompt 文件
- 切换当前运行 Agent 并重建 runtime

---

### 5. 子模块目录 `backend/faust_backend/`

这是 Faust 后端的**能力模块库**，主要存放被主程序 import 的功能模块。

下面介绍当前最重要的几个文件。

#### `faust_backend/config_loader.py`
配置加载器。

主要职责：

- 读取 `faust.config.json` / private 配置
- 解析命令行参数
- 暴露配置字段给其他模块使用
- 提供 `load_configs()` / `reload_configs()`，支撑运行时动态重载

它是整个后端配置系统的入口之一。

---

#### `faust_backend/admin_runtime.py`
后端配置中心 / 运行时管理辅助模块。

主要职责：

- 统一读取并保存公开/私密配置文件
- 屏蔽私密配置的显示值
- 列出可用 Agent 与 Live2D 模型
- 管理 Agent 目录的增删查改
- 读取 / 保存 Agent 核心 Prompt 文件
- 提供运行时摘要数据供配置窗口展示
- 限制危险删除（默认禁止删除当前 Agent 和 `faust`）
- 创建 Agent 时自动补齐 `record/` 目录，用于滚动保存聊天记录 Markdown 文件

这个模块可以理解成：

> **配置中心的数据层 + Agent 目录管理层 + 运行时摘要服务层**

---

#### `faust_backend/llm_tools.py`
**Agent 工具总表**，是项目中非常关键的工具模块。

主要职责：

- 定义可被 Agent 调用的工具函数
- 使用 `@tool`、`add_to_tool_list()` 等方式把函数注册进 `toollist`
- 提供系统工具、文件读写工具、RAG 工具、时间工具、搜索工具等
- 提供当前 Agent 相关路径刷新能力，保证切换 Agent 后 diary/RAG tracker 不会继续指向旧目录

常见能力包括：

- 获取时间
- 获取主机信息
- 执行 Python 代码
- 执行系统命令
- 读写日记
- 读写文本文件
- 搜索/RAG 查询
- 与前端交互的辅助工具

这个文件可以理解成：

> **Faust Agent 可调用能力的汇总入口**

---

#### `faust_backend/rag_client.py`
Faust 对 LightRAG 服务的客户端封装与本地文档追踪器。

主要职责：

- 请求 RAG 服务的 `/health`、`/config`、`/agent`、`/insert`、`/query` 等接口
- 管理 `rag_doc_tracker.json`，以文件路径为索引追踪 RAG 中的文档 ID
- 把每轮聊天自动落盘到 `agents/<agent>/record/YYYYMMDD.md`
- 在后台把当天聊天记录增量同步入 RAG 索引

现在它已经支持随着配置重载 / Agent 切换动态刷新工作目录，而不是停留在 import 时的旧路径。

---

#### `faust_backend/service_manager.py`
统一的后端服务管理器。

当前纳入统一管理的服务包括：

- `asr`
- `tts`
- `mc_operator`
- `rag`

它负责：

- 通过端口探测服务是否在线
- 启动/停止/重启服务
- 读取日志尾部给配置中心展示
- 为主后端提供核心服务自启动能力

---

#### `faust_backend/backend2front.py`
后端到前端的消息桥。

主要职责：

- 向前端发送控制命令
- 推送动作、情绪、窗口任务等
- 管理前端任务队列

它是 `backend-main.py` 与渲染层之间的重要中间模块。

---

#### `faust_backend/events.py`
事件对象池与全局事件标志管理模块。

主要职责：

- 保存反馈事件
- 保存 HIL 事件
- 保存后端与前端队列相关事件
- 提供线程/异步之间的简单同步点

---

#### `faust_backend/trigger_manager.py`
触发器管理器。

主要职责：

- 保存与调度触发器任务
- 支持 watchdog 线程
- 支持时间触发、提醒触发等
- 提供触发器状态查询
- 提供 Trigger CRUD 能力（增删查改）
- 支持插件挂载 append/fire 两阶段过滤钩子

它是项目定时/召回逻辑的基础设施。

---

#### `faust_backend/plugin_system/`
插件系统核心目录。

主要职责：

- 发现并加载 `backend/plugins/*` 插件
- 维护插件状态持久化（`backend/plugins/plugins.state.json`）
- 组合插件 Tool 与 Middleware 注入 Agent runtime
- 提供 Trigger 控制钩子（append/fire 过滤）
- 提供插件热重载检测与自动应用
- 提供插件 Heartbeat 调度入口

关键文件：

- `faust_backend/plugin_system/interfaces.py`
  - 定义 `PluginContext`、`PluginManifest`、`ToolSpec`、`MiddlewareSpec`
  - `PluginContext` 内置 Trigger CRUD 方法
- `faust_backend/plugin_system/manager.py`
  - `PluginManager` 实现插件生命周期、状态控制、组合注入
  - 提供 `hot_reload_tick` 与 `heartbeat_tick`

---

#### `backend/plugins/`
插件目录，每个插件一个子目录（至少包含 `plugin.json` 与 `main.py`）。

当前示例插件：

- `backend/plugins/example_echo/`
  - 示例 Tool
  - 示例 Trigger 过滤钩子
  - 可作为新插件开发模板

---

#### `faust_backend/nimble.py`
灵动交互窗口管理模块。

主要职责：

- 管理 Nimble 会话
- 保存/读取窗口回调结果
- 维护 reminder / expire / result 相关会话状态

配合 `backend-main.py` 中的 `/faust/nimble/*` 系列接口使用。

---

#### `faust_backend/gui_llm_lib.py`
GUI 操作相关的辅助模块，通常用于与界面控制、视觉/桌面操作有关的 LLM 工具能力。

---

#### `faust_backend/rag_client.py`
RAG 客户端模块。

主要职责：

- 对接 RAG 子系统
- 发起文档检索请求
- 维护文档 tracker
- 根据当前 Agent 对齐 RAG 的 agent_id

---

#### `faust_backend/searchapi_patched.py`
封装搜索 API 的适配版本，供 `llm_tools.py` 调用。

---

#### `faust_backend/utils.py`
工具函数集合，供其他后端模块复用。

---

#### `faust_backend/security.py`
安全相关逻辑模块，用于命令检查、工具调用审核等。

---

#### `faust_backend/debug_console.py`
一个面向开发调试的控制台工具，可用于手动向 `/faust/chat` 发送输入、转发命令或反馈 HIL 结果。

---

### 6. 其他后端目录

#### `backend/unittest/`
后端测试目录。

#### `backend/logs/`
后端日志目录。

#### `backend/asr-hub/`
语音识别相关模型或服务资源目录。

#### `backend/tts-hub/`
语音合成相关资源目录。

#### `backend/rag-hub/`
RAG 相关子系统目录。


---

## 四、前端目录 `frontend/` 说明

`frontend/` 是 Faust 的桌面展示前端，采用 **Electron + HTML/CSS/JS**。

### 1. `frontend/package.json`
前端 Electron 项目依赖与脚本配置。

主要内容：

- 项目名：`faust-live2d-frontend`
- 主入口：`electron-main.js`
- 启动脚本：`npm start` -> `electron .`
- 依赖：`ws`
- 开发依赖：`electron`

---

### 2. `frontend/electron-main.js`
Electron 主进程入口。

主要职责：

- 创建透明、无边框、置顶的主窗口
- 创建独立的“配置中心”窗口
- 加载 `index.html`
- 加载 `config-window.html`
- 控制窗口全屏、鼠标穿透等特性
- 提供 IPC 接口：
  - 模型状态保存/读取
  - 设置鼠标忽略
  - 输出 renderer 日志
  - 打开配置中心窗口
- 在主进程中连接后端 `ws://127.0.0.1:13900/faust/command`
- 把后端命令转发给 renderer

它相当于：

> **前端壳程序 + 主进程控制台 + 后端命令桥 + 配置窗口管理器**

---

### 3. `frontend/preload.js`
Electron preload 脚本，用于把主进程能力安全暴露给 renderer。

通常承担：

- 暴露 IPC API
- 暴露模型状态存储 API
- 暴露日志接口
- 暴露后端命令监听接口
- 暴露打开配置中心窗口的 API

---

### 4. `frontend/index.html`
前端主页面，渲染 Live2D 模型容器、控制面板和交互元素。

当前快捷控制器中已包含“配置中心”入口按钮。

---

### 5. `frontend/styles.css`
前端页面样式定义。

---

### 6. `frontend/app.js`
这是前端最核心的业务脚本之一，负责大量界面交互逻辑。

主要职责包括：

- 初始化 PIXI 与 Live2D 模型
- 加载、拖拽、缩放 Live2D 模型
- 保存与恢复模型状态
- 处理 ASR 录音与 VAD 流式逻辑
- 处理文字聊天输入
- 调用后端聊天接口
- 管理 TTS/音频播放
- 处理 Nimble 灵动窗口的显示、提交与关闭
- 接收主进程转发下来的 Faust 指令
- 打开独立配置中心窗口

从体量上看，`app.js` 是：

> **前端展示逻辑 + 音频交互逻辑 + Nimble 动态窗口逻辑 + 聊天交互逻辑** 的综合入口

---

### 7. `frontend/config-window.html`
配置中心独立窗口页面。

主要职责：

- 提供左侧导航栏
- 承载配置表单、Agent 列表、运行控制区
- 使用 `textarea` 风格编辑 Agent 核心文件

---

### 8. `frontend/config-window.css`
配置中心窗口样式文件。

主要职责：

- 提供现代化、卡片式后台 UI 风格
- 实现侧边栏导航、表单、编辑器、弹窗等样式

---

### 9. `frontend/config-window.js`
配置中心窗口主脚本。

主要职责：

- 调用后端 `/faust/admin/*` 系列 API
- 加载与保存配置文件
- 管理 Agent 列表与核心 Prompt 文件
- 执行 Agent 切换、删除、创建
- 触发运行时动态重载

---

### 10. `frontend/live2d_downloader.py`
用于下载或整理 Live2D 模型资源的辅助脚本。

---

### 11. `frontend/start.bat`
前端启动脚本，通常用于快速启动 Electron 前端。

---

### 12. `frontend/2D/`
Live2D 模型资源目录。

### 13. `frontend/libs/`
前端第三方库目录，例如 PIXI、Live2D 支持库等。

---

## 五、配置与角色 Prompt 的工作方式

Faust 的角色人格与任务通过 `backend/agents/<agent_name>/` 目录中的 Markdown 文件定义。
后端启动时会在 `backend-main.py` 中读取这些文件并拼接成完整 Prompt。

组合顺序通常是：

1. `AGENT.md`
2. `ROLE.md`
3. `COREMEMORY.md`
4. `TASK.md`

这样做的优点是：

- 角色设定与程序逻辑分离
- 可单独调整人格 / 核心记忆 / 当前任务
- 有利于多人协作维护 Prompt

配置中心稳定版采用的编辑方式是：

- 不引入 Monaco 等重型编辑器
- 直接使用 `textarea` 进行核心 Prompt 文件编辑
- 优先保证稳定与易维护

---

## 六、运行时数据流简述

下面是当前项目的大致运行流程：

1. 启动 `backend/backend-main.py`
2. 后端创建 Agent、加载 Prompt、注册工具
3. 后端开放 `/faust/chat` 与 `/faust/command` 等接口
4. 启动前端 Electron 应用
5. 前端 `electron-main.js` 建立与 `/faust/command` 的 websocket 连接
6. 前端 `app.js` 负责文字聊天、音频输入、Live2D 展示与 Nimble 窗口
7. 用户通过文本/语音与 Agent 交互
8. Agent 在后端调用 `llm_tools.py` 中注册的工具执行文件操作、RAG 检索、GUI 交互等能力
9. 后端通过 `backend2front.py` 把结果或动作命令推回前端
10. 配置中心窗口通过 `/faust/admin/*` 接口管理配置、Agent 与运行时重载

---

## 七、配置中心与动态重载补充说明

当前项目已经支持一个稳定版配置/控制程序，设计目标是：

- 不要求用户手改 JSON 文件
- 不要求用户在编辑器里直接修改 Agent 核心 Prompt
- 在不重启 FastAPI 进程的情况下，重新加载配置并重建 Agent runtime

### 配置中心可管理的内容

- AI Provider 相关密钥、Base URL、模型名
- 当前 Agent 选择
- Agent 核心文件：`AGENT.md`、`ROLE.md`、`COREMEMORY.md`、`TASK.md`
- Live2D 默认模型与展示参数
- Minecraft bridge / RAG / 安全审查等后端配置

### 动态重载的定义

这里的“动态重载”不是热替换整个 Python 进程，而是：

1. 重新读取公开/私密配置文件
2. 更新运行时配置变量与环境变量
3. 重新计算当前 Agent 与 Prompt
4. 重新创建 Agent runtime
5. 尽量保持 FastAPI 主进程持续运行

### Agent 删除策略

- 默认禁止删除当前正在使用的 Agent
- 默认禁止删除 `faust`
- 其他 Agent 允许通过配置中心删除

---

## 八、适合优先阅读的文件顺序

如果你要快速理解项目，建议按下面顺序阅读：

1. `faust/README.md`
2. `backend/backend-main.py`
3. `backend/faust_backend/admin_runtime.py`
4. `backend/faust_backend/config_loader.py`
5. `backend/faust_backend/llm_tools.py`
6. `backend/faust_backend/backend2front.py`
7. `backend/faust_backend/trigger_manager.py`
8. `backend/faust_backend/plugin_system/manager.py`
9. `document/plugin.md`
10. `frontend/electron-main.js`
11. `frontend/configer_pyside6.py`
12. `frontend/app.js`
13. `frontend/index.html` + `styles.css`
14. `backend/agents/<agent_name>/` 下的 Prompt 文件

---

## 九、总结

当前 `Faust` 项目可以理解为：

- **后端**：一个基于 FastAPI + LangChain/LangGraph 的 Agent 主服务，负责上下文管理、工具调用、配置中心 API 和前后端协调。
- **前端**：一个基于 Electron + Live2D 的桌宠/虚拟形象界面，负责展示、语音、文本交互和动态窗口渲染；同时包含一个独立配置中心窗口。
- **工具层**：通过 `llm_tools.py` 将本地文件、系统命令、搜索、RAG、GUI 控制等能力暴露给 Agent。
- **配置控制层**：通过 `admin_runtime.py`、`/faust/admin/*` 接口和独立配置窗口，实现配置编辑、Agent 管理和运行时动态重载。
- **插件扩展层**：通过 `plugin_system` + `backend/plugins/` 提供 Tool/Middleware 扩展、Trigger 控制、热重载与心跳机制。
- **Prompt 层**：通过 `agents/<agent_name>/` 中的 Markdown 文件定义 Agent 的人格、记忆和任务。
