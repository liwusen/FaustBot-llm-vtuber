# 受限 UI 操作（LimitedUI）：让 OmniJev 看画面替你按键

`ctx.limited_ui(spec, hooks)` 给 Agile 模块一条**看画面 → 小模型决定动作 → 注入键鼠**的链路。
它专为**无人值守的慢节奏循环**设计（回合制、卡牌、战棋、模拟经营、对话类），
定位是"**基本信任主 Agent，限制只对 OmniJev 这个小模型生效**"。

> 一句话理解：**决策来源**由模块（或 OmniJev）给，**护栏**全部在能力基类里，模块绕不过去。

| | 谁 |
|---|---|
| 决策来源 | 你写的钩子 / OMNIJEV Processor（4B-4bit，约 1.2s 一次） |
| 护栏（门限、白名单、前台、遮挡、上限、审计、急停、眨眼） | `LimitedUIDecider` 基类 |
| 目标窗口绑定、注入、截图 | `uidrv/win.py`、`uidrv/input.py`、`uidrv/blink.py` |

**平台：仅 Windows**（非 win32 调用即报错，不降级）。**不新增依赖**。

## 0. 三件必须知道的事

1. **这不是沙箱。** Agile 模块本来就是任意 Python，你完全可以自己 `ctypes` 发 `SendInput`
   绕开这套限制。本能力的限制只对"经由它"的注入生效——它是**知情同意的护栏**，不是权限边界。
2. **只有慢节奏可行。** 每步 ≈ 1.3s（抓帧+眨眼 ~0.1s、Jev ~1.2s、注入 ~0.05s）。
   冷启动 4B-4bit 约 18–45s，显存峰值约 4.7GB。**别放进高频 interval**（≥3s 起）。
3. **有封号风险。** 对在线游戏注入输入可能被判定作弊（`LLKHF_INJECTED` 可检测）。
   **建议只用于单机**；HIL 弹窗里已经写明了这条风险。

## 1. 最小可用模块

```python
from agile_base import AgileModule, AgileContext

module = AgileModule("auto-card", "自动打牌", "1.0.0")

SPEC = {
    "purpose": "自动打牌：每回合问 OmniJev 该出哪张牌",
    "target": {"exe": "MyCardGame.exe", "title_contains": "MyCardGame"},
    "states": {
        "battle": {"describe": "战斗中：能看到双方血量和手牌",
                   "actions": {"space": "结束回合",
                               "1": "打出第1张牌",
                               "click_hand": {"click": [100, 800, 900, 950],
                                              "meaning": "点手牌区中央"}}},
        "menu":   {"describe": "菜单：竖排按钮列表",
                   "actions": {"space": "确认", "esc": "返回"}},
    },
    "limits": {"keys_per_sec": 4, "max_actions": 3000, "max_session_s": 1200},
}

DEC = None


@module.onloadHook()
async def onload(agile: AgileContext):
    global DEC
    DEC = await agile.limited_ui(SPEC)
    res = await DEC.open()          # 会弹 HIL 批准窗口（试运行 / 允许操作 / 拒绝）
    if not res.ok:
        await agile.lwarning(f"UI 会话未打开: {res.reason}")
        DEC = None
        return
    await agile.linfo(f"UI 会话已开（{res.mode}）: {res.window.describe()}")


@module.onunloadHook()
async def onunload(agile: AgileContext):
    if DEC is not None:
        await DEC.close("module_unloaded")


@module.registerInterval(3)          # 每 3 秒一步（≥3s）
async def step(agile: AgileContext):
    if DEC is None:
        return
    d = await DEC.step("轮到你了，下一步做什么？")     # decide + inject + 审计一行
    if not d.ok:
        await agile.linfo(f"本步未注入: {d.describe()}")
```

## 2. `spec` 字段（不合法直接报错，不静默修正）

| 字段 | 必填 | 说明 |
|---|---|---|
| `purpose` | ✅ | 一句话，进 HIL 弹窗与审计 |
| `target` | ✅ | `{"exe": "游戏.exe", "title_contains": "标题片段", "require_fullscreen": false}`；`exe` 与 `title_contains` **都要匹配**（防标题伪装） |
| `states` | ✅ | `{状态名: {"describe": "给模型看的说明", "actions": {...}}}`，至少一个状态 |
| `decider` | | `"omnijev"`（默认）/ `"scripted"`（测试用，不触发真模型） |
| `unknown_policy` | | `"pause"`（默认：松手+暂停，等你或主 Agent 处理）/ `"escalate"`（额外唤醒主 Agent） |
| `gates` | | `{"confidence_min":0.45, "abstain_max":0.35, "noul_deadzone":[0.35,0.65], "margin_min":0.10}` |
| `limits` | | `{"keys_per_sec":4, "max_actions":3000, "max_session_s":1200, "hook_timeout_s":2.0}`；硬天花板 **10/秒、2 万次、2 小时** |
| `escalate` | | `{"priority":"batched", "pause_key":null, "resume_timeout_s":180}`；`tpm_limit` 建议用 `agileOperate(limit)` 设 6 |
| `blink` | | `{"enabled":true, "settle_ms":60, "ack_timeout_ms":300}` |
| `audit` | | `{"snapshot_every":50, "keep_days":3, "keep_images":500}` |
| `processor` | | 传给 OMNIJEV 的 config：`{"size":"4B", "quant":"4bit"}`（可加 `max_pixels`/`state_cache`） |

