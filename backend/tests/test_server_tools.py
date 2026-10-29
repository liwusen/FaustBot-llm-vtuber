"""服务端（Provider 托管）工具：规格映射、中间件注入、与本地工具/压缩的关系。"""

from __future__ import annotations

import asyncio

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from faust_backend.llm.formats import build_server_tool_specs
from faust_backend.llm.provider import ModelProvider, ModelProviders
from faust_backend.agent.runtime.middleware.server_tools import (
    ServerToolMiddleware,
    build_server_tool_middleware,
    resolve_server_tool_specs,
)
from faust_backend.agent.runtime.middleware.tool_call_repair import repair_dangling_tool_calls


@tool
def local_tool(path: str) -> str:
    """一个本地工具。"""
    return f"real:{path}"


def _request(tools=None) -> ModelRequest:
    return ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[HumanMessage(content="hi")],
        tools=list(tools or []),
    )


# ── 规格映射 ──


def test_specs_are_copied_per_instance():
    specs = [{"type": "web_search"}]
    middleware = ServerToolMiddleware(specs)
    specs[0]["type"] = "mutated"

    assert middleware.specs == [{"type": "web_search"}]


# ── 中间件注入 ──


def test_sync_wrap_appends_specs_after_local_tools():
    middleware = ServerToolMiddleware(
        [{"type": "web_search_20250305", "name": "web_search"}]
    )
    seen: list[list] = []

    def handler(request):
        seen.append(list(request.tools))
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request([local_tool]), handler)

    assert seen
    tools = seen[0]
    assert tools[0] is local_tool
    assert tools[-1]["name"] == "web_search"


def test_async_wrap_appends_specs():
    middleware = ServerToolMiddleware([{"google_search": {}}])
    seen: list[list] = []

    async def handler(request):
        seen.append(list(request.tools))
        return ModelResponse(result=[AIMessage(content="ok")])

    asyncio.run(middleware.awrap_model_call(_request([local_tool]), handler))

    assert seen[0][0] is local_tool
    assert seen[0][-1] == {"google_search": {}}


def test_empty_specs_is_passthrough():
    middleware = ServerToolMiddleware([])
    requests: list[ModelRequest] = []

    def handler(request):
        requests.append(request)
        return ModelResponse(result=[AIMessage(content="ok")])

    original = _request([local_tool])
    middleware.wrap_model_call(original, handler)

    assert requests[0] is original


# ── 按模型 spec 解析 ──


def _providers(server_tools: dict[str, list[str]], **overrides) -> ModelProviders:
    provider = ModelProvider(
        name="GW",
        base_url="https://gw.example.com/v1",
        key="sk",
        models=["chat-model", "gpt-model", "claude-model"],
        model_server_tools=server_tools,
    )
    provider.model_catalog_routes = {
        "chat-model": {
            "format": "openai_chat",
            "base_url": "https://gw.example.com/v1",
            "host_matched": True,
        },
        "gpt-model": {
            "format": "openai_responses",
            "base_url": "https://gw.example.com/v1",
            "host_matched": True,
        },
        "claude-model": {
            "format": "anthropic_messages",
            "base_url": "https://gw.example.com/anthropic",
            "host_matched": True,
        },
    }
    for key, value in overrides.items():
        setattr(provider, key, value)
    return ModelProviders(
        providers=[provider], main_model="GW::gpt-model", subagent_models=None
    )


def test_resolve_specs_for_each_format():
    providers = _providers(
        {
            "gpt-model": ["web_search"],
            "claude-model": ["web_search"],
            "chat-model": ["web_search"],
        }
    )

    assert resolve_server_tool_specs(providers, "GW::gpt-model") == [{"type": "web_search"}]
    anthropic = resolve_server_tool_specs(providers, "GW::claude-model")
    assert anthropic[0]["type"] == "web_search_20250305"


def test_resolve_specs_rejects_chat_format():
    providers = _providers({"chat-model": ["web_search"]})

    with pytest.raises(ValueError):
        resolve_server_tool_specs(providers, "GW::chat-model")


def test_resolve_specs_empty_when_not_enabled():
    providers = _providers({})

    assert resolve_server_tool_specs(providers, "GW::gpt-model") == []


def test_build_middleware_returns_none_without_tools():
    providers = _providers({})

    assert build_server_tool_middleware(providers, "GW::gpt-model") is None


def test_build_middleware_returns_instance_with_tools():
    providers = _providers({"gpt-model": ["web_search"]})

    middleware = build_server_tool_middleware(providers, "GW::gpt-model")

    assert isinstance(middleware, ServerToolMiddleware)
    assert middleware.specs == [{"type": "web_search"}]


# ── 服务端工具 content block 不会被本地修复逻辑误删 ──


def test_repair_keeps_server_tool_blocks_and_injects_nothing():
    ai = AIMessage(
        content=[
            {"type": "server_tool_use", "id": "srv1", "name": "web_search"},
            {"type": "web_search_call", "id": "srv1", "status": "completed"},
            {"type": "text", "text": "结果"},
        ]
    )

    repaired = repair_dangling_tool_calls([HumanMessage(content="q"), ai])

    assert len(repaired) == 2
    assert repaired[1].content == ai.content
    assert not any(isinstance(message, ToolMessage) for message in repaired)


def test_build_server_tool_specs_is_stable_for_all_formats():
    assert build_server_tool_specs("openai_responses", ["web_search"]) == [
        {"type": "web_search"}
    ]
    assert build_server_tool_specs("google_gemini", ["web_search"]) == [
        {"google_search": {}}
    ]


# ── 压缩请求：同样带服务端 specs（前缀一致） ──


def test_compact_binds_server_specs_with_mocks(monkeypatch):
    import faust_backend.core.config as conf
    from faust_backend.agent.runtime.compact import CompactMiddleware

    monkeypatch.setattr(conf, "COMPACT_COST_CONTROL_ENABLED", False)
    monkeypatch.setattr(conf, "COMPACT_MAX_INPUT_TOKENS", 0)

    calls: list[tuple[list, object]] = []

    class _Fake:
        _llm_type = "fake"
        profile: dict = {}

        def bind_tools(self, tools, **kwargs):
            calls.append((list(tools), kwargs.get("tool_choice")))
            return self

        def with_retry(self, **_kwargs):
            return self

        async def astream(self, payload, config=None):  # noqa: ANN001
            yield AIMessage(content="摘要")

    fake = _Fake()
    middleware = CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[local_tool],
        context_length=1000,
        keep_rounds=1,
        api_format="openai_responses",
        server_tool_specs=[{"type": "web_search"}],
    )

    asyncio.run(middleware._acompact([HumanMessage(content="q")], None))

    tools, tool_choice = calls[-1]
    assert tool_choice == "none"
    assert tools[0].name == "local_tool"
    assert tools[-1] == {"type": "web_search"}
