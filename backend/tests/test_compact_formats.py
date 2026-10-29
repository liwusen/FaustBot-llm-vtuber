"""会话压缩在四种 API 格式下的行为：用量口径、Anthropic 上限、工具前缀、写回。"""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langchain_core.tools import tool

from faust_backend.runtime import usage as usage_mod
from faust_backend.runtime.compact import (
    CompactMiddleware,
    _reported_tokens,
)


@tool
def read_file(path: str, limit: int = 100) -> str:
    """读取一个文件。"""
    return f"real:{path}"


# ── 用量口径 ──


def _ai(
    content: str = "ok",
    *,
    usage: dict | None = None,
    raw_usage: dict | None = None,
) -> AIMessage:
    message = AIMessage(content=content)
    if usage is not None:
        message.usage_metadata = usage
    if raw_usage is not None:
        message.response_metadata = {"usage": raw_usage}
    return message


def test_anthropic_context_includes_cache_read_and_creation():
    message = _ai(
        raw_usage={
            "input_tokens": 100,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 50,
            "output_tokens": 20,
        }
    )

    snapshot = usage_mod.normalize_usage(message, "anthropic_messages")

    assert snapshot is not None
    assert snapshot.input_tokens == 1050
    assert snapshot.cache_read_tokens == 900
    assert snapshot.cache_creation_tokens == 50
    assert snapshot.context_tokens == 1070


def test_anthropic_does_not_double_count_when_sdk_already_folded_cache():
    message = _ai(
        usage={
            "input_tokens": 1050,
            "output_tokens": 20,
            "total_tokens": 1070,
            "input_token_details": {"cache_read": 900, "cache_creation": 50},
        },
        raw_usage={
            "input_tokens": 100,
            "cache_read_input_tokens": 900,
            "cache_creation_input_tokens": 50,
            "output_tokens": 20,
        },
    )

    snapshot = usage_mod.normalize_usage(message, "anthropic_messages")

    assert snapshot is not None
    assert snapshot.context_tokens == 1070


def test_openai_cached_tokens_are_subset_not_additive():
    message = _ai(
        usage={
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
            "input_token_details": {"cache_read": 800},
        }
    )

    snapshot = usage_mod.normalize_usage(message, "openai_chat")

    assert snapshot is not None
    assert snapshot.context_tokens == 1100  # 不是 1900
    assert snapshot.cache_read_tokens == 800


def test_gemini_cached_tokens_are_subset_not_additive():
    message = _ai(
        usage={
            "input_tokens": 5000,
            "output_tokens": 200,
            "total_tokens": 5200,
            "input_token_details": {"cache_read": 4000},
            "output_token_details": {"reasoning": 120},
        }
    )

    snapshot = usage_mod.normalize_usage(message, "google_gemini")

    assert snapshot is not None
    assert snapshot.context_tokens == 5200
    assert snapshot.reasoning_tokens == 120


def test_usage_without_any_report_returns_none():
    assert usage_mod.normalize_usage(AIMessage(content="x"), "openai_chat") is None
    assert usage_mod.last_usage([HumanMessage(content="x")]) is None


def test_usage_total_falls_back_to_input_plus_output():
    message = _ai(usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 0})

    snapshot = usage_mod.normalize_usage(message, "openai_chat")

    assert snapshot is not None
    assert snapshot.context_tokens == 15


def test_reported_tokens_uses_latest_reporting_message():
    messages = [
        HumanMessage(content="q"),
        _ai("old", usage={"input_tokens": 100, "output_tokens": 1, "total_tokens": 101}),
        HumanMessage(content="q2"),
        AIMessage(content="本地构造，无 usage"),
        _ai(usage={"input_tokens": 10, "output_tokens": 100, "total_tokens": 110}),
    ]

    assert _reported_tokens(messages) == 110


def test_reported_tokens_anthropic_reads_raw_usage():
    messages = [
        HumanMessage(content="q"),
        _ai(
            raw_usage={
                "input_tokens": 10,
                "cache_read_input_tokens": 990,
                "output_tokens": 100,
            }
        ),
    ]

    assert _reported_tokens(messages, "anthropic_messages") == 1100


# ── 截断判定 ──


