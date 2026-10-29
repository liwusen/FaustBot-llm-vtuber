"""会话级（按 websocket 连接）的回合状态。

修复背景
--------
主 Agent 的中断信号此前是进程级全局单例（``runtime.state._agent_abort`` 每轮被
``reset_abort_event()`` 整体替换），强制打断标记同样是 ``routes.chat`` 的模块级
全局。多连接（Electron 重连、多窗口、触发器复用活动连接）会互相踩：

* 连接 B 后开一轮会把全局 abort event 换成新的，于是 A 的 ``interrupt`` 设的是
  **B 的** event —— B 的流被 A 打断，A 自己的生产者却拿着旧对象永远等不到信号；
* 全局 ``_force_interrupting`` 会让任何连接被取消的流都改发 ``done(forced=True)``；
* 触发器任务登记槽位（已随调度器落地删除）曾让插话打到别的连接正在跑的任务上。

这里把两样按连接（websocket）持有的东西留下：每个连接一份 abort event /
强制打断标记，以及本连接当前回合的**调度器句柄**。执行位与队列本身归调度器
（``runtime/scheduler.py``）统一仲裁，不再按连接登记任务。

``events.ignore_trigger_event`` 与 ``turn_gate`` 保持进程级：它们的语义本来就是
「整机是否正在和用户对话」「上一轮回复播出去了没有」，不属于单个连接。

仅由事件循环内的协程使用，不加锁（与 turn_gate 一致）。
"""
from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnState:
    """一个 chat websocket 连接上的回合状态。"""

    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    # 正在执行用户插话强制打断：前台触发器被取消时改发 done(forced) 而非 interrupted
    force_interrupting: bool = False
    # 本连接当前回合的调度器句柄（runtime/scheduler.TurnHandle）：
    # interrupt 通过它撤单/停跑，用户回合因此随连接生死（D21）
    turn_handle: Any = None

    def begin_turn(self) -> asyncio.Event:
        """开始新一轮：换一个全新的 abort event 并返回，供生产者轮询。

        每轮一个全新对象，避免上一轮遗留的 set 状态把新一轮立刻打断。
        """
        self.abort_event = asyncio.Event()
        return self.abort_event

    def interrupt(self) -> None:
        """本连接收到 interrupt：只中止本连接这一轮。"""
        self.abort_event.set()
        handle = self.turn_handle
        if handle is not None and not handle.is_terminal():
            handle.cancel("user_interrupt")


_states: "weakref.WeakKeyDictionary[object, TurnState]" = weakref.WeakKeyDictionary()


def get_turn_state(websocket) -> TurnState:
    """返回该 websocket 的回合状态（首次访问时创建）。"""
    turn = _states.get(websocket)
    if turn is None:
        turn = TurnState()
        _states[websocket] = turn
    return turn


def drop_turn_state(websocket) -> None:
    """连接断开时释放该连接的回合状态。"""
    _states.pop(websocket, None)


def reset() -> None:
    """清空所有连接状态（单测隔离用）。"""
    _states.clear()
