"""ToolCallRepairMiddleware — 始终启用的消息修复中间件。

OpenAI 兼容接口要求历史消息中带 ``tool_calls`` 的 assistant 消息必须
紧跟响应每个 ``tool_call_id`` 的 ``ToolMessage``。当上下文被裁剪、工具
执行被中断，或子代理返回不完整消息序列时，会产生“悬空”的
``tool_calls``，触发如下错误：

    An assistant message with 'tool_calls' must be followed by tool
    messages responding to each 'tool_call_id'. (insufficient tool
    messages following tool_calls message)

本中间件通过 ``wrap_model_call`` / ``awrap_model_call`` 在每次模型调用
前重写 ``request.messages``：

- 为缺失响应的 ``tool_call_id`` 注入占位 ``ToolMessage``；
- 丢弃没有对应 assistant ``tool_calls`` 的孤立 ``ToolMessage``。

修复只作用于本次模型请求，不会写回 checkpoint，因此不会污染持久化
对话状态。

讨厌的终止对话功能!
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

from faust_backend.logger import get_logger

log = get_logger("faust.tool_call_repair")

PLACEHOLDER_CONTENT = "[工具调用结果缺失，已由 FaustBot 自动补全]"


class ToolCallRepairMiddleware(AgentMiddleware):
    """修复发送给模型的对话历史中悬空的 assistant tool_calls。"""

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步入口：修复消息后调用模型。"""
        if not request.messages:
            return handler(request)
        repaired = self._repair_messages(request.messages)
        return handler(request.override(messages=repaired))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步入口：修复消息后调用模型。"""
        if not request.messages:
            return await handler(request)
        repaired = self._repair_messages(request.messages)
        return await handler(request.override(messages=repaired))

    @classmethod
    def _repair_messages(cls, messages: list[AnyMessage]) -> list[AnyMessage]:
        """修复消息序列，返回新列表（不改动原列表）。"""
        repaired: list[AnyMessage] = []
        # tool_call_id -> tool name（来自 assistant 声明的 tool_calls）
        pending: dict[str, str] = {}
        injected = 0
        dropped = 0

        def flush_pending() -> None:
            nonlocal injected
            for tool_call_id, name in pending.items():
                repaired.append(cls._make_placeholder(tool_call_id, name))
                injected += 1
            pending.clear()

        for msg in messages:
            if isinstance(msg, AIMessage):
                tool_calls = list(getattr(msg, "tool_calls", None) or [])
                if tool_calls:
                    # 若前一个 assistant 的 tool_calls 尚未被响应，先补齐
                    if pending:
                        flush_pending()
                    for tc in tool_calls:
                        tcid = cls._tool_call_id(tc)
                        if tcid:
                            pending[tcid] = cls._tool_call_name(tc)
                repaired.append(msg)
            elif isinstance(msg, ToolMessage):
                tcid = getattr(msg, "tool_call_id", None)
                if tcid and tcid in pending:
                    pending.pop(tcid)
                    repaired.append(msg)
                else:
                    # 孤立的 ToolMessage：没有对应 assistant 声明，丢弃
                    dropped += 1
            else:
                # 其他消息（Human/System 等）：若 tool_calls 悬空，先补齐
                if pending:
                    flush_pending()
                repaired.append(msg)

        # 序列末尾仍有未响应的 tool_calls
        if pending:
            flush_pending()

        if injected or dropped:
            log.info(
                "ToolCallRepair: 注入 %d 个占位 ToolMessage，丢弃 %d 个孤立 ToolMessage",
                injected,
                dropped,
            )
        return repaired

    @staticmethod
    def _tool_call_id(tool_call: Any) -> str | None:
        if isinstance(tool_call, dict):
            return str(tool_call.get("id") or "") or None
        return str(getattr(tool_call, "id", "") or "") or None

    @staticmethod
    def _tool_call_name(tool_call: Any) -> str:
        if isinstance(tool_call, dict):
            return str(tool_call.get("name") or "")
        return str(getattr(tool_call, "name", "") or "")

    @staticmethod
    def _make_placeholder(tool_call_id: str, name: str) -> ToolMessage:
        return ToolMessage(
            content=PLACEHOLDER_CONTENT,
            tool_call_id=tool_call_id,
            name=name or None,
        )
