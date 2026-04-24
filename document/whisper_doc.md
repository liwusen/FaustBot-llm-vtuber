### 📝 快速上手 Whisper

使用 Whisper 非常直接，以下是核心步骤：

1.  **安装**：
    ```bash
    pip install openai-whisper
    ```
    > 如果需要加速，建议安装 `whisper` 的同时安装 `setuptools-rust`。

2.  **极简代码示例**：
    ```python
    import whisper

    # 加载模型（可选 tiny, base, small, medium, large）
    # 模型越小速度越快，但精度稍低。base 模型对大多数场景已足够好。
    model = whisper.load_model("base")

    # 转写音频文件
    result = model.transcribe("你的语音文件.mp3")

    # 打印识别出的文字
    print(result["text"])
    ```

你可以从 `tiny` 或 `base` 模型开始尝试，在速度和准确率之间找到适合你项目的平衡点。