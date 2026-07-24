# 基础概念

## 什么是 Agent

AI Agent（智能体）是一个能**自主调用工具**来完成任务的语言模型。不同于普通的"一问一答"聊天机器人，Agent 会：

1. 理解用户意图
2. 决定需要哪些工具
3. 调用工具获取信息或执行操作
4. 根据工具结果继续推理
5. 最终给出完整回复

FaustBot 内部实现了一个Agent.

## OpenAI Chat Completions API

OpenAI API是一套简单的程序使用大模型的规范。

FaustBot 使用 OpenAI 兼容的 API 接口 (即`Openai Chat Completions`)。你只需要一个 API Key ，模型id,和对应的 Base URL。

### API Key

API Key 是你的身份凭证，类似"密码"。从 LLM 服务商获取后填入配置文件：

在 FaustBot 的 **配置中心（Configer）→ AI 服务商 → 主对话密钥** 中填入即可。

格式基本上是`sk-{一大串随机字符}`

### Base URL

不同服务商的 API 地址不同：

这一项应该是一个https://开头的**链接**

在 Configer 的 **AI 服务商 → 主对话接口地址** 中填入。

常见服务商的 Base URL：

| 服务商        | Base URL                        |
| ---------- | ------------------------------- |
| OpenAI     | `https://api.openai.com/v1`     |
| DeepSeek   | `https://api.deepseek.com/v1`   |
| 硅基流动       | `https://api.siliconflow.cn/v1` |
| OpenRouter | `https://openrouter.ai/api/v1`  |
| 本地 Ollama  | `http://localhost:11434/v1`     |

## Embedding API

Embedding模型用于FaustBot记忆库。

Embedding模型可以把一段文本编码为一串数字(向量),程序可以通过计算向量间的相似性来找出相关的文本。

在FaustBot中,记忆库的搜索使用了Embedding模型

Embedding API的配置同样包括 Base URL,API和Model

## 工具调用

工具（Tool）是 AI的"手和脚"——让模型不仅能"说"，还能"做"。

工具的能力，实际上决定了一个LLM模型能够进行什么样的操作

### 工作原理

```
用户: "帮我写一个贪吃蛇游戏"
       │
       ▼
   Agent 推理: 需要 write 工具
       │
       ▼
   Agent 调用: write("snake.py", "...代码...")
       │
       ▼
   工具返回: "已写入 snake.py"
       │
       ▼
   Agent 回复: "已为你创建了贪吃蛇游戏，保存在 snake.py"
```

### FaustBot 的主要工具集

| 工具          | 功能              |
| ----------- | --------------- |
| `read`      | 读取文件            |
| `write`     | 创建/修改文件         |
| `edit`      | 精确代码编辑 (diff)   |
| `execute`   | 运行 Python/系统命令  |
| `search`    | 网页搜索            |
| `find`      | 项目内文件搜索         |
| `minecraft` | Minecraft 机器人控制 |
| `nimble`    | 灵动交互窗口          |
| `browser`   | 浏览器自动化          |
| `memory`    | 记忆库读写           |
| `trigger`   | 定时任务管理          |

### 工具输出截断

为防止工具输出过长消耗大量 Token，FaustBot 对长输出自动截断：

- 短输出（≤120 字符单行）→ 原样返回
- 长输出 → 截断为 500 字符摘要 + `[完整输出: artifact://<id>]`
- Agent 可调用 `read("artifact://<id>")` 获取完整内容

## 模型思维链

思维链（Chain of Thought）是模型在给出最终答案前的**内部推理过程**。

### 普通对话 vs 思维链

```
普通: 用户问 → 模型答

思维链:
用户问 → 模型: "我需要先理解这个问题..."
      → 模型: "让我考虑几种方案..."
      → 模型: "方案A的问题是...方案B更合适..."
      → 最终答案
```

### 为什么重要

- **复杂任务**: 数学计算、代码调试需要分步推理
- **透明性**: 你可以看到模型"在想什么"
- **可纠正**: 发现推理错误时可以纠正方向
- **工具决策**: Agent 选择哪个工具时，思维链解释了为什么

### 在 FaustBot 中

FaustBot 支持两类模型：

| 类型   | 说明          | 示例                        |
| ---- | ----------- | ------------------------- |
| 普通模型 | 一次生成最终答案    | `gpt-4o`, `deepseek-chat` |
| 推理模型 | 先输出思维链再输出答案 | `deepseek-r1`, `o1`, `o3` |

推理模型会先进行内部推理（你可以在界面中看到思考过程），然后再执行工具调用或给出回复。这让 Agent 在复杂任务上表现更可靠。

## 上下文窗口

每次对话，Agent 能看到的内容包括：

1. **系统提示**: Agent 的角色设定和行为规范
2. **对话历史**: 之前的所有用户消息和助手回复
3. **工具调用记录**: 工具名称、参数、返回结果
4. **记忆**: 从记忆库中检索的相关信息

这些内容一起构成**上下文窗口**（Context Window）。窗口大小由模型决定（如 128K tokens），超出部分会被截断。

## 记忆系统

FaustBot 的记忆不是简单的"聊天记录"，而是三层检索系统：

| 层级    | 技术            | 用途     |
| ----- | ------------- | ------ |
| 向量搜索  | nano-vectordb | 语义相似匹配 |
| 关键词搜索 | BM25          | 精确词匹配  |
| 知识图谱  | networkx      | 实体关系推理 |

Agent 在回复前会自动检索记忆库，找到相关信息注入上下文。

## MCP 扩展

MCP（Model Context Protocol）让 Agent 连接外部工具服务器：

```
FaustBot ──stdio/sse──→ MCP Server (如 Playwright)
                            │
                            ├── browser_navigate
                            ├── browser_click
                            └── browser_snapshot
```

一个 MCP Server 可以提供多个工具。FaustBot 自动将它们注册为可用工具，Agent 调用时完全透明。详见 [MCP Server 教程](mcp.md)。

## 下一步

- [安装 FaustBot](installation.md)
- [配置说明](configuration.md)
- [MCP Server](mcp.md)
