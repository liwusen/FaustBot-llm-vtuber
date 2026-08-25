from __future__ import annotations

import asyncio
import json
import math
import threading
import time
from pathlib import Path
from typing import Any

import faust_backend.config_loader as conf
from faust_backend.plugin_system import FaustPlugin, PluginContext, ToolSpec, hookimpl

from faust_backend.logger import get_logger
log = get_logger("faust.plugins.emotion-engine")

from langchain.tools import tool

EMOTION_KEYS = ["joy", "irritation", "pride", "curiosity", "sharpness", "boredom"]
EMOTION_LABELS = {
    "joy": "愉悦",
    "irritation": "烦躁",
    "pride": "傲慢",
    "curiosity": "好奇",
    "sharpness": "毒舌",
    "boredom": "无聊",
}
DEFAULT_EMOTIONS = {
    "joy": 3.0,
    "irritation": 1.0,
    "pride": 4.0,
    "curiosity": 4.0,
    "sharpness": 1.0,
    "boredom": 2.0,
}
EMOTION_TAG_TO_KEY = {
    "JOY": "joy",
    "IRRITATION": "irritation",
    "PRIDE": "pride",
    "CURIOSITY": "curiosity",
    "SHARPNESS": "sharpness",
    "BOREDOM": "boredom",
    "CARE": "joy",
    "CALM": "curiosity",
}
# ── 情绪档位（吸收 mood-chat 的主动调度 / 态度模板能力） ──
CHATTY_DOMINANT_KEYS = ("joy", "curiosity")
QUIET_DOMINANT_KEYS = ("irritation", "sharpness", "boredom")
CHATTY_DOMINANT_THRESHOLD = 5.0
QUIET_TOTAL_THRESHOLD = 12.0
SILENT_BOREDOM_THRESHOLD = 7.0
TIER_LABELS = {"chatty": "活泼", "normal": "正常", "quiet": "低落", "silent": "沉默"}
ATTITUDE_TEMPLATES = {
    "chatty": "你现在心情很好，说话活泼带笑意，愿意主动找话聊，偶尔会小小炫耀一下好心情。",
    "normal": "你现在心情平稳。该吐槽吐槽，该帮忙帮忙，不需要刻意偏向任何情绪。",
    "quiet": "你现在心情低落。回复简短一些，话变少，语气少了平时的活泼，但依然会认真回应。",
    "silent": "你现在情绪沉默。话极少，除非必要否则不主动开口，语气冷淡疏离。",
}
STATE_FILE_NAME = "emotion_state.json"
HISTORY_LIMIT = 512
COREMEMORY_START = "<!-- emotion-engine:start -->"
COREMEMORY_END = "<!-- emotion-engine:end -->"
_PLUGIN: "Plugin | None" = None


def _now() -> float:
    return time.time()


def _clamp(value: float, min_value: float = 0.0, max_value: float = 10.0) -> float:
    return max(min_value, min(max_value, round(float(value), 3)))


# ── 无聊自然增长（非线性饱和曲线） ──
# 设计目标：不对话时长 + 当前无聊值 两个自变量 → 目标无聊值。
# 曲线为有限时间到达的幂次饱和型：b(t) = 10 − (√10 − (√10/T)·t)²，t ∈ [0, T]
#   - 单调递增、导数递减（开头稍快、越接近上限越平缓），"自然"且不会突兀跳变
#   - T = 5400s（1.5 小时）时恰好到达 10.0（之后封顶）
#   - 当前值只升不降：b_target = max(current, curve(t))——自然增长方向由曲线决定，
#     对话带来的降低仍走既有路径（用户消息/回复时 boredom −1 等）
BOREDOM_MAX = 10.0
BOREDOM_SATURATION_SECONDS = 5400.0  # 至少 1.5 小时不对话才升到满值
_BOREDOM_ROOT = math.sqrt(BOREDOM_MAX)
_BOREDOM_K = _BOREDOM_ROOT / BOREDOM_SATURATION_SECONDS


