<p align="center">
    <img src="./document/assets/FaustBot.icon.tiny.png" alt="FaustBot icon" />
</p>
<div align="center">
<h1 align="center">FaustBot</h1>
<a href="https://deepwiki.com/liwusen/FaustBot-llm-vtuber" align="center">
    <img src="https://deepwiki.com/badge.svg" alt="Ask DeepWiki" align="center"/></a>
</div>

### 一个AI驱动的 Vtuber/桌宠

**仍然处于早期开发阶段**

---

### 功能列表

- [x] 多AGENT支持

- [x] ASR 语音识别

- [x] TTS 人声输出

- [x] 音乐播放（唱歌）

- [x] 多模态模型记忆系统

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

- [x] 安全系统，限制Agent的访问权限，并对模型命令进行审核

- [x] 高效的工具实现

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
    %% ── 外部输入 ──
    UI[用户<br/>文本/语音] --> WS
    MC[Minecraft 事件] --> TR
    BL[B站弹幕<br/>blivedm] --> TR
    SC[定时/表达式] --> TR

    %% ── FastAPI 层 ──
    subgraph WS[FastAPI 入口]
        CHAT_WS["/faust/chat (WS)"]
        CMD_WS["/faust/command (WS)"]
        REST["REST 端点<br/>admin / audio / ..."]
        LOG_WS["/faust/logger/ws"]
    end

    %% ── 核心 Agent 运行时 ──
    subgraph AGENT[Agent 运行时]
        EVT[Trigger 事件<br/>调度循环] --> LOCK[Agent 锁]
        CHAT_WS --> LOCK
        LOCK --> AGT[Agent 推理<br/>LangGraph]
    end

    AGT --> REPLY[回复流<br/>SSE / WS delta]

    %% ── 工具层 ──
    subgraph TOOLS[LLM 工具集]
        FILE[文件读写 / Patch]
        SYS[Python/系统执行]
        MC_CTL[Minecraft 控制]
        MEM[记忆搜索]
        DIARY[日记写入]
        MEDIA[音乐播放]
        ANIM[Live2D Motion<br/>VRM Gesture/LookAt]
        NIMBLE[Nimble 交互窗口]
        TRIGGER[Trigger 管理]
        ARAYA_MEM[记忆库操作<br/>araya 专用]
        SKILL[Skill 安装]
        HIL[人工审批]
    end

    AGT --> TOOLS

    %% ── Araya 离线 Agent ──
    subgraph ARAYA[Araya 离线 Agent]
        A_LOOP[空闲轮询<br/>30s 间隔] --> A_SHOULD{空闲超时?}
        A_SHOULD -- yes --> A_AGENT[Araya 推理]
        A_AGENT --> ARAYA_MEM
        A_AGENT --> A_TOOLS[知识图谱工具]
    end

    ARAYA_MEM -.->|共享记忆库| MEM

    %% ── 系统服务 ──
    subgraph SVC[系统服务]
        VAD[VAD 语音检测<br/>/faust/audio/ws/vad]
        TTS[TTS 合成<br/>edge / local / cloud / openai]
        ASR[ASR 转录<br/>local / cloud / openai]
        SVCMGR[子进程管理<br/>TTS / ASR / mc-operator]
    end

    CHAT_WS --> VAD
    UI --> ASR
    REST --> TTS
    REST --> ASR

    %% ── Frontend 桥接 ──
    subgraph FE[Frontend 通信]
        FQ[asyncio.Queue]
        F_SAY[SAY 文本]
        F_MOTION[SET_MOTION]
        F_MODEL[LOAD_MODEL]
        F_MUSIC[PLAYMUSIC / PLAYBG]
        F_NIMBLE[NIMBLE_SHOW / NIMBLE_CLOSE]
        F_VRM[VRM_GESTURE / VRM_LOOKAT / VRM_BONE]
        F_HIL[HIL_APPROVAL]
    end

    REPLY --> F_SAY
    MEDIA --> F_MUSIC
    ANIM --> F_MOTION
    ANIM --> F_VRM
    NIMBLE --> F_NIMBLE
    HIL --> F_HIL

    FQ --> CMD_WS

    %% ── 记忆 / RAG ──
    subgraph MEM_SYS[记忆系统]
        MS[nano-vectordb]
        KG[知识图谱<br/>networkx]
        EMB[Embedding 模型]
        IDX[异步索引]
    end

    MEM --> MS
    MEM --> KG
    DIARY --> MS
    MS --> EMB
    KG --> EMB
    IDX -.-> KG

    %% ── Trigger 调度 ──
    subgraph TR[Trigger 系统]
        STORE[(triggers.json<br/>持久化)]
        WDOG[Watchdog 线程<br/>0.5s 轮询]
        TQ[queue.Queue]
        FILTER[过滤器链<br/>插件注入]
    end

    SC --> STORE
    STORE --> WDOG
    MC --> FILTER
    BL --> FILTER
    WDOG --> TQ
    FILTER --> TQ
    TQ --> EVT

    %% ── Nimble 交互 ──
    subgraph NB[Nimble 系统]
        SESS[会话管理<br/>dict]
        N_RESULT[结果回调<br/>POST /faust/nimble/callback]
    end

    NIMBLE --> SESS
    F_NIMBLE -->|展示窗口| N_RESULT
    N_RESULT -->|提交/关闭| TR

    %% ── Plugin / Skill ──
    subgraph PLG[插件系统]
        PM[PluginManager]
        PLUGIN_DIR[插件目录]
        P_HEART[心跳循环<br/>10s]
    end

    PM --> TOOLS
    PM --> FILTER
    PM --> PLUGIN_DIR
    P_HEART --> PM

    %% ── 配置 / 管理 ──
    subgraph CFG[配置与管理]
        CONFIG[faust.config.json<br/>faust.config.private.json]
        ADMIN[admin_runtime<br/>Agent CRUD / 配置读写]
        RELOAD[运行时重建<br/>rebuild_runtime]
    end

    REST --> ADMIN
    ADMIN --> CONFIG
    ADMIN --> RELOAD
    RELOAD --> AGT

    %% ── 子进程管理 ──
    subgraph PROC[子进程]
        GP[TTS 进程<br/>port 5000]
        WP[ASR 进程<br/>port 1000]
        MCOP[mc-operator<br/>port 18901]
    end

    SVCMGR --> GP
    SVCMGR --> WP
    SVCMGR --> MCOP
    TTS --> GP
    ASR --> WP
    MC_CTL --> MCOP
    MC --> MCOP

    %% ── 样式 ──
    style WS fill:#e1f5fe
    style AGENT fill:#f3e5f5
    style TOOLS fill:#e8f5e8
    style MEM_SYS fill:#e0f2fe
    style TR fill:#fff3e0
    style PLG fill:#fce4ec
    style SVC fill:#f5f5f5
    style FE fill:#f0fdf4
    style ARAYA fill:#f5f0ff
    style CFG fill:#eef2ff
    style PROC fill:#f8fafc
    style NB fill:#fdf4ff
```

~~Backend的一部分代码来源于 [morettt/my-neuro](https://github.com/morettt/my-neuro)~~

| 部分       | 实现                    |
| -------- | --------------------- |
| Backend  | Python为主体,基于langchain |
| Frontend | Electron+Qt           |
