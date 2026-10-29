"""Multimodal Bridge Middleware — built-in, always enabled.

Converts tool image outputs (kind: "multimodal_tool_result") into image_url
multimodal blocks so vision-capable LLMs can see tool-returned images directly.

工具结果的文本里不带 base64（会被按文本计 token），只带 artifact 引用；
图片本体在 OutputStore 里，桥接时按引用取回。

**本中间件永不删除历史消息。** 删掉 ToolMessage 会让它的
``AIMessage(tool_calls)`` 悬空，上游随即报
``An assistant message with 'tool_calls' must be followed by tool messages``
（400 invalid_request_error）。图片消息的收敛交给会话压缩
（``CompactMiddleware``）统一处理，那里会连同 tool 组一起整理。

注入是幂等的：桥接消息带 ``_mm_bridge_source`` 标记（源 ToolMessage 的稳定
key），``before_model`` 只注入「上下文中还没有对应桥接消息」的源。因此进程
重启、重复扫描、压缩丢弃桥接消息后重新注入，都不会产生重复图片块。

Config keys (in faust.config.json, AI Provider section):
    MM_BRIDGE_MAX_SCAN  (int, default 6)   — max ToolMessages scanned per turn
    MM_BRIDGE_MAX_PIXELS (int, default 2_000_000) — resize images whose total pixel count
        (width × height) exceeds this threshold before sending to the LLM. 0 disables resizing.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
from typing import Any

from PIL import Image

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.runtime import Runtime
from typing_extensions import override

import faust_backend.core.config as conf
from faust_backend.agent.runtime.turn.output_store import get_output_store

#: 桥接消息上标记源 ToolMessage 的 additional_kwargs 键（幂等去重依据）
SOURCE_KEY_FIELD = "_mm_bridge_source"


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
        self._max_pixels = 2_000_000

    @override
    def before_model(self, state: AgentState[Any], runtime: Runtime) -> dict[str, Any] | None:
        messages = state.get("messages") or []
        if not messages:
            return None

        max_scan = max(1, int(getattr(conf, 'MM_BRIDGE_MAX_SCAN', 6) or 6))
        self._max_pixels = max(0, int(getattr(conf, 'MM_BRIDGE_MAX_PIXELS', 2000000) or 2000000))
        bridged = self._bridged_source_keys(messages)

        scanned = 0
        additions: list[HumanMessage] = []

        for msg in reversed(messages):
            if not isinstance(msg, ToolMessage):
                continue
            scanned += 1
            if scanned > max_scan:
                break

            tool_key = self._tool_message_key(msg)
            if tool_key in bridged:
                continue

            payload = self._parse_tool_payload(msg)
            if payload is None:
                continue

            mm_msg = self._payload_to_mm_message(payload, tool_key)
            if mm_msg is None:
                continue

            bridged.add(tool_key)
            additions.append(mm_msg)

        if not additions:
            return None

        # reversed 扫描顺序生成，恢复成时间顺序
        additions.reverse()
        return {"messages": additions}

    @override
    async def abefore_model(self, state: AgentState[Any], runtime: Runtime) -> dict[str, Any] | None:
        return self.before_model(state, runtime)

    @staticmethod
    def _bridged_source_keys(messages: list[Any]) -> set[str]:
        """上下文里已存在的桥接消息所对应的源 key（幂等去重依据）。"""
        keys: set[str] = set()
        for msg in messages:
            source = (getattr(msg, "additional_kwargs", None) or {}).get(SOURCE_KEY_FIELD)
            if source:
                keys.add(str(source))
        return keys

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
        """源 ToolMessage 的稳定 key。

        优先用消息 id；缺 id 时用 tool_call_id + 内容 sha256（跨进程稳定，
        因此进程重启后仍能识别「这张图已经桥接过了」）。
        """
        mid = getattr(msg, "id", None)
        if mid is not None:
            return str(mid)
        tcid = getattr(msg, "tool_call_id", None)
        digest = hashlib.sha256(
            str(getattr(msg, "content", "")).encode("utf-8")
        ).hexdigest()[:16]
        return f"fallback:{tcid}:{digest}"

    def _payload_to_mm_message(
        self, payload: dict[str, Any], source_key: str
    ) -> HumanMessage | None:
        max_pixels = self._max_pixels
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
            additional_kwargs={
                "_mm_bridge_generated": True,
                SOURCE_KEY_FIELD: source_key,
            },
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
