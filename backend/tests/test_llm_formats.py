"""API 格式解析：优先级、端点归一化、回退与 JSON 调用。

不涉及网络与真实 Provider 名称：全部用虚构 Provider + 通用规则断言。
"""

from __future__ import annotations

import asyncio

import pytest

from faust_backend.llm import formats as llm_formats
from faust_backend.llm.formats import (
    API_FORMATS,
    build_server_tool_specs,
    detect_api_format,
    detect_url_format,
    family_format,
    forced_no_tool_choice,
    normalize_format_base_url,
    parse_json_object,
    resolve_route,
    supported_server_tools,
)
from faust_backend.llm.provider import ModelProvider


def _provider(**overrides) -> ModelProvider:
    defaults = dict(name="P", base_url="https://api.example.com/v1", key="sk-x")
    defaults.update(overrides)
    return ModelProvider(**defaults)


# ── 端点归一化 ──


@pytest.mark.parametrize(
    ("api_format", "url", "expected"),
    [
        # Anthropic：SDK 自己追加 /v1/messages
        ("anthropic_messages", "https://api.anthropic.com/v1/messages", "https://api.anthropic.com"),
        ("anthropic_messages", "https://api.anthropic.com/v1", "https://api.anthropic.com"),
        ("anthropic_messages", "https://proxy.example.com/anthropic/v1/messages/", "https://proxy.example.com/anthropic"),
        # Google：SDK 自己追加 /v1beta/models/...
        ("google_gemini", "https://generativelanguage.googleapis.com/v1beta", "https://generativelanguage.googleapis.com"),
        ("google_gemini", "https://x.example.com/v1beta/models/gemini-2.5-pro", "https://x.example.com"),
        # OpenAI：保留 /v1，去掉具体资源路径
        ("openai_chat", "https://api.openai.com/v1/chat/completions", "https://api.openai.com/v1"),
        ("openai_responses", "https://api.openai.com/v1/responses", "https://api.openai.com/v1"),
        ("openai_chat", "https://gateway.example.com/zen/go/v1", "https://gateway.example.com/zen/go/v1"),
        # 非法地址
        ("openai_chat", "not a url", None),
        ("openai_chat", "ftp://x.example.com/v1", None),
        ("openai_chat", "", None),
    ],
)
def test_normalize_format_base_url(api_format, url, expected):
    assert normalize_format_base_url(api_format, url) == expected


# ── 通用协议推断 ──


@pytest.mark.parametrize(
    ("npm", "shape", "api", "expected"),
    [
        ("@ai-sdk/anthropic", "", "https://x.example.com/v1", "anthropic_messages"),
        ("", "", "https://x.example.com/anthropic", "anthropic_messages"),
        ("", "", "https://x.example.com/messages", "anthropic_messages"),
        ("@ai-sdk/google", "", "https://x.example.com/v1beta", "google_gemini"),
        ("@ai-sdk/vertex", "", "", "google_gemini"),
        ("", "", "https://x.example.com/google", "google_gemini"),
        ("@ai-sdk/openai", "", "https://api.openai.com/v1", "openai_responses"),
        ("@ai-sdk/openai", "responses", "https://x.example.com/v1", "openai_responses"),
        ("@ai-sdk/openai", "completions", "https://x.example.com/v1", "openai_chat"),
        ("@ai-sdk/xai", "completions", "https://x.example.com/v1", "openai_chat"),
        ("@ai-sdk/openai-compatible", "", "https://x.example.com/v1", "openai_chat"),
        ("", "", "https://x.example.com/v1/responses", "openai_responses"),
        ("", "", "", "openai_chat"),
    ],
)
def test_detect_api_format(npm, shape, api, expected):
    assert detect_api_format(npm=npm, shape=shape, api=api) == expected


def test_detect_url_format():
    assert detect_url_format("https://x.example.com/anthropic") == "anthropic_messages"
    assert detect_url_format("https://x.example.com/v1beta") == "google_gemini"
    assert detect_url_format("https://x.example.com/v1/responses") == "openai_responses"
    assert detect_url_format("https://x.example.com/v1") is None


def test_family_format():
    assert family_format("claude-sonnet-4-5") == "anthropic_messages"
    assert family_format("gemini-3-pro") == "google_gemini"
    assert family_format("gpt-5.1") == "openai_responses"
    assert family_format("o3-mini") == "openai_responses"
    assert family_format("deepseek-v4") is None