### 动作表写法

```python
"actions": {
    "space": "结束回合",                                        # 键 = 按键名，值 = 给模型看的文本
    "1":     "打出第1张牌",
    "click_hand": {"click": [100, 800, 900, 950],              # 键 = 任意内部名
                   "meaning": "点手牌区中央"},                  # box 是 0–1000 归一化坐标（相对客户区）
}
```

- **模型看到的只有文本**（`meaning`），点击动作的 box 只作我方载荷——所以：
  - 文本必须在语义上唯一（跨状态的同名文本必须指向同一个动作键，否则报错）；
  - 文本里就把方位写清楚（"点手牌区中央"比"点这里"强得多）。
- 并集上限 **12 个动作**（所有状态合起来去重后）；写个 `classify` 钩子能大幅降低问数。
- `box` 必须 `0 ≤ x1 < x2 ≤ 1000`、`0 ≤ y1 < y2 ≤ 1000`；点击点取 box 中心。

## 3. 钩子（只两个，都可 sync/async）

```python
async def classify(frame, *, step_id):
    """自己判状态：返回 spec["states"] 里的键，或 None 表示交给 OmniJev。"""
    import numpy as np
    if detect_my_battle_screen(frame.image):
        return "battle"
    return None

def context(frame, *, step_id):
    """给画面补文字（血量/手牌/OCR 结果…），拼进每个问题的末尾。上限 600 字符。"""
    return "血量 32/100，手牌 3 张，敌方有嘲讽随从"

DEC = await agile.limited_ui(SPEC, hooks={"classify": classify, "context": context})
```

- `classify` 命中 ⇒ **跳过状态问**，动作选项只取该状态的子空间（更快、更准、更省 token）；
- `classify` 返回未声明的状态 ⇒ **直接报错**（不会静默忽略）；
- 钩子异常 ⇒ 本步不决策不注入；**连续 3 次 ⇒ 结束会话 + 通知主 Agent**；
- 钩子耗时记入审计 `hook_ms`；`limits.hook_timeout_s`（默认 2s）只对 async 钩子可中断；
- 不做 `decide_action` 钩子——让模块直接给动作就等于绕开 OmniJev，与本能力立意相反。

## 4. 决策器 API

```python
dec = await agile.limited_ui(spec, hooks=None)   # 只创建；不弹窗、不抓帧

res = await dec.open()      # → OpenResult(ok, mode, reason, window, overlap, dry_run_until)
                            #   ok=False 时 reason 说明原因（找不到窗口/回执超时/被拒绝…）

d = await dec.step("意图")  # decide + inject + 审计一行（模块循环用这个）
d = await dec.decide("意图")  # 只决策，不注入
await dec.inject(d)           # 注入前会重新复核（陈旧/前台/遮挡/限速/白名单）

await dec.safe_stop("原因")   # 只松手
await dec.pause("原因")       # 暂停（保留窗口绑定与批准）
await dec.resume()            # 恢复（人处理完之后）
await dec.close("原因")       # 松手 + 结束会话（幂等）
```

`d` 的关键字段：`ok`（是否真的注入成功）、`state` / `state_source`(`hook|model`)、
`action` / `action_kind`(`key|click`) / `action_text`、`point`、`confidence`、`abstain`、
`uncertain_reason`、`describe()`。

> `decide()` 返回 `ok=True` 只代表"通过了门槛"；**注入前的复核失败会把 `d.ok` 改回 `False`**，
> 所以用 `step()` 时看 `d.ok` 就是"这一步有没有真的发出去"。

> `open()` 会等 HIL 批准（最多 120 秒）。**`open()` 返回前不要调用 `step()`**：
> 那时会话还是 closed，`step()` 会抛 `UIDrvError`。interval 循环里请自己记一个"已就绪"标志。

### 每步都做了什么（顺序不可换）

1. 试运行超时 / 会话时长上限 / 暂停恢复 / 急停信号检查；
2. 窗口存活 + 前台检查（失焦 ⇒ 暂停、提示用户点击目标窗口、**连帧都不抓**）；
3. 眨眼（有遮挡时）→ 抓帧 → 帧校验（全黑/零方差 ⇒ 本步不决策）；
4. 钩子 → 组问（**一次 invoke**）→ 门限 → 一致性校验；
5. 注入前：限速 → 前台 → 重新抓帧对比**感知哈希**（画面变了就拒）→ 点击点 `WindowFromPoint` 校验；
6. 注入 → 再抓一帧算 `frame_changed` → 写审计。

