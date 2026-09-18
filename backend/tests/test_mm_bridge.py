"""MultimodalBridgeMiddleware：把「只有 artifact 引用」的工具结果还原成 image_url 块。

工具文本里不放 base64（会被按文本计 token），所以桥接必须自己从 OutputStore
取回图片；取不回来时要安静跳过，而不是把引用再注入一遍。
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import faust_backend.config_loader as conf  # noqa: E402
from faust_backend.runtime.mm_bridge import MultimodalBridgeMiddleware  # noqa: E402


def _png_base64(size: tuple[int, int]) -> str:
    from PIL import Image

    image = Image.new("RGB", size, (200, 30, 30))
    with io.BytesIO() as buf:
        image.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")


def _ref_message(artifact_id: str) -> ToolMessage:
    ref = json.dumps(
        {
            "kind": "multimodal_tool_result",
            "text": f"截图\n[图片: artifact://{artifact_id}]",
            "artifact": artifact_id,
        },
        ensure_ascii=False,
    )
    return ToolMessage(content=ref, tool_call_id="c1", id="t1")


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
