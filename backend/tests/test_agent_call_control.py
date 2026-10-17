"""Agent 调用控制面回归测试（方案 1：P0-B / P1-E + /clear 自锁修复）。

覆盖：
1. 生产者若在「等主 Agent 锁」期间被外部取消，消费者必须收到终结事件，
   而不是永久停在 ``queue.get()`` 上（修复前会一直转圈）。
2. ``rebuild_runtime`` 拿不到主 Agent 锁时返回 busy，且**不**破坏运行中的状态
   （修复前是无超时 ``async with``，会把调用方无限期挂住）。
3. ``/clear`` / ``/compact`` 不再阻塞 WS 接收循环：锁被占用时立即返回，
   由后台任务自行推送事件流；/compact 真的会去抢主 Agent 锁。
4. **``lock_held`` 语义**：调用方已持锁时，嵌套的 Agent 调用/重建引导对话
   不得再次获取同一把锁（asyncio.Lock 不可重入）。这条链曾在 `/clear`
   上把后端锁死 10 分钟（"启动后无法对话"）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest
from langchain_core.messages import AIMessageChunk

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import faust_backend.config_loader as conf
import faust_backend.routes.chat as chat
from faust_backend.runtime import lifecycle, state

pytestmark = pytest.mark.asyncio(loop_scope="module")


class FakeStreamAgent:
    def __init__(self, gate: asyncio.Event | None = None, texts=("你好",)):
        self.gate = gate
        self.texts = texts

    async def astream_events(self, payload, config=None, version="v2"):
        for text in self.texts:
            yield {
                "event": "on_chat_model_stream",
                "data": {"chunk": AIMessageChunk(content=text)},
            }
            if self.gate is not None:
                await self.gate.wait()


class FakeInvokeAgent:
    """假 Agent：ainvoke 立即返回，并记录调用次数。"""

    def __init__(self):
        self.calls: list[dict] = []

    async def ainvoke(self, payload, config=None):
        self.calls.append(payload)
        return {"messages": [{"content": "ok"}]}


class FakeCheckpointer:
    def __init__(self):
        self.deleted: list[str] = []

    async def adelete_thread(self, thread_id: str) -> None:
        self.deleted.append(thread_id)


class FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str):
        self.sent.append(json.loads(raw))


@pytest.fixture
def short_lock_timeout(monkeypatch):
    monkeypatch.setattr(state, "AGENT_LOCK_TIMEOUT", 0.2)
    yield
    if state.agent_lock.locked():
        state.agent_lock.release()
    state.mark_agent_lock_released()


async def _wait_for_event(ws: FakeWebSocket, event_type: str) -> None:
    for _ in range(200):
        if any(m.get("type") == event_type for m in ws.sent):
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"未等到事件 {event_type}，已收到: {[m.get('type') for m in ws.sent]}")


async def test_producer_cancelled_while_waiting_lock_terminates_consumer(monkeypatch):
    """生产者停在等锁处被外部取消：消费者收到终结事件而不是永久等待。"""
    monkeypatch.setattr(state, "AGENT_LOCK_TIMEOUT", 30.0)
    await state.agent_lock.acquire()
    state.mark_agent_lock_acquired("test_holder")
    agen = lifecycle.stream_chat_agent_events(FakeStreamAgent(), {"messages": []})
    try:
        assert (await agen.__anext__())["type"] == "waiting_lock"
        # 推进生成器让它创建生产者；生产者此刻停在 acquire_agent_lock 上
        consumer = asyncio.create_task(agen.__anext__())
        await asyncio.sleep(0.1)
        producers = [
            t for t in asyncio.all_tasks()
            if t.get_name() == "stream_agent_producer" and not t.done()
        ]
        assert producers, "生产者任务未找到"
        for task in producers:
            task.cancel()
        with pytest.raises(RuntimeError, match="terminal event"):
            await asyncio.wait_for(consumer, 5)
    finally:
        state.agent_lock.release()
        state.mark_agent_lock_released()
        await agen.aclose()
    assert not state.agent_lock.locked()


async def test_rebuild_runtime_busy_keeps_runtime_intact(
    short_lock_timeout, monkeypatch, hold_agent_lock
):
    """锁被占用时重建返回 busy，且不清空 state.agent（锁忙 ≠ 运行时损坏）。"""
    sentinel = object()
    monkeypatch.setattr(state, "agent", sentinel)
    async with hold_agent_lock("test_holder"):
        info = await lifecycle.rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    assert info["ready"] is False
    assert info["status"] == "busy"
    assert "等待主 Agent 锁超时" in info["error"]
    assert state.agent is sentinel


async def test_clear_command_does_not_block_receive_loop(short_lock_timeout, monkeypatch):
    """/clear 在 Agent 忙时立刻返回（转后台执行），不再把接收循环挂住。"""
    ws = FakeWebSocket()
    # 锁被占用时绝不应进入真正的清空/重建主体
    monkeypatch.setattr(chat, "_clear_session_locked", _unreachable)

    await state.agent_lock.acquire()
    state.mark_agent_lock_acquired("test_holder")
    try:
        started = time.monotonic()
        handled, reply = await chat._handle_slash_command("/clear", ws)
        elapsed = time.monotonic() - started
        assert handled is True
        assert reply is None  # 事件流由后台任务自行推送
        assert elapsed < 1.0
        await _wait_for_event(ws, "done")
    finally:
        state.agent_lock.release()
        state.mark_agent_lock_released()

    types = [m.get("type") for m in ws.sent]
    assert types[:1] == ["start"]
    delta = "".join(
        str(m.get("content") or "") for m in ws.sent if m.get("type") == "delta"
    )
    assert "正忙" in delta


async def test_compact_command_takes_agent_lock_in_background(short_lock_timeout, monkeypatch):
    """/compact 不阻塞接收循环，且真的会去抢主 Agent 锁（拿不到就报错收尾）。"""
    ws = FakeWebSocket()
    monkeypatch.setattr(state, "compact_middleware", object())  # 只要不是 None 即可
    await state.agent_lock.acquire()
    state.mark_agent_lock_acquired("test_holder")
    try:
        started = time.monotonic()
        handled, reply = await chat._handle_slash_command("/compact", ws)
        elapsed = time.monotonic() - started
        assert handled is True and reply is None
        assert elapsed < 1.0
        await _wait_for_event(ws, "error")
    finally:
        state.agent_lock.release()
        state.mark_agent_lock_released()

    types = [m.get("type") for m in ws.sent]
    assert "compact_done" in types
    error = next(m for m in ws.sent if m.get("type") == "error")
    assert "等待主 Agent 锁超时" in str(error.get("error"))


async def _unreachable() -> str:  # pragma: no cover - 必须不被调用
    raise AssertionError("锁被占用时不应进入清空/重建主体")


# ── lock_held 语义（/clear 自锁回归） ──


async def test_invoke_agent_locked_with_lock_held_does_not_reacquire(short_lock_timeout):
    """调用方已持锁时，嵌套的 Agent 调用不得再次获取同一把锁。"""
    agent = FakeInvokeAgent()
    await state.agent_lock.acquire()
    state.mark_agent_lock_acquired("slash_clear")
    try:
        res = await asyncio.wait_for(
            lifecycle.invoke_agent_locked(agent, {"messages": []}, lock_held=True),
            timeout=5,
        )
        assert state.agent_lock.locked(), "内层调用释放了调用方的锁"
    finally:
        state.agent_lock.release()
        state.mark_agent_lock_released()
    assert res["messages"][-1]["content"] == "ok"
    assert agent.calls


async def test_acquire_agent_lock_rejects_reentrancy(short_lock_timeout):
    """同一任务二次获取必须立刻报"不可重入"，而不是卡满 AGENT_LOCK_TIMEOUT。"""
    await state.agent_lock.acquire()
    state.mark_agent_lock_acquired("outer")
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="不可重入"):
            await lifecycle.acquire_agent_lock("inner")
        assert time.monotonic() - started < 1.0
    finally:
        state.agent_lock.release()
        state.mark_agent_lock_released()


async def test_clear_success_path_completes_and_releases_lock(monkeypatch):
    """/clear 成功路径：锁空闲时必须跑完（清空→重建→引导对话）并释放锁。"""
    ws = FakeWebSocket()
    checkpointer = FakeCheckpointer()
    cleared: list[bool] = []
    monkeypatch.setattr(state, "checkpointer", checkpointer)
    monkeypatch.setattr(state, "subagent_manager", None)
    monkeypatch.setattr(state, "plugin_manager", None)
    monkeypatch.setattr(state, "agent", FakeInvokeAgent())
    monkeypatch.setattr(chat, "reset_output_store", lambda **kw: cleared.append(True))
    monkeypatch.setattr(state, "AGENT_LOCK_TIMEOUT", 0.3)

    async def fake_rebuild(*, reset_dialog=False, no_initial_chat=False, lock_held=False):
        # 真实 rebuild 在临界区之外还会发起一次引导对话：必须遵守同一个 lock_held
        assert lock_held is True
        await lifecycle.invoke_agent_locked(
            state.agent, {"messages": []}, lock_held=lock_held
        )
        return {"ready": True, "status": "ready", "error": ""}

    monkeypatch.setattr(chat, "rebuild_runtime", fake_rebuild)

    handled, reply = await chat._handle_slash_command("/clear", ws)
    assert handled is True and reply is None
    await _wait_for_event(ws, "done")
    await asyncio.sleep(0.05)

    assert cleared, "清空副作用没有执行"
    assert checkpointer.deleted == [str(state.THREAD_ID)]
    assert not state.agent_lock.locked()
    delta = "".join(
        str(m.get("content") or "") for m in ws.sent if m.get("type") == "delta"
    )
    assert "已清空当前会话" in delta


class FakeProviders:
    main_model = "test::model"


class FakeMcpManager:
    def load_config(self, servers) -> None:
        self.servers = servers

    async def sync_servers(self) -> None:
        return None


class FakeAraya:
    def refresh_target_agent(self) -> None:
        return None


def _stub_rebuild_deps(monkeypatch) -> FakeInvokeAgent:
    """把 rebuild 的重活（配置/模板/MCP/Araya/VFS/agent 创建）替身化。

    只保留真正出问题的结构：一把锁 + 临界区 + 临界区**之外**的引导对话。
    """
    import faust_backend.admin_runtime as admin_runtime
    import faust_backend.llm_tools as llm_tools
    import faust_backend.mcp_manager as mcp_manager
    import faust_backend.provider as provider

    fake_agent = FakeInvokeAgent()
    monkeypatch.setattr(conf, "reload_configs", lambda: None)
    monkeypatch.setattr(
        provider, "get_main_credentials", lambda providers: ("k", "k", "https://x")
    )
    monkeypatch.setattr(provider, "get_default_subagent_model", lambda providers: "test::sub")
    monkeypatch.setattr(state, "AGENT_NAME", state.AGENT_NAME)
    monkeypatch.setattr(state, "AGENT_ROOT", state.AGENT_ROOT)
    monkeypatch.setattr(admin_runtime, "sync_template_files", lambda name: {})
    monkeypatch.setattr(state, "makeup_init_prompt", lambda: None)
    monkeypatch.setattr(llm_tools, "refresh_runtime_paths", lambda: None)
    monkeypatch.setattr(state, "get_model_providers", lambda: FakeProviders())
    monkeypatch.setattr(lifecycle.args, "save_in_memory", True)
    monkeypatch.setattr(state, "plugin_manager", None)
    # set_runtime_state 会改这几个全局，测完还原
    monkeypatch.setattr(state, "RUNTIME_READY", state.RUNTIME_READY)
    monkeypatch.setattr(state, "RUNTIME_STATUS", state.RUNTIME_STATUS)
    monkeypatch.setattr(state, "RUNTIME_ERROR", state.RUNTIME_ERROR)
    monkeypatch.setattr(state, "PROMPT", state.PROMPT)

    async def _vfs(refresh=False):
        return object()

    async def _refresh_nodes(vfs):
        return None

    async def _create_agent(**kwargs):
        return fake_agent

    async def _rebuild_subagents(**kwargs):
        return None

    monkeypatch.setattr(lifecycle, "get_faustbot_vfs", _vfs)
    monkeypatch.setattr(lifecycle, "refresh_runtime_nodes", _refresh_nodes)
    monkeypatch.setattr(lifecycle, "_create_agent_with_extensions", _create_agent)
    monkeypatch.setattr(lifecycle, "_rebuild_subagent_manager", _rebuild_subagents)
    monkeypatch.setattr(mcp_manager, "get_mcp_manager", lambda: FakeMcpManager())
    monkeypatch.setattr(
        lifecycle.araya_runtime, "get_araya_runtime", lambda refresh=False: FakeAraya()
    )
    return fake_agent


async def test_rebuild_with_lock_held_runs_bootstrap_without_self_lock(monkeypatch):
    """调用方持锁时 rebuild 的引导对话不得再获取锁（/clear 自锁的完整回归）。"""
    fake_agent = _stub_rebuild_deps(monkeypatch)
    monkeypatch.setattr(state, "AGENT_LOCK_TIMEOUT", 0.3)

    async with lifecycle.TimedAgentLock("slash_clear"):
        info = await asyncio.wait_for(
            lifecycle.rebuild_runtime(
                reset_dialog=True, no_initial_chat=False, lock_held=True
            ),
            timeout=10,
        )
        assert state.agent_lock.locked(), "重建过程释放了调用方的锁"

    assert info["ready"] is True, info
    assert info["initial_chat_skipped"] is False, "引导对话被跳过"
    assert fake_agent.calls, "引导对话没有被调用"
    assert not state.agent_lock.locked()
