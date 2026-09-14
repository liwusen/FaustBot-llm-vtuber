# 灵动窗口（Nimble Window）技能

灵动窗口是显示在虚拟形象旁的 HTML 小窗口，以小组件形式注册，用户可拖动/缩放。
本技能介绍窗口的创建方式、console 双向通信协议，并提供三个可直接使用的游戏模板（三子棋、五子棋、Wordle 猜词）。

## 1. 创建窗口

使用 `showNimbleWindowTool` 创建。html 参数支持两种形式：

- 直接传 HTML 字符串；
- `path:{URI}` 从文件加载，例如本技能自带的模板：
  - `path:skill://nimble-window/tictactoe.html` — 三子棋（井字棋）
  - `path:skill://nimble-window/gomoku.html` — 五子棋
  - `path:skill://nimble-window/wordle.html` — Wordle 猜词（你出题，用户猜）

示例：

```
showNimbleWindowTool(
    html="path:skill://nimble-window/tictactoe.html",
    title="三子棋对战",
    recall_text="正在和用户下三子棋，检查是否轮到你落子。",
    lifespan=3600,
)
```

工具返回 callback_id，后续通信都围绕它进行。

## 2. 双向通信协议（console）

每个窗口在 VFS 暴露三个节点：

| 路径 | 说明 |
|------|------|
| `faustbot://nimble/{callback_id}/summary` | 窗口概览（自动生成，可用 write 覆写正文） |
| `faustbot://nimble/{callback_id}/console` | 终端式对话记录（核心通信通道） |
| `faustbot://nimble/{callback_id}/code-readonly` | 窗口 HTML 源码（只读） |

console 是累积式记录，格式如下：

```
Frontend>{"game":"tictactoe","board":[["X","",""],["","",""],["","",""]],"last_move":[0,0],"turn":"agent"}
You>{"type":"move","pos":[1,1]}
```

- 前端页面调用 `nimble.sendMessage(createEventTrigger, payload)` → 追加 `Frontend>` 行；
  createEventTrigger=true 时会用 trigger 唤醒你。
- 你用 write 工具写入 console 路径 → 追加 `You>` 行，并实时发给页面的 messageHandler：

```
write("faustbot://nimble/{callback_id}/console", '{"type":"move","pos":[1,1]}')
```

保留命令（前端运行时拦截，不进 messageHandler）：

- `{"type":"command","command":"close-window","args":{}}` — 关闭窗口
- `{"type":"command","command":"set-scale","args":{"scale":1.2}}` — 设置缩放
- `{"type":"command","command":"set-coord","args":{"x":0.5,"y":0.5}}` — 设置屏幕坐标（0~1）

## 3. 对弈游戏协议（模板已实现）

### 3.1 棋类（三子棋 / 五子棋）

两个棋类模板使用同一套协议：**每步全盘状态携带**，你无需自己记住棋局。

前端 → 你（用户落子后，trigger 唤醒你）：

```json
{"game":"tictactoe","board":[["X","","O"],["","X",""],["","",""]],"last_move":[1,1],"turn":"agent"}
```

- `board`：完整棋盘。三子棋 3×3，取值 `""`/`"X"`(用户)/`"O"`(你)；
  五子棋 15×15，取值 `""`/`"B"`(用户执黑)/`"W"`(你执白)。
- `last_move`：用户刚下的 `[row, col]`。
- `turn`：`"agent"` 表示轮到你。

你 → 前端（写 console）：

```json
{"type":"move","pos":[row,col]}
```

页面负责落子渲染与胜负判定。若你下了非法位置（已占用/越界），页面会回发：

```json
{"game":"...","type":"invalid_move","reason":"cell occupied","board":[...],"turn":"agent"}
```

此时请重新选择位置。游戏结束时页面发送（不唤醒，仅记录）：

```json
{"game":"...","type":"game_over","winner":"user|agent|draw","board":[...]}
```

对弈建议：收到 trigger 后先读 console 确认最新盘面，再落子；不要凭记忆下棋。

### 3.2 Wordle 猜词（你出题，用户猜）

模板：`path:skill://nimble-window/wordle.html`。规则：6 次机会猜一个 5 字母英文单词，每次猜测后每个字母给出反馈（绿=位置正确、黄=字母在词中但位置不对、灰=词中没有该字母）。

