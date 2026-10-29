"""Subagent 流式事件的进程级广播器。

`SubagentManager` 会在每次 `rebuild_runtime()` 时被整体替换
（见 `runtime/lifecycle.py::_rebuild_subagent_manager`），而 `/faust/chat` 的
WebSocket 转发任务是长生命周期的：它只在连接建立时订阅一次，且前端常驻连接
只在 `onclose` 时才重连。若订阅的是 manager 实例内部的队列，重建之后转发任务
就会永远监听一个再也不会被写入的孤儿队列 —— 前端 Subagent Panel 的实时事件
从此断流（摘要栏却因为每次现读 `state.subagent_manager` 而照常更新）。

因此事件队列必须活在 manager 生命周期之外：本模块用进程级订阅表广播事件，
WS 连接 `subscribe()`、断开 `unsubscribe()`，与当前 manager 是哪一个实例无关。
"""

from __future__ import annotations

import asyncio
from typing import Any

# 单个订阅者的队列上限：前端消费不及时时丢弃最旧事件，绝不阻塞 Subagent 运行。
_DEFAULT_MAXSIZE = 500

_subscribers: set[asyncio.Queue] = set()


def subscribe(maxsize: int = _DEFAULT_MAXSIZE) -> asyncio.Queue:
    """注册一个订阅者并返回只属于它的队列。"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _subscribers.add(queue)
    return queue


def unsubscribe(queue: asyncio.Queue) -> None:
    """注销订阅者（WS 断开时调用）。"""
    _subscribers.discard(queue)


def subscriber_count() -> int:
    """当前订阅者数量（诊断用）。"""
    return len(_subscribers)


def publish(event: dict[str, Any]) -> None:
    """向所有订阅者广播一条 Subagent 事件。

    事件必须携带 `agent_id=subagent-<name>`：主 Agent 事件不允许混进这条通道。
    该约束过去以 `assert` 形式放在消费者（WS 转发任务）里，一旦触发会静默杀死
    转发任务，因此前移到生产者侧显式抛错。
    """
    if str((event or {}).get("agent_id") or "") == "main":
        raise ValueError("Subagent 事件通道不接受主 Agent 事件")
    for queue in list(_subscribers):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # 慢消费者：丢最旧一条再补最新，保证不阻塞生产者、不丢最新状态
            try:
                queue.get_nowait()
                queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
