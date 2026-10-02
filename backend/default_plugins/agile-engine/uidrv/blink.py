"""眨眼协议与前端命令（设计 §12）：命令下行 + 回执等待 + 提示条。

回执通道用插件自己的 ``communicate`` 路由（``POST /faust/plugins/agile-engine/communicate``，
设计 §6.2 的 ``ui_blink_ack``）：它是唯一能把回执**带数据**交回本插件、且跨事件循环安全的通道。
（``/faust/command/feedback`` 的 ``feedback_event_pool`` 是 asyncio.Event，跨 loop 等待不安全；
前端仍会对该路由回执一次，此处不依赖它。）

本模块不做任何探测/注入，只负责"把命令发下去、把回执等回来"——测试直接注入假 hub。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable, Optional


class FrontendLink:
    """后端 → 前端（Electron 渲染器）的命令通道。"""

    def __init__(self, sink: Callable[[str, dict], None], hub: Any, *,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 poll_s: float = 0.02) -> None:
        self._sink = sink
        self._hub = hub
        self._clock = clock
        self._sleep = sleep
        self._poll_s = poll_s

    # ── 眨眼 ──
    async def blink(self, hide: bool, timeout: float) -> bool:
        """藏/显模型并等回执。回执超时返回 False（调用方必须据此拒绝继续，不假设已藏好）。"""
        fid = "ui-blink-" + uuid.uuid4().hex
        self._hub.new_request(fid)
        self._sink("UI_MODEL_BLINK", {"hide": bool(hide), "feedback_id": fid})
        ack = await self._hub.wait_request(fid, timeout, self._poll_s)
        return ack is not None

    # ── 悬浮提示 ──
    async def hint(self, state: str, title: str, text: str) -> None:
        self._sink("UI_CONTROL_HINT", {"state": str(state), "title": str(title), "reason": str(text)})

    # ── 窗口边界（判断是否需要眨眼）──
    async def window_bounds(self, timeout: float) -> Optional[tuple[int, int, int, int]]:
        req = "ui-bounds-" + uuid.uuid4().hex
        self._hub.new_request(req)
        self._sink("UI_WINDOW_BOUNDS", {"req_id": req})
        value = await self._hub.wait_request(req, timeout, self._poll_s)
        if not isinstance(value, dict):
            return None
        try:
            x, y = float(value["x"]), float(value["y"])
            w, h = float(value["width"]), float(value["height"])
        except (KeyError, TypeError, ValueError):
            return None
        if w <= 0 or h <= 0:
            return None
        return (int(round(x)), int(round(y)), int(round(x + w)), int(round(y + h)))

    # ── 会话态（告知渲染器会话开始/结束，并借回执确认插件前端脚本已加载）──
    async def set_session(self, active: bool, timeout: float = 1.0) -> Optional[dict]:
        """告知渲染器会话开始/结束。

        返回 ``{"active": bool}``；超时/未加载插件脚本时返回 None
        （调用方据此 fail-closed：没有渲染器就没法眨眼藏模型，实时会话不能开）。
        """
        req = "ui-session-" + uuid.uuid4().hex
        self._hub.new_request(req)
        self._sink("UI_SESSION_STATE", {"active": bool(active), "req_id": req})
        value = await self._hub.wait_request(req, timeout, self._poll_s)
        return value if isinstance(value, dict) else None