@pytest.mark.parametrize(
    ("max_out", "reason", "usage", "expected"),
    [
        (100, "length", None, True),
        (100, "max_tokens", None, True),
        (100, "stop", None, False),
        (100, None, {"output_tokens": 100}, True),
        (100, None, {"output_tokens": 99}, False),
        (0, "length", None, False),
        (100, "end_turn", {"output_tokens": 5}, False),
    ],
)
def test_is_truncated(max_out, reason, usage, expected):
    assert CompactMiddleware._is_truncated(max_out, reason, usage) is expected


# ── 写回：多条 System 合并 ──


def _middleware(**kwargs) -> CompactMiddleware:
    fake = _FakeModel()
    return CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[read_file],
        context_length=1000,
        keep_rounds=1,
        **kwargs,
    )


def test_build_replacement_merges_all_system_messages():
    mw = _middleware()
    messages = [
        SystemMessage(content="主提示词"),
        SystemMessage(content="角色设定"),
        HumanMessage(content="q"),
        AIMessage(content="a"),
        HumanMessage(content="q2"),
        AIMessage(content="a2"),
    ]

    replacement = mw._build_replacement(messages, "摘要正文")

    assert len(replacement) == 1
    merged = replacement[0]
    assert isinstance(merged, SystemMessage)
    assert "主提示词" in merged.content
    assert "角色设定" in merged.content
    assert "摘要正文" in merged.content
    assert merged.content.count("faust:compact-summary:start") == 1


def test_build_replacement_without_system_uses_human_message():
    mw = _middleware()
    replacement = mw._build_replacement([HumanMessage(content="q")], "摘要")

    assert len(replacement) == 1
    assert isinstance(replacement[0], HumanMessage)
    assert replacement[0].additional_kwargs["lc_source"] == "faust_compact"


# ── 压缩调用：工具前缀与 tool_choice ──


class _FakeModel:
    """记录 bind_tools 参数，astream 产出可控的摘要分片。"""

    _llm_type = "fake"
    profile: dict = {}

    def __init__(
        self,
        *,
        summary: str = "摘要正文",
        stop_reason: str | None = None,
        accept_tool_choice: bool = True,
    ) -> None:
        self.summary = summary
        self.stop_reason = stop_reason
        self.accept_tool_choice = accept_tool_choice
        self.bind_tools_calls: list[tuple[list, object]] = []
        self.bound_max_tokens: int | None = None
        self.bind_log: list[dict] = []

    def _clone(self) -> "_FakeModel":
        clone = _FakeModel(
            summary=self.summary,
            stop_reason=self.stop_reason,
            accept_tool_choice=self.accept_tool_choice,
        )
        clone.bind_tools_calls = self.bind_tools_calls
        clone.bound_max_tokens = self.bound_max_tokens
        clone.bind_log = self.bind_log
        return clone

    def bind_tools(self, tools, **kwargs):
        if kwargs and not self.accept_tool_choice:
            raise TypeError("tool_choice unsupported")
        self.bind_tools_calls.append((list(tools), kwargs.get("tool_choice")))
        return self._clone()

    def bind(self, **kwargs):
        clone = self._clone()
        clone.bind_log.append(dict(kwargs))
        if "max_tokens" in kwargs:
            clone.bound_max_tokens = kwargs["max_tokens"]
        return clone

    def with_retry(self, **_kwargs):
        return self

    async def astream(self, payload, config=None):  # noqa: ANN001
        chunk = AIMessage(
            content=self.summary,
            response_metadata={"stop_reason": self.stop_reason},
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        yield chunk


def _compact_env(monkeypatch, *, cost_control: bool = False, max_out: int = 0) -> None:
    import faust_backend.core.config as conf

    monkeypatch.setattr(conf, "COMPACT_COST_CONTROL_ENABLED", cost_control)
    monkeypatch.setattr(conf, "COMPACT_MAX_OUTPUT_TOKENS", max_out)
    monkeypatch.setattr(conf, "COMPACT_MAX_INPUT_TOKENS", 0)


def test_compact_binds_server_tools_after_mocks_with_anthropic_none(monkeypatch):
    _compact_env(monkeypatch)
    fake = _FakeModel()
    mw = CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[read_file],
        context_length=1000,
        keep_rounds=1,
        api_format="anthropic_messages",
        server_tool_specs=[{"type": "web_search_20250305", "name": "web_search"}],
    )

    asyncio.run(mw._acompact([HumanMessage(content="q")], None))

    tools, tool_choice = fake.bind_tools_calls[-1]
    assert tool_choice == {"type": "none"}
    # mock 客户端工具在前，服务端工具 specs 在后（与主请求顺序一致）
    assert tools[0].name == "read_file"
    assert tools[-1]["name"] == "web_search"


