"""受限 UI 决策器基类：护栏在这里，决策来源在子类（设计 §10）。

职责划分（不可混）：

- **子类**只回答一个抽象问题：``_ask(question, options, frame) -> Answer``（OmniJev / 预设答案）；
- **基类**负责全部护栏：会话生命周期、上限、前台/遮挡校验、帧校验与陈旧检测、门限、
  一致性校验、审计、眨眼、急停与升级。

模块侧入口是 ``AgileContext.limited_ui(spec, hooks)``（见 ``agile_base.py``）；测试用
``deps`` 注入假 ``win`` / ``input`` / ``frontend`` 层，绝不碰真桌面。
"""
from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from .audit import AuditWriter
from .win import TargetSpec, UIDrvError, WindowInfo, WindowNotFoundError, ensure_dpi_aware

# 陈旧检测：注入前的帧与决策帧的感知哈希汉明距离上限（0.5% 的画面对不上就拒绝注入）。
STALE_PHASH_MAX_DISTANCE = 10

# 试运行默认时长（设计 §8.3）
DRY_RUN_SECONDS = 300.0

# HIL 批准弹窗默认超时（设计 §8.2）
HIL_TIMEOUT_SECONDS = 120.0

# 开会话时的握手超时（窗口边界 / 会话态回执）。
# 比眨眼回执（blink.ack_timeout_ms，默认 300ms）宽松得多：开会话只做一次，而且可能撞上
# "后端先起、前端后连"的窗口——命令会在队列里等到 WS 连上才发出去（实测可达数秒）。
# 热路径上的眨眼仍然用 300ms 的短超时。
HANDSHAKE_TIMEOUT_S = 5.0

# 升级后无人处理自动结束会话（设计 §15.4）
ESCALATION_RESOLVE_SECONDS = 180.0

# 失焦提示后多久算"没恢复"（设计 §7.2）
FOCUS_LOST_ESCALATE_SECONDS = 30.0
FOCUS_LOST_ESCALATE_COUNT = 3

# 帧校验连续失败多少次触发升级（设计 §15：帧校验 latch）
FRAME_INVALID_ESCALATE_COUNT = 3

# 钩子连续异常多少次结束会话（设计 §11）
HOOK_ERROR_ESCALATE_COUNT = 3

# context 钩子返回值上限（设计 §11）
HOOK_CONTEXT_MAX_CHARS = 600

# 硬天花板（设计 §14）：超出直接报错，防"配置里多打一个 0"
CEILINGS = {"keys_per_sec": 10, "max_actions": 20000, "max_session_s": 7200}

# 并集动作数上限（设计 §9/§18）
MAX_UNION_ACTIONS = 12

CLICK_BOX_RANGE = (0, 1000)


class SpecError(ValueError):
    """SessionSpec 校验失败（不合法直接报错，不静默修正）。"""


class SessionState(str, Enum):
    CLOSED = "closed"
    DRY_RUN = "dry_run"
    ACTIVE = "active"
    PAUSED = "paused"


# ── 数据形状 ──────────────────────────────────────────────────
@dataclass(frozen=True)
class Frame:
    """一帧画面（客户区裁剪，物理像素）。"""

    image: Any                       # np.uint8[H,W,3] 或 None（仅测试假帧）
    digest: str                      # sha1 前 16 位（内容寻址，用于审计）
    phash: int                       # 64 位 dHash（陈旧检测用）
    rect: tuple[int, int, int, int]  # 屏幕物理像素
    captured_at: float

    def distance(self, other: "Frame") -> int:
        return bin(self.phash ^ other.phash).count("1")

    def phash_hex(self) -> str:
        return f"{self.phash:016x}"


@dataclass(frozen=True)
class Answer:
    """一次提问的答案（形状 = OmniJev answers 的子集）。"""

    key: Optional[str]
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float = 0.0
    abstain: float = 0.0
    noul: Optional[float] = None
    valid: bool = True
    latency_s: float = 0.0
    latency_total_s: Optional[float] = None


@dataclass(frozen=True)
class UIOption:
    """呈现给模型的一个选项。``key`` 是唯一内部键，``label`` 是模型看到的文本。"""

    key: str
    label: str
    kind: str                          # "action" | "state"
    state: Optional[str] = None        # 该动作所属状态
    action: Optional[str] = None       # 动作键（spec.states[s].actions 的键）
    action_kind: Optional[str] = None  # "key" | "click"
    keyname: Optional[str] = None
    box: Optional[tuple[int, int, int, int]] = None


@dataclass(frozen=True)
class AskItem:
    qid: str
    question: str
    options: tuple[UIOption, ...]
    qtype: str = "choice"


@dataclass(frozen=True)
class GateSpec:
    confidence_min: float = 0.45
    abstain_max: float = 0.35
    noul_deadzone: tuple[float, float] = (0.35, 0.65)
    margin_min: float = 0.10


@dataclass(frozen=True)
class LimitSpec:
    keys_per_sec: int = 4
    max_actions: int = 3000
    max_session_s: int = 1200
    hook_timeout_s: float = 2.0


@dataclass(frozen=True)
class EscalateSpec:
    priority: str = "batched"
    pause_key: Optional[str] = None
    resume_timeout_s: float = ESCALATION_RESOLVE_SECONDS


@dataclass(frozen=True)
class BlinkSpec:
    enabled: bool = True
    settle_ms: int = 60
    ack_timeout_ms: int = 300


@dataclass(frozen=True)
class AuditSpec:
    snapshot_every: int = 50
    keep_days: int = 3
    keep_images: int = 500