def _boredom_curve(seconds: float) -> float:
    """不对话时长（秒）→ 自然无聊值（0~10，1.5h 到达 10）。"""
    t = max(0.0, float(seconds))
    if t >= BOREDOM_SATURATION_SECONDS:
        return BOREDOM_MAX
    remainder = _BOREDOM_ROOT - _BOREDOM_K * t
    return max(0.0, min(BOREDOM_MAX, BOREDOM_MAX - remainder * remainder))


def _boredom_target(seconds: float, current: float) -> float:
    """双自变量无聊目标：不对话时长 + 当前无聊值 → 目标值（只升不降）。"""
    return max(float(current), _boredom_curve(seconds))


def _agent_root() -> Path:
    return Path(conf.CONFIG_ROOT) / "agents" / Path(conf.AGENT_NAME)


def _corememory_path() -> Path:
    return _agent_root() / "COREMEMORY.md"


def _run_async_background(coro) -> None:
    def runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(coro)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()


def _read_corememory_state() -> dict[str, Any] | None:
    path = _corememory_path()
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    start = text.find(COREMEMORY_START)
    end = text.find(COREMEMORY_END)
    if start < 0 or end <= start:
        return None
    raw = text[start + len(COREMEMORY_START) : end].strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _write_corememory_state(payload: dict[str, Any]) -> None:
    path = _corememory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        text = ""
    block = f"{COREMEMORY_START}\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n{COREMEMORY_END}"
    start = text.find(COREMEMORY_START)
    end = text.find(COREMEMORY_END)
    if start >= 0 and end > start:
        prefix = text[:start].rstrip()
        suffix = text[end + len(COREMEMORY_END) :].lstrip("\n")
        merged = prefix + "\n\n" + block + "\n"
        if suffix:
            merged += "\n" + suffix
    else:
        header = text.rstrip()
        if header:
            header += "\n\n"
        merged = header + "## Emotion Engine State\n\n" + block + "\n"
    path.write_text(merged, encoding="utf-8")


def _build_proactive_content(tier: str) -> str:
    return "（情绪系统主动寒暄）感觉有点安静，想找你聊聊天。"


def _schedule_proactive_chat(content: str) -> bool:
    """契约第3条模式：后台触发 Agent 主动说话（chat.py 后台触发器同款）。"""
    from faust_backend.runtime import state
    from faust_backend.runtime.lifecycle import invoke_agent_locked

    if state.agent is None:
        log.warning("emotion-engine: agent 未就绪，跳过主动寒暄")
        return False
    try:
        asyncio.create_task(
            invoke_agent_locked(
                state.agent,
                {"messages": [{"role": "user", "content": content}]},
            )
        )
        return True
    except RuntimeError:
        log.error("emotion-engine: 无运行中的事件循环，跳过主动寒暄")
        return False


class EmotionState:
    def __init__(self):
        now = _now()
        self.vector: dict[str, float] = dict(DEFAULT_EMOTIONS)
        self.history: list[dict[str, Any]] = []
        self.last_decay_ts = now
        self.last_interaction_ts = now
        self.recent_user_modes: list[str] = []
        self.last_user_message = ""
        self.last_reply = ""
        self.pending_corememory_sync = True
        self.last_corememory_sync_ts = 0.0
        self.last_diary_ts = 0.0
        self.last_reply_ts = 0.0
        self.last_user_message_ts = 0.0
        self.last_proactive_ts = now
        self.dominant_emotion = "curiosity"


