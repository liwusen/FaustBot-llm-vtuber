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

import faust_backend.core.config as conf
from faust_backend.api.chat import chat as chat
from faust_backend.agent.runtime import rebuild
from faust_backend.agent.runtime import state
from faust_backend.agent.runtime.invoke import (
    acquire_agent_lock,
    invoke_agent_locked,
    stream_chat_agent_events,
)
from faust_backend.agent.runtime.rebuild import rebuild_runtime
from faust_backend.agent.prompt.seed import _seed_system_prompt

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


class FakeState:
    """`aget_state` 的返回值：只需要 `.values["messages"]`。"""

    def __init__(self, messages: list | None = None):
        self.values = {"messages": list(messages or [])}


class FakeInvokeAgent:
    """假 Agent：ainvoke 立即返回，并记录调用；支持 checkpoint 读写。

    `aupdate_state` 按 `add_messages` 的语义处理 `RemoveMessage(REMOVE_ALL)`
    并追加其余消息，这样 `_seed_system_prompt` 的「先清空再重排」能被如实模拟。
    """

    def __init__(self, messages: list | None = None):
        self.calls: list[dict] = []
        self.updates: list[dict] = []
        self.messages: list = list(messages or [])

    async def ainvoke(self, payload, config=None):
        self.calls.append(payload)
        return {"messages": [{"content": "ok"}]}

    async def aget_state(self, config=None):
        return FakeState(self.messages)

    async def aupdate_state(self, config, update, as_node=None):
        from langchain_core.messages import RemoveMessage
        from langgraph.graph.message import REMOVE_ALL_MESSAGES

        self.updates.append(update)
        for message in update.get("messages") or []:
            if isinstance(message, RemoveMessage) and message.id == REMOVE_ALL_MESSAGES:
                self.messages = []
                continue
            self.messages.append(message)
        return None


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
    agen = stream_chat_agent_events(FakeStreamAgent(), {"messages": []})
    try:
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
        info = await rebuild_runtime(reset_dialog=False, no_initial_chat=True)
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


# ── 调度器回合内的自锁回归（/clear 曾经锁死 10 分钟） ──


async def test_invoke_agent_locked_inside_scheduled_turn_does_not_reacquire(short_lock_timeout):
    """调度器回合内嵌套的 Agent 调用不得再次获取同一把锁。

    锁由调度器 worker 持有；回合体内的 ``invoke_agent_locked`` 走"已持锁"路径
    （``acquire_agent_lock`` 在临界区内是 no-op），因此在短超时下也必须立刻返回。
    """
    from faust_backend.agent.runtime import scheduler as agent_scheduler

    agent = FakeInvokeAgent()
    scheduler = agent_scheduler.AgentScheduler()
    seen: list[bool] = []

    async def _run():
        seen.append(state.agent_lock.locked())
        return await invoke_agent_locked(agent, {"messages": []})

    handle = scheduler.submit(
        agent_scheduler.AgentRequest(
            agent_scheduler.TurnKind.USER, _run, label="exclusive", gate="exempt",
            preemptible=False,
        )
    )
    res = await asyncio.wait_for(handle.wait(), timeout=5)
    assert res["messages"][-1]["content"] == "ok"
    assert seen == [True], "回合执行期间主 Agent 锁必须由 worker 持有"
    assert agent.calls
    await scheduler.shutdown(timeout=1.0)
    assert not state.agent_lock.locked()


async def test_acquire_agent_lock_rejects_reentrancy(short_lock_timeout):
    """同一任务二次获取必须立刻报"不可重入"，而不是卡满 AGENT_LOCK_TIMEOUT。"""
    await state.agent_lock.acquire()
    state.mark_agent_lock_acquired("outer")
    try:
        started = time.monotonic()
        with pytest.raises(RuntimeError, match="不可重入"):
            await acquire_agent_lock("inner")
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

    async def fake_rebuild(*, reset_dialog=False, no_initial_chat=False):
        # 真实 rebuild 在同一个临界区里还会发起一次引导对话：绝不能再取一次锁
        await invoke_agent_locked(state.agent, {"messages": []})
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
    import faust_backend.services.admin as admin_runtime
    import faust_backend.llm_tools as llm_tools
    import faust_backend.integrations.mcp as mcp_manager
    import faust_backend.llm.provider as provider

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
    monkeypatch.setattr(rebuild.args, "save_in_memory", True)
    monkeypatch.setattr(state, "plugin_manager", None)
    # set_runtime_state 会改这几个全局，测完还原
    monkeypatch.setattr(state, "RUNTIME_READY", state.RUNTIME_READY)
    monkeypatch.setattr(state, "RUNTIME_STATUS", state.RUNTIME_STATUS)
    monkeypatch.setattr(state, "RUNTIME_ERROR", state.RUNTIME_ERROR)
    monkeypatch.setattr(state, "PROMPT", "TEST SYSTEM PROMPT")

    async def _vfs(refresh=False):
        return object()

    async def _refresh_nodes(vfs):
        return None

    async def _create_agent(**kwargs):
        return fake_agent

    async def _rebuild_subagents(**kwargs):
        return None

    monkeypatch.setattr(rebuild, "get_faustbot_vfs", _vfs)
    monkeypatch.setattr(rebuild, "refresh_runtime_nodes", _refresh_nodes)
    monkeypatch.setattr(rebuild, "_create_agent_with_extensions", _create_agent)
    monkeypatch.setattr(rebuild, "_rebuild_subagent_manager", _rebuild_subagents)
    monkeypatch.setattr(mcp_manager, "get_mcp_manager", lambda: FakeMcpManager())
    monkeypatch.setattr(
        rebuild.araya_runtime, "get_araya_runtime", lambda refresh=False: FakeAraya()
    )
    return fake_agent


