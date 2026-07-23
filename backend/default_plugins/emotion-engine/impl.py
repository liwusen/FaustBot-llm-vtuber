from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter

import faust_backend.config_loader as conf
from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl

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
EMOTION_TAG_RE = re.compile(r"\[\[([A-Z_]+)\]\]")
STATE_FILE_NAME = "emotion_state.json"
HISTORY_LIMIT = 512
COREMEMORY_START = "<!-- emotion-engine:start -->"
COREMEMORY_END = "<!-- emotion-engine:end -->"
_ROUTER = APIRouter()
_PLUGIN: "Plugin | None" = None


def _now() -> float:
    return time.time()


def _clamp(value: float, min_value: float = 0.0, max_value: float = 10.0) -> float:
    return max(min_value, min(max_value, round(float(value), 3)))


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


class EmotionState:
    def __init__(self):
        now = _now()
        self.vector: dict[str, float] = dict(DEFAULT_EMOTIONS)
        self.history: list[dict[str, Any]] = []
        self.last_decay_ts = now
        self.last_interaction_ts = now
        self.silence_ticks = 0
        self.recent_user_modes: list[str] = []
        self.last_user_message = ""
        self.last_reply = ""
        self.pending_corememory_sync = True
        self.last_corememory_sync_ts = 0.0
        self.last_diary_ts = 0.0
        self.dominant_emotion = "curiosity"