def test_compact_uses_string_none_for_openai(monkeypatch):
    _compact_env(monkeypatch)
    fake = _FakeModel()
    mw = CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[read_file],
        context_length=1000,
        keep_rounds=1,
        api_format="openai_chat",
    )

    asyncio.run(mw._acompact([HumanMessage(content="q")], None))

    _tools, tool_choice = fake.bind_tools_calls[-1]
    assert tool_choice == "none"


def test_compact_falls_back_when_tool_choice_unsupported(monkeypatch):
    _compact_env(monkeypatch)
    fake = _FakeModel(accept_tool_choice=False)
    mw = CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[read_file],
        context_length=1000,
        keep_rounds=1,
        api_format="openai_chat",
    )

    asyncio.run(mw._acompact([HumanMessage(content="q")], None))

    assert mw._tool_choice_warned is True
    _tools, tool_choice = fake.bind_tools_calls[-1]
    assert tool_choice is None


def test_compact_raises_on_truncated_summary(monkeypatch):
    _compact_env(monkeypatch)
    fake = _FakeModel(stop_reason="max_tokens")
    mw = CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[read_file],
        context_length=1000,
        keep_rounds=1,
        api_format="anthropic_messages",
        summary_max_tokens=4096,
    )

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(mw._acompact([HumanMessage(content="q")], None))

    assert "4096" in str(excinfo.value)
    # 上限来自模型自身，不是成本控制，因此不改写 _cost_cap_bypassed
    assert mw._cost_cap_bypassed is False


def test_compact_cost_cap_truncation_bypasses_cap(monkeypatch):
    _compact_env(monkeypatch, cost_control=True, max_out=500)
    fake = _FakeModel(stop_reason="length")
    mw = CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[read_file],
        context_length=1000,
        keep_rounds=1,
        api_format="openai_chat",
    )

    with pytest.raises(RuntimeError):
        asyncio.run(mw._acompact([HumanMessage(content="q")], None))

    assert mw._cost_cap_bypassed is True
    assert fake.bind_log[-1]["max_tokens"] == 500


# ── abefore_model：压缩后写回 ──


def test_abefore_model_writes_merged_state(monkeypatch):
    _compact_env(monkeypatch)
    fake = _FakeModel(summary="## 目标与进度\n做完了")
    mw = CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[read_file],
        context_length=1000,
        threshold_ratio=0.5,
        keep_rounds=1,
    )
    messages = [
        SystemMessage(content="sys-a"),
        SystemMessage(content="sys-b"),
        HumanMessage(content="q1"),
        _ai("a1", usage={"input_tokens": 900, "output_tokens": 100, "total_tokens": 1000}),
        HumanMessage(content="q2"),
        _ai("a2", usage={"input_tokens": 900, "output_tokens": 100, "total_tokens": 1000}),
    ]

    result = asyncio.run(mw.abefore_model({"messages": messages}, None))

    assert result is not None
    updated = result["messages"]
    assert isinstance(updated[0], RemoveMessage)
    replacement = updated[1]
    assert isinstance(replacement, SystemMessage)
    assert "sys-a" in replacement.content and "sys-b" in replacement.content
    assert "做完了" in replacement.content


def test_abefore_model_skips_without_usage(monkeypatch):
    _compact_env(monkeypatch)
    fake = _FakeModel()
    mw = CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[read_file],
        context_length=1000,
        threshold_ratio=0.5,
        keep_rounds=1,
    )
    messages = [HumanMessage(content="q"), AIMessage(content="a")]

    assert asyncio.run(mw.abefore_model({"messages": messages}, None)) is None
