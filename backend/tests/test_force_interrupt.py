"""用户插话 (AUTO_FORCE_INTERRUPT) 行为测试。

覆盖:
1. 触发器任务被注册后, 用户插话强制取消并给消息加"(用户插话)"前缀
2. AUTO_FORCE_INTERRUPT 关闭时保持旧行为
3. 无触发器忙状态时消息不变
4. _run_agent_stream 被强制取消时: 锁释放 + 补发 done(forced) 而非 interrupted
5. 常规取消仍发 interrupted
"""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import faust_backend.config_loader as conf
import faust_backend.routes.chat as chat
from faust_backend.runtime import state
from langchain_core.messages import AIMessageChunk

pytestmark = pytest.mark.asyncio(loop_scope="module")


class FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str):
        self.sent.append(json.loads(raw))


@pytest.fixture
def reset_trigger_registry():
    yield
    chat._active_trigger_task = None
    chat._force_interrupting = False
    if state.agent_lock.locked():
        state.agent_lock.release()
    state.mark_agent_lock_released()


async def test_interjection_cancels_busy_trigger(reset_trigger_registry):
    """触发器占锁时用户插话: 取消任务、锁释放、消息加前缀。"""
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    chat._register_trigger_task(task)
    original = conf.AUTO_FORCE_INTERRUPT
    conf.AUTO_FORCE_INTERRUPT = True
    try:
        text = await chat._apply_user_interjection("你好")
        assert text == "(用户插话)你好"
        assert task.cancelled()
        assert chat._active_trigger_task is None
    finally:
        conf.AUTO_FORCE_INTERRUPT = original
        gate.set()


async def test_interjection_disabled_keeps_old_behavior(reset_trigger_registry):
    """AUTO_FORCE_INTERRUPT=False: 不取消触发器, 消息不变。"""
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    chat._register_trigger_task(task)
    original = conf.AUTO_FORCE_INTERRUPT
    conf.AUTO_FORCE_INTERRUPT = False
    try:
        text = await chat._apply_user_interjection("你好")
        assert text == "你好"
        assert not task.done()
    finally:
        conf.AUTO_FORCE_INTERRUPT = original
        gate.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_interjection_no_busy_unchanged(reset_trigger_registry):
    """无触发器忙状态: 消息不变、不打断任何任务。"""
    original = conf.AUTO_FORCE_INTERRUPT
    conf.AUTO_FORCE_INTERRUPT = True
    try:
        assert await chat._apply_user_interjection("你好") == "你好"
    finally:
        conf.AUTO_FORCE_INTERRUPT = original


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
    chat._register_trigger_task(task)
    await asyncio.sleep(0.3)  # 等生产者获锁并停在 gate（持锁）
    assert state.agent_lock.locked()
    chat._force_interrupting = True
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


async def _lock_released() -> bool:
    for _ in range(150):
        if not state.agent_lock.locked():
            return True
        await asyncio.sleep(0.02)
    return not state.agent_lock.locked()
