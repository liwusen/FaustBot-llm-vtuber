import sys
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import faust_backend.subagent_manager as subagent_manager
from faust_backend.runtime import state as runtime_state
from faust_backend.tools.read import read
from faust_backend.routes import subagents as subagent_routes
from faust_backend.tools import subagent as subagent_tools
from faust_backend.runtime.uri import parse


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


def test_read_faustbot_subagent_resources(monkeypatch):
    manager = subagent_manager.SubagentManager()
    worker = subagent_manager.Subagent(name="worker", systemPrompt="demo prompt")
    worker.toolsetNames = ["BASESET"]
    worker.status = "running"
    worker.lastEvent = {"type": "tool_start", "tool_name": "search"}
    worker.finalResult = "done"
    worker.outputStore = [[
        {"messages": [{"role": "user", "content": "do work"}]},
        {"type": "reasoning_delta", "content": "think..."},
        {"type": "tool_start", "tool_name": "read", "args": {"uri": "foo"}},
        {"type": "tool_result", "tool_name": "read", "output": "bar"},
        {"type": "delta", "content": "hello"},
    ]]
    manager.newToolset("BASESET", [])
    manager.subagents["worker"] = worker

    monkeypatch.setattr(runtime_state, "subagent_manager", manager)

    overview = read.func("faustbot://subagents/worker")
    output = read.func("faustbot://subagents/worker/output.md")
    final_result = read.func("faustbot://subagents/worker/finalResult.md")
    index_doc = read.func("faustbot://subagenting.md")
    avatoolset = read.func("faustbot://avatoolset")

    assert "faustbot://subagents/worker/ 内容:\n  faustbot://subagents/worker/finalResult.md\n  faustbot://subagents/worker/output.md\n" in overview
    assert "# System Prompt Of Subagent(worker)" in output
    assert "# Run 1:Main Agent(yourself)" in output
    assert "> 思考:think..." in output
    assert "> 调用工具:read" in output
    assert "hello" in output
    assert '"uri": "foo"' not in output
    assert final_result == "done"
    assert "faustbot://subagents/{name}" in index_doc
    assert "# Available Toolsets" in avatoolset


@pytest.mark.asyncio
async def test_subagent_routes_list_and_delete(monkeypatch):
    manager = subagent_manager.SubagentManager()
    manager.subagents["worker"] = subagent_manager.Subagent(name="worker", status="idle")
    monkeypatch.setattr(runtime_state, "subagent_manager", manager)

    status_payload = await subagent_routes.subagents_status_api()
    assert status_payload["status"] == "ok"
    assert status_payload["items"][0]["name"] == "worker"

    delete_payload = await subagent_routes.delete_subagent_api("worker")
    assert delete_payload == {"status": "ok", "name": "worker", "removed": True}
    assert manager.list_statuses() == []


@pytest.mark.asyncio
async def test_new_subagent_tool_resolves_path_prompt(monkeypatch):
    manager = subagent_manager.SubagentManager()
    manager.setChatModel(object())

    async def fake_new_subagent(**kwargs):
        return {"name": kwargs["agent_name"], "status": "idle", "system_prompt_summary": kwargs["systemPrompt"]}

    monkeypatch.setattr(runtime_state, "subagent_manager", manager)
    monkeypatch.setattr(subagent_tools, "_require_subagent_manager", lambda: manager)
    monkeypatch.setattr(subagent_tools.read, "func", lambda uri: f"loaded:{uri}")
    monkeypatch.setattr(manager, "newSubagent", fake_new_subagent)

    raw = await subagent_tools.newSubagent.coroutine(name="writer", toolset_names=["BASESET"], sysPrompt="path:memory://prompts/demo.md")
    payload = json.loads(raw)
    assert payload["name"] == "writer"
    assert payload["system_prompt_summary"] == "loaded:memory://prompts/demo.md"


@pytest.mark.asyncio
async def test_invoke_subagent_tool_submits_background_job(monkeypatch):
    manager = subagent_manager.SubagentManager()
    monkeypatch.setattr(runtime_state, "subagent_manager", manager)
    monkeypatch.setattr(subagent_tools, "_require_subagent_manager", lambda: manager)
    async def fake_invoke(name, payload):
        return {"name": name, "status": "pending", "queued": payload}
    monkeypatch.setattr(manager, "invokeSubagent", fake_invoke)

    raw = await subagent_tools.invokeSubagent.coroutine(name="worker", message="hello")
    payload = json.loads(raw)
    assert payload["name"] == "worker"
    assert payload["status"] == "pending"
    assert payload["queued"]["messages"][0]["content"] == "hello"


@pytest.mark.asyncio
async def test_wait_for_subagent_tool_waits_for_completion(monkeypatch):
    manager = subagent_manager.SubagentManager()
    monkeypatch.setattr(runtime_state, "subagent_manager", manager)
    monkeypatch.setattr(subagent_tools, "_require_subagent_manager", lambda: manager)

    async def fake_wait(names):
        assert names == ["worker"]
        return [{"name": "worker", "status": "idle"}]

    monkeypatch.setattr(manager, "wait_for_subagents", fake_wait)

    raw = await subagent_tools.wait_for_subagent.coroutine(agent_name_list=["worker"])
    payload = json.loads(raw)
    assert payload == {"items": [{"name": "worker", "status": "idle"}]}


def test_negative_line_selector_parses_last_lines():
    parsed = parse("foo.md:-35--10")
    assert parsed.selector_lines == (-35, -10)
