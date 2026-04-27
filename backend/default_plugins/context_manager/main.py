from __future__ import annotations

import uuid
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import AnyMessage, HumanMessage, RemoveMessage, SystemMessage
from langgraph.runtime import Runtime
from typing_extensions import override

from faust_backend.plugin_system import MiddlewareSpec, PluginContext, PluginManifest


DEFAULT_TRIGGER_USER_KEEP = 3
DEFAULT_NORMAL_USER_KEEP = 50


class ContextPruneMiddleware(AgentMiddleware):
    """自动清理过期用户消息，控制上下文噪声。"""

    def __init__(self, ctx: PluginContext) -> None:
        super().__init__()
        self.ctx = ctx

    @override
    def before_model(self, state: AgentState[Any], runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        if not messages:
            return None

        self._ensure_message_ids(messages)
        trigger_keep, normal_keep = self._resolve_limits()
        removals = self._build_removals(messages, trigger_keep=trigger_keep, normal_keep=normal_keep)
        if not removals:
            return None
        return {"messages": removals}

    @override
    async def abefore_model(
        self, state: AgentState[Any], runtime: Runtime
    ) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    def _resolve_limits(self) -> tuple[int, int]:
        trigger_keep = self._coerce_non_negative_int(
            self.ctx.get_config("TRIGGER_USER_KEEP", DEFAULT_TRIGGER_USER_KEEP),
            DEFAULT_TRIGGER_USER_KEEP,
        )
        normal_keep = self._coerce_non_negative_int(
            self.ctx.get_config("NORMAL_USER_KEEP", DEFAULT_NORMAL_USER_KEEP),
            DEFAULT_NORMAL_USER_KEEP,
        )
        return trigger_keep, normal_keep

    @staticmethod
    def _coerce_non_negative_int(value: Any, default: int) -> int:
        try:
            return max(0, int(value))
        except Exception:
            return default

    @staticmethod
    def _ensure_message_ids(messages: list[AnyMessage]) -> None:
        for msg in messages:
            if getattr(msg, "id", None) is None:
                msg.id = str(uuid.uuid4())

    @staticmethod
    def _is_system_message(msg: AnyMessage) -> bool:
        if isinstance(msg, SystemMessage):
            return True
        return str(getattr(msg, "type", "")).lower() == "system"

    @staticmethod
    def _is_user_message(msg: AnyMessage) -> bool:
        if isinstance(msg, HumanMessage):
            return True
        role = str(getattr(msg, "type", "")).lower()
        return role in {"human", "user"}

    @staticmethod
    def _message_text(msg: AnyMessage) -> str:
        content = getattr(msg, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                if isinstance(item, str):
                    chunks.append(item)
                elif isinstance(item, dict):
                    txt = item.get("text")
                    if txt is not None:
                        chunks.append(str(txt))
                else:
                    chunks.append(str(item))
            return "\n".join(chunks)
        return str(content)

    def _is_trigger_user_message(self, msg: AnyMessage) -> bool:
        if not self._is_user_message(msg):
            return False
        return self._message_text(msg).lstrip().startswith("<Trigger>")

    def _build_removals(
        self,
        messages: list[AnyMessage],
        *,
        trigger_keep: int,
        normal_keep: int,
    ) -> list[RemoveMessage]:
        trigger_indexes: list[int] = []
        normal_indexes: list[int] = []

        for idx, msg in enumerate(messages):
            if self._is_system_message(msg):
                continue
            if not self._is_user_message(msg):
                continue
            if self._is_trigger_user_message(msg):
                trigger_indexes.append(idx)
            else:
                normal_indexes.append(idx)

        remove_indexes: set[int] = set()
        if len(trigger_indexes) > trigger_keep:
            remove_indexes.update(trigger_indexes[: len(trigger_indexes) - trigger_keep])
        if len(normal_indexes) > normal_keep:
            remove_indexes.update(normal_indexes[: len(normal_indexes) - normal_keep])

        removals: list[RemoveMessage] = []
        for idx in sorted(remove_indexes):
            msg_id = getattr(messages[idx], "id", None)
            if msg_id is not None:
                removals.append(RemoveMessage(id=str(msg_id)))
        return removals


class Plugin:
    manifest = PluginManifest(
        plugin_id="context_manager",
        name="自动上下文管理器",
        version="1.0.0",
        description="在保留系统指令的同时，修剪过时的用户上下文",
        author="allenlee",
        homepage="",
        enabled=False,
        permissions=["middleware:context-prune"],
        priority=260,
    )

    def startup(self, ctx: PluginContext) -> None:
        ctx.register_config(
            """
            TRIGGER_USER_KEEP:int:<Trigger>消息保留条数=3
            NORMAL_USER_KEEP:int:普通用户消息保留条数=50
            """
        )

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self, ctx: PluginContext) -> None:
        pass

    def register_tools(self, ctx: PluginContext):
        return []

    def register_middlewares(self, ctx: PluginContext):
        return [
            MiddlewareSpec(
                name="context_prune",
                middleware=ContextPruneMiddleware(ctx),
                priority=260,
                enabled_by_default=True,
                description="在模型调用前清理超量用户消息，保留System消息",
            )
        ]

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "plugin": "context_manager"}

    def Heartbeat(self, ctx: PluginContext) -> None:
        pass


def get_plugin() -> Plugin:
    return Plugin()