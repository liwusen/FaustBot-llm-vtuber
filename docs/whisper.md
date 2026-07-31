# Whisper (本地 ASR) 配置指南

Whisper 是 OpenAI 开源的本地语音识别模型，是 FaustBot 的**默认 ASR 模式**，对中文识别友好，无需额外服务下载即可使用（首次运行会自动下载所选模型权重）。

## 配置步骤

1. 进入配置中心 → 语音页面

2. 把 **ASR 模式** 改为 `whisper`

3. 确认:Whisper 参数是以下值：

   | 配置项 | 说明 | 默认值 |
   | ------ | ---- | ------ |
   | Whisper 模型 | 模型大小，越大越准但越慢（tiny/base/small/medium/large/large-v2/large-v3/turbo） | `small` |
   | Whisper 语言 | 识别语言代码，如 zh、en、ja；留空自动检测 | `zh` |
   | Whisper 初始提示词 | 传给 Whisper 的 initial_prompt，用于引导识别风格与术语，对中文识别质量影响较大 | `以下是简体中文普通话的句子:` |

4. 点击 **保存** 和 **应用**

> 提示：`initial_prompt` 会显著影响中文标点与用词风格，建议保留或按角色设定微调。
> 若需要 GPU 加速，安装带 CUDA 的 PyTorch 后 Whisper 会自动使用显卡。

如需切换到 FunASR 本地引擎，请参见 [FunASR 配置指南](funasr.md)。
