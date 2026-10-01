# Processor 使用（让模块看到画面、听到声音）

Processor 是跑在**独立子进程**里的重计算单元（模型推理、OCR），由 Agent 主动调用。Agile 模块通过
`await agile.processor(name, data)` 使用它们：一次调用 = 借出 → 等就绪 → 推算 → 归还，引用计数不会泄漏。

| | Agile 模块 | Processor |
|---|---|---|
| 跑在哪 | Agent 进程内（无沙箱） | **独立子进程**（模型/显存隔离，崩溃不拖垮 Agent） |
| 谁调用 | 你自己写逻辑 | 你在模块里 `await agile.processor(...)` 调 |
| 一次调用 | 毫秒级纯逻辑 | 首次可能触发 setup（下载模型/量化，分钟级） |

现有三个：`VAD`（语音活动检测）、`OCR`（屏幕取字）、`OMNIJEV`（全模态决策模型：看图/看视频回答带概率的问题）。
**模块的 interval hook 跑在模块自己的 loop 线程里**，`agile.processor()` 内部会切回 backend 主循环，直接 await 即可。

## 调用方式

```python
import numpy as np, pyautogui   # 截图与数组化（模块在 Agent 进程内，可直接用这些库）
from agile_base import AgileModule, AgileContext

@module.registerInterval(30)
async def watch(agile: AgileContext):
    out = await agile.processor("OMNIJEV", {
        "frames": [np.asarray(pyautogui.screenshot().convert("RGB"))],   # 也可直接给图片路径
        "questions": {
            "danger": {"type": "noul", "instructions": "画面里出现了报错弹窗。"},
            "what":   {"type": "choice", "instructions": "当前是哪个界面？",
                       "criteria": {"编辑器": None, "浏览器": None, "终端": None}},
        },
    })
    if out["answers"]["danger"]["noul"] > 0.8:
        await agile.event_fire("screen::error_dialog", out["answers"], "屏幕出现报错弹窗，提醒用户")

def get_agile_module():
    return module
```

| 参数 | 说明 |
|---|---|
| `name` | `"VAD"` / `"OCR"` / `"OMNIJEV"` |
| `data` | 该 Processor 的入参（见下各节） |
| `config=None` | 该 Processor 的配置（如 OCR 语言、OMNIJEV 尺寸/量化）。**已有人用不同 config 持有时会抛 `ProcessorConfigMismatchError`**，不要硬塞 |
| `timeout=None` | 单次推算超时（秒）；缺省用 Processor 自己的 `INVOKE_TIMEOUT` |
| `wait_timeout=None` | 等就绪的上限（秒）。`None` = 一直等——**首次调用可能触发模型下载/量化** |

返回值就是该 Processor 的 `invoke` 结果原文。排查用 `await agile.processor_status("OMNIJEV")`
（返回 `state` / `refcount` / `phase` / `last_error` / `config` / `invokes_failed` 等）。

## 总览

| Processor | 干什么 | 入参要点 | 首次 setup | 单次耗时 | 资源 |
|---|---|---|---|---|---|
| `VAD` | 一段音频帧里有没有人声 | `float32[512]` @16kHz | 下载 silero-vad（秒级） | ~1ms | CPU |
| `OCR` | 图片/截图里的文字与位置 | `uint8[H,W,3\|4]` | 下载 easyocr 模型（分钟级，按语言） | 0.1–2s | CPU 或 GPU |
| `OMNIJEV` | 看图/看视频回答带概率的问题（不生成文本） | `frames` + `questions` | **下载 1.7–8.9GB 权重 + 量化，10–45 分钟** | 0.7–2.5s | GPU（4B+4bit 约 3.8GB 显存） |

共同点：**串行 FIFO**（同一 Processor 同时只有一个 invoke 在跑）；空闲 5 分钟（`PROCESSOR_IDLE_TIMEOUT=300`）
会被回收，下次调用自动重启（权重在磁盘上，重启约 7–20s）。

## VAD —— 语音活动检测

