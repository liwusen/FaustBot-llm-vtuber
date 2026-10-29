"""同步插件 hook 桥（`_run_awaitable_sync`）回归测试。

核心契约：**绝不能在事件循环线程上等待自己**。旧实现在有运行中循环时直接
`run_coroutine_threadsafe(awaitable, loop).result()`：当调用方就是 loop 线程时，
它等的是一个被自己阻塞住的循环 —— 永久自锁。既然 Agent 会话持有主 Agent 锁，
后果就是后端整体冻结、后续所有 Agent 调用等满锁超时。
"""
from __future__ import annotations

import asyncio
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from faust_backend.plugins.system import manager as plugin_manager


def test_on_loop_bridge_returns_instead_of_self_deadlocking():
    """在事件循环线程上调用同步桥：必须返回，而不是等自己被阻塞的 loop。"""
    calls: list[str] = []
    outcome: dict = {}

    async def _hook() -> str:
        calls.append("ran")
        return "ok"

    async def _call_on_loop() -> None:
        outcome["value"] = plugin_manager._run_awaitable_sync(_hook())

    def _run() -> None:
        asyncio.run(_call_on_loop())

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=15)
    assert not thread.is_alive(), "同步 hook 桥在事件循环线程上自锁了"
    assert outcome.get("value") == "ok"
    assert calls == ["ran"]


def test_off_loop_bridge_still_runs_coroutine():
    """没有运行中循环时保持原行为：本线程自建临时循环跑完协程。"""
    calls: list[str] = []

    async def _hook() -> str:
        calls.append("ran")
        return "ok"

    assert plugin_manager._run_awaitable_sync(_hook()) == "ok"
    assert calls == ["ran"]
