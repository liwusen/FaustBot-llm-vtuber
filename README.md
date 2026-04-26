<p align="center">
    <img src="./document/assets/FaustBot.icon.tiny.png" alt="FaustBot icon" />
</p>
<div align="center">
<h1 align="center">FaustBot</h1>
<a href="https://deepwiki.com/liwusen/FaustBot-llm-vtuber" align="center">
    <img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki" align="center"/></a>
</div>
---

### 一个AI驱动的 Vtuber/桌宠

**仍然处于早期开发阶段**

---

### 功能列表

- [x] 多AGENT支持

- [x] ASR 语音识别

- [x] TTS 人声输出

- [x] 音乐播放（唱歌）

- [x] 模型记忆系统(基于向量查找)

- [x] 每个Agent单独的Workspace

- [x] 灵动交互系统 (前端HTML小窗口交互)

- [x] 编辑文件，文件读写等基本工具

- [x] 调用VLM操作用户电脑

- [x] 在线搜索

- [x] AI 玩 Minecraft (基于Mineflyer构建，无缝体验)

- [x] 读取屏幕内容

- [x] 高速响应 平均时间<1s

- [x] 插件系统 [插件市场](https://liwusen.github.io/FaustBot-llm-vtuber/)

- [x] 兼容Openclaw Skill && Clawhub 技能

- [x] 操作网页 (Agent Browser)

- [x] 多模态模型支持

- [x] 云端语音识别/文字转语音支持

- [ ] 给予AI单独的一个可交互Console

- [ ] MCP协议支持

- [ ] 安全系统，限制Agent的访问权限，并对模型命令进行审核

---

### 功能计划(长期)

| 大饼         | 解释                 | 预计时间        |
| ---------- | ------------------ | ----------- |
| Minecraft  | 使用Mineflyer，从底层完成  | 完成          |
| 游览器 操作     | Agent Browser 能力接入 | 完成(Skill系统) |
| OCR/VLLM支持 |                    | 完成          |
| 前端优化       |                    | 完成          |
| 灵动交互       | 允许AI编写HTML实现交互     | 完成          |

---

### 原角色设定

> 浮士德 （FAUST）是《边狱公司》及其衍生作品的登场角色。 原型来源歌剧 《浮士德》。 该罪人为我司巴士打造了“梅菲斯特号”引擎。 她声称自己是都市中最聪慧的存在，没有人能在智慧层面上与她相媲美，这可能并非谬论。 当她应允与您交谈时，您会发现她的态度高高在上，令人不悦。 她对待所有人都有一股微妙的傲慢态度，这似乎永远都无法改变了，因此，我们建议您只要应付一下，点点头就成。

来源于游戏《Limbus Company》,引用自[边狱公司中文维基](https://limbuscompany.huijiwiki.com/wiki/%E9%A6%96%E9%A1%B5)

---

### 

### 技术实现

```mermaid
flowchart TD
    %% 外部输入
    A[用户输入<br/>文本/语音] --> B
    C[Minecraft事件] --> H
    D[定时/表达式触发] --> H
    E[Nimble回调] --> F

    %% API层
    subgraph B[API入口]
        B1[聊天WS]
        B2[命令WS]
        B3[Nimble回调API]
        B4[配置/插件管理]
    end

    %% 核心处理
    B1 --> F[聊天主循环]
    F --> G[Agent推理<br/>LangChain/LangGraph]
    G --> I[工具调用]
    G --> J[回复输出]

    %% 工具层
    subgraph I[工具层]
        I1[文件读写]
        I2[RAG查询]
        I3[Minecraft控制]
        I4[Nimble窗口]
        I5[触发器管理]
        I6[系统/音频]
    end

    %% 数据流
    I2 --> K[RAG系统<br/>查询+增量索引]
    I4 --> L[Nimble会话管理]
    L --> M[触发器注册]
    I5 --> N[触发器持久化]

    %% 触发器系统
    subgraph H[触发器调度]
        H1[Watchdog轮询]
        H2[触发队列]
        H3[过滤器链]
    end

    C --> H3
    D --> H3
    M --> H3
    N --> H1
    H1 --> H2 --> F

    %% 插件系统
    O[插件系统] --> I
    O --> H3

    %% 前端输出
    J --> P[前端流式输出]
    B2 --> Q[命令转发]
    Q --> R[Live2D展示/TTS]
    I6 --> R
    L --> S[Nimble窗口渲染]

    %% 配置管理
    B4 --> T[配置/Agent管理]
    T --> U[运行时重建]
    U --> G

    %% 数据持久化
    K --> V[聊天记录落盘]
    J --> V
    V --> W[RAG增量索引]

    %% 标注
    style B fill:#e1f5fe
    style G fill:#f3e5f5
    style I fill:#e8f5e8
    style H fill:#fff3e0
    style O fill:#fce4ec
```

~~Backend的一部分代码来源于 [morettt/my-neuro](https://github.com/morettt/my-neuro)~~

| 部分       | 实现                    |
| -------- | --------------------- |
| Backend  | Python为主体,基于langchain |
| Frontend | Electron+Qt           |

