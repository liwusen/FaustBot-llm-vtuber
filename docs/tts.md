# GPT-Sovits(Local TTS)配置指南

GPT-Sovits是基于Vits的语音克隆框架。

这是该项目的链接[RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)

使用GPT-Sovits需要显存>=4G的NVIDIA显卡和大约20G的额外磁盘空间

## 1.选择正确的显卡版本

打开配置程序的 组件页面,如图。

![](assets/comp.png)

按照GPU版本选择TTS板块的选择框

如果是Nvidia 50系列显卡,选择 `NVIDIA 50系列`

否则,请选择`Standard`

## 2. 下载Sovits整合包

点击TTS模块的下载,等待最下方的进度条走完

## 3.启动Sovits服务

点击TTS模块的 启动服务

多刷新几次,等待服务状态变为绿色`运行中`

## 4.切换TTS模式到local

进入语音页面,把TTS模式改为`local`

点击 保存 和 应用
