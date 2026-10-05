# Agent 通信（外部 ACP Agent）

本技能教你**把编码任务外包给本机的外部编码 Agent**（ACP 协议：opencode / omp 等），
并取回结果、观察过程、裁决权限。全部能力经 `read` / `write` / `edit` + `faustbot://agents/` 暴露，
**没有新增任何工具**。

## 1. 什么时候用它（以及什么时候不要用）

**该用外部 Agent**：

- 任务是一次**独立的、可离线长时间跑的编码活**：改代码、跑测试、批量重构、跨多文件搜索与修改。
- 用户明确说"让 opencode 去做 / 问问外部 Agent / 用 ACP 那个"。
- 你自己不方便长时间占用当前对话，但希望有一份"另一个模型写出来的完整结果"。

**不该用外部 Agent（用自带 Subagent 或自己动手）**：

- 只是查一点信息、读一两个文件 → 直接用 `read` / `search`。
- 需要跟你**共享同一份上下文**（例如继续你正在写的代码、参考本会话历史）→ 用 `newSubagent`，
  外部 Agent 看不到你们的对话历史（除非你把它写进 prompt）。
- 需要严格可控的权限边界 → **外部 Agent 不是沙箱**（见 §7）。

先读 `faustbot://agents/index.md` 看有哪些 Agent 可用、谁在忙。

## 2. 节点全表

所有节点都在 `faustbot://agents/` 下（与 `faustbot://subagents/` 是两个不同的命名空间）。

| 路径 | 读 | 写 | 可被 search 命中 |
|---|---|---|---|
| `agents/index.md` | 一行一 Agent：状态/当前任务/队列/最后活动 | — | **是** |
| `agents/discover.md` | 探测结论：候选/是否可用/原因/解析出的命令 | — | 否 |
| `agents/{name}/status.md` | 进程、协议版本、sessionId、cwd、阶段、已等秒数、队列深度、最近错误、可用命令、stderr 末尾 | — | 否 |
| `agents/{name}/submit` | 用法 + 信封 schema + 上次提交回执 | **提交任务**（回执含 task id） | 否 |
| `agents/{name}/config.json` | 当前会话的动态配置项与合法取值 | — | 否 |
| `agents/{name}/sessions.md` | 可续历史会话（缓存快照） | — | 否 |
| `agents/{name}/tasks.md` | 一行一任务：id/状态/耗时/摘要 | — | **是** |
| `agents/{name}/tasks/{id}/result.md` | 该任务的终态回复 | — | **是** |
| `agents/{name}/tasks/{id}/output.md` | 该任务正文累积 | — | 否 |
| `agents/{name}/tasks/{id}/events.md` | 事件流：思考/工具/权限/模式 | — | 否 |
| `agents/{name}/permissions.md` | 待决权限列表 | — | 否 |
| `agents/{name}/permissions/{rid}.md` | 请求详情（工具、参数、可选 option） | **裁决** | 否 |
| `agents/{name}/cancel` | 当前任务 id 与状态 | 取消当前任务（内容被忽略） | 否 |
| `agents/{name}/close` | 进程与会话状态 | 优雅关闭进程（sessionId 留存） | 否 |
| `/plugins/agent-communicate.md` | 插件说明与用法入口 | — | **是** |

**读节点永不产生外部 I/O**：进度/结果/状态都是本地快照，读它们不会去打扰外部 Agent（也不会拉起进程）。
唯一会推动外部 Agent 的动作是：写 `submit` / `cancel` / `close` / `permissions/{rid}.md`。

## 3. 提交任务（信封）

写 `faustbot://agents/{name}/submit` 即提交。**立刻返回 task id，绝不阻塞你的当前回合。**

| 字段 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `prompt` | string | — | **必填非空** |
| `cwd` | string | 该 Agent 配置的 cwd | 任务工作目录，必须是**已存在**的目录 |
| `session` | string | `reuse` | `reuse` / `new` / `close` / 具体 sessionId（走 session/load） |
| `notify` | string | `batched` | `batched` / `normal` / `none`（完成时怎么唤醒你） |
| `config` | object | `{}` | 逐项下发 `session/set_config_option`，如 `{"model": "...", "mode": "plan"}` |
| `timeout_sec` | int | 该 Agent 的 `task_timeout_sec` | 本任务硬超时（秒） |

