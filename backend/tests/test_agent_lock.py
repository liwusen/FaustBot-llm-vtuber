"""主 Agent 锁行为回归测试。

覆盖 Fix Plan（锁死锁与等待反馈）的关键契约：
1. 流式锁解耦：消费者暂停/停止读取时，锁只覆盖 LLM 会话本身，不被前端节拍劫持。
2. 锁超时兜底：不可重入二次获取/持锁方阻塞时，按 AGENT_LOCK_TIMEOUT 报错并可恢复。
3. waiting_lock 事件：流式消费第一项即排队反馈。
4. 中断/错误传播后锁必然释放。
"""
import asyncio
import os
import sys

import pytest
from langchain_core.messages import AIMessageChunk

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from faust_backend.runtime import lifecycle
from faust_backend.runtime import state

# 模块级 asyncio.Lock 绑定首个事件循环：全部用例共享同一循环
pytestmark = pytest.mark.asyncio(loop_scope="module")


class FakeStreamAgent:
    """最小 astream_events 假 Agent：产出 delta 事件，可选在 gate 上模拟长任务。"""

    def __init__(self, gate: asyncio.Event | None = None, texts=("你好",), error: Exception | None = None):
        self.gate = gate
        self.texts = texts
        self.error = error

    @staticmethod
    def _chunk(text: str):
        return AIMessageChunk(content=text)

    async def astream_events(self, payload, config=None, version="v2"):
        if self.error is not None:
            raise self.error
        for text in self.texts:
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": self._chunk(text)},
            }
            if self.gate is not None:
                await self.gate.wait()


class FakeInvokeAgent:
    def __init__(self, error: Exception | None = None):
        self.error = error

    async def ainvoke(self, payload, config=None):
        if self.error is not None:
            raise self.error
        return {"messages": [{"content": "ok"}]}


async def _next(agen):
    return await agen.__anext__()


async def _wait_lock_released(timeout: float = 3.0) -> bool:
    for _ in range(int(timeout / 0.02)):
        if not state.agent_lock.locked():
            return True
        await asyncio.sleep(0.02)
    return not state.agent_lock.locked()


@pytest.fixture
def lock_timeout_env():
    """缩短锁超时并在结束后恢复环境、确保锁释放。"""
    original = state.AGENT_LOCK_TIMEOUT
    state.AGENT_LOCK_TIMEOUT = 0.1
    yield
    state.AGENT_LOCK_TIMEOUT = original
    if state.agent_lock.locked():
        state.agent_lock.release()
    state.mark_agent_lock_released()


async def test_waiting_lock_event_emitted_first():
    """流式消费第一项必须是 waiting_lock（前端排队反馈的契约）。"""
    agen = lifecycle.stream_chat_agent_events(FakeStreamAgent(texts=("你好",)), {"messages": []})
    try:
        first = await _next(agen)
        assert first == {"type": "waiting_lock"}
        delta = await _next(agen)
        assert delta["type"] == "delta"
        assert delta["content"] == "你好"
    finally:
        await agen.aclose()
    assert await _wait_lock_released()


async def test_consumer_pause_does_not_hold_lock():
    """消费者暂停读取时锁必须已释放（修复：前端节拍劫持全局锁）。"""
    agen = lifecycle.stream_chat_agent_events(
        FakeStreamAgent(texts=("一", "二", "三")), {"messages": []}
    )
    try:
        assert (await _next(agen))["type"] == "waiting_lock"
        delta = await _next(agen)
        assert delta["type"] == "delta"
        # 故意不推进消费（模拟前端停止读取/hook 阻塞）
        await asyncio.sleep(0.3)
        assert not state.agent_lock.locked()
    finally:
        await agen.aclose()
    assert await _wait_lock_released()


async def test_invoke_agent_locked_timeout_and_recovery(lock_timeout_env):
    """锁被占时按超时报错并报告持有者；释放后可正常恢复调用。"""
    await state.agent_lock.acquire()
    state.mark_agent_lock_acquired("test_holder")
    try:
        with pytest.raises(RuntimeError) as exc_info:
            await lifecycle.invoke_agent_locked(FakeInvokeAgent(), {"messages": []})
        msg = str(exc_info.value)
        assert "等待主 Agent 锁超时" in msg
        assert "test_holder" in msg
    finally:
        state.agent_lock.release()
        state.mark_agent_lock_released()

    res = await lifecycle.invoke_agent_locked(FakeInvokeAgent(), {"messages": []})
    assert res["messages"][-1]["content"] == "ok"
    assert not state.agent_lock.locked()


async def test_stream_timeout_when_lock_held(lock_timeout_env):
    """流式路径等待锁超时：先收到 waiting_lock，随后 RuntimeError，锁不被破坏。"""
    await state.agent_lock.acquire()
    state.mark_agent_lock_acquired("test_holder")
    agen = lifecycle.stream_chat_agent_events(FakeStreamAgent(), {"messages": []})
    try:
        assert (await _next(agen))["type"] == "waiting_lock"
        with pytest.raises(RuntimeError) as exc_info:
            await _next(agen)
        assert "等待主 Agent 锁超时" in str(exc_info.value)
    finally:
        state.agent_lock.release()
        state.mark_agent_lock_released()
        await agen.aclose()
    assert not state.agent_lock.locked()


async def test_stream_error_propagates_and_releases_lock():
    """生产者异常经队列重抛给消费者，且锁必然释放。"""
    agen = lifecycle.stream_chat_agent_events(
        FakeStreamAgent(error=ValueError("boom")), {"messages": []}
    )
    try:
        assert (await _next(agen))["type"] == "waiting_lock"
        with pytest.raises(ValueError, match="boom"):
            await _next(agen)
    finally:
        await agen.aclose()
    assert not state.agent_lock.locked()


async def test_stream_abort_propagates_cancelled():
    """abort_event 触发后消费者收到 CancelledError，锁释放。"""
    abort = asyncio.Event()
    gate = asyncio.Event()
    agen = lifecycle.stream_chat_agent_events(
        FakeStreamAgent(gate=gate, texts=("一", "二")), {"messages": []}, abort_event=abort
    )
    try:
        assert (await _next(agen))["type"] == "waiting_lock"
        assert (await _next(agen))["type"] == "delta"
        abort.set()
        gate.set()  # 唤醒生产者使其在下一事件检查 abort
        with pytest.raises(asyncio.CancelledError):
            await _next(agen)
    finally:
        await agen.aclose()
    assert await _wait_lock_released()
