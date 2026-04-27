from __future__ import annotations

import json
import uuid
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime
from typing_extensions import override

from faust_backend.plugin_system import MiddlewareSpec, PluginContext, PluginManifest


class MultimodalBridgeMiddleware(AgentMiddleware):
    def __init__(self, ctx: PluginContext) -> None:
        super().__init__()
        self.ctx = ctx
        self._processed_tool_keys: set[str] = set()
        self._ttl_by_message_id: dict[str, int] = {}
        self._last_user_signature: str | None = None

    @override
    def before_model(self, state: AgentState[Any], runtime: Runtime) -> dict[str, Any] | None:
        if not bool(self.ctx.get_config("ENABLE_MM_BRIDGE", True)):
            return None

        messages = state.get("messages") or []
        if not messages:
            return None

        max_scan = self._safe_int(self.ctx.get_config("MAX_SCAN_TOOL_MESSAGES", 6), 6)
        max_scan = max(1, max_scan)
        remove_source = bool(self.ctx.get_config("REMOVE_SOURCE_TOOL_MESSAGE", False))
        keep_turns = max(0, self._safe_int(self.ctx.get_config("IMAGE_MESSAGE_KEEP_TURNS", 2), 2))

        scanned = 0
        additions: list[HumanMessage] = []
        removals: list[RemoveMessage] = []
        remove_ids: set[str] = set()

        # 每出现一次新用户输入，所有已追踪图片消息TTL减1，归零即删除。
        user_turns = self._consume_new_user_turns(messages)
        if user_turns > 0:
            expired_ids: list[str] = []
            for mid in list(self._ttl_by_message_id.keys()):
                self._ttl_by_message_id[mid] = int(self._ttl_by_message_id[mid]) - user_turns
                if self._ttl_by_message_id[mid] <= 0:
                    expired_ids.append(mid)
            for mid in expired_ids:
                self._ttl_by_message_id.pop(mid, None)
                self._append_removal(removals, remove_ids, mid)

        for msg in reversed(messages):
            if not isinstance(msg, ToolMessage):
                continue
            scanned += 1
            if scanned > max_scan:
                break

            tool_key = self._tool_message_key(msg)
            if tool_key in self._processed_tool_keys:
                continue

            payload = self._parse_tool_payload(msg)
            if payload is None:
                continue

            mm_msg = self._payload_to_mm_message(payload)
            if mm_msg is None:
                continue

            self._processed_tool_keys.add(tool_key)
            additions.append(mm_msg)
            src_mid = getattr(msg, "id", None)
            if src_mid is not None:
                src_mid = str(src_mid)
                if remove_source or keep_turns == 0:
                    self._append_removal(removals, remove_ids, src_mid)
                else:
                    self._ttl_by_message_id[src_mid] = keep_turns

            mm_mid = getattr(mm_msg, "id", None)
            if mm_mid is not None:
                mm_mid = str(mm_mid)
                if keep_turns == 0:
                    self._append_removal(removals, remove_ids, mm_mid)
                else:
                    self._ttl_by_message_id[mm_mid] = keep_turns

        if not additions and not removals:
            return None

        # 逆序扫描得到的是最新在前，这里反转回时间顺序
        additions.reverse()
        removals.reverse()
        return {"messages": [*removals, *additions]}

    @override
    async def abefore_model(self, state: AgentState[Any], runtime: Runtime) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return default

    @staticmethod
    def _parse_tool_payload(msg: ToolMessage) -> dict[str, Any] | None:
        content = getattr(msg, "content", "")
        if not isinstance(content, str):
            return None
        try:
            data = json.loads(content)
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if str(data.get("kind") or "").strip().lower() != "multimodal_tool_result":
            return None
        return data

    @staticmethod
    def _tool_message_key(msg: ToolMessage) -> str:
        mid = getattr(msg, "id", None)
        if mid is not None:
            return f"id:{mid}"
        tcid = getattr(msg, "tool_call_id", None)
        content = getattr(msg, "content", "")
        return f"fallback:{tcid}:{hash(str(content))}"

    @staticmethod
    def _append_removal(removals: list[RemoveMessage], remove_ids: set[str], mid: str) -> None:
        if not mid or mid in remove_ids:
            return
        remove_ids.add(mid)
        removals.append(RemoveMessage(id=mid))

    def _consume_new_user_turns(self, messages: list[Any]) -> int:
        signature = self._latest_user_signature(messages)
        if signature is None:
            return 0
        if signature == self._last_user_signature:
            return 0
        self._last_user_signature = signature
        return 1

    @staticmethod
    def _latest_user_signature(messages: list[Any]) -> str | None:
        for msg in reversed(messages):
            if not isinstance(msg, HumanMessage):
                continue
            if bool((getattr(msg, "additional_kwargs", {}) or {}).get("_mm_bridge_generated", False)):
                continue
            mid = getattr(msg, "id", None)
            if mid is not None:
                return f"id:{mid}"
            return f"content:{str(getattr(msg, 'content', ''))}"
        return None

    @staticmethod
    def _payload_to_mm_message(payload: dict[str, Any]) -> HumanMessage | None:
        blocks: list[dict[str, Any]] = []

        text = str(payload.get("text") or "工具返回了一张图片，请根据图片内容完成分析。")
        blocks.append({"type": "text", "text": text})

        images = payload.get("images")
        if not isinstance(images, list):
            return None

        for item in images:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            blocks.append({"type": "image_url", "image_url": {"url": url}})

        if len(blocks) <= 1:
            return None

        return HumanMessage(
            id=str(uuid.uuid4()),
            content=blocks,
            additional_kwargs={"_mm_bridge_generated": True},
        )


class Plugin:
    manifest = PluginManifest(
        plugin_id="mm_bridge",
        name="Multimodal Bridge Plugin",
        version="1.0.0",
        description="Convert tool image outputs into image_url multimodal blocks",
        author="faust",
        homepage="",
        enabled=True,
        permissions=["middleware:mm-bridge"],
        priority=120,
    )

    def startup(self, ctx: PluginContext) -> None:
        ctx.register_config(
            """
            ENABLE_MM_BRIDGE:bool:启用多模态桥接=true
            REMOVE_SOURCE_TOOL_MESSAGE:bool:桥接后删除源ToolMessage=false
            MAX_SCAN_TOOL_MESSAGES:int:每轮扫描最近ToolMessage条数=6
            IMAGE_MESSAGE_KEEP_TURNS:int:图片消息保留轮数=2
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
                name="multimodal_bridge",
                middleware=MultimodalBridgeMiddleware(ctx),
                priority=120,
                enabled_by_default=True,
                description="将工具返回的图片JSON转换为模型可读的image_url多模态内容块",
            )
        ]

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "plugin": "mm_bridge"}

    def Heartbeat(self, ctx: PluginContext) -> None:
        pass


def get_plugin() -> Plugin:
    return Plugin()
