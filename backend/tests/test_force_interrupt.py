"""用户插话 (AUTO_FORCE_INTERRUPT) 行为测试。

覆盖:
1. 触发器回合占用执行位时, 用户插话抢占它并给消息加"(用户插话)"前缀
2. AUTO_FORCE_INTERRUPT 关闭时保持旧行为
3. 无触发器忙状态时消息不变
4. _run_agent_stream 被强制取消时: 补发 done(forced) 而非 interrupted
5. 常规取消仍发 interrupted
6. 插话只抢占"触发器/批量"档, 不动正在跑的用户回合
7. 被取消方不响应取消时, 插话按调度器的取消上限放弃等待, 不永久挂住
"""
import asyncio
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import faust_backend.core.config as conf
import faust_backend.routes.chat as chat
from faust_backend.runtime import scheduler as agent_scheduler
from faust_backend.runtime import state
from faust_backend.runtime import turn_state
from langchain_core.messages import AIMessageChunk

pytestmark = pytest.mark.asyncio(loop_scope="module")


class FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str):
        self.sent.append(json.loads(raw))


@pytest.fixture
def reset_trigger_registry():
    turn_state.reset()
    yield
    turn_state.reset()
    if state.agent_lock.locked():
        state.agent_lock.release()
    state.mark_agent_lock_released()


async def _start_trigger_turn(scheduler, block: asyncio.Event) -> agent_scheduler.TurnHandle:
    """在调度器上起一个"正在跑"的触发器回合（阻塞在事件上）。"""

    async def _run():
        await block.wait()

    handle = scheduler.submit(
        agent_scheduler.AgentRequest(
            agent_scheduler.TurnKind.TRIGGER, _run, label="trigger", gate="exempt"
        )
    )
    for _ in range(200):
        if handle.status == agent_scheduler.TurnStatus.RUNNING and handle.task is not None:
            return handle
        await asyncio.sleep(0.01)
    raise AssertionError("触发器回合没有跑起来")


async def test_interjection_cancels_busy_trigger(reset_trigger_registry, monkeypatch):
    """触发器占用执行位时用户插话: 抢占该回合、消息加前缀。"""
    from faust_backend.runtime import frontend_presence

    frontend_presence.mark_online("conn")
    scheduler = agent_scheduler.AgentScheduler()
    monkeypatch.setattr(agent_scheduler, "get_scheduler", lambda: scheduler)
    block = asyncio.Event()
    handle = await _start_trigger_turn(scheduler, block)
    original = conf.AUTO_FORCE_INTERRUPT
    conf.AUTO_FORCE_INTERRUPT = True
    try:
        text = await chat._apply_user_interjection("你好", FakeWebSocket())
        assert text == "(用户插话)你好"
        assert handle.status == agent_scheduler.TurnStatus.INTERRUPTED
        assert handle.forced is True
    finally:
        conf.AUTO_FORCE_INTERRUPT = original
        block.set()
        await scheduler.shutdown(timeout=1.0)
        frontend_presence.reset()


async def test_interjection_disabled_keeps_old_behavior(reset_trigger_registry, monkeypatch):
    """AUTO_FORCE_INTERRUPT=False: 不抢占触发器, 消息不变。"""
    scheduler = agent_scheduler.AgentScheduler()
    monkeypatch.setattr(agent_scheduler, "get_scheduler", lambda: scheduler)
    block = asyncio.Event()
    handle = await _start_trigger_turn(scheduler, block)
    original = conf.AUTO_FORCE_INTERRUPT
    conf.AUTO_FORCE_INTERRUPT = False
    try:
        assert await chat._apply_user_interjection("你好", FakeWebSocket()) == "你好"
        assert handle.status == agent_scheduler.TurnStatus.RUNNING
    finally:
        conf.AUTO_FORCE_INTERRUPT = original
        block.set()
        await scheduler.shutdown(timeout=1.0)


async def test_interjection_no_busy_unchanged(reset_trigger_registry):
    """无触发器忙状态: 消息不变、不打断任何任务。"""
    original = conf.AUTO_FORCE_INTERRUPT
    conf.AUTO_FORCE_INTERRUPT = True
    try:
        assert await chat._apply_user_interjection("你好", FakeWebSocket()) == "你好"
    finally:
        conf.AUTO_FORCE_INTERRUPT = original


async def test_interjection_does_not_touch_running_user_turn(reset_trigger_registry, monkeypatch):
    """插话只抢占触发器/批量档：正在跑的用户回合不受影响。"""
    scheduler = agent_scheduler.AgentScheduler()
    monkeypatch.setattr(agent_scheduler, "get_scheduler", lambda: scheduler)
    block = asyncio.Event()

    async def _run():
        await block.wait()

    user = scheduler.submit(
        agent_scheduler.AgentRequest(
            agent_scheduler.TurnKind.USER, _run, label="user", gate="exempt"
        )
    )
    for _ in range(200):
        if user.status == agent_scheduler.TurnStatus.RUNNING and user.task is not None:
            break
        await asyncio.sleep(0.01)
    original = conf.AUTO_FORCE_INTERRUPT
    conf.AUTO_FORCE_INTERRUPT = True
    try:
        assert await chat._apply_user_interjection("你好", FakeWebSocket()) == "你好"
        assert user.status == agent_scheduler.TurnStatus.RUNNING
    finally:
        conf.AUTO_FORCE_INTERRUPT = original
        block.set()
        await scheduler.shutdown(timeout=1.0)


