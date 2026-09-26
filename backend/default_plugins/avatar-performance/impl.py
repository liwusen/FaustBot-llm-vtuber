"""Avatar Performance：Live2D / 图片模型的表演控制与交互分级回传。

三条流：
1. agent 表演指令 —— 工具 → `FrontendBridge._push` → `/faust/command` → 前端 `performance.js`
   → soullink 引擎 manual 层 / 原生 expression+motion。调用即生效，不等文本。
2. 用户交互 —— 前端指针分级 → `communicate(action="interaction")` → 记入 ring buffer、
   微调情绪；重度额外入队触发器（走 chat.py 的前台流式推送，agent 立即回应）。
3. 能力上报 —— 模型加载成功后 `communicate(action="report_capabilities")` → 本插件缓存，
   作为"当前模型真实可用能力"的唯一事实源（后端读磁盘 model3.json 与前端加载态不一致）。
   缓存缺失或过期时，工具会顺手下发 `REQUEST_AVATAR_CAPABILITIES` 命令要求前端重新上报
   （同一节流窗内只发一次），因此后端重启/插件重载后不必等下一次模型加载。

注意：能力缓存是进程内模块级状态。后端重启或插件重载（任何插件配置变更都会
`reload(force=True)`）后需要等前端下一次上报——模型加载时会上报一次。
"""

from __future__ import annotations

import collections
import json
import math
import time
import uuid
from typing import Any

from langchain.tools import tool

import faust_backend.backend2front as backend2frontend
from faust_backend.logger import get_logger
from faust_backend.plugin_system import FaustPlugin, PluginContext, ToolSpec, hookimpl

log = get_logger("faust.plugins.avatar-performance")

# FACS_KEYS 不用于限定 agent 能用哪些键——那一依据是前端上报的 facs_keys（该模型真实可用的
# 参数映射）。它只校验上报载荷的形状：上报集合中不在 FACS_KEYS 内的键被忽略并回显为
# unknown_facs_keys，避免上游引擎新增键时这里硬失败。
FACS_KEYS = (
    "browInnerUp", "browOuterUp", "browDown",
    "eyeOpen", "eyeSmile", "eyeSquint", "eyeBlinkL", "eyeBlinkR",
    "mouthSmile", "mouthFrown", "mouthOpen", "mouthPucker",
    "gazeX", "gazeY",
    "headX", "headY", "headZ",
    "bodyX", "bodyY", "bodyZ",
    "blush", "tear", "sweat", "breath",
)

# 交互类型 → emotion-engine 的有符号情绪标签（`EmotionEngineStore.apply_signed_emotion_tag_list`）
INTERACTION_NUDGES: dict[str, tuple[str, ...]] = {
    "tap": ("+CURIOSITY",),
    "stroke": ("+JOY", "-BOREDOM"),
    "multi_tap": ("+IRRITATION", "+CURIOSITY"),
    "long_press": ("+IRRITATION",),
    "hover": (),
}
INTERACTION_KINDS = tuple(INTERACTION_NUDGES)
INTERACTION_TIERS = ("light", "heavy")

CAPABILITY_STALE_SECONDS = 120.0   # 超过此秒数未收到上报 → 工具报"前端未就绪"
CAPABILITY_REQUEST_MIN_INTERVAL_SECONDS = 5.0  # 缓存不可用时，最快隔多久再请求前端上报一次
INTERACTION_NOTE_TTL_SECONDS = 120.0  # 超过此秒数的交互不再写进下一轮消息备注
INTERACTION_BUFFER_MAX = 50
HOLD_SECONDS_DEFAULT = 3.0
HOLD_SECONDS_MAX = 30.0
MAX_HOLD_MS = int(HOLD_SECONDS_MAX * 1000)

MODEL_TYPES = ("live2d", "images", "vrm", "unknown")
CLEAR_SCOPES = ("all", "facs", "native")

# 能力缓存（模块级，进程内）
_capabilities: dict[str, Any] = {}
_capabilities_at: float = 0.0
_capabilities_requested_at: float = 0.0
_interactions: collections.deque = collections.deque(maxlen=INTERACTION_BUFFER_MAX)
_last_note_at: float = 0.0


