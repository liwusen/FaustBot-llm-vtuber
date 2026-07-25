# Repository Guidelines

## Project Overview

FaustBot is an AI-driven Vtuber/desktop pet platform with a **Python FastAPI backend** (port 13900) and **Electron frontend**. The backend runs a **LangGraph-based agent** with tool calling, multi-modal memory (vector + knowledge graph + BM25), full-duplex speech (VAD → ASR → Agent → TTS), trigger scheduling, and a plugin system. The frontend renders Live2D/VRM avatars with real-time lip sync and gesture animation.

## Core architecture

### 目录结构

```
faust/
├── backend/                    # Python FastAPI 后端
│   ├── main.py                 # 入口，FastAPI app 创建、路由注册、启动
│   ├── faust_backend/          # 核心模块
│   │   ├── runtime/            # 运行时生命周期、状态管理
│   │   ├── agent/              # Agent 相关（LangGraph）
│   │   ├── plugin_system/      # 插件系统（pluggy）
│   │   ├── memory/             # 多模态记忆（向量 + 知识图谱 + BM25）
│   │   ├── speech/             # 语音处理（VAD/ASR/TTS）
│   │   ├── tools/              # Agent 工具集
│   │   ├── routes/             # FastAPI 路由
│   │   ├── events/             # 事件系统
│   │   └── services/           # 后台服务
│   └── default_plugins/        # 内置插件
├── frontend/                   # Electron 前端
│   ├── electron-main.js        # Electron 主进程
│   ├── index.html              # 主界面
│   └── scripts/                # 前端脚本
└── .runtime/                   # Python 运行时环境
```

### 核心组件

1. **FastAPI 后端** (`backend/main.py`)
   - 端口: 13900
   - 生命周期: `lifespan()` 管理启动/关闭
   - 路由: admin_config, chat, audio, plugins 等

2. **LangGraph Agent** (`runtime/lifecycle.py`)
   - 使用 `langchain.agents.create_agent()` 创建
   - 支持 tool calling、reasoning/thinking 模式
   - Checkpoint: SQLite 持久化 or InMemory

3. **插件系统** (`plugin_system/`)
   - 基于 `pluggy` 实现
   - Hook: `plugin_loaded`, `heartbeat`, `register_routes`, `register_tools` 等
   - 支持工具注入、中间件、前端资源

4. **多模态记忆** (`memory/`)
   - `GraphStore`: 知识图谱 + 向量存储
   - 支持文件写入、聊天记录同步

5. **语音管道** (`speech/`)
   - VAD: 语音活动检测 (`vad_runtime.py`)
   - ASR: 语音识别 (`speech/asr/`)
   - TTS: 语音合成 (`speech/tts/`)
   - 支持本地/云端模式

6. **Agent 工具** (`tools/`)
   - 文件操作: `read`, `write`, `edit`, `find`, `search`
   - 系统: `execute`, `system`, `datetime`
   - 记忆: `memory`, `diary`
   - 特殊: `skill`, `subagent`, `trigger`, `vfs`

7. **触发器系统** (`trigger_manager.py`)
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
   
   异步/同步选择策略:
   
   在网络通信/耗时操作等时，使用异步(async)进行操作，提高效率

   使用With/async with:
   
   对于Lock和文件的Open,应该使用(异步)上下文管理器
   
   脏数据策略:
   
   保证这个项目的所有内部数据文件都只会被这个项目的程序读写,你无需过多考虑**运行时磁盘数据被其他程序修改**的脏数据

   避免使用getattr获取依赖模块的函数/属性:

   调用库/类的成员/attr时,你不应该使用getattr去猜测和试探,而是通过真实的代码读取获取到真正的成员名称

   避免使用Windows API(winsdk,ctypes调用):
  
   除非只有Windows API/外部dll能满足需要,否则不要调用外部dll/windows API

   不要为FaustBot Agent添加冗余工具:
  
   对于简单的信息读取功能,使用 vfs 虚拟文件系统 中的文件放置数据

3. 当你修改了这个项目的依赖时，请在` requirements.txt `中修改

4. 当你修改了AI Agent的能力时，请务必编写 Faust 角色的 PROMPT (修改agents/faust/xxxxx.md)，介绍这些能力，并且告诉它使用条件

5. 在编写文档时,尽可能不要用ASCII Art的形式画图,而是使用Memarid这样的工具

6. 无论进行何种修改,都需要确保单元测试通过

7. 在需要时,可以使用如下方法进行集成测试:

   1. 启动后端服务

   2. 在启动前端时附加命令行参数,让它打开CDP端口

   2. 使用agent-browser命令(如有)或者Playwright连接到CDP端口

   3. 进行操作

   4. 关闭前端和后端