## 5. 急停（三条路，都会先松手）

1. **全局热键 `Ctrl+Alt+K`**（前端 → 插件 → 会话）；
2. **鼠标甩到屏幕角落**（沿用 `PYAUTOGUI_FAILSAFE` 约定）；
3. **配置窗口 Agile 页的 `[停止]`**，或 `agileOperate(action="unload", ...)` / `disable`。

热键/面板停止会**暂停**（不会自动恢复，等你或主 Agent 放行）；角落/上限/窗口异常则会结束或暂停。

另外：目标窗口被重开/关闭、拿不准、到达上限，都会松手并结束或暂停。

## 6. 拿不准 / 失焦 / 上限之后

**能力会**：先松手 → 写审计 → 用 `batched` 优先级唤醒主 Agent（合并窗口 30s，且只在你空闲时注入）
→ 暂停但保留会话 → 表 3 分钟没人处理就自动结束。

**主 Agent 怎么处理**（读证据、改方案、放行）：

```python
read("faustbot://agile/{module}/ops/recent")              # 最近 200 步
read("faustbot://agile/{module}/ops/summary")             # 每问次数/平均把握度/不确定次数/状态来源
read("faustbot://agile/{module}/ops/last_escalation.json")# 最后一次升级的完整细节
read("faustbot://agile/{module}/ops/frame.jpg")           # 最近一帧截图的文件路径

write("faustbot://agile/{module}/control", '{"action":"resume"}')      # 放行
write("faustbot://agile/{module}/control", '{"action":"stop"}')        # 结束
write("faustbot://agile/{module}/control",
      '{"action":"patch_keys","states":{"battle":{"actions":{"1":"打出最左边的牌"}}}}')
```

调参回路（`ops/summary` 里能直接看出来）：
- `frame_changed` 长期 `false` ⇒ 注入根本没被游戏接受（反作弊 / raw input / UIPI），提醒用户；
- `state_source=hook` 比例高 ⇒ 你的 CV 已经够用，可以减少问数、抬阈值；
- 某问 `uncertain` 比例高 ⇒ 改问法（`describe` 写得更可分辨）或抬 `confidence_min`。

## 7. 审计在哪

`~/.faustbot/plugin_data/agile-engine/ui-ops/<模块名>/`

```
{YYYYMMDD}.jsonl            # 每步一行（含 dry_run/state/action/confidence/概率/门限/是否注入/耗时…）
summary.json                # 会话汇总
last_escalation.json        # 最后一次升级的细节
frames/{step_id}-{阶段}.jpg # 升级/异常必存；另外每 50 步一张；超 3 天或 500 张自动清理
```

## 8. 硬性限制（写之前先确认能不能接受）

- 只有**按单个键**与**左键单击一个区域**；没有拖拽/滚轮/双击/相对位移/视角操作；
- **目标窗口必须保持前台**：失焦就暂停（键盘事件本来就只能进前台窗口，
  `PostMessage` 对绝大多数游戏引擎无效）；
- 不支持"游戏不在前台也能操作"、不支持独占全屏 + HDR 抓帧（GDI 会全黑 ⇒ 帧校验会报错并提示
  改成"无边框窗口化"）；
- 模糊图会被门限判为不确定（这是**正确行为**，实测模糊图 confidence 0.28–0.39）；
- 眨眼：桌宠窗口与目标窗口重叠时，抓帧与注入前各藏一次模型（每次 ≤80ms）；
  不重叠则全程不眨眼。

## 9. 常见报错与处置

| 现象 | 原因 / 处置 |
|---|---|
| `open()` 说"找不到目标窗口" | exe 名要写全（`MyGame.exe`），标题片段别写错；窗口被最小化到托盘也算找不到 |
| `open()` 说"回执超时" | 前端渲染器没响应命令（插件前端脚本没加载）⇒ 没法眨眼藏模型、也没法显示提示条，实时会话拒绝开启。可用试运行 |
| 每步都不注入，`inject_result=stale_frame[N]` | 决策到注入之间画面变了（N 越大变化越大）。回合制静止界面 N 通常为 0；长动画过场可以考虑降低问数或等画面稳定 |
| `frame_invalid` | 抓帧全黑或几乎没有对比度（`flat[range=N]`）：独占全屏 / HDR / DRM。改成无边框窗口化 |
| `inject_result=occluded[...]` | 点击点上的顶层窗口不是目标（被别的窗口或我们自己的覆盖层挡住）；眨眼只在重叠时生效 |
| 注入报错上抛 | 目标以管理员运行时会被 UIPI 丢弃——**不静默吞**，需要以管理员身份运行 FaustBot 或降低游戏权限 |
| `conf=0.3` 一直不确定 | 画面语义不清：把 `describe`/`meaning` 写得更具体，或让 `context` 钩子补文字（血量/手牌） |
