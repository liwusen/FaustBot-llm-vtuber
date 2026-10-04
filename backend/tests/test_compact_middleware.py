"""CompactMiddleware 单测（离线，不调用真实 LLM）。

覆盖 __dev__/compact-refactor-plan.md 第 4 节的 1-6 项：
阈值判定、轮次边界不拆 tool 组、写回顺序、mock schema 一致、失败降级、API token 提取。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool  # noqa: E402
from langchain_core.utils.function_calling import convert_to_openai_tool  # noqa: E402
from langgraph.graph.message import REMOVE_ALL_MESSAGES  # noqa: E402

from faust_backend.runtime.compact import (  # noqa: E402
    COMPACT_MARKER_END,
    COMPACT_MARKER_START,
    CompactMiddleware,
    _reported_tokens,
    _strip_summary,
    _wrap_summary,
    build_mock_tools,
)


# ── 测试替身 ──


@tool
def read_file(path: str, limit: int = 100) -> str:
    """读取一个文件。"""
    return f"real:{path}"


class _FakeStream:
    """假模型：bind_tools / with_retry / astream 三件套。

    `_llm_type` 与 `profile` 是父类 SummarizationMiddleware.__init__ 会读取的属性
    （前者用于挑选近似 token 计数器），必须提供。
    """

    _llm_type = "openai-chat"
    profile: dict = {}

    def __init__(self, *, summary: str = "## 目标与进度\n已完成压缩", fail: bool = False):
        self.summary = summary
        self.fail = fail
        self.calls: list[list] = []
        self.bound_tools: list | None = None
        # bind_tools 返回克隆体，with_retry 也返回克隆体；用共享列表记录绑定过的工具
        self.bound_log: list[list] = []

    def bind_tools(self, tools):
        clone = _FakeStream(summary=self.summary, fail=self.fail)
        clone.calls = self.calls
        clone.bound_log = self.bound_log
        clone.bound_tools = list(tools)
        clone.bound_log.append(list(tools))
        return clone

    def with_retry(self, **_kwargs):
        return self

    async def astream(self, payload, config=None):  # noqa: ANN001
        self.calls.append(list(payload))
        if self.fail:
            raise RuntimeError("上游 500")
        for piece in (self.summary[:6], self.summary[6:]):
            yield AIMessage(content=piece)


def _make_middleware(*, fake: _FakeStream | None = None, keep_rounds: int = 1,
                     tools=None, context_length: int = 1000,
                     threshold_ratio: float = 0.8) -> CompactMiddleware:
    fake = fake or _FakeStream()
    return CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=tools if tools is not None else [read_file],
        context_length=context_length,
        threshold_ratio=threshold_ratio,
        keep_rounds=keep_rounds,
    )


def _ai(content: str, *, tokens: int = 0, tool_calls=None) -> AIMessage:
    msg = AIMessage(content=content, tool_calls=tool_calls or [])
    if tokens:
        msg.usage_metadata = {
            "input_tokens": tokens,
            "output_tokens": 1,
            "total_tokens": tokens,
        }
    return msg


# ── 1. API token 提取 ──


def test_reported_tokens_uses_api_value():
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="hi"),
        _ai("old", tokens=500),
        HumanMessage(content="new"),
        _ai("latest", tokens=9000),
    ]
    assert _reported_tokens(messages) == 9000


def test_reported_tokens_zero_without_api_data():
    messages = [SystemMessage(content="sys"), HumanMessage(content="hi")]
    assert _reported_tokens(messages) == 0


# ── 2. mock 工具 schema 逐字节一致 ──


def test_mock_tool_schema_is_byte_identical():
    mock = build_mock_tools([read_file])[0]
    assert convert_to_openai_tool(mock) == convert_to_openai_tool(read_file)
    assert mock.name == read_file.name
    assert mock.description == read_file.description


def test_mock_tool_does_not_execute_real_impl():
    mock = build_mock_tools([read_file])[0]
    assert mock.invoke({"path": "/etc/passwd"}) == ""


# ── 3. 轮次边界：保留 N 轮且不拆 tool 组 ──


def test_cutoff_keeps_last_n_rounds():
    mw = _make_middleware(keep_rounds=1)
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="r1"),
        _ai("a1"),
        HumanMessage(content="r2"),
        _ai("a2"),
        HumanMessage(content="r3"),
        _ai("a3"),
    ]
    # 保留最后 1 轮 → 切在 r3 之前
    assert mw._determine_cutoff_index(messages) == 5


def test_cutoff_never_splits_tool_call_group():
    mw = _make_middleware(keep_rounds=1)
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="r1"),
        _ai("a1"),
        HumanMessage(content="r2"),
        # r2 内的 tool 对：AIMessage(tool_calls) + ToolMessage
        _ai("", tool_calls=[{"id": "call_1", "name": "read_file", "args": {"path": "x"}}]),
        ToolMessage(content="tool out", tool_call_id="call_1"),
        HumanMessage(content="r3"),
        _ai("a3"),
    ]
    cutoff = mw._determine_cutoff_index(messages)
    # 切点落在 r3（索引 6）。若切点被 tool 组阻挡，必须整组保留或整组压缩，
    # 绝不能出现「ToolMessage 被压缩但对应 AIMessage(tool_calls) 被保留」。
    preserved = messages[cutoff:]
    kept_tool_ids = {
        tc["id"] for m in preserved if isinstance(m, AIMessage) for tc in (m.tool_calls or [])
    }
    kept_tool_msgs = {m.tool_call_id for m in preserved if isinstance(m, ToolMessage)}
    # 保留区里不允许出现「有 ToolMessage 但没有对应 tool_calls」的悬空
    assert kept_tool_msgs <= kept_tool_ids
    # 压缩区同理：不能留下悬空的 tool_calls
    dropped = messages[:cutoff]
    dropped_tool_ids = {
        tc["id"] for m in dropped if isinstance(m, AIMessage) for tc in (m.tool_calls or [])
    }
    dropped_tool_msgs = {m.tool_call_id for m in dropped if isinstance(m, ToolMessage)}
    assert dropped_tool_msgs <= dropped_tool_ids


def test_cutoff_advances_to_include_ai_when_boundary_is_tool_message():
    mw = _make_middleware(keep_rounds=1)
    # 构造切点恰好落在 ToolMessage 上的情形：最后 1 轮起点之后的 ToolMessage
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="r1"),
        _ai("", tool_calls=[{"id": "c1", "name": "read_file", "args": {"path": "x"}}]),
        ToolMessage(content="out", tool_call_id="c1"),
        HumanMessage(content="r2"),
        _ai("a2"),
    ]
    cutoff = mw._determine_cutoff_index(messages)
    preserved = messages[cutoff:]
    kept_ids = {
        tc["id"] for m in preserved if isinstance(m, AIMessage) for tc in (m.tool_calls or [])
    }
    kept_tool = {m.tool_call_id for m in preserved if isinstance(m, ToolMessage)}
    assert kept_tool <= kept_ids


def test_cutoff_zero_when_nothing_to_compress():
    mw = _make_middleware(keep_rounds=1)
    messages = [SystemMessage(content="sys"), HumanMessage(content="only")]
    assert mw._determine_cutoff_index(messages) == 0


# ── 4. 写回：摘要并入 System，顺序正确 ──


def test_strip_and_wrap_summary_are_inverse():
    base = "你是 Faust。"
    wrapped = _wrap_summary(base, "## 目标与进度\n做重构")
    assert COMPACT_MARKER_START in wrapped and COMPACT_MARKER_END in wrapped
    assert _strip_summary(wrapped) == base


def test_strip_summary_replaces_previous_summary():
    first = _wrap_summary("你是 Faust。", "第一版摘要")
    second = _wrap_summary(_strip_summary(first), "第二版摘要")
    assert "第一版摘要" not in second
    assert "第二版摘要" in second
    assert second.count(COMPACT_MARKER_START) == 1


def test_build_replacement_merges_into_system():
    mw = _make_middleware()
    messages = [
        SystemMessage(content="你是 Faust。", id="s1"),
        HumanMessage(content="r1", id="h1"),
        _ai("a1", tokens=100),
        HumanMessage(content="r2", id="h2"),
        _ai("a2", tokens=200),
    ]
    replacement = mw._build_replacement(messages, "摘要正文")
    assert len(replacement) == 1
    only = replacement[0]
    assert isinstance(only, SystemMessage)
    assert only.id == "s1"
    assert "你是 Faust。" in only.content
    assert "摘要正文" in only.content


def test_build_replacement_falls_back_to_human_without_system():
    """Subagent 的 system prompt 由 create_agent 在调用时注入，不在 state 里。"""
    mw = _make_middleware()
    messages = [HumanMessage(content="r1", id="h1"), _ai("a1", tokens=100)]
    replacement = mw._build_replacement(messages, "摘要正文")
    assert len(replacement) == 1
    assert isinstance(replacement[0], HumanMessage)
    assert "摘要正文" in replacement[0].content


# ── 5. abefore_model 端到端（离线） ──


def test_abefore_model_skips_without_api_tokens():
    mw = _make_middleware(context_length=1000, threshold_ratio=0.5)
    messages = [SystemMessage(content="sys"), HumanMessage(content="r1"), _ai("a1")]
    assert asyncio.run(mw.abefore_model({"messages": messages}, None)) is None


def test_abefore_model_skips_below_threshold():
    mw = _make_middleware(context_length=10000, threshold_ratio=0.8)
    messages = [
        SystemMessage(content="sys"),
        HumanMessage(content="r1"),
        _ai("a1", tokens=100),
        HumanMessage(content="r2"),
        _ai("a2", tokens=100),
    ]
    assert asyncio.run(mw.abefore_model({"messages": messages}, None)) is None


def test_abefore_model_compacts_and_orders_messages():
    fake = _FakeStream(summary="## 目标与进度\n完成压缩")
    mw = _make_middleware(fake=fake, keep_rounds=1, context_length=1000, threshold_ratio=0.5)
    messages = [
        SystemMessage(content="你是 Faust。", id="s1"),
        HumanMessage(content="r1", id="h1"),
        _ai("a1", tokens=900),
        HumanMessage(content="r2", id="h2"),
        _ai("a2", tokens=950),
    ]
    out = asyncio.run(mw.abefore_model({"messages": messages}, None))
    assert out is not None
    updates = out["messages"]
    # 第一条必须是清空哨兵
    assert isinstance(updates[0], RemoveMessage)
    assert updates[0].id == REMOVE_ALL_MESSAGES
    # 然后是并入摘要的 System（同 id 就地替换）
    assert isinstance(updates[1], SystemMessage)
    assert updates[1].id == "s1"
    assert "完成压缩" in updates[1].content
    # 最后是保留的最近 1 轮
    assert [type(m).__name__ for m in updates[2:]] == ["HumanMessage", "AIMessage"]
    assert updates[2].content == "r2"


def test_compact_call_reuses_full_prefix_with_mock_tools():
    """压缩调用的 payload 前缀必须与主请求一致（保缓存）。"""
    fake = _FakeStream()
    mw = _make_middleware(fake=fake, keep_rounds=1, context_length=1000, threshold_ratio=0.5)
    messages = [
        SystemMessage(content="你是 Faust。", id="s1"),
        HumanMessage(content="r1", id="h1"),
        _ai("a1", tokens=900),
        HumanMessage(content="r2", id="h2"),
        _ai("a2", tokens=950),
    ]
    asyncio.run(mw.abefore_model({"messages": messages}, None))
    assert fake.calls, "压缩调用没有发生"
    sent = fake.calls[0]
    # 前缀与原始 messages 完全一致（逐条同 id），最后追加一条指令
    assert [m.id for m in sent[: len(messages)]] == [m.id for m in messages]
    assert len(sent) == len(messages) + 1
    assert isinstance(sent[-1], HumanMessage)
    assert "调用任何工具" in sent[-1].content
    # 工具被替换成同 schema 的 mock
    assert fake.bound_log, "压缩调用没有绑定工具"
    assert convert_to_openai_tool(fake.bound_log[0][0]) == convert_to_openai_tool(read_file)


def test_abefore_model_degrades_on_failure():
    """压缩失败 → 不改动 checkpoint，返回 None（本轮继续）。"""
    fake = _FakeStream(fail=True)
    mw = _make_middleware(fake=fake, keep_rounds=1, context_length=1000, threshold_ratio=0.5)
    messages = [
        SystemMessage(content="sys", id="s1"),
        HumanMessage(content="r1", id="h1"),
        _ai("a1", tokens=900),
        HumanMessage(content="r2", id="h2"),
        _ai("a2", tokens=950),
    ]
    assert asyncio.run(mw.abefore_model({"messages": messages}, None)) is None