class EmotionEngineStore:
    def __init__(self, data_dir: Path, decay_per_minute: float = 0.1):
        self._lock = threading.RLock()
        self._data_dir = data_dir
        self._decay_per_minute = float(decay_per_minute or 0.1)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._data_dir / STATE_FILE_NAME
        self._state = self._load_state()
        self._save_state()

    def _load_state(self) -> EmotionState:
        raw = None
        if self._state_path.exists():
            try:
                raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            except Exception:
                raw = None
        if raw is None:
            raw = _read_corememory_state() or {}
        state = EmotionState()
        vector = raw.get("vector") or {}
        for key in EMOTION_KEYS:
            if key in vector:
                state.vector[key] = _clamp(vector[key])
        state.history = [
            item
            for item in list(raw.get("history") or [])[-HISTORY_LIMIT:]
            if isinstance(item, dict)
        ]
        state.last_decay_ts = float(raw.get("last_decay_ts") or _now())
        state.last_interaction_ts = float(raw.get("last_interaction_ts") or _now())
        state.recent_user_modes = list(raw.get("recent_user_modes") or [])[-8:]
        state.last_user_message = str(raw.get("last_user_message") or "")
        state.last_reply = str(raw.get("last_reply") or "")
        state.pending_corememory_sync = bool(raw.get("pending_corememory_sync", True))
        state.last_corememory_sync_ts = float(raw.get("last_corememory_sync_ts") or 0.0)
        state.last_diary_ts = float(raw.get("last_diary_ts") or 0.0)
        state.last_reply_ts = float(raw.get("last_reply_ts") or 0.0)
        state.last_user_message_ts = float(raw.get("last_user_message_ts") or 0.0)
        state.last_proactive_ts = float(raw.get("last_proactive_ts") or _now())
        state.dominant_emotion = str(
            raw.get("dominant_emotion") or self._dominant_from_vector(state.vector)
        )
        # ── 离线回归补算（其它情绪按速率衰减；boredom 按非线性曲线补算） ──
        now = _now()
        offline_secs = max(0.0, now - state.last_decay_ts)
        if offline_secs > 60.0:
            offline_minutes = offline_secs / 60.0
            for key in EMOTION_KEYS:
                if key == "boredom":
                    continue
                state.vector[key] = _clamp(
                    state.vector.get(key, 0.0) - self._decay_per_minute * offline_minutes
                )
            state.vector["boredom"] = _clamp(
                _boredom_target(
                    self._silence_seconds(state), float(state.vector.get("boredom", 0.0))
                )
            )
            state.last_decay_ts = now
        return state

    def _save_state(self) -> None:
        payload = {
            "vector": self._state.vector,
            "history": self._state.history[-HISTORY_LIMIT:],
            "last_decay_ts": self._state.last_decay_ts,
            "last_interaction_ts": self._state.last_interaction_ts,
            "recent_user_modes": self._state.recent_user_modes[-8:],
            "last_user_message": self._state.last_user_message,
            "last_reply": self._state.last_reply,
            "pending_corememory_sync": self._state.pending_corememory_sync,
            "last_corememory_sync_ts": self._state.last_corememory_sync_ts,
            "last_diary_ts": self._state.last_diary_ts,
            "last_reply_ts": self._state.last_reply_ts,
            "last_user_message_ts": self._state.last_user_message_ts,
            "last_proactive_ts": self._state.last_proactive_ts,
            "dominant_emotion": self._state.dominant_emotion,
        }
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _silence_seconds(self, state: "EmotionState | None" = None) -> float:
        """距上次对话（用户消息或回复，取较新者）的秒数；无历史返回 0（不增长）。"""
        st = state or self._state
        last = max(st.last_user_message_ts, st.last_reply_ts)
        if last <= 0:
            return 0.0
        return max(0.0, _now() - last)

    def _history_24h(self) -> list[dict[str, Any]]:
        cutoff = _now() - 86400
        return [
            item for item in self._state.history if float(item.get("ts") or 0) >= cutoff
        ]

    def _record_history(
        self, reason: str, deltas: dict[str, float] | None = None
    ) -> None:
        self._state.history.append(
            {
                "ts": int(_now()),
                "reason": reason,
                "deltas": dict(deltas or {}),
                "vector": dict(self._state.vector),
            }
        )
        self._state.history = self._state.history[-HISTORY_LIMIT:]

    def _dominant_from_vector(self, vector: dict[str, float]) -> str:
        return max(EMOTION_KEYS, key=lambda key: float(vector.get(key, 0.0)))

    def _top_emotions(self, count: int|None = 3) -> list[tuple[str, float]]:
        if not count:
            return sorted(
            ((key, float(self._state.vector.get(key, 0.0))) for key in EMOTION_KEYS),
            key=lambda item: item[1],
            reverse=True,
        )
        return sorted(
            ((key, float(self._state.vector.get(key, 0.0))) for key in EMOTION_KEYS),
            key=lambda item: item[1],
            reverse=True,
        )[:count]

    def tier(self) -> str:
        """情绪 → 档位：chatty（活泼）/ normal（正常）/ quiet（低落）/ silent（沉默）。"""
        with self._lock:
            vector = self._state.vector
            dominant = self._state.dominant_emotion
            dominant_value = float(vector.get(dominant, 0.0))
            total = sum(float(vector.get(key, 0.0)) for key in EMOTION_KEYS)
        if dominant == "boredom" and dominant_value > SILENT_BOREDOM_THRESHOLD:
            return "silent"
        if dominant in CHATTY_DOMINANT_KEYS and dominant_value >= CHATTY_DOMINANT_THRESHOLD:
            return "chatty"
        if dominant in QUIET_DOMINANT_KEYS or total <= QUIET_TOTAL_THRESHOLD:
            return "quiet"
        return "normal"

    def _mood_trend(self) -> str:
        """近 3 条非 heartbeat history delta 和的正负 → 上升中 / 下降中 / 平稳。"""
        with self._lock:
            recent = self._non_heartbeat_history()[-3:]
        total = sum(
            float(delta)
            for item in recent
            for delta in (item.get("deltas") or {}).values()
        )
        if total > 0:
            return "上升中"
        if total < 0:
            return "下降中"
        return "平稳"

    def _recent_change_chain(self) -> str:
        """最近 3 条非 heartbeat history reason 变化链。"""
        with self._lock:
            recent = self._non_heartbeat_history()[-3:]
        if not recent:
            return "（暂无）"
        return " → ".join(str(item.get("reason") or "") for item in recent)

    def _non_heartbeat_history(self) -> list[dict[str, Any]]:
        """过滤 heartbeat 记录：10s 心跳几乎恒写尾部，会淹没趋势/变化链。"""
        return [
            item
            for item in self._state.history
            if str(item.get("reason") or "") != "heartbeat"
        ]

    def should_proactive(self, interval: float) -> bool:
        with self._lock:
            return _now() - self._state.last_proactive_ts >= interval

    def mark_proactive_fired(self) -> None:
        with self._lock:
            self._state.last_proactive_ts = _now()
            self._save_state()

    def mark_reply_done(self) -> None:
        with self._lock:
            self._state.last_reply_ts = _now()
            self._save_state()

    def snapshot(self, config: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            cfg = config or {}
            return {
                "vector": dict(self._state.vector),
                "history": self._history_24h(),
                "last_user_message": self._state.last_user_message,
                "last_reply": self._state.last_reply,
                "pending_corememory_sync": self._state.pending_corememory_sync,
                "dominant_emotion": self._state.dominant_emotion,
                "tier": self.tier(),
                "top_emotions": [
                    {"key": key, "label": EMOTION_LABELS[key], "value": value}
                    for key, value in self._top_emotions()
                ],
                "updated_at": int(self._state.last_interaction_ts),
                "config": {
                    "sharp_tongue_enabled": bool(
                        cfg.get("SHARP_TONGUE_REWRITE", False)
                    ),
                    "decay_per_minute": float(cfg.get("DECAY_PER_MINUTE", 0.1) or 0.1),
                    "overlay_intensity": int(cfg.get("OVERLAY_INTENSITY", 50) or 50),
                },
            }

    def note_user_message(self, text: str) -> str:
        with self._lock:
            self._state.last_user_message = text
            self._state.last_interaction_ts = _now()
            self._state.last_user_message_ts = _now()
            # 对话缓解无聊：按比例回退 max(1.0, 30% * 当前无聊值)，
            # 聊过天本身就能打断无聊积累；标签（EmotionInvokeSigned）再做精细调节
            current_boredom = float(self._state.vector.get("boredom", 0.0))
            relief = max(1.0, 0.3 * current_boredom)
            if relief > 0:
                self._state.vector["boredom"] = _clamp(current_boredom - relief)
                self._record_history("user_message_boredom_relief", {"boredom": -relief})
            mode = "task" if len((text or "").strip()) <= 48 else "chat"
            self._state.recent_user_modes.append(mode)
            self._state.recent_user_modes = self._state.recent_user_modes[-8:]
            self._save_state()
        return "\n|[System]:你的当前情绪向量: "+ "; ".join(f"{EMOTION_LABELS[key]}={value:.1f}" for key, value in self._top_emotions(None)) + "\n"

    def apply_emotion_tag(self, tag: str, ) -> dict[str, Any]:
        emotion_key = EMOTION_TAG_TO_KEY.get(str(tag or "").strip().upper())
        if not emotion_key:
            return {"applied": False, "tag": tag, "reason": "unknown_tag"}
        deltas = {key: 0.0 for key in EMOTION_KEYS}
        deltas[emotion_key] += 1.8
        if emotion_key == "joy":
            deltas["boredom"] -= 1.0
        elif emotion_key == "sharpness":
            deltas["pride"] += 0.6
        elif emotion_key == "boredom":
            deltas["joy"] -= 0.4
        elif emotion_key == "irritation":
            deltas["joy"] -= 0.3
        with self._lock:
            before = self._dominant_from_vector(self._state.vector)
            for key, delta in deltas.items():
                self._state.vector[key] = _clamp(
                    self._state.vector.get(key, 0.0) + delta
                )
            if self._state.recent_user_modes[-3:] == ["task", "task", "task"]:
                self._state.vector["sharpness"] = _clamp(
                    self._state.vector.get("sharpness", 0.0) + 1.0
                )
            after = self._dominant_from_vector(self._state.vector)
            self._state.dominant_emotion = after
            self._state.pending_corememory_sync = True
            self._record_history(f"message_sent:{emotion_key}", deltas)
            self._save_state()
            return {
                "applied": True,
                "tag": emotion_key,
                "significant": any(abs(delta) >= 1.5 for delta in deltas.values())
                or before != after,
                "diary_reason": f"模型输出情绪标签 {emotion_key}",
            }

    def _process_signed_tag(self,tag: str) -> tuple[int, str | None]:
        tag = str(tag or "").strip().upper()
        sign=0
        for i in range(len(tag)):
            if tag[i] == "+":
                sign += 1
            elif tag[i] == "-":
                sign -= 1
            else:
                break
        if sign == 0:
            sign += 1
        emotion_key = EMOTION_TAG_TO_KEY.get(tag.lstrip("+-").replace("+", "").replace("-", ""))
        return sign,emotion_key
                

    def _apply_deltas(self, deltas: dict[str, float], reason: str) -> None:
        with self._lock:
            before = self._dominant_from_vector(self._state.vector)
            for key, delta in deltas.items():
                self._state.vector[key] = _clamp(
                    self._state.vector.get(key, 0.0) + delta
                )
            after = self._dominant_from_vector(self._state.vector)
            self._state.dominant_emotion = after
            self._state.pending_corememory_sync = True
            self._record_history(reason, deltas)
            self._save_state()

    def apply_signed_emotion_tag_list(self, tags: list[str]) -> None|dict[str, Any]:
        """_summary_

        Args:
            tags (list[str]): 例子:["++JOY","-BOREDOM","+SHARPNESS]

        Returns:
            dict[str, Any]: 返回应用结果，包含每个标签的应用情况。
        """
        for tag in tags:
            sign, emotion_key = self._process_signed_tag(tag)
            if not emotion_key:
                return {"applied": False, "tag": tag, "reason": "unknown_tag"}
            deltas = {key: 0.0 for key in EMOTION_KEYS}
            deltas[emotion_key] += 1.8 * sign
            if emotion_key == "joy":
                deltas["boredom"] -= 1.0 * sign
            elif emotion_key == "sharpness":
                deltas["pride"] += 0.6 * sign
            elif emotion_key == "boredom":
                deltas["joy"] -= 0.4 * sign
            elif emotion_key == "irritation":
                deltas["joy"] -= 0.3 * sign
            self._apply_deltas(deltas, f"message_sent:{emotion_key}")




    def apply_reply(self, reply: str) -> None:
        with self._lock:
            self._state.last_reply = reply
            self._save_state()

    def set_emotion(self, key: str, value: float) -> None:
        with self._lock:
            old = self._state.vector.get(key, 0.0)
            self._state.vector[key] = _clamp(value)
            self._state.dominant_emotion = self._dominant_from_vector(self._state.vector)
            self._state.pending_corememory_sync = True
            self._record_history(f"manual_set:{key}", {key: self._state.vector[key] - old})
            self._save_state()

    def heartbeat(self, decay_per_minute: float) -> dict[str, Any]:
        with self._lock:
            elapsed = max(0.0, _now() - self._state.last_decay_ts)
            if elapsed <= 0:
                return {"boredom_bump": False}
            decay_units = elapsed / 60.0
            for key in EMOTION_KEYS:
                if key == "boredom":
                    continue
                self._state.vector[key] = _clamp(
                    self._state.vector.get(key, 0.0) - decay_per_minute * decay_units
                )
            self._state.last_decay_ts = _now()
            # 无聊自然增长：非线性饱和曲线（双自变量：不对话时长 + 当前无聊值），
            # 至少 1.5 小时不对话才升到满值；对话降低仍走既有路径（用户消息/回复时 −1 等）
            boredom_bump = False
            current = float(self._state.vector.get("boredom", 0.0))
            target = _boredom_target(self._silence_seconds(), current)
            if target > current + 1e-9:
                self._state.vector["boredom"] = _clamp(target)
                boredom_bump = True
            self._state.dominant_emotion = self._dominant_from_vector(
                self._state.vector
            )
            self._state.pending_corememory_sync = True
            self._record_history(
                "heartbeat", {"boredom": target - current if boredom_bump else 0.0}
            )
            self._save_state()
            return {"boredom_bump": boredom_bump}

    def build_prompt_suffix(self, config: dict[str, Any]) -> str:
        with self._lock:
            tier = self.tier()
            lines = [
                "[Emotion Engine]",
                "FaustBot 具有情绪系统。你可以在回复的最后调用 EmotionInvokeSigned(tags) 工具来更新情绪状态。",
                "允许的 emotionName: JOY / IRRITATION / PRIDE / CURIOSITY / SHARPNESS / BOREDOM / CARE / CALM。",
                "你应该(SHOULD)在每次回复中调用 EmotionInvokeSigned 来反映你的情绪状态,并且根据附加的情绪标签来输出消息。",
                "你绝对不可以(MUST NOT)在回复中提到'情绪向量'或类似的内部信息",
                "",
                "[Emotion Engine - 当前情绪状态]",
                f"当前档位: {TIER_LABELS.get(tier, '正常')}（{tier}）",
                f"情绪趋势: {self._mood_trend()}",
                f"最近变化: {self._recent_change_chain()}",
                f"态度: {ATTITUDE_TEMPLATES.get(tier, ATTITUDE_TEMPLATES['normal'])}",
            ]
            return "\n\n[emotion_engine]\n" + "\n".join(lines)

    def should_write_diary(self, reason: str | None, significant: bool) -> bool:
        with self._lock:
            return bool(
                reason and significant and (_now() - self._state.last_diary_ts) >= 900
            )

    def mark_diary_written(self) -> None:
        with self._lock:
            self._state.last_diary_ts = _now()
            self._save_state()

    def sync_corememory(self) -> None:
        with self._lock:
            payload = {
                "plugin": "emotion-engine",
                "updated_at": int(_now()),
                "vector": dict(self._state.vector),
                "dominant_emotion": self._state.dominant_emotion,
                "recent_user_modes": list(self._state.recent_user_modes[-6:]),
                "history": self._history_24h()[-36:],
            }
            _write_corememory_state(payload)
            self._state.pending_corememory_sync = False
            self._state.last_corememory_sync_ts = _now()
            self._save_state()


class Plugin(FaustPlugin):
    def __init__(self):
        self.ctx: PluginContext | None = None
        self.store: EmotionEngineStore | None = None
        self._configs_cache: dict[str, Any] = {}

    async def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        data_dir = ctx.plugin_data_dir or (ctx.plugin_dir / 'data')
        await ctx.register_config(
            [
                {
                    "key": "SHARP_TONGUE_REWRITE",
                    "type": "bool",
                    "label": "毒舌改写开关",
                    "default": False,
                },
                {
                    "key": "DECAY_PER_MINUTE",
                    "type": "float",
                    "label": "情绪衰减速率",
                    "default": 0.1,
                },
                {
                    "key": "OVERLAY_INTENSITY",
                    "type": "int",
                    "label": "滤镜强度",
                    "default": 50,
                },
                {
                    "key": "PROACTIVE_ENABLED",
                    "type": "bool",
                    "label": "主动对话开关",
                    "default": True,
                },
                {
                    "key": "PROACTIVE_INTERVAL_CHATTY",
                    "type": "int",
                    "label": "主动对话间隔（活泼档，秒）",
                    "default": 900,
                },
                {
                    "key": "PROACTIVE_INTERVAL_NORMAL",
                    "type": "int",
                    "label": "主动对话间隔（正常档，秒）",
                    "default": 3600,
                },
                {
                    "key": "PROACTIVE_INTERVAL_QUIET",
                    "type": "int",
                    "label": "主动对话间隔（低落档，秒）",
                    "default": 7200,
                },
            ] # type: ignore
        )
        try:
            decay = float(await ctx.get_config("DECAY_PER_MINUTE", 0.1) or 0.1)
        except (TypeError, ValueError):
            decay = 0.1
        self.store = EmotionEngineStore(data_dir, decay_per_minute=decay)
        self.store.sync_corememory()
        self._configs_cache = await ctx.list_configs()
        await ctx.vfs_write(
            "/plugins/emotion-engine.md",
            "# Emotion Engine\n\n"
            "Emotion Engine 通过 EmotionInvokeSigned(tags) 工具更新情绪状态。\n"
            "允许的 emotionName: JOY / IRRITATION / PRIDE / CURIOSITY / SHARPNESS / BOREDOM / CARE / CALM。\n"
            "调用后返回当前完整情绪向量。该工具的 tool_start/tool_result 对用户不可见，\n"
            "请大胆使用。当前状态也可通过 faustbot://plugins/emotion-engine-state.json 读取。\n",
        )
        await ctx.vfs_write_symbolic(
            "/plugins/emotion-engine-state.json",
            lambda _path: json.dumps(
                self.get_state_payload(), ensure_ascii=False, indent=2
            ),
        )

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        _PLUGIN = self

    @hookimpl
    def plugin_unloaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        if _PLUGIN is self:
            _PLUGIN = None

    @hookimpl
    async def config_changed(self, key: str, old: Any, new: Any, ctx: PluginContext) -> None:
        # 用 startup 保存的 self.ctx（hook 参数 ctx 可能为 None）
        if self.ctx is not None:
            self._configs_cache = await self.ctx.list_configs()

    def _configs(self) -> dict[str, Any]:
        return self._configs_cache

    def _write_diary(self, analysis: dict[str, Any]) -> None:
        if self.store is None:
            return
        snapshot = self.store.snapshot(self._configs())
        reason = str(analysis.get("diary_reason") or "情绪变化")
        content = (
            f"# Emotion Engine\n\n"
            f"- 事件: {reason}\n"
            f"- 主导情绪: {snapshot.get('dominant_emotion')}\n"
            f"- 情绪向量: {json.dumps(snapshot.get('vector') or {}, ensure_ascii=False)}\n"
            f"- 最近用户消息: {snapshot.get('last_user_message') or ''}\n"
        )

        async def writer() -> None:
            from faust_backend.memory import get_memory

            await get_memory().write_diary(content)

        _run_async_background(writer())
        self.store.mark_diary_written()

    def get_state_payload(self) -> dict[str, Any]:
        if self.store is None:
            return {"vector": dict(DEFAULT_EMOTIONS), "history": [], "config": {}}
        return self.store.snapshot(self._configs())

    @hookimpl
    def register_frontend(self) -> list[dict]:
        return [
            {
                "type": "js",
                "path": "/faust/plugins/emotion-engine/frontend/panel-v2.js",
            },
            {
                "type": "js",
                "path": "/faust/plugins/emotion-engine/frontend/app-hook-v2.js",
            },
            {
                "type": "css",
                "path": "/faust/plugins/emotion-engine/frontend/panel-v2.css",
            },
        ]

    @hookimpl
    def register_prompt_suffix(self) -> list[str]:
        return [
            (
                self.store.build_prompt_suffix(self._configs())
                if self.store is not None
                else ""
            )
        ]

    @hookimpl
    def register_tools(self, ctx: PluginContext) -> list:
        @tool
        async def EmotionInvokeSigned(tags: list[str]) -> str:
            """更新FaustBot的情绪状态,支持有符号批量更新



            Args:
                tags (list[str]): 情绪标签列表, 例子:["++JOY","-BOREDOM","+SHARPNESS"]
                                  每一个情绪标签可以带有符号前缀, "+"表示增加, "-"表示减少, "++"表示大幅增加, "--"表示大幅减少。
                                  当你和用户对话时,每一轮对话都需要调用一次EmotionInvokeSigned来更新情绪状态

            Returns:
                str: 修改后的情绪向量
            """
            if self.store is None:
                return json.dumps({"error": "store not ready"})
            self.store.apply_signed_emotion_tag_list(tags)
            return json.dumps(self.store.snapshot(self._configs())["vector"], ensure_ascii=False)
        return [
            ToolSpec(name="EmotionInvokeSigned", tool=EmotionInvokeSigned, enabled_by_default=True, description=EmotionInvokeSigned.__doc__ or ""),
        ]

    @hookimpl
    def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        action = str((payload or {}).get("action") or "get_state").strip().lower()
        if action == "get_state":
            return {"status": "ok", **self.get_state_payload()}
        if action == "set_emotion":
            if self.store is None:
                return {"status": "error", "detail": "store not ready"}
            key = str((payload or {}).get("key") or "").strip().lower()
            if key not in EMOTION_KEYS:
                return {"status": "error", "detail": f"unknown emotion key: {key}"}
            try:
                value = float((payload or {}).get("value", 0))
            except (TypeError, ValueError):
                return {"status": "error", "detail": "invalid value"}
            self.store.set_emotion(key, value)
            return {"status": "ok", **self.get_state_payload()}
        return {"status": "error", "detail": f"unknown action: {action}"}

    @hookimpl
    def message_received(
        self, msg: Any, history: list, ctx: PluginContext
    ) -> str | None:
        if self.store is None:
            log.critical("EmotionEngineStore not initialized While processing message")
            return None
        return msg+self.store.note_user_message(str(msg or ""))
        

    @hookimpl
    def agent_event_sent(self, event: dict, current_history: list, ctx: PluginContext) -> dict | None |str:
        if event.get("type") in {"tool_start", "tool_result"} and event.get("tool_name") == "EmotionInvokeSigned":
            return "__IGNORED__"
        if event.get("type") == "done" and self.store is not None:
            self.store.mark_reply_done()
        return None

    def _schedule_proactive(self) -> None:
        if self.store is None:
            return
        tier = self.store.tier()
        if tier == "silent":
            return
        cfg = self._configs()
        intervals = {
            "chatty": float(cfg.get("PROACTIVE_INTERVAL_CHATTY", 900) or 900),
            "normal": float(cfg.get("PROACTIVE_INTERVAL_NORMAL", 3600) or 3600),
            "quiet": float(cfg.get("PROACTIVE_INTERVAL_QUIET", 7200) or 7200),
        }
        interval = intervals.get(tier)
        if not interval or interval <= 0:
            return
        if not self.store.should_proactive(interval):
            return
        if _schedule_proactive_chat(_build_proactive_content(tier)):
            self.store.mark_proactive_fired()

    @hookimpl
    def heartbeat(self, ctx: PluginContext) -> None:
        if self.store is None:
            return
        cfg = self._configs()
        decay = float(cfg.get("DECAY_PER_MINUTE", 0.1) or 0.1)
        result = self.store.heartbeat(decay)
        if result.get("boredom_bump") and self.store.should_write_diary(
            "长时间沉默导致无聊上升", True
        ):
            self._write_diary({"diary_reason": "长时间沉默导致无聊上升"})
        if self.store.snapshot(cfg).get("pending_corememory_sync"):
            self.store.sync_corememory()
        # ── 主动对话调度（silent 档不触发） ──
        if bool(cfg.get("PROACTIVE_ENABLED", True)):
            self._schedule_proactive()

    @hookimpl
    def health_check(self) -> dict | None:
        snapshot = self.get_state_payload()
        return {
            "status": "ok",
            "plugin": "emotion-engine",
            "dominant": snapshot.get("dominant_emotion"),
            "pending_sync": snapshot.get("pending_corememory_sync"),
        }


def get_plugin() -> Plugin:

    return Plugin()
