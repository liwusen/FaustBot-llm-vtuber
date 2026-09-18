"""Multimodal Bridge Middleware — built-in, always enabled.

Converts tool image outputs (kind: "multimodal_tool_result") into image_url
multimodal blocks so vision-capable LLMs can see tool-returned images directly.

工具结果的文本里不带 base64（会被按文本计 token），只带 artifact 引用；
图片本体在 OutputStore 里，桥接时按引用取回。

Config keys (in faust.config.json, AI Provider section):
    MM_BRIDGE_MAX_SCAN  (int, default 6)   — max ToolMessages scanned per turn
    MM_BRIDGE_REMOVE_SOURCE (bool, default False) — delete source ToolMessage after bridging
    MM_BRIDGE_KEEP_TURNS (int, default 2)   — image message TTL in user turns; 0 = delete immediately
    MM_BRIDGE_MAX_PIXELS (int, default 2_000_000) — resize images whose total pixel count
        (width × height) exceeds this threshold before sending to the LLM. 0 disables resizing.
"""
from __future__ import annotations

import base64
import io
import json
import uuid
from typing import Any

from PIL import Image

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage
from langgraph.runtime import Runtime
from typing_extensions import override

import faust_backend.config_loader as conf
from faust_backend.runtime.output_store import get_output_store


def _collect_urls(images: Any) -> list[str]:
    """把 images 字段（URL 字符串或 {"url": ...} 字典的列表）规范成 URL 列表。"""
    urls: list[str] = []
    for img in images or []:
        if isinstance(img, str) and img:
            urls.append(img)
        elif isinstance(img, dict) and img.get("url"):
            urls.append(str(img["url"]))
    return urls


class MultimodalBridgeMiddleware(AgentMiddleware):
    def __init__(self) -> None:
        super().__init__()
        self._processed_tool_keys: set[str] = set()
        self._ttl_by_message_id: dict[str, int] = {}
        self._last_user_signature: str | None = None

    @override
    def before_model(self, state: AgentState[Any], runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        if not messages:
            return None

        max_scan = max(1, int(getattr(conf, 'MM_BRIDGE_MAX_SCAN', 6) or 6))
        remove_source = bool(getattr(conf, 'MM_BRIDGE_REMOVE_SOURCE', False))
        keep_turns = max(0, int(getattr(conf, 'MM_BRIDGE_KEEP_TURNS', 2) or 2))
        self._max_pixels = max(0, int(getattr(conf, 'MM_BRIDGE_MAX_PIXELS', 2000000) or 2000000))

        scanned = 0
        additions: list[HumanMessage] = []
        removals: list[RemoveMessage] = []
        remove_ids: set[str] = set()

        # TTL decay: each new user turn reduces tracked image message lifespan by 1
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

        additions.reverse()
        removals.reverse()
        return {"messages": [*removals, *additions]}

    @override
    async def abefore_model(self, state: AgentState[Any], runtime: Runtime) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _parse_tool_payload(msg: ToolMessage) -> dict[str, Any] | None:
        content = getattr(msg, "content", "")
        if isinstance(content, dict):
            data = content
        elif isinstance(content, str):
            try:
                data = json.loads(content)
            except (json.JSONDecodeError, TypeError):
                return None
        else:
            return None
        if not isinstance(data, dict):
            return None
        if data.get("kind") != "multimodal_tool_result":
            return None
        return data

    @staticmethod
    def _tool_message_key(msg: ToolMessage) -> str:
        mid = getattr(msg, "id", None)
        if mid is not None:
            return str(mid)
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
        if self._last_user_signature is None:
            self._last_user_signature = signature
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
                return str(mid)
        return None

    def _payload_to_mm_message(self, payload: dict[str, Any]) -> HumanMessage | None:
        max_pixels = getattr(self, "_max_pixels", 2000000)
        images: list[dict[str, Any]] = []
        for url in self._image_urls(payload):
            resized = self._maybe_resize_image_url(url, max_pixels)
            if resized:
                images.append({"type": "image_url", "image_url": {"url": resized}})
        if not images:
            # 没有可用图片就不注入（ToolMessage 里已有描述文本，注入只会重复一遍）
            return None
        blocks: list[dict[str, Any]] = []
        if payload.get("text"):
            blocks.append({"type": "text", "text": str(payload["text"])})
        blocks.extend(images)
        return HumanMessage(
            content=blocks,
            additional_kwargs={"_mm_bridge_generated": True},
        )

    @staticmethod
    def _image_urls(payload: dict[str, Any]) -> list[str]:
        """收集 payload 里的图片 URL。

        工具结果通常只带 artifact 引用（工具文本里不放 base64），此时从
        OutputStore 取回真实图片；取不到（artifact 已被清理/重启）就不加图片块，
        消息保持小体积，Agent 仍可用 read artifact://<id> 复看。
        """
        urls = _collect_urls(payload.get("images"))
        if urls:
            return urls
        artifact_id = str(payload.get("artifact") or "").strip()
        if not artifact_id:
            return []
        art = get_output_store().get(artifact_id)
        if art is None:
            return []
        urls = _collect_urls((art.metadata or {}).get("images"))
        if urls:
            return urls
        if art.content_base64:
            return [f"data:{art.mime_type or 'image/png'};base64,{art.content_base64}"]
        return []

    @staticmethod
    def _maybe_resize_image_url(url: str, max_pixels: int) -> str | None:
        """If the image's total pixel count exceeds max_pixels, downscale it and
        re-encode as base64 data URL. Only supports data: URLs; other schemes
        (http/file) are passed through unchanged."""
        if not url or max_pixels <= 0:
            return url or None
        if not url.startswith("data:"):
            return url
        try:
            header, b64 = url.split(",", 1)
            if ";base64" not in header:
                return url  # non-base64 data URL, leave as-is
            raw = base64.b64decode(b64)
            img = Image.open(io.BytesIO(raw))
            w, h = img.size
            if w * h <= max_pixels:
                return url
            import math
            scale = math.sqrt(max_pixels / float(w * h))
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            fmt = img.format or "PNG"
            if fmt.upper() not in ("PNG", "JPEG", "WEBP"):
                fmt = "PNG"
            img.save(out, format=fmt)
            mime = header[len("data:"):].removesuffix(";base64")
            return f"data:{mime};base64,{base64.b64encode(out.getvalue()).decode('ascii')}"
        except Exception:
            # 解码/缩放失败时原样透传，不让桥接中断
            return url
