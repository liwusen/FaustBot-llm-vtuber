from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from faust_backend.runtime.middleware import wrap_tools
from faust_backend.runtime.tool_call_repair import (
    PLACEHOLDER_CONTENT,
    ToolCallRepairMiddleware,
)


def _ai(content: str = "", tool_calls: list[dict] | None = None) -> AIMessage:
    return AIMessage(content=content, tool_calls=tool_calls or [])


def _tool(tool_call_id: str, content: str = "ok") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=tool_call_id)


def _tool_ids(messages: list) -> set[str]:
    return {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}


def test_complete_sequence_is_unchanged():
    messages = [
        HumanMessage(content="hi"),
        _ai("thinking", [{"id": "t1", "name": "read", "args": {}}]),
        _tool("t1", "file content"),
        _ai("done"),
    ]
    repaired = ToolCallRepairMiddleware._repair_messages(messages)
    assert repaired == messages


def test_interrupted_tool_call_is_voided_not_placeholder():
    """工具调用被新用户消息打断 → 作废（清空 tool_calls），不注入占位。"""
    messages = [
        HumanMessage(content="hi"),
        _ai("", [{"id": "t1", "name": "read", "args": {}}]),
        HumanMessage(content="hello?"),
    ]
    repaired = ToolCallRepairMiddleware._repair_messages(messages)
    tool_ids = _tool_ids(repaired)
    assert "t1" not in tool_ids, "被打断的调用不应注入占位"
    assert not any(isinstance(m, ToolMessage) for m in repaired)
    # 原 AIMessage 的 tool_calls 被清空
    ai = next(m for m in repaired if isinstance(m, AIMessage))
    assert ai.tool_calls == []


def test_partial_response_then_interrupt_voids_remaining():
    """t1 已响应，t2 未执行就遇到新消息 → t2 作废，t1 保持。"""
    messages = [
        HumanMessage(content="hi"),
        _ai(
            "",
            [
                {"id": "t1", "name": "read", "args": {}},
                {"id": "t2", "name": "search", "args": {}},
            ],
        ),
        _tool("t1"),
        HumanMessage(content="continue"),
    ]
    repaired = ToolCallRepairMiddleware._repair_messages(messages)
    tool_ids = _tool_ids(repaired)
    assert "t1" in tool_ids
    assert "t2" not in tool_ids, "未执行的 t2 应作废而不是注入占位"
    ai = next(m for m in repaired if isinstance(m, AIMessage))
    assert [tc["id"] for tc in ai.tool_calls] == ["t1"]


def test_dangling_tool_calls_at_end_get_placeholder():
    messages = [
        HumanMessage(content="hi"),
        _ai("", [{"id": "t1", "name": "read", "args": {}}]),
    ]
    repaired = ToolCallRepairMiddleware._repair_messages(messages)
    assert "t1" in _tool_ids(repaired)


def test_orphan_tool_message_is_dropped():
    messages = [
        HumanMessage(content="hi"),
        _tool("ghost"),
        _ai("done"),
    ]
    repaired = ToolCallRepairMiddleware._repair_messages(messages)
    assert "ghost" not in _tool_ids(repaired)
    assert any(isinstance(m, AIMessage) for m in repaired)


def test_tool_message_with_unknown_id_after_pending_is_dropped():
    messages = [
        HumanMessage(content="hi"),
        _ai("", [{"id": "t1", "name": "read", "args": {}}]),
        _tool("t2", "unexpected"),
        HumanMessage(content="continue"),
    ]
    repaired = ToolCallRepairMiddleware._repair_messages(messages)
    tool_ids = _tool_ids(repaired)
    assert "t2" not in tool_ids
    # t1 被后面的 HumanMessage 打断 → 作废，不再注入占位
    assert "t1" not in tool_ids


def test_original_list_not_mutated():
    messages = [
        HumanMessage(content="hi"),
        _ai("", [{"id": "t1", "name": "read", "args": {}}]),
        HumanMessage(content="hello?"),
    ]
    snapshot = list(messages)
    ToolCallRepairMiddleware._repair_messages(messages)
    assert messages == snapshot


def test_wrap_tools_skips_non_base_tool_without_crashing():
    """回归：wrap_tools 遇到非 BaseTool 条目应跳过而非 NameError。"""
    from langchain_core.tools import tool as lc_tool

    @lc_tool
    def sample_tool(x: int) -> int:
        """示例工具：返回输入值。"""
        return x

    result = wrap_tools([sample_tool, lambda: "bare callable"])  # type: ignore[list-item]
    assert len(result) == 1
    assert result[0] is sample_tool


def test_wrap_model_call_forwards_repaired_messages():
    middleware = ToolCallRepairMiddleware()
    calls: list[list] = []

    def handler(request):
        calls.append(request.messages)
        from langchain.agents.middleware.types import ModelResponse

        return ModelResponse(result=[_ai("final")])

    from langchain.agents.middleware.types import ModelRequest

    request = ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[
            HumanMessage(content="hi"),
            _ai("", [{"id": "t1", "name": "read", "args": {}}]),
            HumanMessage(content="hello?"),
        ],
    )
    middleware.wrap_model_call(request, handler)
    assert calls, "handler 必须被调用"
    # 被打断的 t1 被作废：转发给模型的消息里不再有该占位/调用
    assert "t1" not in _tool_ids(calls[0])
    ai = next(m for m in calls[0] if isinstance(m, AIMessage))
    assert ai.tool_calls == []
