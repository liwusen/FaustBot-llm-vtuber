"""`runtime/session_stats.py` 与 `GET /faust/session/context` 的单元测试。

覆盖三件事：
1. `format_tokens` 的格式化规则与进位边界；
2. `collect_session_stats` 在「有 API usage / 无 API usage / 空会话」下的输出；
3. `get_checkpoint_messages` 对 checkpointer 缺失与异常结构的容错。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.provider import DEFAULT_CONTEXT_LENGTH, ModelProvider, ModelProviders
from faust_backend.runtime import session_stats
from faust_backend.routes.session import session_context_api


# ── format_tokens ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "0"),
        (1, "1"),
        (812, "812"),
        (999, "999"),
        (1000, "1K"),
        (12000, "12K"),
        (12345, "12K"),
        (128000, "128K"),
        (900000, "900K"),
        (999999, "1M"),  # 进位：不能出现 "1000K"
        (1_000_000, "1M"),
        # 700_000 落在 K 区间，按定稿规则显示 700K（刻意不写 0.7M，见 format_tokens docstring）
        (700000, "700K"),
        (654321, "654K"),
        (1_500_000, "1.5M"),
        (1_950_000, "2M"),  # 整数半进位：不能因浮点误差显示成 1.9M
    ],
)
def test_format_tokens(value, expected):
    assert session_stats.format_tokens(value) == expected


def test_format_tokens_rejects_non_number():
    with pytest.raises(TypeError):
        session_stats.format_tokens("128000")
    with pytest.raises(TypeError):
        session_stats.format_tokens(True)


# ── 测试替身 ────────────────────────────────────────────────────────


class _CheckpointTuple:
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint


class _FakeCheckpointer:
    def __init__(self, checkpoint):
        self._checkpoint = checkpoint
        self.calls = 0

    async def aget_tuple(self, _config):
        self.calls += 1
        return None if self._checkpoint is None else _CheckpointTuple(self._checkpoint)


def _providers(main_model: str = "ds::chat", context_length: int | None = 128000) -> ModelProviders:
    lengths = {"chat": context_length} if context_length else {}
    return ModelProviders(
        providers=[ModelProvider(name="ds", base_url="http://x/v1", models=["chat"], model_context_lengths=lengths)],
        main_model=main_model,
    )


def _patch_session(monkeypatch, *, messages, providers=None):
    async def _fake_messages():
        return list(messages)

    monkeypatch.setattr(session_stats, "get_checkpoint_messages", _fake_messages)
    monkeypatch.setattr(session_stats.state, "get_model_providers", lambda: providers or _providers())
    monkeypatch.setattr(session_stats.conf, "COMPACT_THRESHOLD_RATIO", 0.8)


def _ai_with_usage(total: int, *, input_tokens: int | None = None, output_tokens: int | None = None,
                   cache_read: int | None = None) -> AIMessage:
    """构造带 API usage 的 AIMessage（沿用 test_compact_middleware 的写法：构造后再赋属性）。"""
    message = AIMessage(content="hi")
    usage: dict = {
        "input_tokens": total if input_tokens is None else input_tokens,
        "output_tokens": 0 if output_tokens is None else output_tokens,
        "total_tokens": total,
    }
    if cache_read is not None:
        usage["input_token_details"] = {"cache_read": cache_read}
    message.usage_metadata = usage
    return message


# ── get_checkpoint_messages ─────────────────────────────────────────


def test_get_checkpoint_messages_without_checkpointer(monkeypatch):
    monkeypatch.setattr(session_stats.state, "checkpointer", None)
    assert asyncio.run(session_stats.get_checkpoint_messages()) == []


def test_get_checkpoint_messages_reads_channel_values(monkeypatch):
    messages = [HumanMessage(content="a"), AIMessage(content="b")]
    monkeypatch.setattr(session_stats.state, "checkpointer", _FakeCheckpointer({"channel_values": {"messages": messages}}))
    assert asyncio.run(session_stats.get_checkpoint_messages()) == messages


def test_get_checkpoint_messages_tolerates_bad_checkpoint(monkeypatch):
    monkeypatch.setattr(session_stats.state, "checkpointer", _FakeCheckpointer({"channel_values": {"messages": "not-a-list"}}))
    assert asyncio.run(session_stats.get_checkpoint_messages()) == []


# ── collect_session_stats ───────────────────────────────────────────


def test_collect_session_stats_with_api_usage(monkeypatch):
    messages = [
        HumanMessage(content="你好"),
        _ai_with_usage(12000, input_tokens=11000, output_tokens=1000, cache_read=9000),
    ]
    _patch_session(monkeypatch, messages=messages)

    stats = asyncio.run(session_stats.collect_session_stats())

    assert stats["status"] == "ok"
    assert stats["model"] == "ds::chat"
    assert stats["messages"] == 2
    assert stats["has_api_usage"] is True
    assert stats["used_tokens"] == 12000
    assert stats["used_tokens_text"] == "12K"
    assert stats["context_length"] == 128000
    assert stats["context_length_text"] == "128K"
    assert stats["threshold_tokens"] == 102400
    assert stats["percent"] == pytest.approx(9.4, abs=0.05)
    assert stats["input_tokens"] == 11000
    assert stats["output_tokens"] == 1000
    assert stats["cache_read_tokens"] == 9000


def test_collect_session_stats_without_api_usage_never_guesses(monkeypatch):
    messages = [HumanMessage(content="你好"), AIMessage(content="在")]
    _patch_session(monkeypatch, messages=messages)

    stats = asyncio.run(session_stats.collect_session_stats())

    assert stats["has_api_usage"] is False
    assert stats["used_tokens"] == 0
    assert stats["used_tokens_text"] == "—"
    assert stats["percent"] == 0.0
    assert stats["cache_read_tokens"] is None


def test_collect_session_stats_empty_session(monkeypatch):
    _patch_session(monkeypatch, messages=[])

    stats = asyncio.run(session_stats.collect_session_stats())

    assert stats["messages"] == 0
    assert stats["has_api_usage"] is False
    assert stats["used_tokens_text"] == "—"
    # 空会话也必须给出窗口上限，前端药丸才画得出 "— / 128K"
    assert stats["context_length"] == 128000


def test_collect_session_stats_falls_back_to_default_context_length(monkeypatch):
    messages = [_ai_with_usage(500)]
    _patch_session(monkeypatch, messages=messages, providers=_providers(context_length=None))

    stats = asyncio.run(session_stats.collect_session_stats())

    assert stats["context_length"] == DEFAULT_CONTEXT_LENGTH
    assert stats["used_tokens_text"] == "500"


# ── 路由 ───────────────────────────────────────────────────────────


def test_session_context_route_returns_stats(monkeypatch):
    messages = [_ai_with_usage(700000, input_tokens=690000, output_tokens=10000)]
    _patch_session(monkeypatch, messages=messages)

    payload = asyncio.run(session_context_api())

    assert payload["status"] == "ok"
    assert payload["used_tokens_text"] == "700K"
    assert payload["context_length_text"] == "128K"


# ── /session 命令文本（与统计模块共用实现，输出不得漂移） ──────────────


def test_session_token_summary_text_shape(monkeypatch):
    from faust_backend.routes.chat import _session_token_summary

    messages = [
        HumanMessage(content="你好"),
        _ai_with_usage(12000, input_tokens=11000, output_tokens=1000, cache_read=9000),
    ]
    _patch_session(monkeypatch, messages=messages)

    text = asyncio.run(_session_token_summary())

    assert text.splitlines() == [
        "模型: ds::chat",
        "messages=2",
        "prompt_tokens=11000",
        "completion_tokens=1000",
        "total_tokens=12000",
        "缓存命中(cache_read)=9000",
        "上下文长度=128000 触发阈值=102400 (80%) 当前占用=9.4%",
    ]


def test_session_token_summary_without_usage(monkeypatch):
    from faust_backend.routes.chat import _session_token_summary

    _patch_session(monkeypatch, messages=[HumanMessage(content="你好")])

    text = asyncio.run(_session_token_summary())

    assert text.splitlines() == [
        "模型: ds::chat",
        "messages=1",
        "tokens: 暂无 API 计数（本会话尚无 LLM 响应上报 usage）",
    ]


def test_session_token_summary_empty_session(monkeypatch):
    from faust_backend.routes.chat import _session_token_summary

    _patch_session(monkeypatch, messages=[])

    assert asyncio.run(_session_token_summary()) == "当前会话没有可统计的上下文。"
