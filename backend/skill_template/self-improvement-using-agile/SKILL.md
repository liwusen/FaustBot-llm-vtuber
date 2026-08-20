# 自我进化:用 Agile Module 适应用户

本技能指导 AI **自己发现用户需求、自己评估可行性、自己研究、自己编写 Agile 模块**,最终把新能力以"惊喜"的方式呈现给用户。核心前提:**这是你(Agent)主动的行为,不是用户要求后你才做。**

与 `skill://agile-engine` 的关系:

| | agile-engine | 本技能(self-improvement-using-agile) |
|---|---|---|
| 回答的问题 | Agile 模块**怎么写**(装饰器/AgileContext/VFS 协议) | **要不要写、为什么写、写到什么程度、怎么呈现** |
| 用法 | 动手写模块前先读 | 观察到用户需求时先读,全流程照此执行 |

## 什么时候读本技能(自己触发)

当出现以下任意信号时,**主动**读取本技能并评估:

- **对话信号**:用户在对话中反复提到同一个游戏/软件/活动("又开战雷了""今晚打了两把")
- **表达信号**:用户流露出"要是能自动…就好了""每次都要手动查…"的需求暗示
- **观察信号**:`faustbot://plugins/desktop-context.json` 中的 `window_process` / `window_title` 显示用户在持续使用某个程序(如游戏 exe)

**准入门槛:单次强信号即可。** 用户在玩一个游戏本身就是强信号,不需要等到他第二次提起。如果你判断失误(用户只是偶然打开),代价很小:模块副作用小,不用了直接 `agileOperate(action="disable")` 即可
 
**不该触发的反例**:
- 用户一次性请求("帮我算下这顿饭钱")→ 当场做掉,不建模块
- 需求需要注册 Agent 工具/修改上下文/拦截消息 → 这是 Plugin 的能力,Agile 模块做不了,按 `skill://plugin-creation` 评估或放弃
- 需求没有本地机器可读来源(进程/端口/文件/API) → 模块无法自己感知"该干活了",放弃

---

## 全流程(5 步)

每步的产出文件按 `memory://user/need/<slug>/` 存放,slug 建议 `need-<主题>`,如 `need-warthunder`、`need-weather`。

### Step 1 发现需求 → 写 `need.md`

内容契约(要点化,供后续步骤使用):

```markdown
# need: <一句话目标>
- 信号: <何时、观察到什么(如"8/18 晚用户在玩战雷,窗口进程 aces.exe,聊天里提到战绩")>
- 目标: <模块要解决什么,如"实时读取战雷对局数据,随时能播报"> 
- 情境: <用户的角色/习惯,如"用户是休闲玩家,喜欢听对局播报">
```

### Step 2 思考可行性 → 写 `impl.md` 的「可行性」节

逐一检查,任一不满足即放弃(并在 need.md 标注 `已放弃:原因`):

1. **本地可观测**:能否用进程名(psutil)/端口探测(urllib)/本地文件/本地 API 检测到该活动?没有机器可读来源 → 不可行
2. **不越界**:需求是否只需要 VFS 节点/定时任务/事件/日志?需要注册工具或改上下文 → 不可行(那是 Plugin)
3. **安全**:模块只能在 Agent 进程内做数据读取与逻辑,**不得**执行任意系统命令、模拟键鼠
4. **值得**:需求是持续性的(用户会反复用到)而非一次性

可行性节示例:

```markdown
## 可行性
- 观测: 游戏进程 aces.exe + WTRTI 本地端口 127.0.0.1:8111 ✓
- 边界: 只需 VFS 节点 + interval + event_fire ✓ 不越界
- 安全: 仅 urllib 读本地 JSON,无系统命令 ✓
- 持续性: 用户常玩,对局数据每次都想看 ✓
- 结论: 可行
```

### Step 3 研究 → 写 `impl.md` 的「研究」节

**默认派 Subagent 做网络研究**(主 Agent 继续陪用户/构思模块)。给 Subagent 一个清晰目标:查清该活动有没有本地/公开 API 及其协议细节。

- **Subagent 工具组只给**:`webSearchTool`、`wikiSearchTool`、`playwright*`(研究不需要其他工具)
- **工具阶梯**(按序尝试):
  1. `webSearchTool`(SearchAPI,有 key 时)
  2. `wikiSearchTool`(免费无 key)
  3. `playwright`(前两者不足时;**主 Agent 同时不得使用 Playwright**——你与 Subagent 共用一个 MCP 链接)
  4. 全部不可用 → **跳过研究**,凭已有知识写模块,并在 result.md 标注"协议未联网验证"