@dataclass(frozen=True)
class SessionSpec:
    decider: str = "omnijev"
    purpose: str = ""
    target: TargetSpec = field(default_factory=lambda: TargetSpec(exe=""))
    states: dict[str, dict[str, Any]] = field(default_factory=dict)
    unknown_policy: str = "pause"
    gates: GateSpec = field(default_factory=GateSpec)
    limits: LimitSpec = field(default_factory=LimitSpec)
    escalate: EscalateSpec = field(default_factory=EscalateSpec)
    blink: BlinkSpec = field(default_factory=BlinkSpec)
    audit: AuditSpec = field(default_factory=AuditSpec)
    processor: dict[str, Any] = field(default_factory=lambda: {"size": "4B", "quant": "4bit"})
    raw: dict[str, Any] = field(default_factory=dict)

    # ── 校验入口 ──
    @staticmethod
    def parse(raw: dict[str, Any]) -> "SessionSpec":
        if not isinstance(raw, dict):
            raise SpecError(f"spec 必须是 dict，收到 {type(raw).__name__}")
        allowed = {"decider", "purpose", "target", "states", "unknown_policy", "gates",
                   "limits", "escalate", "blink", "audit", "processor"}
        extra = sorted(set(raw) - allowed)
        if extra:
            raise SpecError(f"spec 里存在未知字段: {', '.join(extra)}（允许: {', '.join(sorted(allowed))}）")

        states_raw = raw.get("states")
        if not isinstance(states_raw, dict) or not states_raw:
            raise SpecError("spec.states 必须是非空 dict（至少一个状态）")
        states: dict[str, dict[str, Any]] = {}
        for name, body in states_raw.items():
            sname = str(name).strip()
            if not sname:
                raise SpecError("状态名不能为空")
            if not isinstance(body, dict):
                raise SpecError(f"states[{sname}] 必须是 dict")
            bad = sorted(set(body) - {"describe", "actions"})
            if bad:
                raise SpecError(f"states[{sname}] 存在未知字段: {', '.join(bad)}")
            describe = str(body.get("describe") or "").strip()
            if not describe:
                raise SpecError(f"states[{sname}] 缺少非空 describe")
            actions_raw = body.get("actions")
            if not isinstance(actions_raw, dict) or not actions_raw:
                raise SpecError(f"states[{sname}].actions 必须是非空 dict")
            actions: dict[str, dict[str, Any]] = {}
            labels: dict[str, str] = {}
            for akey, aval in actions_raw.items():
                key = str(akey).strip()
                if not key:
                    raise SpecError(f"states[{sname}] 的动作键不能为空")
                parsed = _parse_action(sname, key, aval)
                if parsed["meaning"] in labels and labels[parsed["meaning"]] != key:
                    raise SpecError(
                        f"states[{sname}] 的动作文本 {parsed['meaning']!r} 同时指向 "
                        f"{labels[parsed['meaning']]!r} 与 {key!r}；模型只能看到文本，必须唯一"
                    )
                labels[parsed["meaning"]] = key
                actions[key] = parsed
            states[sname] = {"describe": describe, "actions": actions}

        # 并集动作数 ≤ 12（去重按 呈现文本+动作键）
        union: set[tuple[str, str]] = set()
        for sname, body in states.items():
            for akey, act in body["actions"].items():
                union.add((act["meaning"], akey))
        if len(union) > MAX_UNION_ACTIONS:
            raise SpecError(
                f"所有状态的动作并集有 {len(union)} 个（上限 {MAX_UNION_ACTIONS}）；"
                "请减少状态/动作，或给模块写 classify 钩子让动作只在子空间里选"
            )
        # 跨状态同名文本必须指向同一动作键（否则并集问法下模型无法分辨）
        by_label: dict[str, str] = {}
        for sname, body in states.items():
            for akey, act in body["actions"].items():
                prev = by_label.get(act["meaning"])
                if prev is not None and prev != akey:
                    raise SpecError(
                        f"动作文本 {act['meaning']!r} 在多个状态里指向不同动作键（{prev!r} / {akey!r}）："
                        "并集问法下不可分辨，请改文本"
                    )
                by_label[act["meaning"]] = akey

        target_raw = raw.get("target") or {}
        if not isinstance(target_raw, dict):
            raise SpecError("spec.target 必须是 dict")
        tbad = sorted(set(target_raw) - {"exe", "title_contains", "require_fullscreen"})
        if tbad:
            raise SpecError(f"spec.target 存在未知字段: {', '.join(tbad)}")
        if not str(target_raw.get("exe") or "").strip():
            raise SpecError("spec.target.exe 必填（防标题伪装）")

        unknown_policy = str(raw.get("unknown_policy") or "pause").strip().lower()
        if unknown_policy not in ("pause", "escalate"):
            raise SpecError(f"unknown_policy 只能是 pause / escalate，收到 {unknown_policy!r}")

        gates_raw = raw.get("gates") or {}
        if not isinstance(gates_raw, dict):
            raise SpecError("spec.gates 必须是 dict")
        gbad = sorted(set(gates_raw) - {"confidence_min", "abstain_max", "noul_deadzone", "margin_min"})
        if gbad:
            raise SpecError(f"spec.gates 存在未知字段: {', '.join(gbad)}")
        d = GateSpec()
        confidence_min = _unit(gates_raw.get("confidence_min", d.confidence_min), "gates.confidence_min")
        abstain_max = _unit(gates_raw.get("abstain_max", d.abstain_max), "gates.abstain_max")
        margin_min = _unit(gates_raw.get("margin_min", d.margin_min), "gates.margin_min")
        dz = gates_raw.get("noul_deadzone", list(d.noul_deadzone))
        if (not isinstance(dz, (list, tuple)) or len(dz) != 2):
            raise SpecError("gates.noul_deadzone 必须是 [lo, hi] 两个数")
        lo, hi = _unit(dz[0], "gates.noul_deadzone[0]"), _unit(dz[1], "gates.noul_deadzone[1]")
        if not lo < hi:
            raise SpecError(f"gates.noul_deadzone 需要 lo < hi，收到 [{lo}, {hi}]")
        gates = GateSpec(confidence_min, abstain_max, (lo, hi), margin_min)

        limits_raw = raw.get("limits") or {}
        if not isinstance(limits_raw, dict):
            raise SpecError("spec.limits 必须是 dict")
        lbad = sorted(set(limits_raw) - {"keys_per_sec", "max_actions", "max_session_s", "hook_timeout_s"})
        if lbad:
            raise SpecError(f"spec.limits 存在未知字段: {', '.join(lbad)}")
        dl = LimitSpec()
        keys_per_sec = _int_pos(limits_raw.get("keys_per_sec", dl.keys_per_sec), "limits.keys_per_sec")
        max_actions = _int_pos(limits_raw.get("max_actions", dl.max_actions), "limits.max_actions")
        max_session_s = _int_pos(limits_raw.get("max_session_s", dl.max_session_s), "limits.max_session_s")
        hook_timeout_s = float(limits_raw.get("hook_timeout_s", dl.hook_timeout_s))
        if not (0 < hook_timeout_s <= 60):
            raise SpecError(f"limits.hook_timeout_s 必须在 (0, 60]，收到 {hook_timeout_s}")
        for key, value in (("keys_per_sec", keys_per_sec), ("max_actions", max_actions),
                           ("max_session_s", max_session_s)):
            if value > CEILINGS[key]:
                raise SpecError(f"limits.{key}={value} 超过硬天花板 {CEILINGS[key]}（设计上禁止配置得更大）")
        limits = LimitSpec(keys_per_sec, max_actions, max_session_s, hook_timeout_s)

        esc_raw = raw.get("escalate") or {}
        if not isinstance(esc_raw, dict):
            raise SpecError("spec.escalate 必须是 dict")
        ebad = sorted(set(esc_raw) - {"priority", "tpm_limit", "pause_key", "resume_timeout_s"})
        if ebad:
            raise SpecError(f"spec.escalate 存在未知字段: {', '.join(ebad)}")
        de = EscalateSpec()
        priority = str(esc_raw.get("priority", de.priority) or de.priority).strip().lower()
        if priority not in ("batched", "normal", "interrupt"):
            raise SpecError(f"escalate.priority 只能是 batched / normal / interrupt，收到 {priority!r}")
        pause_key = esc_raw.get("pause_key", de.pause_key)
        pause_key = str(pause_key).strip() if pause_key else None
        escalate = EscalateSpec(
            priority=priority,
            pause_key=pause_key,
            resume_timeout_s=float(esc_raw.get("resume_timeout_s", de.resume_timeout_s)),
        )

        blink_raw = raw.get("blink") or {}
        if not isinstance(blink_raw, dict):
            raise SpecError("spec.blink 必须是 dict")
        bbad = sorted(set(blink_raw) - {"enabled", "settle_ms", "ack_timeout_ms"})
        if bbad:
            raise SpecError(f"spec.blink 存在未知字段: {', '.join(bbad)}")
        db = BlinkSpec()
        blink = BlinkSpec(
            enabled=bool(blink_raw.get("enabled", db.enabled)),
            settle_ms=int(blink_raw.get("settle_ms", db.settle_ms)),
            ack_timeout_ms=int(blink_raw.get("ack_timeout_ms", db.ack_timeout_ms)),
        )
        if blink.ack_timeout_ms <= 0:
            raise SpecError("blink.ack_timeout_ms 必须为正")
        if not (0 <= blink.settle_ms <= 1000):
            raise SpecError(f"blink.settle_ms 必须在 [0, 1000]，收到 {blink.settle_ms}")

        audit_raw = raw.get("audit") or {}
        if not isinstance(audit_raw, dict):
            raise SpecError("spec.audit 必须是 dict")
        abad = sorted(set(audit_raw) - {"snapshot_every", "keep_days", "keep_images"})
        if abad:
            raise SpecError(f"spec.audit 存在未知字段: {', '.join(abad)}")
        da = AuditSpec()
        audit = AuditSpec(
            snapshot_every=max(1, int(audit_raw.get("snapshot_every", da.snapshot_every))),
            keep_days=max(0, int(audit_raw.get("keep_days", da.keep_days))),
            keep_images=max(0, int(audit_raw.get("keep_images", da.keep_images))),
        )

        processor_raw = raw.get("processor") or {}
        if not isinstance(processor_raw, dict):
            raise SpecError("spec.processor 必须是 dict")
        pbad = sorted(set(processor_raw) - {"size", "quant", "max_pixels", "state_cache"})
        if pbad:
            raise SpecError(f"spec.processor 存在未知字段: {', '.join(pbad)}")

        purpose = str(raw.get("purpose") or "").strip()
        if not purpose:
            raise SpecError("spec.purpose 必填（进 HIL 弹窗与审计，主 Agent 写的一句话）")

        return SessionSpec(
            decider=str(raw.get("decider") or "omnijev").strip().lower() or "omnijev",
            purpose=purpose,
            target=TargetSpec(
                exe=str(target_raw.get("exe")).strip(),
                title_contains=(str(target_raw["title_contains"]).strip()
                                if target_raw.get("title_contains") else None),
                require_fullscreen=bool(target_raw.get("require_fullscreen", False)),
            ),
            states=states,
            unknown_policy=unknown_policy,
            gates=gates,
            limits=limits,
            escalate=escalate,
            blink=blink,
            audit=audit,
            processor=dict(processor_raw),
            raw=dict(raw),
        )

    # ── 便捷查询 ──
    def action(self, state: str, key: str) -> dict[str, Any]:
        return self.states[state]["actions"][key]

    def union_options(self) -> list[UIOption]:
        """所有状态动作的并集（按 文本+动作键 去重，保持状态/动作声明顺序）。"""
        out: list[UIOption] = []
        seen: set[tuple[str, str]] = set()
        for sname, body in self.states.items():
            for akey, act in body["actions"].items():
                label = act["meaning"]
                if (label, akey) in seen:
                    continue
                seen.add((label, akey))
                out.append(UIOption(key=f"{sname}::{akey}", label=label, kind="action",
                                    state=sname, action=akey, action_kind=act["kind"],
                                    keyname=act.get("key"), box=act.get("box")))
        return out

    def state_options(self) -> list[UIOption]:
        return [UIOption(key=sname, label=body["describe"], kind="state")
                for sname, body in self.states.items()]

    def state_action_options(self, state: str) -> list[UIOption]:
        out = []
        for akey, act in self.states[state]["actions"].items():
            out.append(UIOption(key=akey, label=act["meaning"], kind="action",
                                state=state, action=akey, action_kind=act["kind"],
                                keyname=act.get("key"), box=act.get("box")))
        return out

    def all_actions_text(self) -> list[str]:
        """HIL 弹窗里展示的"允许的动作清单"。"""
        lines = []
        for sname, body in self.states.items():
            acts = "、".join(f"{a['meaning']}" for a in body["actions"].values())
            lines.append(f"{sname}: {acts}")
        return lines

    def digest(self) -> str:
        import json as _json
        return hashlib.sha1(
            _json.dumps(self.raw, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]


def _parse_action(state: str, key: str, value: Any) -> dict[str, Any]:
    """动作值：``"按键名" 的文本`` 简写，或 ``{"click": [x1,y1,x2,y2], "meaning": "..."}``。"""
    if isinstance(value, str):
        meaning = value.strip()
        if not meaning:
            raise SpecError(f"states[{state}].actions[{key}] 的文本为空")
        if key.lower() in ("click",):
            raise SpecError(f"states[{state}].actions[{key}] 用了保留名 'click' 作为按键名")
        return {"kind": "key", "key": key, "meaning": meaning}
    if isinstance(value, dict):
        bad = sorted(set(value) - {"click", "meaning"})
        if bad:
            raise SpecError(f"states[{state}].actions[{key}] 存在未知字段: {', '.join(bad)}")
        meaning = str(value.get("meaning") or "").strip()
        if not meaning:
            raise SpecError(f"states[{state}].actions[{key}] 缺少非空 meaning")
        box = value.get("click")
        if not (isinstance(box, (list, tuple)) and len(box) == 4):
            raise SpecError(f"states[{state}].actions[{key}].click 必须是 [x1,y1,x2,y2]")
        try:
            nums = [int(v) for v in box]
        except (TypeError, ValueError):
            raise SpecError(f"states[{state}].actions[{key}].click 必须是整数坐标") from None
        lo, hi = CLICK_BOX_RANGE
        for v in nums:
            if not (lo <= v <= hi):
                raise SpecError(f"states[{state}].actions[{key}].click 的坐标必须在 0–1000（归一化），收到 {v}")
        x1, y1, x2, y2 = nums
        if not (x1 < x2 and y1 < y2):
            raise SpecError(f"states[{state}].actions[{key}].click 需要 x1<x2 且 y1<y2，收到 {nums}")
        return {"kind": "click", "box": (x1, y1, x2, y2), "meaning": meaning}
    raise SpecError(f"states[{state}].actions[{key}] 必须是字符串（按键）或 {{click, meaning}} 对象")


def _unit(value: Any, name: str) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise SpecError(f"{name} 必须是数字，收到 {value!r}") from None
    if not (0.0 <= v <= 1.0):
        raise SpecError(f"{name} 必须在 [0, 1]，收到 {v}")
    return v


def _int_pos(value: Any, name: str) -> int:
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise SpecError(f"{name} 必须是整数，收到 {value!r}") from None
    if v <= 0:
        raise SpecError(f"{name} 必须为正，收到 {v}")
    return v


# ── 会话结果 ──────────────────────────────────────────────────
@dataclass
class OpenResult:
    ok: bool
    mode: str                      # "live" | "dry_run" | "none"
    reason: str
    window: Optional[WindowInfo] = None
    overlap: bool = False
    dry_run_until: Optional[float] = None


@dataclass
class UIDecision:
    """一步的产出：状态 + 动作 + 概率 + 帧绑定，**尚未注入**。"""

    step_id: int
    ok: bool
    state: Optional[str]
    state_source: Optional[str]          # hook | model
    state_conf: Optional[float]
    question_id: str
    action: Optional[str]
    action_kind: Optional[str]           # key | click
    action_text: Optional[str]
    point: Optional[tuple[int, int]]     # 屏幕物理像素（点击）
    box: Optional[tuple[int, int, int, int]]
    confidence: Optional[float]
    abstain: Optional[float]
    probabilities: dict[str, float]
    gate_pass: bool
    uncertain_reason: Optional[str]
    frame_digest: str
    frame_phash: str
    frame_path: Optional[str]
    hook_ms: float
    capture_ms: float
    ask_ms: float
    latency_total_s: Optional[float]
    dry_run: bool
    escalate_reason: Optional[str] = None
    note: str = ""

    def describe(self) -> str:
        if not self.ok:
            return f"step {self.step_id}: 不注入（{self.uncertain_reason}）"
        where = f"@{self.point}" if self.point else ""
        return (f"step {self.step_id}: {self.state or '-'}({self.state_source}) → "
                f"{self.action_text or self.action}{where} conf={self.confidence}")


@dataclass
class UIDeps:
    """可注入的运行时依赖（测试全换成假的）。"""

    win: Any
    input: Any
    frontend: Any                                  # FrontendLink 或 None
    audit_root: Path
    processor_ask: Optional[Callable[[dict, dict], Awaitable[dict]]] = None
    hil: Optional[Callable[[dict, float], Awaitable[str]]] = None
    escalate: Optional[Callable[[dict, str], Awaitable[None]]] = None
    signal: Any = None                             # ControlHub 或 None
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


class LimitedUIDecider(ABC):
    """受限制 UI 决策器。护栏在基类，决策来源在子类。"""

    def __init__(self, module: str, spec: SessionSpec, hooks: dict[str, Any] | None,
                 deps: UIDeps) -> None:
        self.module = str(module)
        self.spec = spec
        self.deps = deps
        self.hooks = dict(hooks or {})
        self.session_id = uuid.uuid4().hex[:12]
        self.state = SessionState.CLOSED
        self.window: Optional[WindowInfo] = None
        self.audit = AuditWriter(module, deps.audit_root, snapshot_every=spec.audit.snapshot_every,
                                 keep_days=spec.audit.keep_days, keep_images=spec.audit.keep_images,
                                 session_id=self.session_id)
        self.overlap = False
        self.closed_reason: Optional[str] = None

        self._step = 0
        self._opened_at = 0.0
        self._dry_run_until: Optional[float] = None
        self._injections = 0
        self._inject_times: list[float] = []
        self._pending: Optional[tuple[UIDecision, Frame]] = None
        self._frame_fail_streak = 0
        self._hook_error_streak = 0
        self._ask_fail_streak = 0
        self._focus_lost_since: Optional[float] = None
        self._focus_lost_count = 0
        self._pause_reason: Optional[str] = None
        self._escalation_watchdog: Optional[asyncio.Task] = None
        self._hint_state: Optional[str] = None
        self._snapshot_counter = 0
        self._closing = False
        self._session_metrics: dict[str, float] = {"ask_ms": 0.0, "capture_ms": 0.0,
                                                   "inject_ms": 0.0, "blink_ms": 0.0}

    # ── 只读状态（面板 / 测试）──
    @property
    def step_count(self) -> int:
        return self._step

    @property
    def injection_count(self) -> int:
        return self._injections

    @property
    def opened_at(self) -> float:
        return self._opened_at

    @property
    def pause_reason(self) -> Optional[str]:
        return self._pause_reason

    @property
    def elapsed_s(self) -> float:
        return (self.deps.clock() - self._opened_at) if self._opened_at else 0.0

    def patch_states(self, states_patch: dict[str, Any]) -> None:
        """热更新动作表（主 Agent 经 ``/agile/{module}/control`` 的 ``patch_keys``）。

        传入 ``{状态名: {"describe": ..., "actions": {...}}}``；状态名必须已存在，
        合并后的 spec 会**完整重新校验**（不合法直接抛错，不静默接受）。
        """
        if not states_patch:
            raise SpecError("patch_keys 需要非空的 states")
        raw = dict(self.spec.raw)
        states = {k: dict(v) for k, v in raw.get("states", {}).items()}
        for name, body in states_patch.items():
            if name not in states:
                raise SpecError(f"patch_keys 里的状态 {name!r} 不在 spec.states 中")
            if not isinstance(body, dict):
                raise SpecError(f"patch_keys[{name}] 必须是 dict")
            states[name] = {**states[name], **body}
        raw["states"] = states
        self.spec = SessionSpec.parse(raw)
        self.audit.note("patch_states", states=sorted(states_patch))

    # ── 唯一抽象方法：决策来源 ────────────────────────────────
    @abstractmethod
    async def _ask(self, question: str, options: list[UIOption], *, frame: Frame) -> Answer:
        """返回 {key, probabilities, confidence, abstain, latency_s}（OmniJev answers 的形状）。"""

    async def _ask_batch(self, items: list[AskItem], *, frame: Frame) -> dict[str, Answer]:
        """一次 invoke 打包所有问；默认退化为逐个 ``_ask``（OmniJevDecider 覆写为真打包）。"""
        out: dict[str, Answer] = {}
        for item in items:
            out[item.qid] = await self._ask(item.question, list(item.options), frame=frame)
        return out

    # ── 可覆写：抓帧 ─────────────────────────────────────────
    async def capture(self) -> Frame:
        """默认实现：客户区 GDI 截图（物理像素）+ 帧校验。"""
        if self.window is None:
            raise UIDrvError("会话未打开，无法抓帧")
        import numpy as np
        from PIL import ImageGrab
        rect = self.deps.win.client_rect(self.window.hwnd)
        img = ImageGrab.grab(bbox=rect, all_screens=True).convert("RGB")
        arr = np.asarray(img)
        return self._make_frame(arr, rect)

    def _make_frame(self, arr: Any, rect: tuple[int, int, int, int]) -> Frame:
        digest = hashlib.sha1(memoryview(arr).tobytes()).hexdigest()[:16]
        return Frame(image=arr, digest=digest, phash=perceptual_hash(arr), rect=rect,
                     captured_at=self.deps.clock())

    @staticmethod
    def validate_frame(frame: Frame) -> Optional[str]:
        """帧校验（设计 §9/§19）：尺寸合理、非全黑、有可辨识的对比度。

        "全黑"用**最大像素值**、"几乎无对比度"用**动态范围**（max-min）判定，而不是标准差：
        深色 UI/菜单是合法画面但方差可以很小（实测假窗口 std=0.28、range=56），用 std 会把
        正常画面误判成坏帧。真正抓不到画面的情形（独占全屏/DRM/HDR）是纯色或全黑。
        """
        import numpy as np
        arr = frame.image
        if arr is None:
            return "no_image"
        try:
            a = np.asarray(arr)
        except Exception:  # noqa: BLE001
            return "unreadable"
        if a.ndim != 3 or a.shape[0] < 64 or a.shape[1] < 64:
            return f"shape_{getattr(a, 'shape', '?')}"
        if a.size == 0:
            return "empty"
        top, low = int(a.max()), int(a.min())
        if top <= 8:
            return "all_black"
        if (top - low) < 8:
            return f"flat[range={top - low}]"
        return None

    async def capture_hidden(self) -> Frame:
        """眨眼（藏模型）期间抓一帧。

        用于核对"眨眼期间画面里没有立绘"（设计 §20.2）。窗口不重叠时不真眨，等价于普通抓帧。
        """
        return await self._with_blink(self.capture)

    # ── 会话生命周期 ─────────────────────────────────────────
    async def open(self) -> OpenResult:
        """解析窗口 → 问前端窗口边界 → 校验坐标 → HIL 批准 → 建会话（设计 §8.1）。"""
        ensure_dpi_aware()
        if self.state is not SessionState.CLOSED:
            return OpenResult(ok=False, mode="none", reason="会话已经打开")
        try:
            window = self.deps.win.resolve(self.spec.target)
        except (WindowNotFoundError, UIDrvError) as exc:
            return OpenResult(ok=False, mode="none", reason=str(exc))
        self.window = window

        if self.spec.blink.enabled and self.deps.frontend is not None:
            bounds = await self.deps.frontend.window_bounds(timeout=HANDSHAKE_TIMEOUT_S)
            if bounds is None:
                self.window = None
                return OpenResult(ok=False, mode="none",
                                  reason="前端未回报窗口边界（UI_WINDOW_BOUNDS 回执超时）："
                                         "无法判断是否需要眨眼，拒绝开会话")
            bad = self._check_screen_consistency(bounds)
            if bad:
                self.window = None
                return OpenResult(ok=False, mode="none", reason=bad)
            self.overlap = rects_overlap(bounds, window.client_rect)
        else:
            self.overlap = False

        mode = "live"
        if self.deps.hil is not None:
            choice = await self.deps.hil(self._hil_payload(window, self.overlap), HIL_TIMEOUT_SECONDS)
            if choice == "reject" or choice == "timeout":
                await self._notify_rejected(choice)
                self.window = None
                return OpenResult(ok=False, mode="none",
                                  reason=("用户拒绝了本次 UI 操作" if choice == "reject"
                                          else "HIL 批准弹窗超时（按拒绝处理）"))
            mode = "dry_run" if choice == "dry_run" else "live"
        else:
            mode = "live"

        # 批准弹窗关闭后，主动把目标窗口切回前台一次（那次失焦是我们造成的，设计 §7.2 唯一例外）
        self._restore_foreground_once()

        ack = None
        if self.deps.frontend is not None:
            ack = await self.deps.frontend.set_session(True, timeout=HANDSHAKE_TIMEOUT_S)
            if ack is None:
                self.window = None
                return OpenResult(ok=False, mode="none",
                                  reason="渲染器没有确认会话（UI_SESSION_STATE 回执超时）："
                                         "插件前端脚本可能未加载，眨眼与提示条都用不了，拒绝开会话")

        now = self.deps.clock()
        self._opened_at = now
        self._dry_run_until = now + DRY_RUN_SECONDS if mode == "dry_run" else None
        self.state = SessionState.DRY_RUN if mode == "dry_run" else SessionState.ACTIVE
        self.audit.ensure_dirs()
        self.audit.cleanup()
        self.audit.session_start(purpose=self.spec.purpose, target=window.describe(), mode=mode,
                                 limits={"keys_per_sec": self.spec.limits.keys_per_sec,
                                         "max_actions": self.spec.limits.max_actions,
                                         "max_session_s": self.spec.limits.max_session_s},
                                 actions=self.spec.all_actions_text(),
                                 spec_digest=self.spec.digest())
        if self.deps.signal is not None:
            self.deps.signal.attach(self)
        return OpenResult(ok=True, mode=mode,
                          reason=("试运行：只决策不注入" if mode == "dry_run"
                                  else "已允许操作"), window=window, overlap=self.overlap,
                          dry_run_until=self._dry_run_until)

    def _check_screen_consistency(self, bounds: tuple[int, int, int, int]) -> Optional[str]:
        """前端窗口边界必须落在物理屏内，否则前后端坐标系统不一致（DPI 未对齐）。"""
        try:
            sw, sh = self.deps.win.screen_size()
        except Exception:  # noqa: BLE001
            return None
        l, t, r, b = bounds
        if r <= l or b <= t:
            return f"前端回报的窗口边界非法: {bounds}"
        if l < -8 or t < -8 or r > sw + 8 or b > sh + 8:
            return (f"前端窗口边界 {bounds} 超出物理屏 {sw}x{sh}：前后端 DPI 不一致，"
                    "拒绝开会话（否则点击坐标会错位）")
        return None

    def _hil_payload(self, window: WindowInfo, overlap: bool) -> dict[str, Any]:
        acts = "\n".join(f"  · {line}" for line in self.spec.all_actions_text())
        summary = "\n".join([
            f"目标窗口: {window.title}",
            f"进程: {window.exe}  PID {window.pid}",
            f"路径: {window.path}",
            f"客户区: {window.client_rect[0]},{window.client_rect[1]} – "
            f"{window.client_rect[2]},{window.client_rect[3]}",
            "",
            f"模块目的: {self.spec.purpose}",
            "",
            "允许的动作清单（Jev 能选的全部范围）:",
            acts,
            "",
            f"上限: {self.spec.limits.keys_per_sec} 次/秒、{self.spec.limits.max_actions} 次、"
            f"{self.spec.limits.max_session_s // 60} 分钟；试运行 {int(DRY_RUN_SECONDS // 60)} 分钟",
            f"桌面模型遮挡: {'会每步闪一次以让开画面' if overlap else '不重叠，不会闪烁'}",
            "",
            "按「拒绝」或超时（120 秒）= 不注入任何输入。",
            "风险提示: 自动化操作可能违反游戏服务条款，建议只用于单机。",
        ])
        return {
            "title": "UI 操作请求",
            "summary": summary,
            "severity": "warning",
            "buttons": [
                {"value": "dry_run", "label": "试运行", "default": True},
                {"value": "allow", "label": "允许操作"},
                {"value": "reject", "label": "拒绝"},
            ],
        }

    async def _notify_rejected(self, choice: str) -> None:
        if self.deps.escalate is None:
            return
        try:
            await self.deps.escalate({
                "module": self.module, "reason": "hil_rejected", "choice": choice,
                "purpose": self.spec.purpose,
            }, f"游戏「{self.spec.target.title_contains or self.spec.target.exe}」的 UI 操作请求"
               f"被{'拒绝' if choice == 'reject' else '超时拒绝'}，本次不注入任何输入。")
        except Exception:  # noqa: BLE001 - 通知失败不改判"不注入"
            pass

    async def _notify(self, reason: str, recall: str, extra: dict[str, Any]) -> None:
        """非升级类的通知（试运行结束等）：不改会话状态，只唤醒主 Agent。"""
        if self.deps.escalate is None:
            return
        try:
            await self.deps.escalate({"module": self.module, "reason": reason, **extra}, recall)
        except Exception as exc:  # noqa: BLE001
            await self.audit_note("notify_failed", reason=reason, error=str(exc))

    # ── 一步决策 ─────────────────────────────────────────────
    async def step(self, intent: str) -> UIDecision:
        """``decide`` + ``inject`` 的组合（模块循环用这个；审计每步一行）。"""
        d = await self.decide(intent)
        if d.ok:
            await self.inject(d)
        return d

    async def decide(self, intent: str) -> UIDecision:
        self._require_open()
        self._flush_pending("superseded")
        self._step += 1
        step_id = self._step
        d = UIDecision(step_id=step_id, ok=False, state=None, state_source=None, state_conf=None,
                       question_id="action", action=None, action_kind=None, action_text=None,
                       point=None, box=None, confidence=None, abstain=None, probabilities={},
                       gate_pass=False, uncertain_reason=None, frame_digest="", frame_phash="",
                       frame_path=None, hook_ms=0.0, capture_ms=0.0, ask_ms=0.0,
                       latency_total_s=None, dry_run=self.state is SessionState.DRY_RUN)

        reason = await self._tick_housekeeping()
        if reason is not None:
            d.uncertain_reason = reason
            return d

        frame, cap_reason = await self._capture_for_step(d)
        if frame is None:
            d.uncertain_reason = cap_reason or "frame_invalid"
            await self._write_step(d, injected=False, inject_result=f"blocked[{d.uncertain_reason}]")
            return d

        ctx_text, hook_ms, hook_err = await self._run_context_hook(frame, step_id)
        d.hook_ms = hook_ms
        if hook_err is not None:
            d.uncertain_reason = "hook_error"
            d.note = hook_err
            await self._on_hook_error(step_id, hook_err)
            return d

        state, state_source, state_conf, classify_err = await self._run_classify_hook(frame, step_id)
        if classify_err is not None:
            d.uncertain_reason = "hook_error"
            d.note = classify_err
            await self._on_hook_error(step_id, classify_err)
            return d

        items = self._build_questions(state, ctx_text, intent)
        t0 = self.deps.clock()
        try:
            answers = await self._ask_batch(items, frame=frame)
        except Exception as exc:  # noqa: BLE001 - Processor 异常：本步不注入
            d.uncertain_reason = "ask_failed"
            d.note = str(exc)
            await self._on_ask_error(step_id, exc)
            return d
        d.ask_ms = (self.deps.clock() - t0) * 1000.0

        guess = self._resolve(items, answers, state)
        d.state = guess["state"]
        d.state_source = state_source
        d.state_conf = state_conf if state_source == "hook" else guess.get("state_conf")
        d.question_id = guess.get("question_id") or "action"
        d.action = guess.get("action")
        d.action_kind = guess.get("action_kind")
        d.action_text = guess.get("action_text")
        d.box = guess.get("box")
        d.confidence = guess.get("confidence")
        d.abstain = guess.get("abstain")
        d.probabilities = guess.get("probabilities") or {}
        d.latency_total_s = guess.get("latency_total_s")
        d.frame_digest = frame.digest
        d.frame_phash = frame.phash_hex()
        d.gate_pass = bool(guess.get("gate_pass"))
        d.uncertain_reason = guess.get("uncertain_reason")
        d.note = guess.get("note") or ""
        d.ok = d.uncertain_reason is None and d.action is not None

        if d.ok and self.state is SessionState.ACTIVE and d.action_kind == "click":
            d.point = self._box_center(d.box, frame)

        if not d.ok:
            await self._on_uncertain(d, frame)
            await self._write_step(d, injected=False, inject_result=f"blocked[{d.uncertain_reason}]")
        else:
            # 该步通过了护栏：审计行等到 inject() 落笔（或下一次 decide/pause/close 补写）
            self._pending = d
        return d

    async def _tick_housekeeping(self) -> Optional[str]:
        """每步开头：试运行超时、会话时长上限、暂停恢复、急停信号。返回非 None = 本步不决策。"""
        now = self.deps.clock()
        if self.state is SessionState.CLOSED:
            return "session_closed"

        if self._dry_run_until is not None and now >= self._dry_run_until:
            await self.close("dry_run_timeout")
            await self._notify("dry_run_timeout",
                               f"试运行 {int(DRY_RUN_SECONDS // 60)} 分钟已到，只做了决策、没有注入任何输入。"
                               f"审计与截图：faustbot://agile/{self.module}/ops",
                               {"dry_run_s": DRY_RUN_SECONDS, "steps": self._step})
            return "dry_run_timeout"
        if now - self._opened_at >= self.spec.limits.max_session_s:
            await self._limit_hit("limit_session")
            return "limit_session"

        if self.deps.signal is not None:
            if self.deps.signal.take_stop(self.module):
                await self.safe_stop("hotkey_stop")
                await self.pause("manual_stop")
                await self._hint("lost", "已手动暂停")
                return "manual_stop"

        if self._at_failsafe_corner():
            await self.safe_stop("failsafe_corner")
            await self.pause("failsafe_corner")
            await self._hint("lost", "鼠标在屏幕角落：已停手（移开鼠标后写 control 恢复）")
            return "failsafe_corner"

        if self.state is SessionState.PAUSED:
            if self._pause_reason == "focus_lost":
                return await self._focus_still_lost(now)
            return self._pause_reason or "paused"
        return None

    async def _note_focus_lost(self, hint: bool) -> str:
        """失焦计数 / 提示 / 超限升级（设计 §7.2：连续 3 次或 30 秒未恢复 ⇒ 升级）。"""
        now = self.deps.clock()
        self._focus_lost_count += 1
        self._focus_lost_since = self._focus_lost_since or now
        if hint:
            await self.pause("focus_lost")
            await self._hint("lost", f"目标窗口没有选中，请点击「{self.window.title}」窗口")
        if (self._focus_lost_count >= FOCUS_LOST_ESCALATE_COUNT
                or now - self._focus_lost_since >= FOCUS_LOST_ESCALATE_SECONDS):
            await self.escalate(
                "focus_timeout",
                f"连续 {self._focus_lost_count} 次失焦且已 {now - self._focus_lost_since:.0f} 秒未恢复")
            return "focus_timeout"
        return "focus_lost"

    async def _focus_still_lost(self, now: float) -> Optional[str]:
        fg = self._safe_foreground()
        if fg == self._target_hwnd():
            await self.resume()
            return None
        return await self._note_focus_lost(hint=False)

    async def _capture_for_step(self, d: UIDecision) -> tuple[Optional[Frame], Optional[str]]:
        hwnd = self._target_hwnd()
        if not self.deps.win.is_window(hwnd):
            fresh = self._resolve_target_window()
            if fresh is None:
                await self.close("window_gone")
                await self.escalate("window_gone",
                                    "目标窗口在会话期间消失（关闭/重开且找不到同名窗口），已结束会话")
                d.note = "目标窗口已关闭或句柄失效"
                return None, "window_gone"
            await self.audit_note("window_rebound", before=hwnd, after=fresh.hwnd, title=fresh.title)
            self.window = fresh
            self.overlap = True
            hwnd = fresh.hwnd
        fg = self._safe_foreground()
        if fg != hwnd:
            # 失焦：连帧也不抓（此时画面可能已被覆盖）——设计 §8.4
            return None, await self._note_focus_lost(hint=True)

        t0 = self.deps.clock()
        try:
            frame = await self._with_blink(self.capture)
        except UIDrvError as exc:
            d.note = str(exc)
            await self.pause("blink_timeout")
            return None, "blink_timeout"
        d.capture_ms = (self.deps.clock() - t0) * 1000.0
        self._session_metrics["capture_ms"] += d.capture_ms
        bad = self.validate_frame(frame)
        if bad is not None:
            self._frame_fail_streak += 1
            if self._frame_fail_streak == 1:
                await self.audit_note("frame_invalid", detail=bad, step_id=d.step_id)
            if self._frame_fail_streak >= FRAME_INVALID_ESCALATE_COUNT:
                self.audit.save_frame(frame, d.step_id, "invalid")
                await self.escalate(
                    "frame_invalid",
                    f"连续 {self._frame_fail_streak} 帧校验失败（{bad}）：可能是独占全屏/HDR/DRM 抓帧全黑，"
                    "请把游戏改成「无边框窗口化」重试")
                self._frame_fail_streak = 0
            return None, "frame_invalid"
        self._frame_fail_streak = 0
        return frame, None

    # ── 钩子 ─────────────────────────────────────────────────
    async def _run_context_hook(self, frame: Frame, step_id: int) -> tuple[str, float, Optional[str]]:
        hook = self.hooks.get("context")
        if hook is None:
            return "", 0.0, None
        t0 = self.deps.clock()
        try:
            raw = await asyncio.wait_for(hook(frame, step_id=step_id),
                                         timeout=self.spec.limits.hook_timeout_s)
        except asyncio.TimeoutError:
            return "", (self.deps.clock() - t0) * 1000.0, (
                f"context 钩子超过 {self.spec.limits.hook_timeout_s}s 未返回")
        except Exception as exc:  # noqa: BLE001
            return "", (self.deps.clock() - t0) * 1000.0, f"context 钩子异常: {exc!r}"
        ms = (self.deps.clock() - t0) * 1000.0
        text = "" if raw is None else str(raw)
        if len(text) > HOOK_CONTEXT_MAX_CHARS:
            await self.audit_note("hook_context_truncated",
                                  detail=f"{len(text)} → {HOOK_CONTEXT_MAX_CHARS}", step_id=step_id)
            text = text[:HOOK_CONTEXT_MAX_CHARS]
        return text.strip(), ms, None

    async def _run_classify_hook(self, frame: Frame, step_id: int) -> tuple[Optional[str], str, Optional[float], Optional[str]]:
        hook = self.hooks.get("classify")
        if hook is None:
            return None, "model", None, None
        try:
            raw = await asyncio.wait_for(hook(frame, step_id=step_id),
                                         timeout=self.spec.limits.hook_timeout_s)
        except asyncio.TimeoutError:
            return None, "model", None, f"classify 钩子超过 {self.spec.limits.hook_timeout_s}s 未返回"
        except Exception as exc:  # noqa: BLE001
            return None, "model", None, f"classify 钩子异常: {exc!r}"
        if raw is None:
            return None, "model", None, None
        name = str(raw).strip()
        if name not in self.spec.states:
            return None, "model", None, (
                f"classify 钩子返回了未声明的状态 {name!r}；"
                f"spec.states 只有: {', '.join(self.spec.states)}")
        return name, "hook", 1.0, None

    async def _on_hook_error(self, step_id: int, message: str) -> None:
        self._hook_error_streak += 1
        await self.audit_note("hook_error", detail=message, step_id=step_id,
                              streak=self._hook_error_streak)
        if self._hook_error_streak >= HOOK_ERROR_ESCALATE_COUNT:
            await self.escalate("hook_error",
                                f"模块钩子连续 {self._hook_error_streak} 次异常，最后一次: {message}")
            await self.close("hook_error")
        else:
            await self.safe_stop("hook_error")

    async def _on_ask_error(self, step_id: int, exc: BaseException) -> None:
        """Processor 不可用/超时：本步不注入 + 审计；连续 3 次 ⇒ 升级（设计 §19）。"""
        self.audit.append({"type": "step", "step_id": step_id,
                           "dry_run": self.state is SessionState.DRY_RUN,
                           "uncertain_reason": "ask_failed", "injected": False,
                           "inject_result": "ask_failed",
                           "error": f"{type(exc).__name__}: {exc}"})
        self.audit.record_step({"step_id": step_id, "injected": False,
                                "uncertain_reason": "ask_failed"})
        self._ask_fail_streak += 1
        if self._ask_fail_streak >= 3:
            self._ask_fail_streak = 0
            await self.escalate("processor_error",
                                f"Processor 连续 3 次调用失败，最后一次: {type(exc).__name__}: {exc}")

    # ── 提问构造与解析 ───────────────────────────────────────
    def _build_questions(self, state: Optional[str], ctx_text: str, intent: str) -> list[AskItem]:
        """组问（设计 §9 步骤 4）：classify 命中只问动作；否则状态问 + 并集动作问，一次 invoke。"""
        head = f"{intent.strip()}\n" if intent and intent.strip() else ""
        extra = f"\n附加信息（模块钩子提供）: {ctx_text}" if ctx_text else ""
        if state is not None:
            item = AskItem(qid="action",
                           question=f"{head}下一步执行哪个动作？" + extra,
                           options=tuple(self.spec.state_action_options(state)))
            return [item]
        union = self.spec.union_options()
        if len(union) > MAX_UNION_ACTIONS:
            raise SpecError(f"并集动作数 {len(union)} 超过 {MAX_UNION_ACTIONS}")
        return [
            AskItem(qid="state",
                    question=f"{head}当前游戏处于哪个状态？" + extra,
                    options=tuple(self.spec.state_options())),
            AskItem(qid="action",
                    question=f"{head}下一步执行哪个动作？（先按画面判断状态，再选该状态下合理的动作）"
                             + extra,
                    options=tuple(union)),
        ]

    def _resolve(self, items: list[AskItem], answers: dict[str, Answer],
                 hooked_state: Optional[str]) -> dict[str, Any]:
        """门限 + 一致性校验（设计 §9 步骤 5-6）。"""
        state: Optional[str] = hooked_state
        state_conf: Optional[float] = None
        out: dict[str, Any] = {"state": state, "state_conf": None, "gate_pass": False,
                               "uncertain_reason": None, "note": ""}

        if hooked_state is None:
            ans = answers.get("state")
            if ans is None:
                out["uncertain_reason"] = "no_answer"
                return out
            gate = self._gate_choice(ans, [o.key for o in items[0].options])
            state_conf = ans.confidence
            out["state_conf"] = state_conf
            if gate is not None:
                out["uncertain_reason"] = "unknown_state" if gate == "unknown_option" else gate
                out["confidence"] = ans.confidence
                out["abstain"] = ans.abstain
                out["probabilities"] = ans.probabilities
                return out
            state = ans.key
            out["state"] = state

        action_item = next((it for it in items if it.qid == "action"), None)
        if action_item is None:
            out["uncertain_reason"] = "no_action_question"
            return out
        ans = answers.get("action")
        if ans is None:
            out["uncertain_reason"] = "no_answer"
            return out
        out["confidence"] = ans.confidence
        out["abstain"] = ans.abstain
        out["probabilities"] = ans.probabilities
        out["latency_total_s"] = ans.latency_total_s
        gate = self._gate_choice(ans, [o.key for o in action_item.options])
        if gate is not None:
            out["uncertain_reason"] = gate
            return out
        chosen = next((o for o in action_item.options if o.key == ans.key), None)
        if chosen is None:
            out["uncertain_reason"] = "unknown_option"
            out["note"] = f"模型选了不在选项里的动作 {ans.key!r}"
            return out
        out["question_id"] = action_item.qid
        out["action"] = chosen.action
        out["action_kind"] = chosen.action_kind
        out["action_text"] = chosen.label
        out["box"] = chosen.box
        out["gate_pass"] = True
        if hooked_state is None:
            declared = self.spec.states.get(state or "", {}).get("actions", {})
            if chosen.action not in declared:
                out["gate_pass"] = False
                out["uncertain_reason"] = "inconsistent"
                out["note"] = (f"模型判状态 {state!r}，却选了不属于该状态的动作 "
                               f"{chosen.action!r}（模型自相矛盾）")
                return out
        return out

    def _gate_choice(self, ans: Answer, option_keys: list[str]) -> Optional[str]:
        """门限（设计 §9.1）。返回非 None = 不确定的原因。

        顺序：分布有效性 → 是否给出选择 → 选择是否在选项内 → abstain → confidence → margin。
        （abstain 先于 confidence：模型说"以上都不是"时最该报的就是 abstain。）
        """
        g = self.spec.gates
        if not ans.valid:
            return "invalid_distribution"
        if ans.key is None:
            return "no_choice"
        if ans.key not in option_keys:
            return "unknown_option"
        if ans.abstain > g.abstain_max:
            return "abstain"
        if ans.confidence < g.confidence_min:
            return "low_confidence"
        if ans.noul is not None:
            lo, hi = g.noul_deadzone
            if lo <= ans.noul <= hi:
                return "noul_deadzone"
        if g.margin_min > 0 and ans.probabilities:
            values = sorted(ans.probabilities.values(), reverse=True)
            if len(values) >= 2 and (values[0] - values[1]) < g.margin_min:
                return "low_margin"
        return None

    def _box_center(self, box: Optional[tuple[int, int, int, int]],
                    frame: Frame) -> Optional[tuple[int, int]]:
        """0–1000 归一化 box → 客户区物理像素 → 屏幕物理像素（设计 §7.3）。"""
        if box is None:
            return None
        rect = self.deps.win.client_rect(self._target_hwnd())
        l, t, r, b = rect
        w, h = r - l, b - t
        x1, y1, x2, y2 = box
        cx = l + int(round((x1 + x2) / 2 / 1000.0 * w))
        cy = t + int(round((y1 + y2) / 2 / 1000.0 * h))
        return (max(l, min(r - 1, cx)), max(t, min(b - 1, cy)))

    # ── 不确定 → 暂停 / 升级 ─────────────────────────────────
    async def _on_uncertain(self, d: UIDecision, frame: Frame) -> None:
        await self.safe_stop(f"uncertain:{d.uncertain_reason}")
        self._snapshot_counter += 1
        await self.audit_note("uncertain", step_id=d.step_id, reason=d.uncertain_reason,
                              note=d.note, confidence=d.confidence, abstain=d.abstain)
        if d.uncertain_reason in ("low_confidence", "abstain", "low_margin", "noul_deadzone",
                                  "invalid_distribution", "no_choice", "unknown_option",
                                  "inconsistent", "unknown_state", "no_answer"):
            self.audit.save_frame(frame, d.step_id, "uncertain")
            if self.spec.unknown_policy == "escalate":
                d.escalate_reason = d.uncertain_reason
                await self.escalate(d.uncertain_reason, d.note or f"第 {d.step_id} 步拿不准")
            else:
                await self.pause(f"uncertain:{d.uncertain_reason}")
        else:
            await self.pause(f"error:{d.uncertain_reason}")

    # ── 注入 ─────────────────────────────────────────────────
    async def inject(self, d: UIDecision) -> None:
        """注入前复核（digest/前台/WindowFromPoint/白名单/限速）→ 眨眼 → 注入 → 审计。"""
        self._require_open()
        if not d.ok:
            raise UIDrvError(f"该步未通过护栏，不能注入: {d.uncertain_reason}")
        if self.state is SessionState.DRY_RUN:
            d.note = (d.note + " / ").lstrip(" /") + "试运行：未注入"
            await self._write_step(d, injected=False, inject_result="dry_run")
            return
        if self.state is not SessionState.ACTIVE:
            d.note = (d.note + " / ").lstrip(" /") + f"会话非活动（{self.state.value}），未注入"
            await self._write_step(d, injected=False, inject_result=f"not_active[{self.state.value}]")
            return

        # 1) 上限：速率 / 总次数
        now = self.deps.clock()
        self._inject_times = [t for t in self._inject_times if now - t < 1.0]
        if len(self._inject_times) >= self.spec.limits.keys_per_sec:
            await self._refuse(d, "limit_rate")
            await self._limit_hit("limit_rate")
            return
        if self._injections >= self.spec.limits.max_actions:
            await self._refuse(d, "limit_actions")
            await self._limit_hit("limit_actions")
            return

        # 2) 前台 + 窗口存活（句柄失效时按 exe+标题重新解析一次：游戏可能被重开，设计 §7.1）
        hwnd = self._target_hwnd()
        if not self.deps.win.is_window(hwnd):
            fresh = self._resolve_target_window()
            if fresh is None or fresh.hwnd == hwnd:
                await self._refuse(d, "window_gone")
                await self.close("window_gone")
                await self.escalate("window_gone",
                                    "目标窗口在会话期间消失（关闭/重开且找不到同名窗口），已结束会话")
                return
            await self.audit_note("window_rebound", before=hwnd, after=fresh.hwnd,
                                  title=fresh.title)
            self.window = fresh
            hwnd = fresh.hwnd
            self.overlap = True          # 换了窗口，遮挡关系未知 → 保守地继续眨眼
        if self._safe_foreground() != hwnd:
            await self._refuse(d, "not_foreground")
            await self.safe_stop("focus_lost_at_inject")
            await self.pause("focus_lost")
            await self._hint("lost", f"目标窗口没有选中，请点击「{self.window.title}」窗口")
            return

        # 3) 陈旧检测：重新抓一帧对比感知哈希
        t0 = self.deps.clock()
        try:
            fresh = await self._with_blink(self.capture)
        except UIDrvError as exc:
            await self._refuse(d, "blink_timeout", detail=str(exc))
            await self.pause("blink_timeout")
            return
        self._session_metrics["capture_ms"] += (self.deps.clock() - t0) * 1000.0
        bad = self.validate_frame(fresh)
        if bad is not None:
            await self._refuse(d, "frame_invalid", detail=bad)
            return
        dist = _phash_dist(fresh, d)
        if dist > STALE_PHASH_MAX_DISTANCE:
            await self._refuse(d, "stale_frame", detail=str(dist))
            await self.safe_stop("stale_frame")
            await self.pause("stale_frame")
            return

        # 4) 点击逐点校验（设计 §7.4）
        if d.action_kind == "click":
            point = d.point or self._box_center(d.box, fresh)
            if point is None:
                await self._refuse(d, "no_point")
                return
            d.point = point
            root = self.deps.win.root_window_at(*point)
            if root != hwnd:
                await self._refuse(d, "occluded", detail=str(root))
                await self.pause("occluded")
                return

        # 5) 注入
        t0 = self.deps.clock()
        try:
            if d.action_kind == "key":
                await self.deps.input.key_tap(d.action, 30)
            else:
                self.deps.input.click(*d.point)
        except Exception as exc:  # noqa: BLE001 - 注入报错必须上抛并记 ERROR（设计 §19）
            await self.audit_note("inject_failed", step_id=d.step_id, error=f"{type(exc).__name__}: {exc}")
            await self._write_step(d, injected=False, inject_result=f"error[{type(exc).__name__}]")
            await self.safe_stop("inject_error")
            raise
        inject_ms = (self.deps.clock() - t0) * 1000.0
        self._session_metrics["inject_ms"] += inject_ms
        now = self.deps.clock()
        self._inject_times.append(now)
        self._injections += 1

        # 6) 注入后：下一帧是否变化（调参回路用；不眨眼）
        changed: Optional[bool] = None
        try:
            after = await self.capture()
            changed = _phash_dist(after, d) > STALE_PHASH_MAX_DISTANCE
        except Exception:  # noqa: BLE001
            changed = None
        await self._write_step(d, injected=True, inject_result="ok", frame_changed=changed,
                               inject_ms=inject_ms)

    async def _refuse(self, d: UIDecision, reason: str, *, detail: str | None = None) -> None:
        """注入前复核拒绝：把原因写回决策（模块据此知道"这一步没生效"）并落审计行。"""
        d.ok = False
        d.uncertain_reason = reason
        await self._write_step(d, injected=False,
                               inject_result=f"{reason}[{detail}]" if detail else reason)

    async def _write_step(self, d: UIDecision, *, injected: bool, inject_result: str,
                          frame_changed: Optional[bool] = None, inject_ms: float = 0.0) -> None:
        self._pending = None
        rec = self._step_record(d, injected=injected, inject_result=inject_result,
                                frame_changed=frame_changed, inject_ms=inject_ms)
        self.audit.append(rec)
        self.audit.record_step(rec)
        if self._step % 100 == 0:
            self.audit.cleanup()
        self.audit.summarize(force=True)      # summary.json 始终是最新的（主 Agent 在读它）

    def _step_record(self, d: UIDecision, *, injected: bool, inject_result: str,
                     frame_changed: Optional[bool] = None, inject_ms: float = 0.0) -> dict[str, Any]:
        """设计 §16 的每行字段。"""
        return {
            "type": "step",
            "session_id": self.session_id,
            "step_id": d.step_id,
            "dry_run": d.dry_run,
            "state": d.state,
            "state_source": d.state_source,
            "state_conf": d.state_conf,
            "question_id": d.question_id,
            "action": d.action,
            "action_kind": d.action_kind,
            "action_text": d.action_text,
            "point": list(d.point) if d.point else None,
            "confidence": d.confidence,
            "abstain": d.abstain,
            "probabilities": d.probabilities,
            "gate_pass": d.gate_pass,
            "uncertain_reason": d.uncertain_reason,
            "escalate_reason": d.escalate_reason,
            "note": d.note or None,
            "injected": injected,
            "inject_result": inject_result,
            "frame_digest": d.frame_digest,
            "frame_phash": d.frame_phash,
            "frame_changed": frame_changed,
            "hook_ms": round(d.hook_ms, 1),
            "capture_ms": round(d.capture_ms, 1),
            "ask_ms": round(d.ask_ms, 1),
            "inject_ms": round(inject_ms, 1),
            "latency_total_s": d.latency_total_s,
        }

    async def audit_note(self, kind: str, **fields: Any) -> None:
        self.audit.note(kind, **fields)

    # ── 松手 / 暂停 / 恢复 ───────────────────────────────────
    async def safe_stop(self, reason: str) -> list[str]:
        """把本次按下的键显式 keyup（任何结束路径都必须先走这里）。返回已松开的键。"""
        try:
            released = list(self.deps.input.release_all())
        except Exception as exc:  # noqa: BLE001
            released = []
            await self.audit_note("release_failed", reason=reason, error=str(exc))
        if released:
            await self.audit_note("release", reason=reason, released=released)
        return released

    async def pause(self, reason: str) -> None:
        if self.state in (SessionState.CLOSED, SessionState.DRY_RUN):
            return
        if self.state is not SessionState.PAUSED:
            self.audit.note("pause", reason=reason)
        self.state = SessionState.PAUSED
        self._pause_reason = reason
        self._flush_pending("paused")

    async def resume(self) -> None:
        if self.state is not SessionState.PAUSED:
            return
        self.state = SessionState.ACTIVE
        self._pause_reason = None
        self._focus_lost_count = 0
        self._focus_lost_since = None
        self.audit.note("resume")
        await self._hint("ok", "")

    async def close(self, reason: str) -> None:
        """松手 + 结束会话（原因入审计）。可重复调用。"""
        if self.state is SessionState.CLOSED:
            return
        prev = self.state
        self.state = SessionState.CLOSED
        self.closed_reason = reason
        released = await self.safe_stop(f"close:{reason}")
        self._flush_pending("closed")
        self.audit.session_end(reason=reason, released=released,
                               detail={"prev_state": prev.value,
                                       "steps": self._step, "injections": self._injections})
        if self.deps.frontend is not None:
            try:
                await self._hint("ok", "")
                await self.deps.frontend.set_session(False)
            except Exception:  # noqa: BLE001
                pass
        if self.deps.signal is not None:
            self.deps.signal.detach(self)
        if self._escalation_watchdog is not None:
            self._escalation_watchdog.cancel()
            self._escalation_watchdog = None

    async def _limit_hit(self, reason: str) -> None:
        await self.safe_stop(reason)
        await self.close(reason)
        await self.escalate(reason, f"会话到达上限（{reason}），已松手并结束会话")

    # ── 升级 ─────────────────────────────────────────────────
    async def escalate(self, reason: str, detail: str) -> None:
        """顺序不可换：先松手 → 可选 pause_key → event_fire → 暂停保留会话 → 3 分钟兜底（设计 §15）。"""
        await self.safe_stop(f"escalate:{reason}")
        if self.spec.escalate.pause_key:
            try:
                await self.deps.input.key_tap(self.spec.escalate.pause_key, 30)
            except Exception as exc:  # noqa: BLE001
                await self.audit_note("pause_key_failed", error=str(exc))
        stats = self.audit.summarize(force=True)
        self.audit.escalation({"reason": reason, "detail": detail, "step_id": self._step,
                               "stats": stats})
        recall = (f"游戏「{self.window.title if self.window else self.spec.target.exe}」"
                  f"第 {self._step} 步拿不准（{reason}）：{detail}；已停手。"
                  f"最近 20 步与截图：faustbot://agile/{self.module}/ops/recent"
                  f"（汇总 ops/summary、截图 ops/frame.jpg）")
        if self.deps.escalate is not None:
            try:
                await self.deps.escalate({
                    "module": self.module, "reason": reason, "detail": detail,
                    "step_id": self._step, "ops": f"faustbot://agile/{self.module}/ops",
                    "recent": f"faustbot://agile/{self.module}/ops/recent",
                    "summary": f"faustbot://agile/{self.module}/ops/summary",
                    "last_escalation": f"faustbot://agile/{self.module}/ops/last_escalation.json",
                    "frame": f"faustbot://agile/{self.module}/ops/frame.jpg",
                    "elapsed_s": round(self.deps.clock() - self._opened_at, 1),
                    "injections": self._injections,
                }, recall)
            except Exception as exc:  # noqa: BLE001
                await self.audit_note("escalate_fire_failed", error=str(exc))
        if self.state is SessionState.ACTIVE:
            self.state = SessionState.PAUSED
            self._pause_reason = f"escalate:{reason}"
        self._arm_escalation_watchdog(reason)

    def _arm_escalation_watchdog(self, reason: str) -> None:
        if self._escalation_watchdog is not None:
            self._escalation_watchdog.cancel()

        async def _watch() -> None:
            try:
                await self.deps.sleep(self.spec.escalate.resume_timeout_s)
            except asyncio.CancelledError:
                return
            if self.state is SessionState.CLOSED:
                return
            if self._pause_reason and self._pause_reason.startswith("escalate:"):
                await self.close("escalation_timeout")
                await self.audit_note("escalation_timeout", reason=reason)
                if self.deps.escalate is not None:
                    try:
                        await self.deps.escalate(
                            {"module": self.module, "reason": "escalation_timeout",
                             "original": reason},
                            f"游戏「{self.window.title if self.window else self.spec.target.exe}」的"
                            f"升级 {self.spec.escalate.resume_timeout_s:.0f} 秒无人处理，已自动结束会话。")
                    except Exception:  # noqa: BLE001
                        pass

        try:
            self._escalation_watchdog = asyncio.ensure_future(_watch())
        except RuntimeError:  # 没有运行中的 loop（同步上下文）
            self._escalation_watchdog = None

    # ── 眨眼 / 提示 ──────────────────────────────────────────
    async def _with_blink(self, fn: Callable[[], Awaitable[Any]]) -> Any:
        """需要眨眼时：藏模型 → 回执 → 执行 fn → 显示模型（设计 §12）。"""
        frontend = self.deps.frontend
        if frontend is None or not self.spec.blink.enabled or not self.overlap:
            return await fn()
        t0 = self.deps.clock()
        ok = await frontend.blink(True, timeout=self.spec.blink.ack_timeout_ms / 1000.0)
        if not ok:
            raise UIDrvError(
                f"眨眼回执超时（{self.spec.blink.ack_timeout_ms}ms）：前端没有确认已隐藏模型，"
                "拒绝继续（不假设已经藏好了）")
        if self.spec.blink.settle_ms:
            await self.deps.sleep(self.spec.blink.settle_ms / 1000.0)
        try:
            return await fn()
        finally:
            self._session_metrics["blink_ms"] += (self.deps.clock() - t0) * 1000.0
            await frontend.blink(False, timeout=self.spec.blink.ack_timeout_ms / 1000.0)

    async def _hint(self, state: str, text: str) -> None:
        if self.deps.frontend is None or state == self._hint_state:
            return
        self._hint_state = state
        try:
            await self.deps.frontend.hint(state, self.window.title if self.window else "", text)
        except Exception:  # noqa: BLE001 - 提示是尽力而为
            pass

    # ── 工具 ─────────────────────────────────────────────────
    def _restore_foreground_once(self) -> None:
        """批准弹窗关闭后把目标窗口切回前台一次（唯一一次主动抢焦点）。"""
        if self.window is None:
            return
        if self._safe_foreground() == self._target_hwnd():
            return
        try:
            self.deps.win.set_foreground(self._target_hwnd())
            self.audit.note("foreground_restored")
        except Exception as exc:  # noqa: BLE001
            self.audit.note("foreground_restore_failed", error=str(exc))

    def _at_failsafe_corner(self) -> bool:
        """鼠标甩到屏幕角落 = 急停（沿用 pyautogui FAILSAFE 的既有约定，容差 5px）。"""
        try:
            x, y = self.deps.input.cursor_pos()
            vx, vy, vw, vh = self.deps.win.virtual_screen()
        except Exception:  # noqa: BLE001 - 取不到光标就不做这条判定
            return False
        tol = 5
        corners = ((vx, vy), (vx + vw - 1, vy), (vx, vy + vh - 1), (vx + vw - 1, vy + vh - 1))
        return any(abs(x - cx) <= tol and abs(y - cy) <= tol for cx, cy in corners)

    def _resolve_target_window(self) -> WindowInfo | None:
        try:
            return self.deps.win.resolve(self.spec.target)
        except UIDrvError:
            return None

    def _require_open(self) -> None:
        if self.state is SessionState.CLOSED:
            raise UIDrvError(f"会话未打开或已结束（{self.closed_reason or '未 open()'}）")

    def _target_hwnd(self) -> int:
        return int(self.window.hwnd) if self.window is not None else 0

    def _safe_foreground(self) -> int:
        try:
            return int(self.deps.win.foreground_hwnd())
        except Exception:  # noqa: BLE001
            return -1

    def _flush_pending(self, reason: str) -> None:
        """补写上一步"已通过护栏但模块没调 inject"的记录（保证每步一行）。"""
        if self._pending is None:
            return
        d = self._pending
        self._pending = None
        rec = self._step_record(d, injected=False, inject_result=f"skipped[{reason}]")
        self.audit.append(rec)
        self.audit.record_step(rec)

    def vfs_nodes(self) -> dict[str, tuple[Any, str]]:
        """挂到 faustbot:// 的只读节点（设计 §16）。由 ``limited_ui`` 注册。

        注：本仓库的 VFS 一个节点要么是文件要么是目录，"最近 200 步文本"因此挂在
        ``ops/recent``，``ops`` 本身是目录（``ops/summary``、``ops/last_escalation.json``、
        ``ops/frame.jpg`` 与设计一致）。
        """
        base = f"/agile/{self.module}/ops"
        return {
            f"{base}/recent": (self.audit.ops_text, "UI 操作最近 200 步（只读文本）"),
            f"{base}/summary": (self.audit.summary_text,
                                "UI 操作会话汇总：每问次数/平均把握度/不确定与状态来源"),
            f"{base}/last_escalation.json": (self.audit.last_escalation_text,
                                             "最后一次升级的完整细节"),
            f"{base}/frame.jpg": (self._latest_frame_text,
                                  "最近一帧截图的文件路径（用 read(路径) 直接看图）"),
        }

    def _latest_frame_text(self) -> str:
        path = self.audit.latest_frame_path()
        return str(path) if path is not None else "(尚无截图)"


def _phash_dist(frame: Frame, d: UIDecision) -> int:
    try:
        want = int(d.frame_phash, 16)
    except (TypeError, ValueError):
        return 0
    return bin(frame.phash ^ want).count("1")


def perceptual_hash(image: Any) -> int:
    """64 位 dHash（9x8 灰度差分），用于"同一画面"判定。"""
    import numpy as np
    a = np.asarray(image)
    if a.ndim == 3:
        gray = a[:, :, :3].mean(axis=2)
    else:
        gray = a.astype("float32")
    try:
        from PIL import Image
        small = np.asarray(Image.fromarray(gray.astype("uint8")).convert("L").resize((9, 8)))
    except Exception:  # noqa: BLE001
        small = gray[:8, :9]
    bits = 0
    for y in range(8):
        for x in range(8):
            bits = (bits << 1) | int(small[y, x] > small[y, x + 1])
    return bits


def rects_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])
