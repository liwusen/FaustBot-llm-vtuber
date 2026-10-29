"""ToolCallRepairMiddleware — 始终启用的消息修复中间件。

OpenAI 兼容接口要求历史消息中带 ``tool_calls`` 的 assistant 消息必须
紧跟响应每个 ``tool_call_id`` 的 ``ToolMessage``。当上下文被裁剪、工具
执行被中断，或子代理返回不完整消息序列时，会产生“悬空”的
``tool_calls``，触发如下错误：

    An assistant message with 'tool_calls' must be followed by tool
    messages responding to each 'tool_call_id'. (insufficient tool
    messages following tool_calls message)

``repair_dangling_tool_calls`` 是唯一实现，两类调用方共用：

- ``ToolCallRepairMiddleware`` 在每次模型调用前重写 ``request.messages``
  （``wrap_model_call`` / ``awrap_model_call``）；
- ``CompactMiddleware`` 在构造压缩请求前修复历史——压缩调用直接打
  provider、不经过中间件链，必须自己修复。

规则：

- 对**被打断/作废**的 ``tool_calls``（后续被 Human/System/新 AI 消息打断，
  没有对应 ToolMessage）清空该 AIMessage 的 ``tool_calls`` —— 这类是
  正常的中断流程（用户新消息、agent 中断），不是历史损坏，不应注入占位；
- 对**序列结尾**真实悬空的 ``tool_calls``（上下文被裁剪 / 工具执行被中断
  的遗留）注入占位 ``ToolMessage``；
- 丢弃没有对应 assistant ``tool_calls`` 的孤立 ``ToolMessage``。
  修复只作用于本次请求，不写回 checkpoint，因此不会污染持久化
  对话状态。

``AIMessage.invalid_tool_calls``（参数 JSON 解析失败）与 ``tool_calls`` 一样
会被 langchain_openai 序列化成 OpenAI 的 ``tool_calls``
（``if message.tool_calls or message.invalid_tool_calls``），因此两者一并处理。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from typing_extensions import override

from faust_backend.core.log import get_logger

log = get_logger("faust.tool_call_repair")

PLACEHOLDER_CONTENT = "[工具调用结果缺失，已由 FaustBot 自动补全]"

#: content 里代表「模型发起工具调用」的块类型（Anthropic / Responses / v1 标准）。
#: `server_tool_use` / `web_search_call` 是 provider 托管的服务端工具，没有
#: tool_call_id，也**不需要**客户端响应，绝不能在这里被清掉。
_TOOL_CALL_BLOCK_TYPES = frozenset({"tool_use", "function_call", "tool_call"})


def repair_dangling_tool_calls(messages: list[AnyMessage]) -> list[AnyMessage]:
    """修复消息序列里悬空的 assistant tool_calls，返回新列表（不改动入参）。

    区分两种“悬空”并分别处理：
    1. 被打断/作废的工具调用：AIMessage(tool_calls) 后面跟的是
       Human/System/新的 AIMessage（没有对应 ToolMessage 跟进）——
       说明该调用从未执行就被放弃（如用户新消息打断、agent 中断）。
       此时**清空该 AIMessage 的 tool_calls**（而不是注入占位），
       避免 LLM 看到“调用了工具但没有结果”的幽灵调用。
    2. 序列真正结尾的悬空：消息序列在 AIMessage(tool_calls) 处结束——
       可能是上下文被裁剪/工具执行被中断的遗留，此时**注入占位**
       ToolMessage，满足 OpenAI 兼容接口“tool_calls 必须被响应”的要求。

    只有情况 2 才会注入占位，因此修复器只在极少数（真实损坏）场景生效。
    """
    repaired: list[AnyMessage] = []
    # tool_call_id -> (tool name, AIMessage 在 repaired 中的下标)
    pending: dict[str, tuple[str, int]] = {}
    injected = 0
    dropped = 0

    def drop_pending() -> None:
        """被打断的工具调用：清空对应 AIMessage 的 tool_calls（作废）。"""
        nonlocal dropped
        for tool_call_id, (_name, ai_idx) in pending.items():
            ai_message = repaired[ai_idx]
            if isinstance(ai_message, AIMessage) and _all_tool_calls(ai_message):
                repaired[ai_idx] = _without_tool_call(ai_message, tool_call_id)
                dropped += 1
        pending.clear()

    def flush_pending() -> None:
        """序列结尾的悬空：注入占位 ToolMessage。"""
        nonlocal injected
        for tool_call_id, (name, _ai_idx) in pending.items():
            repaired.append(_make_placeholder(tool_call_id, name))
            injected += 1
        pending.clear()

    for message in messages:
        if isinstance(message, AIMessage):
            tool_calls = _all_tool_calls(message)
            if tool_calls:
                # 前一批 tool_calls 尚未被响应，又有新的 AI 调用 → 前一批作废
                if pending:
                    drop_pending()
                ai_idx = len(repaired)
                repaired.append(message)
                for tool_call in tool_calls:
                    tool_call_id = _tool_call_id(tool_call)
                    if tool_call_id:
                        pending[tool_call_id] = (_tool_call_name(tool_call), ai_idx)
            else:
                # 无 tool_calls 的 AI：若前一批悬空，作废
                if pending:
                    drop_pending()
                repaired.append(message)
        elif isinstance(message, ToolMessage):
            tool_call_id = getattr(message, "tool_call_id", None)
            if tool_call_id and tool_call_id in pending:
                pending.pop(tool_call_id)
                repaired.append(message)
            else:
                # 孤立的 ToolMessage：没有对应 assistant 声明，丢弃
                dropped += 1
        else:
            # 其他消息（Human/System 等）：若前一批 tool_calls 悬空，
            # 说明被新输入打断，作废而非注入占位
            if pending:
                drop_pending()
            repaired.append(message)

    # 序列末尾仍有未响应的 tool_calls（真·裁切/中断遗留）→ 注入占位
    if pending:
        flush_pending()

    if injected or dropped:
        log.info(
            "ToolCallRepair: 注入 %d 个占位 ToolMessage，作废 %d 个被打断的 tool_calls",
            injected,
            dropped,
        )
    return repaired


class ToolCallRepairMiddleware(AgentMiddleware):
    """在每次模型调用前修复请求消息里悬空的 assistant tool_calls。"""

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步入口：修复消息后调用模型。"""
        if not request.messages:
            return handler(request)
        return handler(request.override(messages=repair_dangling_tool_calls(request.messages)))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步入口：修复消息后调用模型。"""
        if not request.messages:
            return await handler(request)
        return await handler(
            request.override(messages=repair_dangling_tool_calls(request.messages))
        )


def _all_tool_calls(message: AIMessage) -> list[Any]:
    """真实 tool_calls + 参数解析失败的 invalid_tool_calls。

    两者都会被序列化成 OpenAI 的 ``tool_calls``，都必须被逐条响应。
    """
    return [
        *(getattr(message, "tool_calls", None) or []),
        *(getattr(message, "invalid_tool_calls", None) or []),
    ]


def _without_tool_call(message: AIMessage, tool_call_id: str) -> AIMessage:
    """返回清空了指定 tool_call 的 AIMessage 副本（结构化列表 + content 块同步清理）。

    只清 `tool_calls` 是不够的：Anthropic 的 `tool_use`、OpenAI Responses 的
    `function_call` 是 **content block**，序列化时会重新变成上游的工具调用声明。
    若只删结构化列表，content 里仍留着调用却没有对应结果，Anthropic/Responses
    照样 400（"tool_use ids were found without tool_result blocks"）。
    """
    cleared = message.model_copy(deep=True)
    cleared.tool_calls = [
        tool_call
        for tool_call in (cleared.tool_calls or [])
        if _tool_call_id(tool_call) != tool_call_id
    ]
    cleared.invalid_tool_calls = [
        tool_call
        for tool_call in (getattr(cleared, "invalid_tool_calls", None) or [])
        if _tool_call_id(tool_call) != tool_call_id
    ]
    if isinstance(cleared.content, list):
        cleared.content = [
            block
            for block in cleared.content
            if not (
                isinstance(block, dict)
                and str(block.get("type") or "") in _TOOL_CALL_BLOCK_TYPES
                and str(block.get("id") or "") == tool_call_id
            )
        ]
    return cleared


def _tool_call_id(tool_call: Any) -> str | None:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "") or None
    return str(getattr(tool_call, "id", "") or "") or None


def _tool_call_name(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("name") or "")
    return str(getattr(tool_call, "name", "") or "")


def _make_placeholder(tool_call_id: str, name: str) -> ToolMessage:
    return ToolMessage(
        content=PLACEHOLDER_CONTENT,
        tool_call_id=tool_call_id,
        name=name or None,
    )
