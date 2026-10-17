"""会话级（按 websocket 连接）的回合状态。

修复背景
--------
主 Agent 的中断信号此前是进程级全局单例（``runtime.state._agent_abort`` 每轮被
``reset_abort_event()`` 整体替换），强制打断标记与触发器任务登记同样是
``routes.chat`` 的模块级全局。多连接（Electron 重连、多窗口、触发器复用活动连接）
会互相踩：

* 连接 B 后开一轮会把全局 abort event 换成新的，于是 A 的 ``interrupt`` 设的是
  **B 的** event —— B 的流被 A 打断，A 自己的生产者却拿着旧对象永远等不到信号；
* 全局 ``_force_interrupting`` 会让任何连接被取消的流都改发 ``done(forced=True)``；
* ``_active_trigger_task`` 全进程只有一个槽位，插话只能命中最后登记的那个任务。

这里把这三样东西按连接（websocket）持有：每个连接一份 abort event / 强制打断
标记 / 触发器任务集合，互不影响。

``events.ignore_trigger_event`` 与 ``turn_gate`` 保持进程级：它们的语义本来就是
「整机是否正在和用户对话」「上一轮回复播出去了没有」，不属于单个连接。

仅由事件循环内的协程使用，不加锁（与 turn_gate 一致）。
"""
from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass, field


@dataclass
class TurnState:
    """一个 chat websocket 连接上的回合状态。"""

    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    # 正在执行用户插话强制打断：前台触发器被取消时改发 done(forced) 而非 interrupted
    force_interrupting: bool = False
    # 本连接当前的消费者任务（用户消息流），供 interrupt 直接取消
    active_task: asyncio.Task | None = None
    # 本连接拥有的触发器任务（前台触发器流式推送到本连接）
    trigger_tasks: set[asyncio.Task] = field(default_factory=set)

    def begin_turn(self) -> asyncio.Event:
        """开始新一轮：换一个全新的 abort event 并返回，供生产者轮询。

        每轮一个全新对象，避免上一轮遗留的 set 状态把新一轮立刻打断。
        """
        self.abort_event = asyncio.Event()
        return self.abort_event

    def interrupt(self) -> None:
        """本连接收到 interrupt：只中止本连接这一轮。"""
        self.abort_event.set()
        task = self.active_task
        if task is not None and not task.done():
            task.cancel()


_states: "weakref.WeakKeyDictionary[object, TurnState]" = weakref.WeakKeyDictionary()

# 未绑定到具体连接的触发器任务（后台触发器 / batched 批量注入）：它们占用的是同一个
# 主 Agent，用户插话时仍需打断，但不属于任何连接，因此不随连接断开被清理。
_unowned_trigger_tasks: set[asyncio.Task] = set()


def get_turn_state(websocket) -> TurnState:
    """返回该 websocket 的回合状态（首次访问时创建）。"""
    turn = _states.get(websocket)
    if turn is None:
        turn = TurnState()
        _states[websocket] = turn
    return turn


def register_trigger_task(task: asyncio.Task, owner=None) -> None:
    """登记占用了主 Agent 的触发器任务。

    owner 为前台触发器流式推送的目标 chat websocket；后台/batched 任务没有 owner。
    """
    if owner is None:
        _unowned_trigger_tasks.add(task)
    else:
        get_turn_state(owner).trigger_tasks.add(task)


def clear_trigger_task(task: asyncio.Task, owner=None) -> None:
    """任务结束后注销登记（owner 传 None 时两个集合都清，便于 finally 统一调用）。"""
    _unowned_trigger_tasks.discard(task)
    if owner is not None:
        get_turn_state(owner).trigger_tasks.discard(task)


def active_trigger_tasks(owner=None) -> list[asyncio.Task]:
    """当前活跃的触发器任务：owner 自己的 + 未绑定的后台任务。

    刻意不包含其它连接的触发器任务——修复前插话会取消全进程唯一登记的那一个，
    也就是可能把别的连接正在跑的触发器打断。
    """
    tasks = [task for task in _unowned_trigger_tasks if not task.done()]
    if owner is not None:
        tasks += [task for task in get_turn_state(owner).trigger_tasks if not task.done()]
    return tasks


def drop_turn_state(websocket) -> None:
    """连接断开时释放该连接的回合状态（避免残留的触发器任务登记）。"""
    _states.pop(websocket, None)


def reset() -> None:
    """清空所有连接状态（单测隔离用）。"""
    _states.clear()
    _unowned_trigger_tasks.clear()