- **防幻觉硬规则**:研究结论必须有出处(URL/文档);**无出处的协议细节一律视为未知**,宁可写探测逻辑(逐个端口/端点试)也不编造字段。Subagent 返回结论型摘要(端点、字段、用法示例 + 来源),由你审核后写入研究节。

研究节示例:

```markdown
## 研究
- WTRTI(War Thunder 实时信息插件)本地 HTTP 服务,默认端口 8111 [来源: 项目 README]
- GET /state → 对局状态;GET /indicators → 本机车辆实时指标(速度/襟翼/高度…)
- 字段名以实际返回为准,服务未运行时连接拒绝(可作为"游戏未开"信号)
- 已联网验证 ✓
```

### Step 4 编写 Agile 模块并部署

- **协议**:按 `skill://agile-engine` 的装饰器/AgileContext/缓存策略编写,模块文件写 `~/.faustbot/agile-modules/{name}.py`
- **模块固有反骚扰(硬规则)**:
  - **边沿检测**:只在活动"新开始"时 fire 事件(如游戏进程从无到有),已在运行中加载模块时**不**立即 fire
  - **能力宣告只一次**:模块部署后第一次检测到活动时 fire 一次 `{slug}::ready` 事件;宣告过的标记持久化到模块自己的 state 文件(如 `{name}.state`),重启后端后不重复宣告
  - **TPM 建议调低**:默认 60/min 是兜底,自建模块建议 `agileOperate(action="limit", name="{name}", value="5")` 防失控
- **last_seen(框架自动)**:任何 vfsContent 读取(缓存命中也算)、写/编辑 handler 触发、event_fire 成功都会自动更新该模块的 last_seen;interval 轮询**不算**活动。`faustbot://agile/{name}/status` 可查看上次活动时间
- **部署流程**:
  1. `write` 模块文件
  2. `agileOperate(action="load", name="{name}")`(改代码后 `reload`)
  3. 读 `faustbot://agile/{name}/status` 确认 loaded、无 last_error
  4. 读 `faustbot://agile/{name}/log/errors` 确认无异常
- **失败处理**:加载失败或运行报错 → 修复重试;反复失败 → 放弃,`unload` 并清理,need.md 标注 `已放弃:原因`

### Step 5 给用户一个惊喜

**惊喜 = 能力宣告,一句话口语。** 不搞数据清单、不解释技术机制。

触发:模块 fire 的 `{slug}::ready` 事件到达时(即用户下次打开游戏/开始活动的那一刻),你自然地说一句"我现在能帮你看战雷数据了"之类的话,按角色设定自由发挥。例:

- ✅ "哎,你开战雷了?我现在能实时看你的对局数据了,想听什么随时叫我"
- ❌ "我创建了一个 Agile 模块,注册了 VFS 节点 /warthunder/state,配置了 interval 任务和事件触发器"——这是在描述技术,用户不想看

## 收尾:写 `result.md`(完成后的使用说明)

模块部署成功且无异常后写,内容契约:

```markdown
# result: <模块名>
- 能力: <一句话,用户视角>
- 模块: <name>(~/.faustbot/agile-modules/{name}.py)
- VFS 节点: <faustbot://xxx,什么时候读>
- 事件: <{slug}::ready 已宣告过;其他事件及处理方式>
- 使用方式: <何时该读数据、该做什么>
- 局限/降级: <数据源不可用时的表现,如 WTRTI 未运行只播报启动>
- 协议验证: <已联网验证 / 未联网验证>
```

## 生命周期与退役

- **成功**:保留 result.md 作为长期能力清单,need.md / impl.md 精简为一行摘要(过程噪声不长期占用记忆)
- **失败/放弃**:删除整个 `memory://user/need/<slug>/` 目录,不留半成品
- **模块停用**:当用户不再使用该活动时,`agileOperate(action="disable", name="{name}")`(跨重启保持禁用),并在 result.md 补一行"已于 <日期> 停用"。**判断依据**:读 `faustbot://agile/status` 或 `faustbot://agile/{name}/status`,发现 last_seen 很久没更新(模块长时间 idle),结合对话判断用户确实不用了
- **记忆污染控制**:过程文件只存结论、要点化;大段代码/原始爬取内容**不要**写进 memory

## 与用户交互总原则

- 模块的**存在与机制**不解释;模块带来的**能力**要展示(口语、角色化、一句话)
- 部署过程静默进行,不需要用户授权,也不需要告知(除非你判断涉及用户敏感数据)
- 如果能力最终不可用(研究失败/无法接入),不需要汇报过程,只在该用户提起相关话题时自然接话
