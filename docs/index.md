# FaustBot

AI 驱动的虚拟主播 / 桌面宠物 —— 能对话、能操作电脑、能玩 Minecraft

[快速开始](#快速安装) | [GitHub](https://github.com/liwusen/FaustBot-llm-vtuber)

## 核心特性

| 特性 | 说明 |
|------|------|
| **多模态对话** | 文字 + 语音输入输出，支持摄像头 / 屏幕截图理解，实时 VAD 打断 |
| **长期记忆** | 向量搜索 + 知识图谱 + BM25 关键词，多模态树状记忆系统 |
| **Live2D / VRM** | 实时渲染 Live2D 或 VRM 模型，唇形同步、手势动画、表情驱动 |
| **强力工具集** | 文件读写、代码执行、网页搜索、浏览器自动化、GUI 控制 |
| **Minecraft 集成** | AI 控制 Mineflyer 机器人，在游戏中建造、探索、战斗 |
| **插件 & MCP** | 兼容 Openclaw Skill / ClawHub 技能，原生 MCP 客户端支持 |

## 技术架构

| 组件 | 技术栈 |
|------|--------|
| **Backend** | Python FastAPI + LangGraph Agent |
| **Frontend** | Electron + Live2D SDK / VRM |
| **语音** | Silero VAD + Whisper ASR + Edge TTS |
| **记忆** | nano-vectordb + networkx + BM25 |
| **通信** | WebSocket 双向 + REST 管理接口 |
| **平台** | Windows 10/11 x64 |

## 快速安装

下载最新的整合包，解压，双击 `frontend/start.bat`

> 详见 [安装指南](installation.md)

## 文档

- [项目介绍](getting-started.md)
- [安装指南](installation.md)
- [配置说明](configuration.md)
- [Agent 基础](agent-basics.md)
- [内置插件](plugins/index.md)
- [MCP Server](mcp.md)
- [开发者环境](devinstall.md)

## 链接

- [GitHub](https://github.com/liwusen/FaustBot-llm-vtuber)
- [插件市场](https://liwusen.github.io/FaustBot-llm-vtuber/)
