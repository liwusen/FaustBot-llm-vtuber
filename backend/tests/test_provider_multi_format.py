"""按模型构建四种 API 格式的 Chat 模型（参数、头部、思考、关闭）。

全部用虚构 Provider；不发任何网络请求（models 列表非空时 auto_load 直接返回）。
"""

from __future__ import annotations

import asyncio

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

from faust_backend.llm_formats import (
    build_chat_model_from_spec,
    close_chat_model,
)
from faust_backend.provider import (
    ModelProviders,
    ModelProvider,
    get_main_openai_credentials,
    get_model_api_format,
    try_main_openai_credentials,
)
from faust_backend.thinking import ReasoningChatOpenAI

MODELS = ("chat-model", "claude-model", "gemini-model", "gpt-model")


def _providers(**provider_overrides) -> tuple[ModelProviders, ModelProvider]:
    provider = ModelProvider(
        name="GW",
        base_url="https://gw.example.com/v1",
        key="sk-test",
        models=list(MODELS),
    )
    provider.model_catalog_routes = {
        "chat-model": {
            "format": "openai_chat",
            "base_url": "https://gw.example.com/v1",
            "host_matched": True,
        },
        "claude-model": {
            "format": "anthropic_messages",
            "base_url": "https://gw.example.com/anthropic",
            "host_matched": True,
        },
        "gemini-model": {
            "format": "google_gemini",
            "base_url": "https://gw.example.com/google",
            "host_matched": True,
        },
        "gpt-model": {
            "format": "openai_responses",
            "base_url": "https://gw.example.com/v1",
            "host_matched": True,
        },
    }
    for key, value in provider_overrides.items():
        setattr(provider, key, value)
    providers = ModelProviders(providers=[provider], main_model="GW::chat-model")
    return providers, provider


def _build(providers: ModelProviders, spec: str, intensity=None, purpose="chat"):
    return asyncio.run(
        build_chat_model_from_spec(providers, spec=spec, intensity=intensity, purpose=purpose)
    )


# ── 四种格式的类与关键参数 ──


def test_builds_openai_chat():
    providers, _ = _providers()

    model = _build(providers, "GW::chat-model")

    assert isinstance(model, ChatOpenAI)
    assert not isinstance(model, ReasoningChatOpenAI)
    assert str(model.openai_api_base) == "https://gw.example.com/v1"
    assert model.stream_usage is True


def test_builds_openai_responses_stateless():
    providers, _ = _providers()

    model = _build(providers, "GW::gpt-model")

    assert isinstance(model, ChatOpenAI)
    assert model.use_responses_api is True
    assert model.output_version == "responses/v1"
    assert model.store is False
    assert model.use_previous_response_id is False


def test_builds_anthropic_with_explicit_max_tokens():
    providers, _ = _providers()

    model = _build(providers, "GW::claude-model")

    assert isinstance(model, ChatAnthropic)
    assert model.max_tokens == 8192  # 未配置上限时的安全默认
    assert str(model._client.base_url).rstrip("/") == "https://gw.example.com/anthropic"
    assert model.stream_usage is True


def test_builds_gemini_with_output_cap():
    providers, _ = _providers()

    model = _build(providers, "GW::gemini-model")

    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.max_output_tokens == 8192
    assert str(model.base_url).rstrip("/") == "https://gw.example.com/google"


# ── Anthropic max_tokens 规则 ──


def test_anthropic_max_tokens_uses_model_cap():
    providers, provider = _providers()
    provider.model_max_output_tokens = {"claude-model": 32000}

    assert _build(providers, "GW::claude-model").max_tokens == 32000


def test_anthropic_max_tokens_must_exceed_thinking_budget():
    providers, provider = _providers()
    provider.model_max_output_tokens = {"claude-model": 32000}
    provider.thinking_type = "openai"

    model = _build(providers, "GW::claude-model", intensity="high")

    assert model.thinking == {"type": "enabled", "budget_tokens": 8192}
    assert model.max_tokens == 8192 + 1024


def test_anthropic_compact_uses_bounded_summary_cap():
    providers, provider = _providers()
    provider.model_max_output_tokens = {"claude-model": 32000}

    model = _build(providers, "GW::claude-model", purpose="compact")

    assert model.max_tokens == 8192

    provider.model_max_output_tokens = {"claude-model": 2000}
    model = _build(providers, "GW::claude-model", purpose="compact")
    # 摘要至少要有 4096，否则会被截断成半句话
    assert model.max_tokens == 4096


# ── 思考参数按格式翻译 ──


def test_chat_thinking_returns_reasoning_subclass():
    providers, provider = _providers()
    provider.thinking_type = "qwen"

    model = _build(providers, "GW::chat-model", intensity="medium")

    assert isinstance(model, ReasoningChatOpenAI)


def test_responses_thinking_uses_reasoning_effort():
    providers, provider = _providers()
    provider.thinking_type = "openai"

    model = _build(providers, "GW::gpt-model", intensity="low")

    assert model.reasoning == {"effort": "low"}


def test_gemini_thinking_uses_budget_and_thoughts():
    providers, provider = _providers()
    provider.thinking_type = "openai"

    model = _build(providers, "GW::gemini-model", intensity="medium")

    assert model.thinking_budget == 4096
    assert model.include_thoughts is True


