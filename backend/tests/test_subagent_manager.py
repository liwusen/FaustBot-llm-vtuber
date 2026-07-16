import sys
from pathlib import Path

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import faust_backend.subagent_manager as subagent_manager


class FakeSaver:
    def __init__(self, conn=None):
        self.conn = conn
        self.setup_called = False

    async def setup(self):
        self.setup_called = True


class FakeAgent:
    def __init__(self, events=None):
        self.events = list(events or [])

    async def astream_events(self, payload, config=None, version=None):
        for event in self.events:
            yield event


@pytest.mark.asyncio
async def test_new_subagent_initializes_checkpointer_lazily(monkeypatch):
    calls = {}

    async def fake_connect(path):
        calls["path"] = path
        return object()

    def fake_create_agent(**kwargs):
        calls["kwargs"] = kwargs
        return FakeAgent()

    monkeypatch.setattr(subagent_manager.aiosqlite, "connect", fake_connect)
    monkeypatch.setattr(subagent_manager, "AsyncSqliteSaver", FakeSaver)
    monkeypatch.setattr(subagent_manager, "create_agent", fake_create_agent)

    manager = subagent_manager.SubagentManager()
    manager.setChatModel(object())

    await manager.newSubagent(agent_name="worker")

    assert calls["path"] == ":memory:"
    assert manager.checkpointer is not None
    assert manager.checkpointer.setup_called is True
    assert calls["kwargs"]["checkpointer"] is manager.checkpointer
    assert "worker" in manager.subagents


@pytest.mark.asyncio
async def test_new_subagent_rejects_unknown_toolset(monkeypatch):
    async def fake_connect(path):
        return object()

    monkeypatch.setattr(subagent_manager.aiosqlite, "connect", fake_connect)
    monkeypatch.setattr(subagent_manager, "AsyncSqliteSaver", FakeSaver)
    monkeypatch.setattr(subagent_manager, "create_agent", lambda **kwargs: FakeAgent())

    manager = subagent_manager.SubagentManager()
    manager.setChatModel(object())

    with pytest.raises(ValueError, match="Unknown toolset"):
        await manager.newSubagent(agent_name="worker", toolsetsNames=["missing"])


@pytest.mark.asyncio
async def test_ainvoke_subagent_rejects_when_lock_is_held():
    manager = subagent_manager.SubagentManager()
    manager.subagents["worker"] = subagent_manager.Subagent(name="worker", agent=FakeAgent())
    await manager.subagents["worker"].lock.acquire()

    stream = manager.ainvokeSubagent("worker", {"messages": []})
    with pytest.raises(RuntimeError, match="currently locked"):
        await stream.__anext__()

    manager.subagents["worker"].lock.release()


@pytest.mark.asyncio
async def test_abort_subagent_is_idempotent():
    manager = subagent_manager.SubagentManager()

    assert await manager.abortSubagent("missing") is False

    event = subagent_manager.Event()
    manager.abortEvents["worker"] = event

    assert await manager.abortSubagent("worker") is True
    assert event.is_set() is True


@pytest.mark.asyncio
async def test_ainvoke_subagent_streams_and_cleans_up():
    events = [
        {"event": "on_chat_model_stream", "data": {"chunk": type("Chunk", (), {"type": "ai", "content": "hello", "additional_kwargs": {}})()}},
        {"event": "on_tool_start", "name": "search", "data": {"input": '{"q":"x"}'}, "run_id": "call-1"},
        {"event": "on_tool_end", "name": "search", "data": {"output": {"ok": True}}, "run_id": "call-1"},
    ]
    manager = subagent_manager.SubagentManager()
    manager.subagents["worker"] = subagent_manager.Subagent(name="worker", agent=FakeAgent(events=events))

    got = []
    async for item in manager.ainvokeSubagent("worker", {"messages": []}):
        got.append(item)

    assert got == [
        {"type": "delta", "content": "hello"},
        {"type": "tool_start", "tool_name": "search", "args": {"q": "x"}, "call_id": "call-1"},
        {"type": "tool_result", "tool_name": "search", "output": {"ok": True}, "call_id": "call-1"},
    ]
    assert "worker" not in manager.abortEvents
    assert manager.subagents["worker"].lock.locked() is False