async def test_rebuild_inside_exclusive_turn_seeds_prompt_without_self_lock(monkeypatch):
    """调度器回合内 rebuild 的引导步骤不得再取锁（/clear 自锁的完整回归）。

    斜杠命令现在跑在 ``exclusive`` 回合里：锁由 worker 持有，rebuild 内部的
    ``TimedAgentLock`` 与引导写入都必须是 no-op，重建结束时锁归 worker 释放。

    `reset_dialog=True` 的引导是**纯状态写入**（植入角色 Prompt），不发起模型
    调用：system-only 的模型请求在 Gemini/Anthropic 上非法（contents are
    required / messages 不能为空），历史实现因此把 /clear 变成必然降级。
    """
    from faust_backend.agent.runtime import scheduler as agent_scheduler

    fake_agent = _stub_rebuild_deps(monkeypatch)
    monkeypatch.setattr(state, "AGENT_LOCK_TIMEOUT", 0.3)
    scheduler = agent_scheduler.AgentScheduler()
    locked_during: list[bool] = []

    async def _run():
        info = await rebuild_runtime(reset_dialog=True, no_initial_chat=False)
        locked_during.append(state.agent_lock.locked())
        return info

    handle = scheduler.submit(
        agent_scheduler.AgentRequest(
            agent_scheduler.TurnKind.USER, _run, label="exclusive", gate="exempt",
            preemptible=False,
        )
    )
    info = await asyncio.wait_for(handle.wait(), timeout=10)

    assert info["ready"] is True, info
    assert info["initial_chat_skipped"] is False, "引导流程被跳过"
    assert fake_agent.calls == [], "reset_dialog 不得发起模型调用（system-only 非法）"
    assert fake_agent.updates, "引导流程必须写入 checkpoint"
    from langchain_core.messages import SystemMessage

    assert isinstance(fake_agent.messages[0], SystemMessage)
    assert fake_agent.messages[0].content == state.PROMPT
    assert locked_during == [True], "重建过程中锁必须由调度器持有"
    await scheduler.shutdown(timeout=1.0)
    assert not state.agent_lock.locked()


# ── 角色 Prompt 植入（reset_dialog 的引导步骤） ──


async def test_seed_system_prompt_writes_on_empty_checkpoint(monkeypatch):
    from langchain_core.messages import SystemMessage

    monkeypatch.setattr(state, "PROMPT", "角色设定")
    agent = FakeInvokeAgent()

    seeded = await _seed_system_prompt(agent)

    assert seeded is True
    assert agent.calls == []
    assert len(agent.messages) == 1
    assert isinstance(agent.messages[0], SystemMessage)
    assert agent.messages[0].content == "角色设定"


async def test_seed_system_prompt_is_idempotent(monkeypatch):
    from langchain_core.messages import HumanMessage, SystemMessage

    monkeypatch.setattr(state, "PROMPT", "角色设定")
    agent = FakeInvokeAgent(
        messages=[SystemMessage(content="角色设定"), HumanMessage(content="hi")]
    )

    seeded = await _seed_system_prompt(agent)

    assert seeded is False
    assert agent.updates == []
    assert [type(m).__name__ for m in agent.messages] == ["SystemMessage", "HumanMessage"]


async def test_seed_system_prompt_replaces_stale_system_and_keeps_history(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

    monkeypatch.setattr(state, "PROMPT", "新设定")
    agent = FakeInvokeAgent(
        messages=[
            SystemMessage(content="旧设定"),
            HumanMessage(content="hi"),
            AIMessage(content="hello"),
        ]
    )

    seeded = await _seed_system_prompt(agent)

    assert seeded is True
    assert [type(m).__name__ for m in agent.messages] == [
        "SystemMessage",
        "HumanMessage",
        "AIMessage",
    ]
    assert agent.messages[0].content == "新设定"
    assert agent.messages[1].content == "hi"
    assert agent.calls == []


async def test_seed_system_prompt_skips_empty_prompt(monkeypatch):
    monkeypatch.setattr(state, "PROMPT", "")

    agent = FakeInvokeAgent()

    assert await _seed_system_prompt(agent) is False
    assert agent.updates == []
