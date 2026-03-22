# FaustBot-llm-vtuber

---

### 一个AI驱动的 Vtuber/桌宠

模仿Neuro Sama

**仍然处于早期开发阶段**

---

### 功能列表

- [x] 多AGENT支持

- [x] ASR 语音识别

- [x] TTS 人声输出

- [x] 音乐播放（唱歌）

- [x] 模型记忆系统(基于RAG系统)

- [x] Agent单独的Workspace

- [x] (独创)灵动交互系统 (前端HTML小窗口交互)

- [x] 编辑文件，文件读写等基本工具

- [x] 调用VLM操作用户电脑

- [x] 在线搜索

- [x] AI 玩 Minecraft (基于Mineflyer构建，无缝体验)

- [x] 高速响应 平均时间<1s

- [ ] 操作网页 (Agent Browser)

- [ ] 给予AI单独的一个可交互Console

- [ ] MCP协议支持

- [ ] 安全系统，限制Agent的访问权限，并对模型命令进行审核

---

### 功能计划(长期)

| 大饼          | 解释                 | 预计时间 |
| ----------- | ------------------ | ---- |
| Minecraft   | 使用Mineflyer，从底层完成  | 完成   |
| 原创Live 2d形象 |                    | 待定   |
| TTS 歌曲转换    |                    |      |
| 游览器 操作      | Agent Browser 能力接入 |      |
| OCR/VLLM支持  |                    |      |
| 前端优化        |                    | 完成   |
| 灵动交互        | 允许AI编写HTML实现交互     | 完成   |

---

### 原角色设定

> 浮士德 （FAUST）是《边狱公司》及其衍生作品的登场角色。 原型来源歌剧 《浮士德》。 该罪人为我司巴士打造了“梅菲斯特号”引擎。 她声称自己是都市中最聪慧的存在，没有人能在智慧层面上与她相媲美，这可能并非谬论。 当她应允与您交谈时，您会发现她的态度高高在上，令人不悦。 她对待所有人都有一股微妙的傲慢态度，这似乎永远都无法改变了，因此，我们建议您只要应付一下，点点头就成。

来源于游戏《Limbus Company》,引用自[边狱公司中文维基](https://limbuscompany.huijiwiki.com/wiki/%E9%A6%96%E9%A1%B5)

---

### 技术实现

~~Backend的一部分代码来源于 [morettt/my-neuro](https://github.com/morettt/my-neuro)~~

现在已经不再有来源于[morettt/my-neuro](https://github.com/morettt/my-neuro)的内容了

| 部分       | 实现                    |
| -------- | --------------------- |
| Backend  | Python为主体,基于langchain |
| Frontend | Electron+Qt           |