def test_thinking_disabled_by_provider_preset_none():
    providers, provider = _providers()
    provider.thinking_type = "none"

    chat = _build(providers, "GW::chat-model", intensity="high")
    anthropic = _build(providers, "GW::claude-model", intensity="high")

    assert not isinstance(chat, ReasoningChatOpenAI)
    assert anthropic.thinking is None


def test_thinking_disabled_when_catalog_says_no_reasoning():
    providers, provider = _providers()
    provider.thinking_type = "openai"
    provider.model_catalog_routes["claude-model"]["reasoning"] = False

    model = _build(providers, "GW::claude-model", intensity="high")

    assert model.thinking is None


def test_purpose_compact_disables_thinking():
    providers, provider = _providers()
    provider.thinking_type = "openai"

    model = _build(providers, "GW::gpt-model", intensity="high", purpose="compact")

    assert model.reasoning is None


# ── 头部注入 ──


def test_user_agent_injected_for_all_formats():
    providers, provider = _providers(opencode_go=False)

    chat = _build(providers, "GW::chat-model")
    responses = _build(providers, "GW::gpt-model")
    anthropic = _build(providers, "GW::claude-model")
    gemini = _build(providers, "GW::gemini-model")

    assert chat.default_headers["User-Agent"] == "faustbot/3 (langchain)"
    assert responses.default_headers["User-Agent"] == "faustbot/3 (langchain)"
    assert anthropic.default_headers["User-Agent"] == "faustbot/3 (langchain)"
    assert gemini.additional_headers["User-Agent"] == "faustbot/3 (langchain)"


def test_opencode_session_header_injected_for_all_formats():
    providers, provider = _providers(opencode_go=True)

    chat = _build(providers, "GW::chat-model")
    responses = _build(providers, "GW::gpt-model")
    anthropic = _build(providers, "GW::claude-model")
    gemini = _build(providers, "GW::gemini-model")

    assert chat.default_headers.get("x-opencode-session")
    assert responses.default_headers.get("x-opencode-session")
    assert anthropic.default_headers.get("x-opencode-session")
    assert gemini.additional_headers.get("x-opencode-session")


# ── stream_usage 三态 ──


def test_stream_usage_can_be_disabled():
    providers, provider = _providers(stream_usage=False)

    assert _build(providers, "GW::chat-model").stream_usage is False
    assert _build(providers, "GW::claude-model").stream_usage is False


def test_stream_usage_can_be_forced_on():
    providers, provider = _providers(stream_usage=True)

    assert _build(providers, "GW::chat-model").stream_usage is True


# ── 错误路径 ──


def test_unknown_model_raises():
    providers, _ = _providers()

    with pytest.raises(ValueError):
        _build(providers, "GW::nope")


def test_unknown_provider_raises():
    providers, _ = _providers()

    with pytest.raises(ValueError):
        _build(providers, "NOPE::chat-model")


def test_invalid_format_base_url_raises():
    providers, provider = _providers(format_base_urls={"anthropic_messages": "nope"})

    with pytest.raises(ValueError):
        _build(providers, "GW::claude-model")


# ── 关闭客户端 ──


def test_close_chat_model_handles_all_formats():
    providers, _ = _providers()

    async def _run():
        for spec in ("GW::chat-model", "GW::gpt-model", "GW::claude-model", "GW::gemini-model"):
            model = await build_chat_model_from_spec(providers, spec=spec)
            await close_chat_model(model)
        await close_chat_model(None)

    asyncio.run(_run())


# ── OpenAI 兼容凭据（内部功能共用） ──


def test_openai_credentials_for_chat_main_model():
    providers, _ = _providers()

    assert try_main_openai_credentials(providers) == (
        "chat-model",
        "sk-test",
        "https://gw.example.com/v1",
    )
    assert get_main_openai_credentials(providers)[0] == "chat-model"


def test_openai_credentials_reject_anthropic_main_model():
    providers, _ = _providers()
    providers.main_model = "GW::claude-model"

    assert try_main_openai_credentials(providers) is None
    with pytest.raises(RuntimeError) as excinfo:
        get_main_openai_credentials(providers)
    assert "format_base_urls" in str(excinfo.value)


def test_openai_credentials_empty_when_unconfigured():
    providers = ModelProviders(providers=[], main_model=None)

    assert try_main_openai_credentials(providers) is None
    with pytest.raises(RuntimeError):
        get_main_openai_credentials(providers)


def test_openai_credentials_prefer_explicit_chat_endpoint():
    providers, provider = _providers(
        format_base_urls={"openai_chat": "https://gw.example.com/chat/v1"}
    )
    providers.main_model = "GW::gpt-model"

    assert try_main_openai_credentials(providers) == (
        "gpt-model",
        "sk-test",
        "https://gw.example.com/chat/v1",
    )


def test_get_model_api_format():
    providers, _ = _providers()

    assert get_model_api_format(providers, "GW::claude-model") == "anthropic_messages"
    assert get_model_api_format(providers, "GW::gpt-model") == "openai_responses"
    assert get_model_api_format(providers, "bad-spec") == "openai_chat"
