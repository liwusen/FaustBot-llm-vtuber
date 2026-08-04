# FaustBot 使用指南

本技能介绍 FaustBot 的整体架构、用户数据目录（`~/.faustbot`）的主要文件与目录用途，以及官方文档站点入口。当用户询问 FaustBot 的构成、配置存放位置、数据目录结构，或想查阅完整文档时使用本技能。

## FaustBot 架构概览

FaustBot 由两个进程组成：

- **后端**：Python FastAPI 服务（默认端口 `13900`）。负责运行 LangGraph Agent、语音管道（VAD→ASR→Agent→TTS）、多模态记忆（向量 + 知识图谱 + BM25）、触发器调度与插件系统。
- **前端**：Electron 桌面应用。渲染 Live2D/VRM 虚拟形象，提供实时口型同步、手势动画与 WebSocket 聊天。

核心组件：

| 组件 | 位置 | 说明 |
|------|------|------|
| FastAPI 入口 | `backend/main.py` | 应用创建、路由注册、生命周期管理 |
| LangGraph Agent | `backend/faust_backend/runtime/lifecycle.py` | 主 Agent 构建，支持 tool calling 与 thinking/reasoning 模式 |
| 插件系统 | `backend/faust_backend/plugin_system/` | 基于 pluggy，支持工具注入、中间件、前端资源 |
| 多模态记忆 | `backend/faust_backend/memory/` | GraphStore：知识图谱 + 向量存储 + BM25 |
| 语音管道 | `backend/faust_backend/speech/` | VAD 检测 → ASR 识别 → Agent 处理 → TTS 合成 |
| Agent 工具 | `backend/faust_backend/tools/` | read/write/edit/execute/subagent/trigger/vfs 等 |
| 触发器 | `backend/faust_backend/trigger_manager.py` | 定时触发与事件触发 |
| 虚拟文件系统 | `backend/faust_backend/tools/vfs.py` | `faustbot://` 协议，Agent 读取插件数据、配置 |
| 前端 | `frontend/` | Electron 桌面应用 + Live2D/VRM 渲染 |

## 用户数据目录（~/.faustbot）

FaustBot 的所有用户数据都存放在 `~/.faustbot/`（Windows 下为 `C:\Users\<用户名>\.faustbot\`）。

### 根目录文件

| 文件 | 用途 |
|------|------|
| `faust.config.json` | 公共配置（TTS/ASR 模式、模型路径、Live2D/VRM 设置等） |
| `faust.config.private.json` | 私有配置（API Key 等敏感项，不随公共配置同步） |
| `provider.private.json` | **AI Provider 配置**：服务商列表（名称/Base URL/Key/模型/思考格式）、主模型与 Subagent 模型选择 |
| `ui-settings.json` | 前端 UI 设置 |
| `vrm_config.json` / `vrm_poses.json` | VRM 模型配置与姿势预设 |
| `blive_config.json` | B 站直播弹幕监听配置 |
| `desktop-mood.rules.json` | 桌面心情（desktop-mood 插件）规则 |
| `persistent_nimble.json` | Nimble 窗口持久化数据 |
| `website_content.md` | 网站内容（供 Agent 参考的资料） |

### 目录

| 目录 | 用途 |
|------|------|
| `agents/` | 角色目录，每个角色一个子目录（如 `agents/faust/`）。含 AGENT.md / ROLE.md / COREMEMORY.md / TASK.md 等角色文件 |
| `agents/faust/` | 当前主角色的完整数据：记忆库、日记、知识库、检查点、Subagent 数据、触发器、Skill |
| `agents/faust/skill.d/` | 已安装的 Skill（内置 Skill 启动时自动复制到这里） |
| `agents/faust/memory/` | 记忆库（content/graph/meta/attachments） |
| `agents/faust/diary/` | 日记 |
| `agents/faust/kb/` | 知识库（树形 + 向量索引） |
| `agents/faust/faust_checkpoint.db` | Agent 会话检查点（SQLite） |
| `agents/faust/subagents.db` / `subagents.json` | Subagent 状态与持久化 |
| `agents/faust/triggers.json` | 触发器配置 |
| `models/` | 虚拟形象模型（2D Live2D / VRM / image） |
| `plugins/` | 用户安装的插件 |
| `plugin_data/` | 插件的运行时数据（desktop-mood、rss-watcher、song-studio 等） |
| `voices/` | 本地 TTS 参考音频与文本 |
| `prompts/` | 提示词模板（如 Subagent 的 worker_template.md） |
| `logs/` | 日志 |
| `cache/` | 缓存（如 Edge TTS 音色列表） |
| `data/` | 运行时数据（如 Araya 最后 trace） |
| `__dev__/` | 开发与计划笔记（不参与运行） |

### 角色目录中的关键子目录（agents/faust/）

| 文件/目录 | 用途 |
|-----------|------|
| `AGENT.md` / `ROLE.md` / `COREMEMORY.md` / `TASK.md` | 角色四大文件：核心指示 / 角色设定 / 核心记忆（可写）/ 任务参考指南 |
| `memory/` | 长期记忆（graph.json 为知识图谱） |
| `kb/` + `kb_index/` + `kb_meta/` | 知识库内容、向量索引与元数据 |
| `rag_chat_history_records.json` | 聊天记录同步（RAG 用） |
| `rag_doc_tracker.json` | RAG 文档追踪 |
| `record/` | 对话记录 |
| `artifact.json` | 最近的工具输出 artifact 索引 |

## 文档站点

FaustBot 的完整文档站点基于 MkDocs 构建，发布在：

- **https://faustbot.allenlee.xyz/**

包含：项目介绍、安装指南、配置说明、Agent 基础概念、MCP、插件开发指南、语音配置（GPT-SoVITS / FunASR）等。源码中的 `docs/` 目录即文档源文件（`mkdocs.yml` 定义导航结构）。

## 使用建议

- 用户问"这个配置放在哪" → 查上面的 `~/.faustbot` 表格
- 用户问"FaustBot 是怎么工作的" → 读架构概览部分
- 用户想深入了解某功能 → 引导到 https://faustbot.allenlee.xyz/
- 配置中心（Configer）中大部分设置都写回 `~/.faustbot` 下的 JSON 文件，直接改文件前建议先通过配置中心操作
