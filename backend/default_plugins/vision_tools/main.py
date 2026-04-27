from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pyautogui
from PIL import Image

try:
    from langchain.tools import tool
except Exception:
    def tool(func):
        return func

from faust_backend.plugin_system import PluginContext, PluginManifest, ToolSpec


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_str(value: Any, default: str) -> str:
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def _resize_image_keep_ratio(img: Image.Image, max_edge: int) -> Image.Image:
    max_edge = max(64, int(max_edge))
    w, h = img.size
    longest = max(w, h)
    if longest <= max_edge:
        return img
    scale = float(max_edge) / float(longest)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return img.resize((nw, nh), Image.LANCZOS)


def _image_to_data_url(img: Image.Image, jpeg_quality: int) -> str:
    jpeg_quality = max(30, min(95, int(jpeg_quality)))
    rgb_img = img.convert("RGB")
    buf = io.BytesIO()
    rgb_img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _make_payload(text: str, image_url: str, meta: dict[str, Any] | None = None) -> str:
    payload = {
        "kind": "multimodal_tool_result",
        "text": text,
        "images": [{"url": image_url}],
    }
    if meta:
        payload["meta"] = meta
    return json.dumps(payload, ensure_ascii=False)


def _resolve_max_pixels(ctx: PluginContext) -> int:
    configured = ctx.get_config("MAX_PIXELS", 1600)
    return max(64, _safe_int(configured, 1600))


def _save_temp_jpeg(img: Image.Image, prefix: str, quality: int) -> Path:
    temp_dir = Path("temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    out_path = (temp_dir / f"{prefix}_{uuid4().hex}.jpg").resolve()
    img.convert("RGB").save(out_path, format="JPEG", quality=max(30, min(95, quality)), optimize=True)
    return out_path


class Plugin:
    manifest = PluginManifest(
        plugin_id="vision_tools",
        name="Vision Tools Plugin",
        version="1.0.0",
        description="Capture screen or read image file and return multimodal payload",
        author="faust",
        homepage="",
        enabled=True,
        permissions=["tool:vision-capture", "tool:vision-read-file"],
        priority=130,
    )

    def startup(self, ctx: PluginContext) -> None:
        ctx.register_config(
            """
            ENABLE_CAPTURE_SCREEN:bool:启用屏幕截图工具=true
            ENABLE_READ_IMAGE_FILE:bool:启用本地读图工具=true
            IMAGE_RETURN_MODE:str:图片返回模式(data_url/file_url)=data_url
            MAX_PIXELS:int:图片最长边像素上限=1600
            JPEG_QUALITY:int:JPEG压缩质量=85
            """
        )

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self, ctx: PluginContext) -> None:
        pass

    def register_middlewares(self, ctx: PluginContext):
        return []

    def register_tools(self, ctx: PluginContext):
        @tool
        def captureScreenImageTool(prompt: str = "请基于这张屏幕截图完成任务") -> str:
            """
            Description:
                抓取当前屏幕并返回多模态结果契约(JSON字符串)，供中间件转换为 image_url 内容块。
            Args:
                prompt (str): 给模型的提示文本，会作为多模态 text 块传入。
            Returns:
                str: 多模态工具结果 JSON 字符串。
            """
            if not bool(ctx.get_config("ENABLE_CAPTURE_SCREEN", True)):
                return json.dumps({"error": "屏幕截图工具已禁用"}, ensure_ascii=False)
            try:
                mode = _safe_str(ctx.get_config("IMAGE_RETURN_MODE", "data_url"), "data_url").strip().lower()
                max_edge = _resolve_max_pixels(ctx)
                quality = _safe_int(ctx.get_config("JPEG_QUALITY", 85), 85)

                img = pyautogui.screenshot()
                img = _resize_image_keep_ratio(img, max_edge)

                if mode == "file_url":
                    out_path = _save_temp_jpeg(img, "vision_capture", quality)
                    image_url = out_path.as_uri()
                else:
                    image_url = _image_to_data_url(img, quality)

                return _make_payload(
                    text=str(prompt or "请基于这张屏幕截图完成任务"),
                    image_url=image_url,
                    meta={"source": "screen_capture", "mode": mode},
                )
            except Exception as e:
                return json.dumps({"error": f"屏幕截图失败: {str(e)}"}, ensure_ascii=False)

        @tool
        def readImageFileTool(path: str, prompt: str = "请基于这张图片完成任务") -> str:
            """
            Description:
                读取本地图片文件并返回多模态结果契约(JSON字符串)，供中间件转换为 image_url 内容块。
            Args:
                path (str): 本地图片路径。
                prompt (str): 给模型的提示文本，会作为多模态 text 块传入。
            Returns:
                str: 多模态工具结果 JSON 字符串。
            """
            if not bool(ctx.get_config("ENABLE_READ_IMAGE_FILE", True)):
                return json.dumps({"error": "本地读图工具已禁用"}, ensure_ascii=False)
            try:
                src = Path(path).expanduser().resolve()
                if not src.exists() or not src.is_file():
                    return json.dumps({"error": f"图片文件不存在: {src}"}, ensure_ascii=False)

                mode = _safe_str(ctx.get_config("IMAGE_RETURN_MODE", "data_url"), "data_url").strip().lower()
                max_edge = _resolve_max_pixels(ctx)
                quality = _safe_int(ctx.get_config("JPEG_QUALITY", 85), 85)

                with Image.open(src) as raw:
                    img = _resize_image_keep_ratio(raw, max_edge)
                    if mode == "file_url":
                        out_path = _save_temp_jpeg(img, "vision_read", quality)
                        image_url = out_path.as_uri()
                    else:
                        image_url = _image_to_data_url(img, quality)

                return _make_payload(
                    text=str(prompt or "请基于这张图片完成任务"),
                    image_url=image_url,
                    meta={"source": "local_file", "path": str(src), "mode": mode},
                )
            except Exception as e:
                return json.dumps({"error": f"读取图片失败: {str(e)}"}, ensure_ascii=False)

        return [
            ToolSpec(
                name="captureScreenImageTool",
                tool=captureScreenImageTool,
                enabled_by_default=True,
                description=captureScreenImageTool.__doc__,
            ),
            ToolSpec(
                name="readImageFileTool",
                tool=readImageFileTool,
                enabled_by_default=True,
                description=readImageFileTool.__doc__,
            ),
        ]

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "plugin": "vision_tools"}

    def Heartbeat(self, ctx: PluginContext) -> None:
        pass


def get_plugin() -> Plugin:
    return Plugin()
