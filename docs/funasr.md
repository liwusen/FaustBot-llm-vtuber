# FunASR (local ASR) 配置指南

1. 进入配置中心

2. 打开组件页面

   ![](assets/comp.png)

3. 在 FunASR (ASR) 模块中，使用复选框选择使用哪种 PyTorch 推理框架

   包括 cpu, cu121, cu128, cu130

   建议使用 cpu 推理，默认 cpu 即可

   勾选"使用阿里云镜像"

   如果想要使用 GPU 推理，请见 PyTorch 版本选择

4. 页面最下方将会出现日志，等待进度条走到最右边

5. 点击 FunASR (ASR) 模块的"启动服务"

6. 多刷新几次，等待服务状态变为绿色"运行中"

7. 进入语音页面，把 ASR 模式改为 local，点击保存和应用

# PyTorch 版本选择

| 显卡CUDA版本 | PyTorch |
| -------- | ------- |
| 12.0+    | cu121   |
| 12.8+    | cu128   |
| 13.0+    | cu130   |
| 无        | cpu     |