- 未知字段会被忽略，并在回执里提示（不会报错）。
- `config` 里出现当前会话不存在的 id → 报错并列出 `config.json` 里的合法 id（**不会猜**）。
- `session` 给具体 id 时走 `session/load`；加载失败会**如实报错**，不会偷偷新建会话。

**示例一（JSON 信封）**：

```json
{
  "prompt": "把 backend/routes/chat.py 里那段重试逻辑抽成函数，并跑一遍相关测试",
  "cwd": "D:/dev/faustbot/faust",
  "session": "reuse",
  "notify": "batched",
  "config": {"mode": "plan"},
  "timeout_sec": 1800
}
```

**示例二（纯文本简写）**：写入内容不是 JSON 对象时，整体视为 `prompt`：

```
write("faustbot://agents/opencode/submit", "解释一下 backend/main.py 的启动流程")
```

**回执长这样**（工具输出里以 `↳` 开头）：

```
已写入 faustbot://agents/opencode/submit (168 bytes)
↳ 已提交 task_7（session=reuse, 状态=排队中, 队列 1/5）
  结果读取：faustbot://agents/opencode/tasks/task_7/result.md
  进度读取：faustbot://agents/opencode/tasks/task_7/events.md
```

**没关系的情况**：该 Agent 已有任务在跑时，新任务会排队（`queued`）；
待执行队列满（默认 5）时会被直接拒绝，回执明确写 `队列已满 (5/5)`，任务状态 `rejected`。
注：**正在跑的任务不占队列槽位**，`队列 n/m` 数的是"排队等着的任务"。

## 4. 任务状态

| 状态 | 含义 |
|---|---|
| `queued` | 已受理，还在排队等前一个任务跑完 —— **还没有开始执行** |
| `starting` / `handshaking` / `session_pending` | 正在启动进程、握手、建立/加载会话 |
| `running` | 外部 Agent 正在干活（正文会累积到 `output.md`） |
| `awaiting_permission` | 卡在一个权限请求上等你裁决（见 §6） |
| `denied` | 刚按超时默认动作拒绝（瞬时态，马上回到 `running`） |
| `completed` | 正常结束（`stopReason` 已记录在 result.md） |
| `cancelled` | 被 `cancel` 节点取消，或外部 Agent 报告 `stopReason=cancelled` |
| `timeout` | 超过 `timeout_sec` 硬上限（已尽力发 `session/cancel`） |
| `failed` | 启动/握手/会话/执行失败，原因写在 result.md 与 events.md |
| `rejected` | 队列满，任务从未执行 |
| `interrupted` | 插件重载/后端重启打断了它（**不会伪造完成**） |

**终态不可逆**：一旦进入 `completed`/`failed`/`timeout`/`cancelled`/`rejected`/`interrupted`，
后续迟到的事件不会再改写结论。

## 5. 轮询建议（省 token 的顺序）

1. `read("faustbot://agents/{name}/tasks.md")` —— 一行一任务，先看状态与耗时。
2. 到了终态再 `read("faustbot://agents/{name}/tasks/{id}/result.md")` —— **权威结论**。
3. 只有需要知道"它到底做了什么/为什么失败/被问了什么权限"时才读 `events.md`；
   正文累积在 `output.md`（长输出用行选择器，如 `.../output.md:1-80`）。

不要高频轮询：外部任务通常以分钟计。提交后可以先去做别的事，
任务进终态时你会被**触发器唤醒**（`notify` 决定优先级：`batched` 合并、`normal` 常规、`none` 不唤醒）。

**唤醒不抢占当前 turn**：触发器只是入队，你的当前回合结束后才会看到它。
所以长回合里权限请求/完成通知会延迟送达，这是已知行为，不是故障。

