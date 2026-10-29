# Repository Guidelines

## Project Overview

FaustBot is an AI-driven Vtuber/desktop pet platform with a **Python FastAPI backend** (port 13900) and **Electron frontend**. The backend runs a **LangGraph-based agent** with tool calling, multi-modal memory (vector + knowledge graph + BM25), full-duplex speech (VAD → ASR → Agent → TTS), trigger scheduling, and a plugin system. The frontend renders Live2D/VRM avatars with real-time lip sync and gesture animation.

## Core architecture

### 目录结构

```
faust/
├── backend/                    # Python FastAPI 后端
│   ├── main.py                 # 入口：建 app、register_routes、start_services
│   ├── faust_backend/          # 核心模块（领域包）
│   │   ├── core/               # 跨切面基础设施（叶子，不 import 业务包）
│   │   │                       #   config(唯一路径锚点) / log / message / events /
│   │   │                       #   frontend / utils / security / uri / ui_settings
│   │   ├── llm/                # 模型接入层（provider/formats/thinking/models_dev）
│   │   ├── agent/              # 一切「会思考的东西」
│   │   │   ├── runtime/        #   state / invoke / build / rebuild / lock /
│   │   │   │                   #   compact / scheduler / middleware/ / turn/
│   │   │   ├── subagent/       #   manager / events
│   │   │   ├── araya/          #   辅助 Agent 运行时
│   │   │   ├── prompt/         #   character_card / seed
│   │   │   ├── skill.py        #   技能管理
│   │   │   └── trigger.py      #   触发器管理
│   │   ├── live/               # B 站直播（manager / mode）
│   │   ├── avatar/             # 虚拟形象配置（vrm_config / vrm_pose）
│   │   ├── integrations/       # 外部系统（mcp / minecraft / nimble / omnijev / searchapi）
│   │   ├── services/           # 后台服务与运维（admin / manager / update / components）
│   │   ├── plugins/            # 插件生态（system/ = pluggy 主体，market.py）
│   │   ├── api/                # 只做 HTTP/WS 适配（admin/ chat/ system/ + 单文件）
│   │   ├── app/                # 进程装配：lifespan.py + services.py
│   │   ├── memory/             # 多模态记忆（向量 + 知识图谱 + BM25）
│   │   ├── speech/             # 语音处理（ASR/TTS）
│   │   ├── tools/              # Agent 工具集
│   │   ├── processors/         # 重计算子进程框架（VAD/OCR 等）
│   │   ├── vendor/ blivedm/    # vendored 第三方，勿改
│   │   └── download_vad.py     # ⚠ 唯一豁免：CI 按脚本路径调用，勿搬
│   └── default_plugins/        # 内置插件
├── frontend/                   # Electron 前端
│   ├── electron-main.js        # Electron 主进程
│   ├── index.html              # 主界面
│   └── scripts/                # 前端脚本
└── .runtime/                   # Python 运行时环境
```

> 依赖方向：`core/` 与 `llm/` 是叶子；`agent/` 只依赖 `core`/`llm`/`tools`；
> 只有 `app/` 与 `api/` 知道全部业务包。路径锚点只有 `core/config.py` 一处
> （`BACKEND_ROOT` / `PACKAGE_ROOT` / `REPO_ROOT`），守卫见
> `backend/tests/test_architecture.py`。

### 核心组件

1. **FastAPI 后端** (`backend/main.py`)
   
   - 端口: 13900
   - 生命周期: `app/lifespan.py` 的 `lifespan()` 管理启动/关闭
   - 路由: `api/register_routes(app)` 统一注册（admin / chat / system 分组）
   - 路由模块不写业务逻辑，只做 HTTP/WS 适配

