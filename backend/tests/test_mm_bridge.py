"""MultimodalBridgeMiddleware：把「只有 artifact 引用」的工具结果还原成 image_url 块。

工具文本里不放 base64（会被按文本计 token），所以桥接必须自己从 OutputStore
取回图片；取不回来时要安静跳过，而不是把引用再注入一遍。

另外两条硬约束（回归）：
1. 桥接绝不删除任何历史消息——删除 ToolMessage 会让它的 AIMessage(tool_calls)
   悬空，上游直接 400 invalid_request_error（见 tool_call_repair 模块说明）。
2. 注入幂等：同一源 ToolMessage 在上下文里最多对应一条桥接消息，进程重启后
   也不会重复注入。
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, RemoveMessage, ToolMessage

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import faust_backend.core.config as conf  # noqa: E402
from faust_backend.runtime.mm_bridge import MultimodalBridgeMiddleware  # noqa: E402


def _png_base64(size: tuple[int, int]) -> str:
    from PIL import Image

    image = Image.new("RGB", size, (200, 30, 30))
    with io.BytesIO() as buf:
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _ref_message(
    artifact_id: str, *, tool_call_id: str = "c1", message_id: str = "t1"
) -> ToolMessage:
    ref = json.dumps(
        {
            "kind": "multimodal_tool_result",
            "text": f"截图\n[图片: artifact://{artifact_id}]",
            "artifact": artifact_id,
        },
        ensure_ascii=False,
    )
    return ToolMessage(content=ref, tool_call_id=tool_call_id, id=message_id)


def _state(artifact_id: str) -> Any:
    return {"messages": [HumanMessage(content="看图", id="u1"), _ref_message(artifact_id)]}


def _image_urls(messages: list) -> list[str]:
    urls: list[str] = []
    for msg in messages:
        if not isinstance(msg, HumanMessage):
            continue
        if not (msg.additional_kwargs or {}).get("_mm_bridge_generated"):
            continue
        content = msg.content
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image_url":
                urls.append(str(block["image_url"]["url"]))
    return urls


def _put_image(store, *, text: str = "截图") -> str:
    return store.put_multimodal(
        {
            "kind": "multimodal_tool_result",
            "text": text,
            "images": [{"url": f"data:image/png;base64,{_png_base64((120, 120))}"}],
        },
        tool_name="read",
    )


def test_bridge_rehydrates_image_from_artifact_ref(isolated_output_store, monkeypatch):
    """只有引用时，桥接从 OutputStore 取回图片，并按 MAX_PIXELS 缩放。"""
    from PIL import Image

    monkeypatch.setattr(conf, "MM_BRIDGE_MAX_PIXELS", 640000)
    artifact_id = isolated_output_store.put_multimodal(
        {
            "kind": "multimodal_tool_result",
            "text": "截图",
            "images": [{"url": f"data:image/png;base64,{_png_base64((1200, 1200))}"}],
        },
        tool_name="read",
    )

    out = MultimodalBridgeMiddleware().before_model(_state(artifact_id), None)
    urls = _image_urls(out["messages"])

    assert len(urls) == 1
    assert urls[0].startswith("data:image/png;base64,")
    with Image.open(io.BytesIO(base64.b64decode(urls[0].split(",", 1)[1]))) as sent:
        assert sent.width * sent.height <= 640000
        assert sent.width < 1200


def test_bridge_skips_ref_without_artifact(isolated_output_store):
    """artifact 已被清理（新对话/重启）时不注入任何消息，上下文保持干净。"""
    assert MultimodalBridgeMiddleware().before_model(_state("gone_1"), None) is None


def test_bridge_never_removes_messages(isolated_output_store):
    """回归：桥接只新增消息，绝不删除源 ToolMessage（否则 tool_calls 悬空）。"""
    artifact_id = _put_image(isolated_output_store)
    out = MultimodalBridgeMiddleware().before_model(_state(artifact_id), None)

    assert out is not None
    assert not any(isinstance(m, RemoveMessage) for m in out["messages"])
    assert all(isinstance(m, HumanMessage) for m in out["messages"])


def test_bridge_is_idempotent_across_restart(isolated_output_store):
    """进程重启（内存状态丢失）后不得重复注入同一张图。"""
    artifact_id = _put_image(isolated_output_store)
    state = _state(artifact_id)

    first = MultimodalBridgeMiddleware().before_model(state, None)
    assert first is not None and len(first["messages"]) == 1

    merged = {**state, "messages": [*state["messages"], *first["messages"]]}
    assert MultimodalBridgeMiddleware().before_model(merged, None) is None


def test_bridge_injects_only_unbridged_sources(isolated_output_store):
    """已有桥接消息的源跳过，只注入缺失的那一条。"""
    first_id = _put_image(isolated_output_store, text="第一张")
    second_id = _put_image(isolated_output_store, text="第二张")

    state = _state(first_id)
    first = MultimodalBridgeMiddleware().before_model(state, None)
    assert first is not None

    state_with_second = {
        "messages": [
            *state["messages"],
            *first["messages"],
            _ref_message(second_id, tool_call_id="c2", message_id="t2"),
        ]
    }
    out = MultimodalBridgeMiddleware().before_model(state_with_second, None)

    assert out is not None
    assert len(out["messages"]) == 1
    assert _image_urls(out["messages"])

# ── 跨格式兼容：桥接产出的 image_url 块必须能被 Anthropic / Gemini 接受 ──
# 桥接统一产出 OpenAI 风格的 {"type": "image_url"}；官方 SDK 会各自转换
# （Anthropic -> image/source，Gemini -> inline_data）。这里锁住这个假设，
# 避免以后有人「按格式手写图片块」反而引入不兼容结构。


def test_bridge_message_converts_to_anthropic_image_block(isolated_output_store, monkeypatch):
    from langchain_anthropic import ChatAnthropic

    monkeypatch.setattr(conf, "MM_BRIDGE_MAX_SCAN", 6)
    store = isolated_output_store
    artifact_id = _put_image(store)
    state = _state(artifact_id)
    messages = MultimodalBridgeMiddleware().before_model(state, None)["messages"]

    model = ChatAnthropic(model="claude-x", api_key="k", max_tokens=64)
    payload = model._get_request_payload(messages)

    content = payload["messages"][0]["content"]
    image_blocks = [block for block in content if block.get("type") == "image"]
    assert image_blocks, content
    assert image_blocks[0]["source"]["type"] == "base64"
    assert image_blocks[0]["source"]["media_type"] == "image/png"


def test_bridge_message_converts_to_gemini_inline_data(isolated_output_store, monkeypatch):
    import langchain_google_genai.chat_models as google_chat_models

    monkeypatch.setattr(conf, "MM_BRIDGE_MAX_SCAN", 6)
    store = isolated_output_store
    artifact_id = _put_image(store)
    state = _state(artifact_id)
    messages = MultimodalBridgeMiddleware().before_model(state, None)["messages"]

    _system, history = google_chat_models._parse_chat_history(
        messages, model="gemini-2.5-pro"
    )
    parts = [
        part
        for content in history
        if content is not None
        for part in (content.parts or [])
    ]
    assert any(getattr(part, "inline_data", None) for part in parts), parts
