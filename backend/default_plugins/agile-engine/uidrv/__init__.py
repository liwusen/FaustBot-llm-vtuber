"""uidrv：受限 UI 操作能力（LimitedUI）的实现包（设计 §6.1）。

对外只有三件事：

- ``create_decider(name, ...)``：按 ``spec["decider"]`` 选实现（默认 ``"omnijev"``，测试用 ``"scripted"``）；
- ``hub``：模块级信号汇聚点——前端经插件 ``communicate`` 路由回传的窗口边界回执 / 停止，
  以及面板要的会话状态（设计 §17）；
- 各具体模块（``decider`` / ``win`` / ``input`` / ``blink`` / ``audit``）。

跨线程约定：前端信号由 backend 主事件循环（FastAPI）投递，会话循环跑在模块自己的 interval
线程里，因此 hub 里只用 ``threading`` 原语 + 轮询等待，绝不跨 loop 等待 asyncio.Event。
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from .audit import AuditWriter
from .blink import FrontendLink
from .decider import (CEILINGS, DRY_RUN_SECONDS, AskItem, Answer, AuditSpec, BlinkSpec,
                      EscalateSpec, Frame, GateSpec, LimitSpec, LimitedUIDecider, OpenResult,
                      SessionSpec, SessionState, SpecError, UIDecision, UIDeps, UIOption,
                      perceptual_hash, rects_overlap)
from .omnijev import OmniJevDecider
from .scripted import ScriptedDecider, synthetic_frame

__all__ = [
    "AuditWriter", "FrontendLink", "AskItem", "Answer", "AuditSpec", "BlinkSpec", "CEILINGS",
    "DRY_RUN_SECONDS", "EscalateSpec", "Frame", "GateSpec", "LimitSpec", "LimitedUIDecider",
    "OmniJevDecider", "OpenResult", "ScriptedDecider", "SessionSpec", "SessionState", "SpecError",
    "UIDecision", "UIDeps", "UIOption", "ControlHub", "hub", "create_decider", "perceptual_hash",
    "rects_overlap", "synthetic_frame",
]

REGISTRY: dict[str, type[LimitedUIDecider]] = {
    "omnijev": OmniJevDecider,
    "scripted": ScriptedDecider,
}


def create_decider(name: str, module: str, spec: SessionSpec, hooks: dict[str, Any] | None,
                   deps: UIDeps, **extra: Any) -> LimitedUIDecider:
    key = str(name or "omnijev").strip().lower()
    cls = REGISTRY.get(key)
    if cls is None:
        raise SpecError(f"未知的 decider {name!r}；可选: {', '.join(sorted(REGISTRY))}")
    return cls(module, spec, hooks, deps, **extra) if extra else cls(module, spec, hooks, deps)


class ControlHub:
    """前端 → 插件的信号汇聚（线程安全）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, dict[str, Any]] = {}
        self._stop: dict[str, bool] = {}
        self._stop_all = False
        self._sessions: dict[str, LimitedUIDecider] = {}

    # ── 会话登记（面板状态用）──
    def attach(self, decider: LimitedUIDecider) -> None:
        with self._lock:
            self._sessions[decider.module] = decider

    def detach(self, decider: LimitedUIDecider) -> None:
        with self._lock:
            if self._sessions.get(decider.module) is decider:
                self._sessions.pop(decider.module, None)

    def session_status(self) -> dict[str, Any]:
        with self._lock:
            sessions = list(self._sessions.values())
        items = []
        for dec in sessions:
            items.append({
                "module": dec.module,
                "state": dec.state.value,
                "steps": dec.step_count,
                "injections": dec.injection_count,
                "elapsed_s": round(dec.elapsed_s, 1),
                "dry_run": dec.state is SessionState.DRY_RUN,
                "purpose": dec.spec.purpose,
            })
        return {"status": "ok", "sessions": items}

    # ── 前端 → 后端 ──
    def new_request(self, req_id: str) -> None:
        with self._lock:
            self._requests[req_id] = {"value": None, "done": False}

    def resolve_request(self, req_id: str, value: Any = True) -> bool:
        with self._lock:
            entry = self._requests.get(req_id)
            if entry is None:
                return False
            entry["value"] = value
            entry["done"] = True
        return True

    def request_stop(self, module: str | None = None) -> None:
        with self._lock:
            if module:
                self._stop[str(module)] = True
            else:
                self._stop_all = True

    # ── 会话侧（interval loop）──
    async def wait_request(self, req_id: str, timeout: float, poll_s: float = 0.02) -> Any:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._lock:
                entry = self._requests.get(req_id)
                done = bool(entry and entry["done"])
                value = entry["value"] if entry else None
            if done:
                with self._lock:
                    self._requests.pop(req_id, None)
                return value
            if time.monotonic() >= deadline:
                with self._lock:
                    self._requests.pop(req_id, None)
                return None
            await asyncio.sleep(poll_s)

    def take_stop(self, module: str) -> bool:
        with self._lock:
            hit = self._stop.pop(str(module), False) or self._stop_all
            if self._stop_all:
                self._stop_all = False
            return hit


hub = ControlHub()