```python
out = await agile.processor("VAD", {"audio": frame})     # frame: np.float32[512]
# → {"probability": 0.97, "is_speech": True}
```

- 窗口固定 **512 样本 / 16kHz（32ms）**，形状不对直接报错；阈值 0.5 由 `is_speech` 给出，阈值自己判断就用 `probability`。
- 无 config。CPU、单次毫秒级，适合**高频**调用。
- 注意：Agile 模块拿不到麦克风音频（音频走 VAD websocket 通道），这个 Processor 主要给语音链路用；
  模块要用它，得自己有音频来源（例如读 wav 文件切片）。

## OCR —— 屏幕/图片取字

```python
out = await agile.processor("OCR", {"image": np.asarray(pyautogui.screenshot().convert("RGB"))})
# → [{"text": "保存", "confidence": 0.98, "box": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]}, ...]
out = await agile.processor("OCR", {"image": img, "detail": 0})
# → ["保存", "取消", ...]（只要文本，快一些）
```

- `image`：`uint8[H,W,3]` 或 `[H,W,4]`（RGBA 截图会自动丢 alpha）；坐标是**像素**。
- config：`{"langs": ["ch_sim", "en"], "gpu": False}`。换语言会重新 setup（下载对应模型）。
- 默认 CPU；`gpu: True` 明显更快但会和 OMNIJEV 抢显存（OMNIJEV-4B 4bit 占约 3.8GB）。
- 适合：读窗口标题/按钮文字、判断某个文案是否出现。**要判断"点哪里"就别只靠 OCR**——用 OMNIJEV 的区域选项。

## OMNIJEV —— 看图回答带概率的问题（重点）

一次调用 = 一次前向，**不生成文本**，直接给出每个问题的概率分布。四种问法（`"type"` 只能是这三种之一）：

| type | 示例 | 返回 |
|---|---|---|
| `noul` | `{"type":"noul","instructions":"画面里在下雨。"}` | `{"noul": P(是)}` |
| `choice` | `{"type":"choice","instructions":"下一步点哪里？","criteria":{"保存按钮":null,"关闭按钮":null}}` | `{"choice": key, "probabilities": {...}, "abstain": P(都不是), "valid": bool, "confidence": c}` |
| `choice`+区域 | `{"type":"choice","instructions":"主体在哪？","options":[{"key":"左上","region":{"box":[0,0,333,333]}}, ...]}` | 同上（`box` 用 0–1000 归一化坐标） |
| `score` | `{"type":"score","instructions":"任务进度？","levels":["刚开始","过半","快完成"]}` | `{"score": level, "probabilities": {...}, "confidence": c}` |

入参：

```python
out = await agile.processor("OMNIJEV", {
    "frames": ["D:/shot.png"],                 # 或 np.uint8[H,W,3|4]；多张 → 拼成编号面板（"第一张/第二张"可指代）
    "questions": {"qid": {...}},               # 必填，非空 dict
}, config={"size": "4B", "quant": "4bit"})
# → {"answers": {"qid": {...}}, "frames": [...], "video": {...}, "tokens": 722, "latency_s": 2.27}
```

- **视频**：`{"video": {"path": "clip.mp4"}, "questions": {...}}`（与 `frames` 二选一；用 ffmpeg 抽 16 帧拼一张图，本机要求 PATH 上有 `ffmpeg`/`ffprobe`）。
  `video_state()` 返回的 `{"n_frames","timestamps","duration"}` 会随 `out["video"]` 回传。
- config：`size` `"4B"`（默认）/`"2B"`/`"0.8B"`；`quant` `"4bit"`（默认）/`"8bit"`/`"none"`；`max_pixels`（默认 `768*28*28`）；`state_cache`（默认 4）。
  改 `size`/`quant` 会触发新的 setup（下载/量化），改 `max_pixels`/`state_cache` 不会。