**你负责出题**，流程是「创建窗口 + 立刻下发题目」两步（同一次回复里连续两个工具调用即可）：

```
showNimbleWindowTool(
    html="path:skill://nimble-window/wordle.html",
    title="Wordle 猜词",
    recall_text="正在和用户玩 Wordle，用户每次猜测后都会唤醒你，检查 console 最新一条 Frontend> 行。",
    lifespan=3600,
)
write("faustbot://nimble/{callback_id}/console", '{"type":"set-word","word":"CRANE","hint":"常见名词"}')
```

- `word`：必填，**必须恰好 5 个 A-Z 字母**（大小写均可，页面统一转大写）。页面不接受非 5 字母内容，会回发 `error` 让你重发。
- `hint`：可选，显示在状态栏下方的一行灰字提示（如词性、主题、首字母）。**绝不要在 hint 里泄露答案**。
- 判分由页面本地完成并即时上色，不需要你逐字母判断，你也不必在回复里复述判分结果。

前端 → 你（**每一次有效猜测都会 trigger 唤醒你**，含猜对的那一次）：

```json
{"game":"wordle","type":"guess","guess":"CRANE","result":["correct","absent","present","absent","absent"],
 "solved":false,"finished":false,"attempts_used":1,"attempts_left":5,
 "board":[{"guess":"CRANE","result":[...]}]}
```

- `result`：每个字母的判定，取值 `correct` / `present` / `absent`。
- `solved`：本次猜中；`finished`：本局结束（猜中或 6 次用尽）。
- `attempts_used` / `attempts_left`：已用与剩余次数。
- `board`：本局全部历史猜测（每步全量携带，你不必凭记忆还原进度）。
- 非法输入（长度/字符不对）由页面本地拦下，不会唤醒你，也不会消耗次数。

其他前端 → 你的消息：

```json
{"game":"wordle","type":"restart_request"}                    // 用户点了「换一题」，请重新下发 set-word
{"game":"wordle","type":"error","reason":"...","got":"..."}    // 你的 word 不合法（不是 5 个 A-Z 字母），请重新下发
{"game":"wordle","type":"revealed","answer":"...","attempts_used":2,"board":[...]}  // 你主动 reveal 后页面的回执
```

你 → 前端（写 console）：

```json
{"type":"set-word","word":"CRANE","hint":"常见名词"}   // 出题 / 换题（会清空棋盘）
{"type":"reveal"}                                     // 直接公布答案（用户放弃时用）
```

互动建议：收到 `guess` 后可以就着反馈吐槽或打气（例如「黄了？再想想」），然后等下一次唤醒；猜中时给点祝贺，6 次用尽时公布答案并可以再发一个 `set-word` 直接开下一局。

## 4. 页面内可用的 JS API（写自定义 HTML 时参考）

脚本以 `new Function('nimble', code)` 执行，`nimble` 对象为每窗口独立注入：

- `nimble.sendMessage(createEventTrigger:bool, payload)` — 发消息给你（Promise）,createEventTrigger代表是否使用EventTrigger唤醒你
- `nimble.setMessageHandler(func)` — 接收你写入 console 的消息（保留命令除外）
- `nimble.resize(width, height)` / `nimble.setFullscreen(enabled)` / `nimble.getConfig()`
- 元素加 `class="nimble-pass-through"` 可在点击穿透模式下不阻挡桌面操作
- 可以读取三个模板来学习API使用方法

**窗口是亮色主题**：外壳背景为 `rgba(255,255,255,0.94)`、文字色 `#1a2433`。自定义 HTML 必须使用深色文字与浅色块，可直接抄模板配色：

| 用途 | 取值 |
|------|------|
| 主文字 | `#1a2433` |
| 次要文字 | `#647086` |
| 描边/分隔 | `rgba(190,201,217,0.9)` |
| 主色（按钮/强调） | `#3f6be8` |
| 浅色块底 | `#eef1f6` |
| 警示/强调红 | `#e54447` |

不要写 `color:#fff` 之类的浅色文字，在白底窗口里会看不见。

注意：模板内使用了固定的元素 id，同一游戏模板同时只开一个窗口。