# ── resolve_route 优先级 ──


def _multi_format_provider(**overrides) -> ModelProvider:
    provider = _provider(models=["arg-model", "claude-next", "gpt-next", "chat-model"])
    provider.model_catalog_routes = {
        "arg-model": {
            "format": "anthropic_messages",
            "base_url": "https://api.example.com/anthropic",
            "host_matched": True,
        },
        "chat-model": {
            "format": "openai_chat",
            "base_url": "https://api.example.com/v1",
            "host_matched": True,
        },
    }
    for key, value in overrides.items():
        setattr(provider, key, value)
    return provider


def test_manual_override_wins():
    provider = _multi_format_provider(
        api_format="google_gemini", model_api_formats={"arg-model": "openai_chat"}
    )

    decision = resolve_route(provider, "arg-model")

    assert decision.api_format == "openai_chat"
    assert decision.source == "manual"
    assert decision.base_url == "https://api.example.com/v1"


def test_provider_default_wins_over_catalog():
    provider = _multi_format_provider(api_format="openai_responses")

    decision = resolve_route(provider, "arg-model")

    assert decision.api_format == "openai_responses"
    assert decision.source == "provider_default"
    assert decision.base_url == "https://api.example.com/v1"


def test_catalog_wins_over_url_path():
    provider = _multi_format_provider(base_url="https://api.example.com/anthropic/v1")

    decision = resolve_route(provider, "chat-model")

    assert decision.api_format == "openai_chat"
    assert decision.source == "catalog"


def test_url_path_used_when_no_catalog_route():
    provider = _provider(base_url="https://api.example.com/anthropic", models=["m"])

    decision = resolve_route(provider, "m")

    assert decision.api_format == "anthropic_messages"
    assert decision.source == "url_path"
    # 保留 /anthropic 前缀：SDK 会自己追加 /v1/messages
    assert decision.base_url == "https://api.example.com/anthropic"


def test_family_fallback_only_for_multi_format_providers():
    provider = _multi_format_provider(models=["arg-model", "chat-model", "claude-next"])

    decision = resolve_route(provider, "claude-next")

    assert decision.api_format == "anthropic_messages"
    assert decision.source == "family"


def test_single_format_provider_does_not_guess_by_family():
    provider = _provider(
        base_url="https://api.example.com/v1", models=["claude-next", "chat-model"]
    )
    provider.model_catalog_routes = {
        "chat-model": {"format": "openai_chat", "base_url": "https://api.example.com/v1"}
    }

    decision = resolve_route(provider, "claude-next")

    # 只有一种格式的 Provider 上出现 claude 模型名，不能据此切成 Anthropic
    assert decision.api_format == "openai_chat"
    assert decision.source == "fallback"


def test_fallback_is_openai_chat():
    provider = _provider(models=["mystery"])

    decision = resolve_route(provider, "mystery")

    assert decision.api_format == "openai_chat"
    assert decision.source == "fallback"
    assert decision.base_url == "https://api.example.com/v1"


def test_format_base_urls_override_catalog_and_provider():
    provider = _multi_format_provider(
        format_base_urls={"anthropic_messages": "https://proxy.example.com/anthropic/v1"}
    )

    decision = resolve_route(provider, "arg-model")

    assert decision.api_format == "anthropic_messages"
    assert decision.base_url == "https://proxy.example.com/anthropic"


def test_invalid_format_base_url_raises():
    provider = _multi_format_provider(
        format_base_urls={"anthropic_messages": "not-a-url"}
    )

    with pytest.raises(ValueError):
        resolve_route(provider, "arg-model")


def test_explicit_format_without_endpoint_raises():
    provider = _provider(base_url="", models=["m"], model_api_formats={"m": "anthropic_messages"})

    with pytest.raises(ValueError):
        resolve_route(provider, "m")


def test_catalog_route_ignored_when_host_mismatch():
    provider = _provider(base_url="https://proxy.example.com/v1", models=["claude"])
    provider.model_catalog_routes = {
        "claude": {
            "format": "anthropic_messages",
            # host 不匹配时 enrich 不会写入 base_url；这里即使写了也不能用
            "base_url": "https://foreign.example.com/anthropic",
            "host_matched": False,
        }
    }

    decision = resolve_route(provider, "claude")

    assert decision.api_format == "anthropic_messages"
    assert decision.base_url == "https://proxy.example.com"