2. **LangGraph Agent** (`agent/runtime/`)
   
   - 使用 `langchain.agents.create_agent()` 创建（`build.py::_create_agent_with_extensions`）
   - 调用入口 `invoke.py`（`invoke_agent_locked` / `stream_chat_agent_events`）
   - 重建入口 `rebuild.py`（`rebuild_runtime`，锁在 `lock.py`）
   - 支持 tool calling、reasoning/thinking 模式
   - Checkpoint: SQLite 持久化 or InMemory

3. **插件系统** (`plugins/system/`)
   
   - 基于 `pluggy` 实现
   - Hook: `plugin_loaded`, `heartbeat`, `register_routes`, `register_tools` 等
   - 支持工具注入、中间件、前端资源

4. **多模态记忆** (`memory/`)
   
   - `GraphStore`: 知识图谱 + 向量存储
   - 支持文件写入、聊天记录同步

5. **语音管道** (`speech/`)
   
   - VAD: 语音活动检测（`processors/builtin/vad.py`，跑在受管子进程内）
   - ASR: 语音识别 (`speech/asr/`)
   - TTS: 语音合成 (`speech/tts/`)
   - 支持本地/云端模式

6. **Agent 工具** (`tools/`)
   
   - 文件操作: `read`, `write`, `edit`, `find`, `search`
   - 系统: `execute`, `system`, `datetime`
   - 记忆: `memory`, `diary`
   - 特殊: `skill`, `subagent`, `trigger`, `vfs`

7. **触发器系统** (`agent/trigger.py`)
   
   - 支持定时触发、事件触发
   - 与插件系统集成

8. **虚拟文件系统** (`tools/vfs.py`)
   
   - `faustbot://` 协议
   - 用于 Agent 读取插件数据、配置等

9. **前端** (`frontend/`)
   
   - Electron 桌面应用
   - Live2D/VRM 虚拟形象渲染
   - WebSocket 与后端实时通信

## [IMPORTANT]Develop Rules

1. 这个项目的Python环境位于.runtime下

2. 在编写时，请遵循以下原则
   
   不进行错误隐瞒:
   
   当程序出现严重错误时，比如依赖库缺失等，不应该忽略这个问题随后静默运行，而是应该立刻报错
   
   不保留冗余代码:
   
   对于已经确定修改过的库和API等，你在调用时**不需要**考虑对于老版本的兼容
   
   异步策略:
   
   尽可能全部使用异步函数
   
   当你想在一个函数中使用异步,结果发现这个这个函数的调用者是同步的,那么,(SHOULD)沿着调用链依次向上寻找,全部改为异步
   
   当你确实无法使用异步时,(MUST)向用户报告,等待用户指示
   
   脏数据策略:
   
   保证这个项目的所有内部数据文件都只会被这个项目的程序读写,你无需过多考虑**运行时磁盘数据被其他程序修改**的脏数据
   
   避免使用getattr获取依赖模块的函数/属性:
   
   调用库/类的成员/attr时,你不应该使用getattr去猜测和试探,而是通过真实的代码读取获取到真正的成员名称
   
   不要为FaustBot Agent添加冗余工具:
   
   对于简单的信息读取功能,使用 vfs 虚拟文件系统 中的文件放置数据
   
   不要(MUST NOT)使用 紫黑色 UI风格:
   
   所有前端页面使用和当前Configer一样的亮色简约设计风

3. 当你修改了这个项目的依赖时，请修改 ` requirements.txt `

4. 当你修改了AI Agent的能力时，请务必修改 Faust 角色的 PROMPT (修改agents/faust/xxxxx.md)，介绍这些能力，并且告诉它使用条件

5. 不要用ASCII Art的形式画图,而是使用Memarid

6. 无论进行何种修改,都需要确保单元测试通过

7. 在用户要求时,可以使用如下方法进行集成测试:
   
   1. 启动后端服务
   
   2. 在启动前端时附加命令行参数,让它打开CDP端口
   
   3. 使用agent-browser命令(如有)或者Playwright连接到CDP端口
   
   4. 进行操作
   
   5. 关闭前端和后端

8. 不允许在主分支(main)上提交,开发提交应该提交到dev branch