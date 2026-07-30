# 灵动窗口（Nimble Window）技能

灵动窗口是显示在虚拟形象旁的 HTML 小窗口，以小组件形式注册，用户可拖动/缩放。
本技能介绍窗口的创建方式、console 双向通信协议，并提供两个可直接使用的对弈游戏模板。

## 1. 创建窗口

使用 `showNimbleWindowTool` 创建。html 参数支持两种形式：

- 直接传 HTML 字符串；
- `path:{URI}` 从文件加载，例如本技能自带的模板：
  - `path:skill://nimble-window/tictactoe.html` — 三子棋（井字棋）
  - `path:skill://nimble-window/gomoku.html` — 五子棋

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

两个游戏模板使用同一套协议：**每步全盘状态携带**，你无需自己记住棋局。

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

## 4. 页面内可用的 JS API（写自定义 HTML 时参考）

脚本以 `new Function('nimble', code)` 执行，`nimble` 对象为每窗口独立注入：

- `nimble.sendMessage(createEventTrigger, payload)` — 发消息给你（Promise）
- `nimble.setMessageHandler(func)` — 接收你写入 console 的消息（保留命令除外）
- `nimble.resize(width, height)` / `nimble.setFullscreen(enabled)` / `nimble.getConfig()`
- 元素加 `class="nimble-pass-through"` 可在点击穿透模式下不阻挡桌面操作

注意：模板内使用了固定的元素 id，同一游戏模板同时只开一个窗口。
