# FaustBot 快速配置说明

FaustBot 的所有配置通过 **配置中心（Configer）** 完成,完成配置大约需要10分钟

## 前置知识

如果您没有了解/使用过AI软件,请先阅读[基本概念](agent-basics.md)

## 打开配置中心

启动 FaustBot 后，右键托盘图标或在主窗口点击配置按钮，即可打开 Configer 窗口。

Configer 左侧边栏按功能分为 13 个模块，每个模块对应一组相关配置。

## 必填配置：对话 LLM 服务

这是 **唯一必须配置的模块**——没有 API 凭证，AI功能无法运行。

进入 **AI 服务商** 页面：

| 配置项     | 说明                                              |
| ------- | ----------------------------------------------- |
| 主对话模型   | 填写模型名称，如 `gpt-4o`、`deepseek-chat`               |
| 主对话接口地址 | 填写 API Base URL，如 `https://api.deepseek.com/v1` |
| 主对话密钥   | 填写 API Key，如 `sk-xxxx`                          |

### 常见服务商填写示例

| 服务商        | 主对话接口地址                         | 模型名                       |
| ---------- | ------------------------------- | ------------------------- |
| OpenAI     | `https://api.openai.com/v1`     | `gpt-4o`                  |
| DeepSeek   | `https://api.deepseek.com/v1`   | `deepseek-chat`           |
| 硅基流动       | `https://api.siliconflow.cn/v1` | `deepseek-ai/DeepSeek-V3` |
| OpenRouter | `https://openrouter.ai/api/v1`  | `openai/gpt-4o`           |
| 本地 Ollama  | `http://localhost:11434/v1`     | `qwen2.5:7b`              |

## 选填配置:Embedding 模型

Embedding模型用于记忆系统的编码,填写后记忆系统才能发挥全部功能

Embedding模型同样有Embedding 模型,Embedding 接口地址与Embedding 密钥三个项目。格式类似对话 LLM 服务的配置

特别的,FaustBot要求Embedding模型支持1536维度的输出

以下是经过测试可用的模型列表

| 平台        | 模型                     |
| --------- | ---------------------- |
| 阿里云(Qwen) | text-embedding-v4      |
| OpenAI    | text-embedding-3-small |

> **离线模式**：如果没有 Embedding API Key，可在 Configer **AI 服务商**模块中的高级配置开启 "BM25 Only 模式"，此时仅使用本地关键词检索，不需调用外部 Embedding API。

## 语音识别和语音生成(语音页面)

语音识别和语音生成的功能如下:

语音识别:用户对AI说话

语音生成:AI可以说话

#### 以下是可用的语音识别模式:

| 名称      | 解释                                       | 配置指南                    |
| ------- | ---------------------------------------- | ----------------------- |
| Local模式 | 使用funasr进行语音识别,需要占用>3G的内存,可选使用GPU推理,准确性好 | [FunASR配置指南](funasr.md) |
| OpenAI  | 使用OpenAI API进行语音识别,不建议使用                 | 无                       |

#### 以下是可用的语音生成模式:

| 名称       | 解释                                                | 配置指南                    |
| -------- | ------------------------------------------------- | ----------------------- |
| Local    | 使用经典的GPT-Sovits进行语音生成,**支持克隆声音**,但**必须要显存>4G的显卡** | [Local TTS配置指南](tts.md) |
| OpenAI   | 使用OpenAI API进行语音生成,不建议使用                          | 无                       |
| Edge-tts | 使用Microsoft Edge API进行语音生成,无需任何配置,默认选项            | 无(默认)                   |

*注释:fausbot-cloud这一项尚不可用,参见[FaustBot Cloud仓库](https://github.com/liwusen/FaustBot_Cloud)*
