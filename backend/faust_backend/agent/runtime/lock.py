"""主 Agent 锁：带超时兜底、可重入自检、调度器回合感知。

自 ``runtime/lifecycle.py`` 整块挖出（P4 布局拆分），逻辑一字未改。
"""

from __future__ import annotations

import asyncio

import faust_backend.agent.runtime.state as state
from faust_backend.core.log import get_logger

log = get_logger("faust.lifecycle")


async def acquire_agent_lock(owner: str) -> None:
    """获取主 Agent 锁，带超时兜底。超时说明出现了真死锁（不可重入二次获取、
    持锁路径被意外阻塞等），此时抛错并报告当前持有者，而不是无限等待。

    调度器临界区内（``scheduler.in_scheduled_turn()``）直接返回：锁已由调度器
    worker 为本回合持有，本任务只是它的子孙，二次获取必然自锁。这与"同一任务
    二次获取"不同——后者仍是真 bug，继续报错。

    同一任务二次获取会**立刻**报错：asyncio.Lock 不可重入，真等下去只会卡满
    AGENT_LOCK_TIMEOUT（/clear 曾经就这样把后端锁死 10 分钟：嵌套的 Agent 调用
    必须在调度器的同一个回合里执行，而不是二次取锁）。
    """
    from faust_backend.agent.runtime import scheduler

    if scheduler.in_scheduled_turn():
        return
    if state.agent_lock_owner_task is asyncio.current_task():
        raise RuntimeError(
            f"主 Agent 锁不可重入：当前任务已经持有它（持有者="
            f"[{state.agent_lock_owner or '未知'}]，新的请求方=[{owner}]）。"
            "调用链里嵌套了 Agent 调用，应放在调度器的同一个回合里执行。"
        )
    try:
        await asyncio.wait_for(state.agent_lock.acquire(), timeout=state.AGENT_LOCK_TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"等待主 Agent 锁超时（{state.AGENT_LOCK_TIMEOUT:g}s）：当前持有者 "
            f"[{state.agent_lock_owner or '未知'}]。这可能意味着持锁方已死锁或被阻塞。"
        ) from None
    state.mark_agent_lock_acquired(owner)


def release_agent_lock() -> None:
    """释放主 Agent 锁（**本调用点是否拥有它**）。

    调度器回合内是 no-op：锁归回合所有，由 worker（或流式生产者在 LLM 会话结束时
    提前）统一释放，嵌套的辅助调用（``TimedAgentLock`` 退出、``invoke_agent_locked``
    返回）不得把回合的锁放掉——否则回合中段会出现"锁空窗"，外部持锁方可以插进来。
    """
    from faust_backend.agent.runtime import scheduler

    if scheduler.in_scheduled_turn():
        return
    state.agent_lock.release()
    state.mark_agent_lock_released()


def release_llm_stream_lock() -> None:
    """流式生产者结束时的释放。

    回合内：LLM 会话一结束就提前放掉本回合的锁（交付阶段的 websocket 发送/插件
    hook 阻塞不再拖住全局锁——方案 1 的 P0 语义；worker 收尾时的释放是幂等的）。
    回合外：就是普通释放。
    """
    from faust_backend.agent.runtime import scheduler

    if scheduler.in_scheduled_turn():
        scheduler.release_turn_lock()
        return
    state.agent_lock.release()
    state.mark_agent_lock_released()


class AgentLockBusy(RuntimeError):
    """主 Agent 锁在超时内没有拿到（Agent 正忙，或持锁方僵死）。"""


class TimedAgentLock:
    """`async with` 形式的带超时主 Agent 锁。

    与 `acquire_agent_lock` 同一实现：超时抛 `AgentLockBusy`，而不是无限等待。
    修复前 rebuild 用的是裸 `async with state.agent_lock`，是全工程唯一一处
    无超时获取，把斜杠命令与管理接口都变成了可以永久挂起的调用。
    """

    __slots__ = ("owner",)

    def __init__(self, owner: str) -> None:
        self.owner = owner

    async def __aenter__(self) -> None:
        try:
            await acquire_agent_lock(self.owner)
        except RuntimeError as e:
            raise AgentLockBusy(str(e)) from None

    async def __aexit__(self, exc_type, exc, tb) -> None:
        release_agent_lock()