# ── 描述与管理端负载 ──


def test_describe_provider_formats_includes_errors():
    provider = _provider(base_url="", models=["m"], model_api_formats={"m": "anthropic_messages"})

    described = llm_formats.describe_provider_formats(provider)

    assert described["m"]["source"] == "error"
    assert "format_base_urls" in described["m"]["reason"]


def test_describe_all_formats_covers_providers():
    providers = type("P", (), {})()
    providers.providers = [_provider(name="A", models=["m"]), _provider(name="B", models=[])]

    described = llm_formats.describe_all_formats(providers)

    assert set(described) == {"A", "B"}
    assert described["A"]["m"]["api_format"] == "openai_chat"
    assert described["B"] == {}


def test_api_formats_tuple_matches_frontend_contract():
    assert API_FORMATS == (
        "openai_chat",
        "openai_responses",
        "anthropic_messages",
        "google_gemini",
    )


# ── 服务端工具规格 ──


def test_build_server_tool_specs_per_format():
    assert build_server_tool_specs("openai_responses", ["web_search"]) == [
        {"type": "web_search"}
    ]
    anthropic = build_server_tool_specs("anthropic_messages", ["web_search"])
    assert anthropic[0]["type"] == "web_search_20250305"
    assert anthropic[0]["name"] == "web_search"
    assert build_server_tool_specs("google_gemini", ["web_search"]) == [{"google_search": {}}]


def test_build_server_tool_specs_rejects_chat_and_unknown():
    with pytest.raises(ValueError):
        build_server_tool_specs("openai_chat", ["web_search"])
    with pytest.raises(ValueError):
        build_server_tool_specs("openai_responses", ["nope"])


def test_supported_server_tools_excludes_chat():
    assert supported_server_tools("openai_chat") == []
    assert supported_server_tools("google_gemini") == ["web_search"]


def test_forced_no_tool_choice_per_format():
    assert forced_no_tool_choice("openai_chat") == "none"
    assert forced_no_tool_choice("google_gemini") == "none"
    # Anthropic 的字符串 "none" 会被当成「调用名为 none 的工具」
    assert forced_no_tool_choice("anthropic_messages") == {"type": "none"}


# ── JSON 调用 ──


def test_parse_json_object_tolerates_fences_and_noise():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('前言 {"a": {"b": 2}} 后记') == {"a": {"b": 2}}


def test_parse_json_object_raises_on_garbage():
    with pytest.raises(ValueError):
        parse_json_object("")
    with pytest.raises(ValueError):
        parse_json_object("no json here")


class _JsonModel:
    def __init__(self, content: str, *, supports_response_format: bool = True) -> None:
        self.content = content
        self.supports_response_format = supports_response_format
        self.bound: dict | None = None
        self.calls: list = []

    def bind(self, **kwargs):
        if not self.supports_response_format and "response_format" in kwargs:
            raise TypeError("response_format unsupported")
        clone = _JsonModel(self.content, supports_response_format=self.supports_response_format)
        clone.bound = kwargs
        clone.calls = self.calls
        return clone

    async def ainvoke(self, messages):
        self.calls.append(list(messages))
        from langchain_core.messages import AIMessage

        return AIMessage(content=self.content)


def test_ainvoke_json_uses_response_format_only_for_chat():
    from langchain_core.messages import HumanMessage

    chat_model = _JsonModel('{"ok": true}')
    assert asyncio.run(
        llm_formats.ainvoke_json(chat_model, [HumanMessage(content="x")], "openai_chat")
    ) == {"ok": True}
    assert chat_model.calls  # 实际调用发生在 clone 上，但共享 calls 列表
    assert chat_model.bound is None

    anthropic_model = _JsonModel('{"ok": false}')
    result = asyncio.run(
        llm_formats.ainvoke_json(
            anthropic_model, [HumanMessage(content="x")], "anthropic_messages"
        )
    )
    assert result == {"ok": False}


def test_ainvoke_json_falls_back_when_response_format_rejected():
    from langchain_core.messages import HumanMessage

    model = _JsonModel('{"ok": 1}', supports_response_format=False)
    result = asyncio.run(
        llm_formats.ainvoke_json(model, [HumanMessage(content="x")], "openai_chat")
    )
    assert result == {"ok": 1}