async def test_interjection_bounded_when_target_swallows_cancel(
    reset_trigger_registry, monkeypatch
):
    """被取消方吞掉取消时插话不无限等待，按调度器的取消上限放弃。"""
    monkeypatch.setattr(agent_scheduler, "TASK_CANCEL_TIMEOUT_SEC", 0.1)
    scheduler = agent_scheduler.AgentScheduler()
    monkeypatch.setattr(agent_scheduler, "get_scheduler", lambda: scheduler)

    async def stubborn():
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            await asyncio.sleep(0.4)  # 吞掉取消：模拟卡在不可取消区
            raise

    handle = scheduler.submit(
        agent_scheduler.AgentRequest(
            agent_scheduler.TurnKind.TRIGGER, stubborn, label="stubborn", gate="exempt"
        )
    )
    for _ in range(200):
        if handle.task is not None:
            break
        await asyncio.sleep(0.01)
    original = conf.AUTO_FORCE_INTERRUPT
    conf.AUTO_FORCE_INTERRUPT = True
    try:
        started = time.monotonic()
        assert await chat._apply_user_interjection("你好", FakeWebSocket()) == "(用户插话)你好"
        assert time.monotonic() - started < 1.0
    finally:
        conf.AUTO_FORCE_INTERRUPT = original
        if handle.task is not None:
            handle.task.cancel()
        await scheduler.shutdown(timeout=1.0)


class GateAgent:
    """astream_events 兼容假 Agent：产出一条 delta 后停在 gate 上（持锁长任务）。"""

    def __init__(self, gate: asyncio.Event):
        self.gate = gate

    async def astream_events(self, payload, config=None, version="v2"):
        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": AIMessageChunk(content="触发回复")},
        }
        await self.gate.wait()


async def test_force_cancelled_stream_sends_done_forced(reset_trigger_registry):
    """强制打断前台触发器流: 锁释放 + 补发 done(forced), 不发 interrupted。"""
    ws = FakeWebSocket()
    gate = asyncio.Event()
    task = asyncio.create_task(chat._run_agent_stream(ws, "触发任务", agent=GateAgent(gate)))
    await asyncio.sleep(0.3)  # 等生产者获锁并停在 gate（持锁）
    assert state.agent_lock.locked()
    # 插话标记在"发起方连接"的回合状态上，前台触发器的流读的正是同一份
    turn_state.get_turn_state(ws).force_interrupting = True
    task.cancel()
    await asyncio.wait_for(task, 5)
    types = [m.get("type") for m in ws.sent]
    assert "done" in types
    done_msg = next(m for m in ws.sent if m.get("type") == "done")
    assert done_msg.get("forced") is True
    assert "interrupted" not in types
    assert await asyncio.wait_for(_lock_released(), 3)


async def test_normal_cancel_sends_interrupted(reset_trigger_registry):
    """常规取消（非插话）: 仍发 interrupted。"""
    ws = FakeWebSocket()
    gate = asyncio.Event()
    task = asyncio.create_task(chat._run_agent_stream(ws, "触发任务", agent=GateAgent(gate)))
    await asyncio.sleep(0.3)
    task.cancel()
    await asyncio.wait_for(task, 5)
    types = [m.get("type") for m in ws.sent]
    assert "interrupted" in types
    assert not any(m.get("type") == "done" and m.get("forced") for m in ws.sent)


async def test_interrupt_of_one_connection_does_not_abort_another(reset_trigger_registry):
    """连接 A 的 interrupt 只影响 A 这一轮，不能打断 B 正在跑的流。"""
    ws_a, ws_b = FakeWebSocket(), FakeWebSocket()
    gate_a, gate_b = asyncio.Event(), asyncio.Event()
    task_a = asyncio.create_task(
        chat._run_agent_stream(ws_a, "A任务", agent=GateAgent(gate_a))
    )
    task_b = asyncio.create_task(
        chat._run_agent_stream(ws_b, "B任务", agent=GateAgent(gate_b))
    )
    turn_state.get_turn_state(ws_a).turn_handle = _TaskHandle(task_a)
    turn_state.get_turn_state(ws_b).turn_handle = _TaskHandle(task_b)
    await asyncio.sleep(0.4)
    try:
        # A 的中断：只取消 A 的任务与 A 的 abort event
        turn_state.get_turn_state(ws_a).interrupt()
        await asyncio.wait_for(task_a, 5)
        assert not task_b.done()
        assert not turn_state.get_turn_state(ws_b).abort_event.is_set()
    finally:
        turn_state.get_turn_state(ws_b).interrupt()
        gate_a.set()
        gate_b.set()
        await asyncio.gather(task_a, task_b, return_exceptions=True)


class _TaskHandle:
    """最小的调度器句柄替身：把 interrupt 映射到某个任务上（无调度器路径用）。"""

    def __init__(self, task: asyncio.Task):
        self.task = task

    def is_terminal(self) -> bool:
        return self.task.done()

    def cancel(self, reason: str = "") -> bool:
        self.task.cancel()
        return True


async def _lock_released() -> bool:
    for _ in range(150):
        if not state.agent_lock.locked():
            return True
        await asyncio.sleep(0.02)
    return not state.agent_lock.locked()
