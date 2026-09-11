"""统一模型重试中间件的接线与行为测试。

覆盖三处 Agent 构造点（主 Agent / Subagent / Araya）都挂载 ModelRetryMiddleware，
且瞬时故障确实被重试、重试耗尽后原样抛出。
"""

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from langchain.agents import create_agent
from langchain.agents.middleware import ModelRetryMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

import faust_backend.araya_runtime as araya_runtime
import faust_backend.subagent_manager as subagent_manager
from faust_backend.runtime import model_retry
from faust_backend.runtime.model_retry import with_model_retry
from faust_backend.runtime.tool_call_repair import ToolCallRepairMiddleware


class _FlakyModel(BaseChatModel):
    """前 fail_times 次调用抛瞬时异常，之后返回固定回复。"""

    fail_times: int = 0
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "flaky-test-model"

    def bind_tools(self, tools, **kwargs):  # type: ignore[override]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("transient model failure")
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="pong"))])


def _retries(middlewares) -> list:
    return [m for m in middlewares if isinstance(m, ModelRetryMiddleware)]


def test_with_model_retry_replaces_instead_of_stacking():
    stale = ModelRetryMiddleware()
    stack = with_model_retry([ToolCallRepairMiddleware(), stale])

    assert len(_retries(stack)) == 1
    # 旧实例被替换，重试位于最内层（最贴近真实模型调用）
    assert stack[-1] is not stale
    assert isinstance(stack[0], ToolCallRepairMiddleware)


@pytest.mark.asyncio
async def test_transient_model_failure_is_retried(monkeypatch):
    monkeypatch.setattr(model_retry, "INITIAL_DELAY", 0.0)
    model = _FlakyModel(fail_times=2)
    agent = create_agent(model=model, tools=[], middleware=with_model_retry())

    result = await agent.ainvoke({"messages": [{"role": "user", "content": "ping"}]})

    assert model.calls == model_retry.MAX_RETRIES + 1
    assert result["messages"][-1].content == "pong"


@pytest.mark.asyncio
async def test_exhausted_retries_raise_original_error(monkeypatch):
    monkeypatch.setattr(model_retry, "INITIAL_DELAY", 0.0)
    model = _FlakyModel(fail_times=99)
    agent = create_agent(model=model, tools=[], middleware=with_model_retry())

    with pytest.raises(ConnectionError):
        await agent.ainvoke({"messages": [{"role": "user", "content": "ping"}]})

    assert model.calls == model_retry.MAX_RETRIES + 1


def test_main_agent_composition_gets_retry_middleware(monkeypatch):
    import faust_backend.mcp_manager as mcp_manager
    from faust_backend.runtime import lifecycle, state

    class _FakeMcp:
        def get_langchain_tools(self):
            return []

    monkeypatch.setattr(state, "plugin_manager", None)
    monkeypatch.setattr(lifecycle.llm_tools, "get_tools_for_agent", lambda agent: [])
    monkeypatch.setattr(mcp_manager, "get_mcp_manager", lambda: _FakeMcp())

    tools, middlewares = lifecycle._compose_runtime_extensions()

    assert tools == []
    assert len(_retries(middlewares)) == 1
    assert middlewares[-1].__class__.__name__ == "ModelRetryMiddleware"


class _FakeSaver:
    def __init__(self, conn=None):
        self.setup_called = False

    async def setup(self):
        self.setup_called = True


class _FakeAgent:
    async def ainvoke(self, *args, **kwargs):  # pragma: no cover - 仅占位
        return {}


@pytest.mark.asyncio
async def test_subagent_agents_get_retry_middleware(monkeypatch):
    captured: list[dict] = []

    async def fake_connect(path):
        return object()

    def fake_create_agent(**kwargs):
        captured.append(kwargs)
        return _FakeAgent()

    monkeypatch.setattr(subagent_manager.aiosqlite, "connect", fake_connect)
    monkeypatch.setattr(subagent_manager, "AsyncSqliteSaver", _FakeSaver)
    monkeypatch.setattr(subagent_manager, "create_agent", fake_create_agent)

    manager = subagent_manager.SubagentManager()
    manager.setChatModel(object())  # type: ignore[arg-type]

    # 默认路径（无中间件）、显式覆盖路径、以及已含重试的组合栈都不能叠加重试
    await manager.newSubagent(agent_name="plain")
    await manager.newSubagent(
        agent_name="custom", middlewares=[ToolCallRepairMiddleware()]
    )
    await manager.newSubagent(
        agent_name="composed",
        middlewares=with_model_retry([ToolCallRepairMiddleware()]),
    )

    assert len(captured) == 3
    for kwargs in captured:
        assert len(_retries(kwargs["middleware"])) == 1


@pytest.mark.asyncio
async def test_araya_agent_gets_retry_middleware(monkeypatch):
    import faust_backend.provider as provider_mod
    from faust_backend.runtime import state as runtime_state

    captured: dict = {}

    class _DummyModel:
        model_name = "dummy"

    async def fake_build_main_chat_model(*args, **kwargs):
        return _DummyModel()

    def fake_create_agent(**kwargs):
        captured.update(kwargs)
        return _DummyModel()

    monkeypatch.setattr(provider_mod, "build_main_chat_model", fake_build_main_chat_model)
    monkeypatch.setattr(runtime_state, "get_model_providers", lambda: object())
    monkeypatch.setattr(araya_runtime, "create_agent", fake_create_agent)
    monkeypatch.setattr(araya_runtime.ArayaRuntime, "_build_tools", lambda self: [])

    await araya_runtime.ArayaRuntime()._init_agent()

    assert len(_retries(captured["middleware"])) == 1
