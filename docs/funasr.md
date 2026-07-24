# FunASR (local ASR)配置指南

1. 进入配置中心

2. 打开 组件页面

![](assets/comp.png)

3. FunASR(ASR)模块的,使用复选框选择使用哪种PyTorch推理框架

    包括cpu,cu121,cu128,cu130

    建议使用cpu推理,默认cpu即可

    勾选 使用阿里云镜像 

    如果想要使用GPU推理,请见`PyTorch 版本选择`

4. 页面最下方将会出现日志,等待进度条走到最右边

5. 点击FunASR (ASR)模块的 启动服务

6. 多刷新几次,等待服务状态变为绿色 运行中

7. 进入 语音 页面,把ASR模式改为 local,点击 保存 和 应用。

# PyTorch 版本选择

| 显卡CUDA版本 | PyTorch |
| -------- | ------- |
| 12.0+    | cu121   |
| 12.8+    | cu128   |
| 13.0+    | cu130   |
| 无        | cpu     |