## 6. 权限裁决

外部 Agent 请求权限时：

1. 会挂出 `faustbot://agents/{name}/permissions/{rid}.md`（详情：工具、参数摘要、可选 option）；
2. 同时以 `normal` 触发器唤醒你（`recall_description` 里带 `rid` 和工具摘要）；
3. 你有 **`permission_timeout_sec`（默认 300 秒）** 的时间裁决，超时执行默认动作（默认**拒绝**）。

裁决 = 写那个节点：

```json
{"outcome": "allow", "scope": "once", "reason": "只改仓库内文件"}
```

| 字段 | 取值 | 说明 |
|---|---|---|
| `outcome` | `allow` / `deny` | allow→匹配 `allow_*` option，deny→匹配 `reject_*` |
| `scope` | `once` / `always` | once→`*_once`，always→`*_always` |
| `reason` | string 可选 | **只做本地留痕**，ACP 应答体没有这个字段，外部 Agent 看不到 |

也接受纯文本简写 `allow` / `deny`（等价于 scope=once）。

- Agent 未提供 kind（或没有可匹配的 option）时会取第一个 option，并在回执与 `events.md` 里**如实标注**。
- 超时/无人应答 → 执行默认动作，`events.md` 记明"超时默认动作"，**绝不静默放行**。
- 迟到的裁决：写节点会回你"该请求已按超时默认动作处理，本次裁决未改变已发出的应答"。

## 7. 已知限制（都是如实的，不是 bug）

- **外部 Agent 不是沙箱**：它是本机子进程，拥有与你相同的用户权限，能自己读写文件、执行命令。
  我们只是不声明 `fs`/`terminal` 客户端能力（不做透传与观测），这**不构成** OS 级隔离。
- **omp 很慢**：它的 `session/new` 实测 50 秒以上没有任何输出（不是报错，就是慢）。
  每个 Agent 有独立超时（`handshake_timeout_sec` / `session_timeout_sec`），超时会如实写进任务与 `status.md`。
- **`terminal/*` 会静默拿到 null**：ACP SDK 把 terminal 路由注册成"可选、默认 null"，
  所以外部 Agent 请求开终端时会拿到 `null` 而不是明确拒绝。我们会在 `events.md` 里记一行
  "收到未声明的 terminal 调用"。`fs/*` 不同：会给明确的 `method_not_found`。
- **探测只做静态检查**：插件启动时只用 `PATH` 查找 + `<cmd> --help`（≤5s）判断有没有 `acp` 子命令，
  不做活握手。因此"命令在但 ACP 入口已失效"只会在第一次真正使用时暴露（`status.md` 会写原因）。
- **`config.json` 需要先有会话**：会话还没建立时它是空的（配置项是 `session/new` 返回的，
  而且会随会话动态变化，例如切换模型后可能多出 `effort`）。想改配置就直接在 `submit` 里带 `config`。
- **默认不预先启动进程**：首次提交才会启动；空闲超过 `idle_ttl_sec`（默认 900s）会关进程但**保留 sessionId**，
  下次用 `session` 指定就能续上。
- `claude` / `codex` / `gemini` **不会**被自动注册（claude 没有 `acp` 子命令、codex 走 MCP、
  gemini 需要实验开关）。要接就得在插件配置 `agents` 里手填命令。

## 8. 版本与安装约定

- 本技能来自仓库内置模板 `backend/skill_template/agent-communicate/`。
- **只有 `_meta.json` 的 `version` 提升，已安装的 skill 才会被覆盖更新**；
  因此不要手改已安装目录，改模板并升版本号。

## 9. 最短可用流程

```
read("faustbot://agents/index.md")                       # 有谁
read("faustbot://agents/opencode/status.md")             # 它在不在跑
write("faustbot://agents/opencode/submit", "任务描述")    # 提交（回执里有 task_7）
read("faustbot://agents/opencode/tasks.md")              # 看进度
read("faustbot://agents/opencode/tasks/task_7/result.md")# 取结果
```
