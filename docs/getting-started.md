# 项目介绍

**FaustBot** 是一个 AI 驱动的 Vtuber / 桌面宠物平台。它不仅能对话，还能操作你的电脑——读文件、执行代码、搜索网页、甚至玩 Minecraft。

## 核心能力

| 能力           | 说明                                     |
| ------------ | -------------------------------------- |
| 多模态对话        | 文字 + 语音输入输出，支持摄像头 / 屏幕截图理解             |
| 长期记忆         | 树状记忆系统：向量搜索 + 知识图谱 + BM25 关键词检索        |
| Live2D / VRM | 实时渲染 Live2D 或 VRM 模型，支持唇形同步和手势动画       |
| 工具集          | 文件读写、代码执行、网页搜索、浏览器自动化、Minecraft 控制     |
| 插件系统         | 兼容 Openclaw Skill / ClawHub 技能，MCP 客户端 |
| 灵动窗口         | 前端 HTML 小窗口交互，AI 可创建自定义 UI             |
| Araya 离线代理   | 空闲时自动挖掘知识图谱，寻找隐藏关联                     |

## 技术架构

- **Backend**: Python FastAPI + LangGraph Agent + nano-vectordb + networkx 知识图谱
- **Frontend**: Electron + Live2D SDK / VRM + WebSocket 双向通信
- **语音**: Silero VAD + Whisper ASR + Edge/TTS 合成

## 快速链接

- [GitHub](https://github.com/liwusen/FaustBot-llm-vtuber)
- [插件市场](https://liwusen.github.io/FaustBot-llm-vtuber/)