- **成本**：4B+4bit 首次 setup ≈ 10GB 下载 + 量化，耗时按网速分钟到几十分钟；之后单次 0.7–2.5s、显存约 3.8GB。
  **别放进高频 interval**（≥10s 起，且注意 `event_fire` 的每分钟触发上限）。
- **概率是校准过的**：`noul`/`abstain`/`confidence` 可直接用于阈值（如 `> 0.8` 才报警）；但要清楚 4bit 量化会让
  0.5 附近的概率漂移，用于阈值报警时先用真实画面自测阈值。
- 画面坐标：`out["frames"]` 是实际喂给模型的图片路径（数组会落盘到 `~/.faustbot/models/omnijev/frames/`，
  内容寻址、重复截图不重写），排查"模型看到的是什么"时直接读它。

## 组合模式

```python
# 1) 定时看屏幕 + 报警（视觉巡检）
@module.registerInterval(20)
async def patrol(agile: AgileContext):
    img = np.asarray(pyautogui.screenshot().convert("RGB"))
    out = await agile.processor("OMNIJEV", {"frames": [img], "questions": {
        "hazard": {"type": "noul", "instructions": "画面里有火灾、烟雾或明显危险。"},
        "where":  {"type": "choice", "instructions": "危险区域在哪？", "options": [
            {"key": "上部", "region": {"box": [0,0,1000,500]}},
            {"key": "下部", "region": {"box": [0,500,1000,1000]}}]}}})
    a = out["answers"]
    if a["hazard"]["noul"] > 0.75:
        await agile.event_fire("patrol::hazard", a, "监控画面疑似危险，请查看",
                               priority="interrupt")

# 2) 抓某个按钮/输入框的文字（OCR 轮询，便宜）
@module.registerInterval(5)
async def title(agile: AgileContext):
    items = await agile.processor("OCR", {"image": np.asarray(pyautogui.screenshot().convert("RGB")), "detail": 1})
    hits = [i["text"] for i in items if "错误" in i["text"]]
    if hits:
        await agile.lwarning(f"屏幕出现错误文案: {hits[:3]}")
```

## 错误与排查

| 异常 | 含义 / 处理 |
|---|---|
| `ProcessorNotFoundError` | 名字写错（只有 `VAD`/`OCR`/`OMNIJEV`） |
| `ProcessorStartError` | 启动失败：setup 出错（下载/量化失败、缺依赖、显存不足）→ `last_error` 与日志在 `processor_status()` |
| `ProcessorNotReadyError` | 还在启动/已被回收：本入口已先 `wait_until_ready`，通常只有 `timeout` 传得很小时才会遇到 |
| `ProcessorInvokeError` | 子进程里 invoke 抛异常（入参形状不对、模型报错）——消息里有子进程 traceback 尾部 |
| `ProcessorConfigMismatchError` | 已有持有者且 config 不一致：要么复用同一 config，要么等它释放 |
| `ProcessorTimeoutError` | 等待就绪/推算超时（`wait_timeout`/`timeout` 太小）；模型没坏，下次调用会继续 |

排查顺序：`await agile.processor_status("OMNIJEV")` 看 `state`/`last_error` → 读该 Processor 的日志
（管理接口 `GET /faust/admin/processors/OMNIJEV?include_log=true`，或让 Agent 去读）→ 再复现一次调用。

## 注意

- 模块里**不要**在 interval 里同步阻塞地做重活（截图 + 推理已经够重）：OMNIJEV 单次 1–2.5s，interval 别低于 10s。
- 一次调用会**串行排队**：同 Processor 的并发调用按 FIFO 排队，不会互相插队。
- 推理期间会占用显存（4B+4bit ≈ 3.8GB）；与本地 TTS/图像生成共用同一张卡时注意余量。
- 首次调用可能长时间等待（下载/量化）：要么接受（`wait_timeout=None`），要么传 `wait_timeout` 并在失败时用
  `processor_status()` 解释原因，不要让模块静默卡死。
- 这些能力**只读**画面/音频，不做键鼠模拟；需要操作的场景请回到 Agent 侧的对应工具。
