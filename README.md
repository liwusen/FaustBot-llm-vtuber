<p align="center">
    <img src="./docs/assets/FaustBot.icon.tiny.png" alt="FaustBot icon" />
</p>
<div align="center">
<h1 align="center">FaustBot</h1>
<a href="https://deepwiki.com/liwusen/FaustBot-llm-vtuber" align="center">
    <img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki" align="center"/></a>
</div>

### 一个AI驱动的 Vtuber/桌宠

### 🎉Faustbot 2.0 已发布!

---

> [!WARNING]
> 使用前须知:本项目仅供技术参考和学习。不得使用本软件生成不符合法律法规的内容，否则后果自负

---

### 功能列表

- [x] 多角色支持

- [x] ASR 语音识别

- [x] TTS 人声输出

- [x] 音乐播放（唱歌）

- [x] 多模态树状 长期记忆系统

- [x] 灵动交互系统 (前端HTML小窗口交互)

- [x] 编辑文件，文件读写等基本能力

- [x] 多模态能力:读屏幕\摄像头\图片

- [x] 在线搜索

- [x] AI 玩 Minecraft (基于Mineflyer构建，无缝体验)

- [x] 多功能插件系统 [插件市场](https://liwusen.github.io/FaustBot-llm-vtuber/)

- [x] 兼容Openclaw Skill && Clawhub 技能

- [x] 云端语音识别/文字转语音支持

- [x] 安全系统，限制Agent的访问权限，并对模型命令进行审核

- [x] 高效的工具实现

- [x] MCP 客户端支持

- [x] AI自动操作游览器

- [x] 歌曲翻唱

---

### 原角色设定

> 浮士德 （FAUST）是《边狱公司》及其衍生作品的登场角色。 原型来源歌剧 《浮士德》。 该罪人为我司巴士打造了“梅菲斯特号”引擎。 她声称自己是都市中最聪慧的存在，没有人能在智慧层面上与她相媲美，这可能并非谬论。 当她应允与您交谈时，您会发现她的态度高高在上，令人不悦。 她对待所有人都有一股微妙的傲慢态度，这似乎永远都无法改变了，因此，我们建议您只要应付一下，点点头就成。

来源于游戏《Limbus Company》,引用自[边狱公司中文维基](https://limbuscompany.huijiwiki.com/wiki/%E9%A6%96%E9%A1%B5)

---


### 致谢

参考了 [morettt/my-neuro](https://github.com/morettt/my-neuro)(asr_api.py,ASR.bat,TTS.bat)

TTS基于 [RVC-Boss/GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)

mc-operator基于 [PrismarineJS/mineflayer](https://github.com/PrismarineJS/mineflayer)

歌曲翻唱基于 [Plachtaa/seed-vc](https://github.com/Plachtaa/seed-vc)

Agent 的部分工具,虚拟文件系统参考 [can1357/oh-my-pi](https://github.com/can1357/oh-my-pi)

## 技术实现

| 部分       | 实现                    |
| -------- | --------------------- |
| Backend  | Python为主体,基于langchain |
| Frontend | Electron          |