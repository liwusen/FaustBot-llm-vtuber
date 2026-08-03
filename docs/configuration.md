# FaustBot 快速配置说明

FaustBot 的所有配置通过 **配置中心（Configer）** 完成，完成配置大约需要10分钟

## 前置知识

如果您没有了解/使用过 AI 软件，请先阅读[基本概念](agent-basics.md)

## 打开配置中心

启动 FaustBot 后，右键托盘图标或在主窗口点击配置按钮，即可打开 Configer 窗口。

Configer 左侧边栏按功能分为 13 个模块，每个模块对应一组相关配置。

## 必填配置：对话 LLM 服务

这是 **唯一必须配置的模块**——没有 API 凭证，AI功能无法运行。

进入 **AI 服务商** 页面。该页面以 **Provider（服务商）** 为单位管理模型：

### 添加 Provider

点击 **添加 Provider** 按钮，在弹出的窗口中填写：

| 配置项 | 说明 |
| ---- | ----------------------------------------------- |
| 名称 | 自定义服务商名称，如 `deepseek`、`openai` |
| Base URL | API 接口地址，如 `https://api.deepseek.com/v1` |
| API Key | 如 `sk-xxxx` |

保存 Provider 后，可以在窗口内的 **模型管理** 中手动添加模型名，也可以点击 **自动加载** 按钮从服务商的 `/models` 接口拉取可用模型列表。

### 选择主模型与 Subagent 模型

保存 Provider 后，页面下方的 **Models 列表** 会汇总所有 Provider 的模型，每一行包含：

| 列 | 说明 |
| --- | --- |
| Provider | 模型所属服务商 |
| 模型 | 模型名 |
| 主模型 | 单选（radio），勾选后作为主 Agent 的对话模型 |
| Subagent | 多选（checkbox），勾选后允许作为 Subagent（子任务代理）使用 |
| 操作 | 编辑/删除模型 |

> **注意**：模型以 `服务商名::模型名`（如 `deepseek::deepseek-v4-flash`）的形式被引用，主模型只能选择一个，Subagent 模型可以勾选多个。

### 保存

所有 Provider 与模型的选择修改都**不会单独保存**，修改完成后点击窗口顶部的 **保存** 按钮统一生效。服务商凭证（API Key）会保存在本地配置文件中，不会随公共配置上传。

### 高级：思考模式

Provider 可配置思考模式（thinking），部分服务商（如 DeepSeek）的模型支持思考（reasoning）。将思考模式设为"无"可关闭思考，加快响应速度。

### 常见服务商填写示例

| 服务商 | Base URL | 模型名（示例） |
| ---- | ------------------------------- | ------------------------- |
| OpenAI | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 硅基流动 | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` |
| OpenRouter | `https://openrouter.ai/api/v1` | `openai/gpt-4o` |
| 本地 Ollama | `http://localhost:11434/v1` | `qwen2.5:7b` |

## 选填配置：Embedding 模型

Embedding 模型用于记忆系统的编码，填写后记忆系统才能发挥全部功能

Embedding 模型同样有 Embedding 模型、Embedding 接口地址与 Embedding 密钥三个项目。格式类似对话 LLM 服务的配置

特别的，FaustBot 要求 Embedding 模型支持 1536 维度的输出

以下是经过测试可用的模型列表

| 平台        | 模型                     |
| --------- | ---------------------- |
| 阿里云(Qwen) | text-embedding-v4      |
| OpenAI    | text-embedding-3-small |

> **离线模式**：如果没有 Embedding API Key，可在 Configer **AI 服务商**模块中的高级配置开启 "BM25 Only 模式"，此时仅使用本地关键词检索，不需调用外部 Embedding API。

## 语音识别和语音生成（语音页面）

语音识别和语音生成的功能如下：

- 语音识别：用户对 AI 说话
- 语音生成：AI 可以说话

### 可用的语音识别模式

| 名称      | 解释 | 配置指南 |
| --------- | ---- | -------- |
| Whisper（默认） | 使用 OpenAI Whisper 进行本地语音识别，可配置模型大小、语言与初始提示词，对中文识别友好 | [Whisper 配置指南](whisper.md) |
| FunASR | 使用 FunASR 进行本地语音识别，需要占用 >3G 的内存，可选使用 GPU 推理，准确性好 | [FunASR 配置指南](funasr.md) |
| OpenAI  | 使用 OpenAI API 进行语音识别，不建议使用 | 无 |

### 可用的语音生成模式

| 名称       | 解释 | 配置指南 |
| ---------- | ---- | -------- |
| gpt-sovits    | 使用经典的 GPT-SoVITS 进行本地语音生成，**支持克隆声音**，但**必须要显存 >4G 的显卡** | [gpt-sovits TTS 配置指南](tts.md) |
| OpenAI   | 使用 OpenAI API 进行语音生成，不建议使用 | 无 |
| Edge-tts | 使用 Microsoft Edge API 进行语音生成，无需任何配置，默认选项 | 无（默认） |

> **注释**：FaustBot Cloud 这一项尚不可用，参见 [FaustBot Cloud 仓库](https://github.com/liwusen/FaustBot_Cloud)