def _now() -> float:
    return time.time()


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _str_list(value: Any) -> list[str]:
    """字符串列表化：非列表返回空，逐项 strip 并丢弃空串（空 Motions 组名是无意义请求）。"""
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _hold_ms(seconds: Any, default_seconds: float) -> int:
    """hold_seconds → 毫秒，越界夹到 [0, HOLD_SECONDS_MAX]。"""
    number = _finite_number(seconds)
    if number is None:
        number = default_seconds
    return int(round(min(max(number, 0.0), HOLD_SECONDS_MAX) * 1000))


def _ok(command: str, applied: dict[str, Any] | None = None, clamped: list[str] | None = None, hold_ms: int = 0) -> str:
    return json.dumps(
        {
            "status": "ok",
            "command": command,
            "applied": applied or {},
            "clamped": clamped or [],
            "hold_ms": hold_ms,
        },
        ensure_ascii=False,
    )


def _err(message: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"status": "error", "error": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _request_capabilities() -> None:
    """请求前端重新上报能力；同一节流窗内只发一次（每个 Avatar 工具调用都会走到这里）。"""
    global _capabilities_requested_at
    now = _now()
    if now - _capabilities_requested_at < CAPABILITY_REQUEST_MIN_INTERVAL_SECONDS:
        return
    _capabilities_requested_at = now
    backend2frontend.frontendRequestAvatarCapabilities()


def _capabilities_error() -> str | None:
    """能力缓存不可用时的错误文案；可用返回 None。

    缓存在前端加载模型时上报一次，后端重启/插件重载/前端重连都会让它落空或过期，工具自己
    等不回来——因此这里顺带下发 REQUEST_AVATAR_CAPABILITIES 让前端立刻补报。
    """
    if not _capabilities:
        _request_capabilities()
        return "前端尚未上报模型能力（模型可能未加载完成），已请求前端重新上报，请稍后重试"
    age = _now() - _capabilities_at
    if age > CAPABILITY_STALE_SECONDS:
        _request_capabilities()
        return f"前端已 {age:.0f} 秒未上报能力（连接断开？），已请求前端重新上报，请稍后重试"
    return None


def _normalize_capabilities(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """上报载荷 → 内部能力快照 + 被忽略的未知 FACS 键。"""
    model_type = str(raw.get("model_type") or "").strip().lower()
    if model_type not in MODEL_TYPES:
        model_type = "unknown"
    reported_facs = _str_list(raw.get("facs_keys"))
    unknown_facs = [key for key in reported_facs if key not in FACS_KEYS]
    snapshot = {
        "model_type": model_type,
        "model_path": str(raw.get("model_path") or "").strip(),
        "emotions": _str_list(raw.get("emotions")),
        "expressions": _str_list(raw.get("expressions")),
        "motions": _str_list(raw.get("motions")),
        "facs_keys": [key for key in reported_facs if key in FACS_KEYS],
        "profile_source": str(raw.get("profile_source") or "none").strip(),
        "at": _now(),
    }
    return snapshot, unknown_facs


def _capabilities_snapshot() -> dict[str, Any]:
    return dict(_capabilities)


def _interactions_snapshot() -> dict[str, Any]:
    return {
        "count": len(_interactions),
        "max": INTERACTION_BUFFER_MAX,
        "interactions": list(_interactions),
    }


def _summarize(items: list[dict[str, Any]]) -> str:
    """把同一批交互压成可读短句；重复的同类交互合并为「…（×N）」。

    鼠标掠过（hover）这类高频轻交互会在窗口内反复出现，逐条拼接会把备注撑成噪声。
    """
    order: list[str] = []
    counts: dict[str, int] = {}
    for item in items:
        text = str(item.get("summary") or item.get("kind") or "交互")
        if text not in counts:
            order.append(text)
            counts[text] = 0
        counts[text] += 1
    return "；".join(
        f"{text}（×{counts[text]}）" if counts[text] > 1 else text for text in order
    )


def _capability_notes(caps: dict[str, Any]) -> list[str]:
    notes = [
        "工具调用立即生效（早于该轮文本）；文本 token <{MotionName}> / <{EXPRESSION:Name}> 在该句被 TTS 播到时生效，用于让动作穿插在语音中间。",
        "setAvatarParameters 使用语义 FACS 键（模型无关），hold_seconds 到期后自动交还 soullink 引擎。",
    ]
    if caps.get("model_type") == "images":
        notes.append("图片模型没有动作组与参数层：用 setAvatarExpression 换图；playAvatarMotion / setAvatarParameters 会明确报错。")
        return notes
    if not caps.get("motions"):
        notes.append("该模型未声明任何 Motions 组，playAvatarMotion 无可用名称。")
    if not caps.get("expressions"):
        notes.append("该模型未声明任何 Expressions（原生 exp3），setAvatarExpression 无可用名称。")
    if not caps.get("facs_keys"):
        notes.append("该模型无可用参数层（profile 生成失败？），setAvatarParameters 会明确报错。")
    return notes


# ── 工具（全部 async，直接操作模块级缓存 + 下发 AVATAR_* 命令） ──


@tool
async def listAvatarCapabilities() -> str:
    """
    Description:
        获取当前模型（Live2D / 图片）真正可用的表演能力：情绪名、原生表情名、原生动作组名、
        语义 FACS 键。事实源是前端加载模型后的上报，不是磁盘上的 model3.json。
        调用 setAvatar* 之前先用它确认名称，不要凭记忆猜。
    Args:
        None
    Returns:
        str(json): model_type / model_path / emotions / expressions / motions / facs_keys /
                   profile_source / capabilities_age / notes。失败时 {"status":"error",...}。
    """
    error = _capabilities_error()
    if error:
        return _err(error)
    caps = _capabilities_snapshot()
    return json.dumps(
        {
            "status": "ok",
            "model_type": caps.get("model_type"),
            "model_path": caps.get("model_path"),
            "emotions": caps.get("emotions") or [],
            "expressions": caps.get("expressions") or [],
            "motions": caps.get("motions") or [],
            "facs_keys": caps.get("facs_keys") or [],
            "profile_source": caps.get("profile_source"),
            "capabilities_age": round(_now() - _capabilities_at, 1),
            "notes": _capability_notes(caps),
        },
        ensure_ascii=False,
    )


@tool
async def setAvatarEmotion(emotion: str, intensity: float = 0.6) -> str:
    """
    Description:
        切换模型情绪（驱动 soullink 引擎的 VAD 表演层，连续过渡、不打断 idle/眨眼）。
        可用情绪名见 listAvatarCapabilities() 的 emotions。
    Args:
        emotion (str): 情绪名。
        intensity (float): 强度 0~1，越界会被夹取并写进 clamped，默认 0.6。
    Returns:
        str(json): {"status":"ok","command":"AVATAR_EMOTION",...} 或 {"status":"error",...}。
    """
    error = _capabilities_error()
    if error:
        return _err(error)
    available = list(_capabilities.get("emotions") or [])
    name = str(emotion or "").strip()
    if name not in available:
        return _err(f"未知情绪: {name}", available=available)
    value = _finite_number(intensity)
    if value is None:
        return _err(f"intensity 必须是有穷数: {intensity!r}")
    bounded = min(max(value, 0.0), 1.0)
    clamped = ["intensity"] if abs(bounded - value) > 1e-9 else []
    backend2frontend.frontendAvatarCommand("AVATAR_EMOTION", {"emotion": name, "intensity": round(bounded, 4)})
    return _ok("AVATAR_EMOTION", applied={"emotion": name, "intensity": round(bounded, 4)}, clamped=clamped)


@tool
async def setAvatarExpression(name: str, hold_seconds: float = 3.0) -> str:
    """
    Description:
        切换原生表情并保持 hold_seconds：Live2D 走原生 exp3（名称见 listAvatarCapabilities() 的
        expressions），图片模型走情绪图组（名称见 emotions）。保持期间引擎不抢表情，到期自动交还。
    Args:
        name (str): 表情名（图片模型下为情绪图组名）。
        hold_seconds (float): 保持秒数，夹到 [0, 30]，默认 3。
    Returns:
        str(json): {"status":"ok","command":"AVATAR_EXPRESSION",...} 或 {"status":"error",...}。
    """
    error = _capabilities_error()
    if error:
        return _err(error)
    if _capabilities.get("model_type") == "images":
        available = list(_capabilities.get("emotions") or [])
        label = "情绪图组"
    else:
        available = list(_capabilities.get("expressions") or [])
        label = "表情"
    target = str(name or "").strip()
    if target not in available:
        return _err(f"未知{label}: {target}", available=available)
    hold_ms = _hold_ms(hold_seconds, HOLD_SECONDS_DEFAULT)
    backend2frontend.frontendAvatarCommand("AVATAR_EXPRESSION", {"name": target, "hold_ms": hold_ms})
    return _ok("AVATAR_EXPRESSION", applied={"name": target}, hold_ms=hold_ms)


@tool
async def playAvatarMotion(name: str, hold_seconds: float | None = None) -> str:
    """
    Description:
        播放原生动作（motion3 组名，见 listAvatarCapabilities() 的 motions），保持 hold_seconds。
        图片模型没有动作组，调用会明确报错，请改用 setAvatarExpression。
    Args:
        name (str): 动作组名。
        hold_seconds (float, optional): 保持秒数，夹到 [0, 30]，默认 3。
    Returns:
        str(json): {"status":"ok","command":"AVATAR_MOTION",...} 或 {"status":"error",...}。
    """
    error = _capabilities_error()
    if error:
        return _err(error)
    if _capabilities.get("model_type") == "images":
        return _err("图片模型没有动作组，请改用 setAvatarExpression")
    available = list(_capabilities.get("motions") or [])
    target = str(name or "").strip()
    if target not in available:
        return _err(f"未知动作: {target}", available=available)
    hold_ms = _hold_ms(hold_seconds, HOLD_SECONDS_DEFAULT)
    backend2frontend.frontendAvatarCommand("AVATAR_MOTION", {"name": target, "hold_ms": hold_ms})
    return _ok("AVATAR_MOTION", applied={"name": target}, hold_ms=hold_ms)


@tool
async def setAvatarParameters(facs: dict[str, float], hold_seconds: float = 3.0) -> str:
    """
    Description:
        按语义 FACS 键（模型无关）做参数级控制，例如 {"headZ": 0.4} 歪头、{"gazeX": -0.5} 看向左侧。
        可用键见 listAvatarCapabilities() 的 facs_keys；值域 [-1, 1]，越界会被夹取并写进 clamped。
        保持 hold_seconds 后自动交还引擎。
    Args:
        facs (dict[str, float]): FACS 键 → 数值。
        hold_seconds (float): 保持秒数，夹到 [0, 30]，默认 3。
    Returns:
        str(json): {"status":"ok","command":"AVATAR_FACS","applied":{...},"clamped":[...],"hold_ms":3000} 或错误。
    """
    error = _capabilities_error()
    if error:
        return _err(error)
    available = list(_capabilities.get("facs_keys") or [])
    if not available:
        return _err("当前模型无可用参数层（图片模型或 profile 生成失败）")
    if not isinstance(facs, dict) or not facs:
        return _err("facs 必须是非空对象", available=available)
    keys = [str(key).strip() for key in facs]
    unusable = [key for key in keys if key not in available]
    if unusable:
        return _err(f"键不可用: {', '.join(unusable)}", available=available)
    applied: dict[str, float] = {}
    clamped: list[str] = []
    for key, raw_value in facs.items():
        name = str(key).strip()
        value = _finite_number(raw_value)
        if value is None:
            return _err(f"参数值必须是有穷数: {name}={raw_value!r}")
        bounded = min(max(value, -1.0), 1.0)
        if abs(bounded - value) > 1e-9:
            clamped.append(name)
        applied[name] = round(bounded, 4)
    hold_ms = _hold_ms(hold_seconds, HOLD_SECONDS_DEFAULT)
    backend2frontend.frontendAvatarCommand("AVATAR_FACS", {"facs": applied, "hold_ms": hold_ms})
    return _ok("AVATAR_FACS", applied=applied, clamped=clamped, hold_ms=hold_ms)


@tool
async def clearAvatarPerformance(scope: str = "all") -> str:
    """
    Description:
        提前清除 agent 对模型的表演覆盖，把控制权交还 soullink 引擎。
    Args:
        scope (str): "all"（全部）/ "facs"（参数层）/ "native"（原生表情与动作）。
    Returns:
        str(json): {"status":"ok","command":"AVATAR_CLEAR",...} 或 {"status":"error",...}。
    """
    target = str(scope or "all").strip().lower()
    if target not in CLEAR_SCOPES:
        return _err(f"未知 scope: {target}", available=list(CLEAR_SCOPES))
    backend2frontend.frontendAvatarCommand("AVATAR_CLEAR", {"scope": target})
    return _ok("AVATAR_CLEAR", applied={"scope": target})


TOOLS = (
    listAvatarCapabilities,
    setAvatarEmotion,
    setAvatarExpression,
    playAvatarMotion,
    setAvatarParameters,
    clearAvatarPerformance,
)


def _prompt_suffix() -> str:
    lines = [
        "[Avatar Performance]",
        "你可以直接控制当前模型（Live2D / 图片）的表演，工具调用立即生效（早于该轮文本）：",
        "- listAvatarCapabilities()：先查当前模型可用的情绪/表情/动作组/FACS 键，不要凭记忆猜名字。",
        "- setAvatarEmotion(emotion, intensity)：切换情绪（驱动引擎的 VAD 表演层，连续过渡）。",
        "- setAvatarExpression(name, hold_seconds)：切换原生表情（exp3；图片模型下是情绪图组）。",
        "- playAvatarMotion(name, hold_seconds)：播放原生动作（motion3 组名；图片模型不可用）。",
        "- setAvatarParameters(facs, hold_seconds)：按语义 FACS 键（模型无关）做参数级控制，例如 {\"headZ\": 0.4} 歪头。",
        "- clearAvatarPerformance(scope)：提前把表演控制权交还引擎。",
        "使用条件：想“现在立刻”改变情绪/表情/动作/姿态时用这些工具；想让动作穿插在语音中间、跟着某句话出现时，"
        "在输出里写 <{MotionName}> / <{EXPRESSION:Name}>（该 token 在所属句子被播到时生效）。",
        "两者同属 agent 表演层，共用同一个原生覆盖槽：后写覆盖前写，hold_seconds 到期后自动交还引擎。",
        "用户对模型的点击/拖动/长按会分级回传给你：轻度交互不打断你，会在下一轮消息里以“（刚才用户与你互动：…）”出现；"
        "重度交互（连续戳、长按、拖动）会立即打断并触发你回应。"
        "需要主动查阅最近交互时读 faustbot://avatar/interactions.json。",
    ]
    if _capabilities:
        lines.append(
            f"当前模型：{_capabilities.get('model_type')}；"
            f"情绪 {len(_capabilities.get('emotions') or [])} 个、"
            f"表情 {len(_capabilities.get('expressions') or [])} 个、"
            f"动作组 {len(_capabilities.get('motions') or [])} 个、"
            f"可用 FACS 键 {len(_capabilities.get('facs_keys') or [])} 个。"
        )
    else:
        lines.append(
            "当前尚未收到前端的能力上报（模型未加载完成？）：这些工具会明确报错，并自动请求前端"
            "重新上报；稍等片刻重试即可（也可先 listAvatarCapabilities() 确认）。"
        )
    return "\n".join(lines)


def _tool_description(func: Any) -> str:
    """工具文档的首行摘要（跳过 "Description:" 标签行，供插件面板/工具表展示）。"""
    for line in (getattr(func, "description", "") or "").splitlines():
        text = line.strip()
        if not text or text.lower().startswith("description:"):
            continue
        return text
    return ""


class Plugin(FaustPlugin):
    def __init__(self):
        self.ctx: PluginContext | None = None

    async def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        await ctx.vfs_write_symbolic(
            "/avatar/capabilities.json",
            lambda _path: json.dumps(_capabilities_snapshot(), ensure_ascii=False, indent=2),
            description="当前模型的表演能力快照（前端上报：情绪/表情/动作组/FACS 键）",
        )
        await ctx.vfs_write_symbolic(
            "/avatar/interactions.json",
            lambda _path: json.dumps(_interactions_snapshot(), ensure_ascii=False, indent=2),
            description="最近用户与模型的交互记录（最多 50 条）",
        )

    @hookimpl
    async def plugin_unloaded(self, ctx: PluginContext) -> None:
        """插件卸载/禁用时移除 VFS 节点，避免 agent 读到上一任模型的能力快照。"""
        if ctx is None:
            return
        try:
            await ctx.vfs_delete("/avatar")
        except Exception as exc:  # 清理失败不应影响插件卸载
            log.debug("avatar-performance VFS 清理失败: %s", exc)

    @hookimpl
    def register_tools(self, ctx: PluginContext) -> list:
        return [
            ToolSpec(
                name=func.name,
                tool=func,
                enabled_by_default=True,
                description=_tool_description(func),
            )
            for func in TOOLS
        ]

    @hookimpl
    def register_prompt_suffix(self) -> list[str]:
        return [_prompt_suffix()]

    @hookimpl
    async def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        action = str((payload or {}).get("action") or "get_state").strip().lower()
        if action == "get_state":
            return {
                "status": "ok",
                "capabilities": _capabilities_snapshot(),
                "capabilities_age": round(_now() - _capabilities_at, 1) if _capabilities else None,
                "interactions": list(_interactions),
            }
        if action == "report_capabilities":
            return self._handle_report(payload)
        if action == "interaction":
            return await self._handle_interaction(payload, ctx)
        return {"status": "error", "detail": f"unknown action: {action}"}

    @hookimpl
    def message_received(self, msg: Any, history: list, ctx: PluginContext) -> str | None:
        global _last_note_at
        now = _now()
        fresh = [
            item
            for item in _interactions
            if float(item.get("at") or 0.0) > _last_note_at
            and now - float(item.get("at") or 0.0) <= INTERACTION_NOTE_TTL_SECONDS
        ]
        if not fresh:
            return None
        # 游标只用于注入去重：ring buffer 本身不消费，仍是 VFS / get_state 的数据源
        _last_note_at = now
        return f"{msg}\n（刚才用户与你互动：{_summarize(fresh)}）"

    # ── communicate 内部实现 ──

    def _handle_report(self, payload: dict | None) -> dict[str, Any]:
        global _capabilities, _capabilities_at
        raw = (payload or {}).get("capabilities")
        if not isinstance(raw, dict):
            return {"status": "error", "detail": "capabilities must be an object"}
        snapshot, unknown_facs = _normalize_capabilities(raw)
        _capabilities = snapshot
        _capabilities_at = float(snapshot["at"])
        log.info(
            "能力上报: type=%s path=%s motions=%d expressions=%d facs=%d profile=%s",
            snapshot["model_type"],
            snapshot["model_path"],
            len(snapshot["motions"]),
            len(snapshot["expressions"]),
            len(snapshot["facs_keys"]),
            snapshot["profile_source"],
        )
        return {
            "status": "ok",
            "model_type": snapshot["model_type"],
            "facs_key_count": len(snapshot["facs_keys"]),
            "unknown_facs_keys": unknown_facs,
        }

    async def _handle_interaction(self, payload: dict | None, ctx: PluginContext) -> dict[str, Any]:
        raw = (payload or {}).get("interaction")
        if not isinstance(raw, dict):
            return {"status": "error", "detail": "interaction must be an object"}
        tier = str(raw.get("tier") or "").strip().lower()
        if tier not in INTERACTION_TIERS:
            return {"status": "error", "detail": f"tier must be one of {list(INTERACTION_TIERS)}: {tier!r}"}
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in INTERACTION_NUDGES:
            return {"status": "error", "detail": f"kind must be one of {list(INTERACTION_KINDS)}: {kind!r}"}
        reported_at = _finite_number(raw.get("at"))
        if reported_at is None:
            return {"status": "error", "detail": "at must be a finite number"}

        record = dict(raw)
        record["tier"] = tier
        record["kind"] = kind
        record["reported_at"] = reported_at
        # 入库时间以服务端时钟为准：前端时钟漂移会让备注游标（_last_note_at）失效
        record["at"] = _now()
        if not str(record.get("summary") or "").strip():
            record["summary"] = f"用户与模型交互（{kind}）"
        _interactions.append(record)

        nudge = list(INTERACTION_NUDGES[kind])
        if nudge:
            await self._apply_nudge(nudge)

        triggered = False
        if tier == "heavy":
            triggered = await self._enqueue_trigger(record, ctx)
        return {"status": "ok", "tier": tier, "nudge": nudge, "triggered": triggered}

    async def _apply_nudge(self, tags: list[str]) -> None:
        """把交互映射成有符号情绪标签，落到已有 EmotionEngineStore。失败只记日志。"""
        from faust_backend.runtime import state as runtime_state

        manager = runtime_state.plugin_manager
        if manager is None:
            log.debug("plugin_manager 未就绪，跳过情绪微调: %s", tags)
            return
        try:
            await manager.communicate("emotion-engine", {"action": "apply_tags", "tags": tags})
        except Exception as exc:
            log.warning("交互情绪微调失败: %s", exc)

    async def _enqueue_trigger(self, record: dict[str, Any], ctx: PluginContext) -> bool:
        """重度交互入队触发器：有前端连接时走 chat.py 的前台流式推送，agent 立即回应。"""
        if ctx is None:
            return False
        summary = str(record.get("summary") or "")
        try:
            await ctx.trigger_create(
                {
                    "id": f"avatar_interaction::{uuid.uuid4().hex[:8]}",
                    "type": "event",
                    "event_name": "avatar_interaction",
                    "payload": record,
                    "recall_description": f"用户与模型交互：{summary}",
                }
            )
            return True
        except Exception as exc:
            log.warning("交互触发器入队失败: %s", exc)
            return False


def get_plugin() -> Plugin:
    return Plugin()