class EmotionEngineStore:
    def __init__(self, plugin_dir: Path):
        self._lock = threading.RLock()
        self._data_dir = plugin_dir / "data"
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
        state.silence_ticks = int(raw.get("silence_ticks") or 0)
        state.recent_user_modes = list(raw.get("recent_user_modes") or [])[-8:]
        state.last_user_message = str(raw.get("last_user_message") or "")
        state.last_reply = str(raw.get("last_reply") or "")
        state.pending_corememory_sync = bool(raw.get("pending_corememory_sync", True))
        state.last_corememory_sync_ts = float(raw.get("last_corememory_sync_ts") or 0.0)
        state.last_diary_ts = float(raw.get("last_diary_ts") or 0.0)
        state.dominant_emotion = str(
            raw.get("dominant_emotion") or self._dominant_from_vector(state.vector)
        )
        return state

    def _save_state(self) -> None:
        payload = {
            "vector": self._state.vector,
            "history": self._state.history[-HISTORY_LIMIT:],
            "last_decay_ts": self._state.last_decay_ts,
            "last_interaction_ts": self._state.last_interaction_ts,
            "silence_ticks": self._state.silence_ticks,
            "recent_user_modes": self._state.recent_user_modes[-8:],
            "last_user_message": self._state.last_user_message,
            "last_reply": self._state.last_reply,
            "pending_corememory_sync": self._state.pending_corememory_sync,
            "last_corememory_sync_ts": self._state.last_corememory_sync_ts,
            "last_diary_ts": self._state.last_diary_ts,
            "dominant_emotion": self._state.dominant_emotion,
        }
        self._state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

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

    def _top_emotions(self, count: int = 3) -> list[tuple[str, float]]:
        return sorted(
            ((key, float(self._state.vector.get(key, 0.0))) for key in EMOTION_KEYS),
            key=lambda item: item[1],
            reverse=True,
        )[:count]

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

    def note_user_message(self, text: str) -> None:
        with self._lock:
            self._state.last_user_message = text
            self._state.last_interaction_ts = _now()
            self._state.silence_ticks = 0
            mode = "task" if len((text or "").strip()) <= 48 else "chat"
            self._state.recent_user_modes.append(mode)
            self._state.recent_user_modes = self._state.recent_user_modes[-8:]
            self._save_state()

    def apply_emotion_tag(self, tag: str) -> dict[str, Any]:
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

    def apply_reply(self, reply: str) -> None:
        with self._lock:
            self._state.last_reply = reply
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
            self._state.silence_ticks += 1
            boredom_bump = False
            if self._state.silence_ticks >= 6:
                self._state.vector["boredom"] = _clamp(
                    self._state.vector.get("boredom", 0.0) + 1.0
                )
                self._state.silence_ticks = 0
                boredom_bump = True
            self._state.dominant_emotion = self._dominant_from_vector(
                self._state.vector
            )
            self._state.pending_corememory_sync = True
            self._record_history("heartbeat", {"boredom": 1.0 if boredom_bump else 0.0})
            self._save_state()
            return {"boredom_bump": boredom_bump}

    def build_prompt_suffix(self, config: dict[str, Any]) -> str:
        with self._lock:
            top = self._top_emotions(3)
            lines = [
                "Faust 具有情绪系统。请在最终回复中只输出一个隐藏情绪标签，格式必须是 [[JOY]] / [[IRRITATION]] / [[PRIDE]] / [[CURIOSITY]] / [[SHARPNESS]] / [[BOREDOM]] 之一。",
                "标签放在回复末尾即可，其他位置不要使用这种格式。该标签会在发送给用户前被移除。",
                "标签应反映 Faust 当前的主导情绪与本轮回答状态，但不要为了输出标签而牺牲回答质量。",
                "当前向量: "
                + "; ".join(f"{EMOTION_LABELS[key]}={value:.1f}" for key, value in top),
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


@_ROUTER.get("/state")
async def get_state():
    plugin = _PLUGIN
    if plugin is None:
        return {"status": "error", "detail": "plugin not loaded"}
    return {"status": "ok", **plugin.get_state_payload()}


class Plugin(FaustPlugin):
    def __init__(self):
        self.ctx: PluginContext | None = None
        self.store: EmotionEngineStore | None = None

    def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.store = EmotionEngineStore(ctx.plugin_dir)
        ctx.register_config(
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
            ] # type: ignore
        )
        self.store.sync_corememory()
        ctx.vfs_write(
            "/plugins/emotion-engine.md",
            "# Emotion Engine\n\n"
            "Emotion Engine 通过模型在回复末尾输出的 [[EMOTION_NAME]] 标签来更新 Faust 的情绪状态。\n"
            "允许的标签包括 [[JOY]] [[IRRITATION]] [[PRIDE]] [[CURIOSITY]] [[SHARPNESS]] [[BOREDOM]] [[CARE]] [[CALM]]。\n"
            "需要查看当前状态时，读取 faustbot://plugins/emotion-engine-state.json。\n",
        )
        ctx.vfs_write_symbolic(
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

    def _configs(self) -> dict[str, Any]:
        return self.ctx.list_configs() if self.ctx else {}

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

    def register_routes(self) -> list:
        return [_ROUTER]

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

    def register_prompt_suffix(self) -> list[str]:
        return [
            (
                self.store.build_prompt_suffix(self._configs())
                if self.store is not None
                else ""
            )
        ]

    @hookimpl
    def message_received(
        self, msg: Any, history: list, ctx: PluginContext
    ) -> str | None:
        if self.store is None:
            return None
        self.store.note_user_message(str(msg or ""))
        return None

    @hookimpl
    def message_sent(self, msg: str, response: Any, ctx: PluginContext) -> Any:
        if self.store is None:
            return response
        reply = str(response or "")
        tags = EMOTION_TAG_RE.findall(reply)
        clean_reply = EMOTION_TAG_RE.sub("", reply).strip()
        analysis = None
        if tags:
            analysis = self.store.apply_emotion_tag(tags[-1])
        self.store.apply_reply(clean_reply)
        if analysis and self.store.should_write_diary(
            str(analysis.get("diary_reason") or ""), bool(analysis.get("significant"))
        ):
            self._write_diary(analysis)
        if self.store.snapshot(self._configs()).get("pending_corememory_sync"):
            self.store.sync_corememory()
        return clean_reply

    def heartbeat(self, ctx: PluginContext) -> None:
        if self.store is None:
            return
        decay = (
            float(self.ctx.get_config("DECAY_PER_MINUTE", 0.1) or 0.1)
            if self.ctx
            else 0.1
        )
        result = self.store.heartbeat(decay)
        if result.get("boredom_bump") and self.store.should_write_diary(
            "长时间沉默导致无聊上升", True
        ):
            self._write_diary({"diary_reason": "长时间沉默导致无聊上升"})
        if self.store.snapshot(self._configs()).get("pending_corememory_sync"):
            self.store.sync_corememory()

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
