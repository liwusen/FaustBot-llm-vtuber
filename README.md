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

```mermaid
flowchart TD
    A[外部输入/事件源] --> A1[用户文本输入<br/>WebSocket /faust/chat]
    A --> A2[Minecraft事件<br/>minecraft_client.py]
    A --> A3[Nimble用户提交/关闭<br/>/faust/nimble/callback]
    A --> A4[定时/间隔/事件触发器<br/>trigger_manager.py]

    subgraph B[入口层 backend-main.py]
        B1[/faust/chat 接收消息/]
        B2[/faust/command 主循环/]
        B3[设置 ignore_trigger_event<br/>避免触发器干扰当前对话]
        B4[消息去重与限流]
        B5[会话上下文管理]
    end

    A1 --> B1
    B1 --> B3
    B3 --> B4
    B4 --> B5

    subgraph C[模型执行层]
        C1[组装用户消息]
        C2[Agent执行器<br/>create_agent deepseek-chat]
        C3[加载角色上下文<br/>AGENT.md ROLE.md COREMEMORY.md TASK.md]
        C4[流式输出消息 chunk]
        C5[完整回复组装]
    end

    B5 --> C1
    C1 --> C2
    C3 --> C2
    C2 --> C4
    C4 --> C5

    subgraph D[工具与数据处理层 llm_tools.py]
        D1[普通工具<br/>时间/系统/日记/Python]
        D2[RAG 查询工具<br/>rag_client.py]
        D3[Minecraft 操作工具<br/>minecraft_client.send_command]
        D4[Nimble 交互工具<br/>创建窗口/等待回调]
        D5[Trigger 工具<br/>append_trigger]
        D6[HIL 人审请求]
        
        D2 --> D2A[异步RAG查询]
        D2A --> D2B[返回上下文或注册触发器]
    end

    C2 -->|按需调用工具| D1
    C2 -->|按需调用工具| D2
    C2 -->|按需调用工具| D3
    C2 -->|按需调用工具| D4
    C2 -->|按需调用工具| D5
    C2 -->|按需调用工具| D6

    D2B --> E
    D3 --> D3A[发送命令到 mc-operator]
    D3A --> D3B[返回 command_result]
    D3B --> C2

    D4 --> D4A[nimble.create_session]
    D4A --> D4B[backend2front 发送 NIMBLE_SHOW]
    D4B --> F3
    D4C[前端展示交互窗口] --> A3

    A3 --> A3B[nimble.set_result / close_session]
    A3B --> E

    A2 --> A2B[收到 mc event]
    A2B --> A2C[append_trigger event=mc_event]
    A2C --> E

    subgraph E[触发器调度中心 trigger_manager.py]
        E1[触发器持久化到 triggers.json]
        E2[watchdog 轮询<br/>datetime/interval/py-eval/event]
        E3[满足条件后放入 trigger_queue]
        E4[触发器执行去重<br/>防止递归触发]
    end

    A4 --> E1
    D5 --> E1
    A2C --> E1
    E1 --> E2
    E2 --> E3
    E3 --> E4

    E4 -->|触发器触发| B2
    E4 -->|异常处理| G2

    B2 --> B2A[从 trigger_queue 取任务]
    B2A --> B2B[设置 ignore_trigger_event]
    B2B --> B2C[拼装 trigger_text]
    B2C --> C1

    C5 --> F1[WebSocket 返回完整回复]
    C4 --> F2[实时流式输出]

    subgraph F[前端与表现层]
        F1[聊天窗口显示完整消息]
        F2[聊天窗口实时显示]
        F3[backend2front 指令队列]
        F4[前端执行语音播放/角色表现]
        F5[前端展示HIL交互界面]
    end

    C5 --> F3
    F2 --> F1
    F3 --> F4
    D6 -->|发送HIL请求| F3
    F3 --> F5

    subgraph G[监控与异常处理层]
        G1[性能埋点与日志]
        G2[错误上报与重试]
        G3[对话历史持久化]
        G4[工具调用超时处理]
    end

    C2 --> G1
    D2 --> G4
    D3 --> G4
    D6 --> G2
    E --> G2
    C5 --> G3

    subgraph H[状态管理]
        H1[对话模式: 普通/触发器/HIL等待]
        H2[ignore_trigger_event 标志位]
        H3[会话隔离]
    end

    B3 --> H2
    B5 --> H3
    D6 --> H1
    E4 --> H1
```

~~Backend的一部分代码来源于 [morettt/my-neuro](https://github.com/morettt/my-neuro)~~

现在已经不再有来源于https://github.com/morettt/my-neuro的内容了

| 部分       | 实现                    |
| -------- | --------------------- |
| Backend  | Python为主体,基于langchain |
| Frontend | Electron+Qt           |
