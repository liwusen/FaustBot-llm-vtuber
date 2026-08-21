from __future__ import annotations

import asyncio
import base64
import io
import time
from typing import Any

import pyautogui
from langchain.tools import tool
from PIL import Image

from faust_backend.logger import get_logger
from faust_backend.plugin_system import FaustPlugin, PluginContext, ToolSpec, hookimpl

log = get_logger("faust.plugins.quick-screen-view")

PLUGIN_NS = "/plugins/quick-screen-view"
FOCUS_PATH = PLUGIN_NS + "/focus"
TEXT_PATH = PLUGIN_NS + "/text"

ERROR_PREFIX = "[quick-screen-view]"

CONFIG_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "screen-model",
        "type": "str",
        "label": "屏幕分析模型 (provider::model)",
        "default": "",
    },
    {
        "key": "mode",
        "type": "str",
        "label": "工作模式 (tool / vfs)",
        "default": "tool",
    },
    {
        "key": "screen-scale",
        "type": "float",
        "label": "截图缩放比例 (0 < scale ≤ 1)",
        "default": 0.5,
    },
    {
        "key": "text-cache-seconds",
        "type": "float",
        "label": "text 读取缓存秒数 (0 = 关闭缓存)",
        "default": 60.0,
    },
]

SYSTEM_PROMPT = """你是屏幕分析助手。你会收到一张主显示器截图和用户的 focus 指示。
请按照 focus 指示，用结构化 Markdown 文本概括屏幕内容。

必须使用以下固定格式输出：

# 屏幕概览
- 主要界面 / 应用 / 窗口
- 关键可见信息（标题、状态栏、数值等）

## 与 focus 相关
- 与 focus 指示直接相关的细节

固定格式输出完之后，如果你认为还有值得补充的内容，可以继续追加自定义 Markdown 章节。

如果 focus 为空或没有明确指示，只输出「屏幕概览」部分（即通用概括）。"""


def _capture_screenshot() -> Image.Image:
    """截取主显示器全屏截图（pyautogui，与内置 read 工具同链路）。"""
    image = pyautogui.screenshot()
    if not isinstance(image, Image.Image):
        raise RuntimeError("截图返回了无效图像对象")
    return image.convert("RGBA")


def _image_to_data_url(image: Image.Image) -> str:
    with io.BytesIO() as buf:
        image.save(buf, format="PNG")
        raw = buf.getvalue()
    return f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"


class Plugin(FaustPlugin):
    def __init__(self) -> None:
        self.ctx: PluginContext | None = None
        self._focus_value = ""
        self._cache_focus: str | None = None
        self._cache_ts = 0.0
        self._cache_text: str | None = None

    # ── 配置 ──
    async def _config(self, key: str, default: Any = None) -> Any:
        if self.ctx is None:
            return default
        try:
            return await self.ctx.get_config(key, default)
        except Exception:
            return default

    async def _mode(self) -> str:
        return str(await self._config("mode", "tool") or "tool").strip().lower()

    # ── 缓存 ──
    def _clear_cache(self) -> None:
        self._cache_focus = None
        self._cache_ts = 0.0
        self._cache_text = None

    def _current_focus(self) -> str:
        return str(self._focus_value or "")

    # ── VFS 节点内容函数 ──
    def _read_focus(self, _path: str) -> str:
        return self._current_focus()

    def _write_focus(self, _node: Any, content: Any) -> None:
        """focus 节点写处理器：更新 focus 值并清空 text 缓存，保证写后必重算。"""
        self._focus_value = str(content or "")
        self._clear_cache()

    async def _read_text(self, _path: str) -> str:
        """text 节点内容函数（异步）：截图 + 调 screen-model 按当前 focus 概括。"""
        return await self._analyze(self._current_focus())

    # ── 核心分析（Tool 与 VFS 共用） ──
    async def _analyze(self, focus: str) -> str:
        spec = str(await self._config("screen-model", "") or "").strip()
        if not spec:
            return (
                f"{ERROR_PREFIX} screen-model 未配置：请在配置中心为 quick-screen-view "
                "插件设置 screen-model（格式 provider::model）"
            )
        try:
            window = float(await self._config("text-cache-seconds", 60.0) or 0.0)
        except (TypeError, ValueError):
            window = 0.0
        now = time.time()
        if (
            window > 0
            and self._cache_text is not None
            and self._cache_focus == focus
            and (now - self._cache_ts) < window
        ):
            return self._cache_text

        try:
            scale = float(await self._config("screen-scale", 0.5) or 0.5)
        except (TypeError, ValueError):
            scale = 0.5
        if not (0 < scale <= 1):
            scale = 0.5

        try:
            image = await asyncio.to_thread(_capture_screenshot)
            if scale < 1.0:
                image = image.resize(
                    (
                        max(1, int(image.width * scale)),
                        max(1, int(image.height * scale)),
                    ),
                    Image.Resampling.LANCZOS,
                )
            data_url = _image_to_data_url(image)

            from faust_backend.provider import (
                build_ReasoningChatOpenAI_from_spec,
                parse_spec,
            )
            from faust_backend.runtime import state
            from langchain_core.messages import HumanMessage

            # 先校验 spec 格式，无效立即给出可行动错误
            parse_spec(spec)
            providers = state.get_model_providers()
            chat = await build_ReasoningChatOpenAI_from_spec(
                providers, spec, intensity=None
            )
            focus_text = (
                f"focus 指示：{focus}"
                if focus
                else "focus 指示：（无，请输出通用屏幕概览）"
            )
            message = HumanMessage(
                content=[
                    {"type": "text", "text": focus_text},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ]
            )
            response = await chat.ainvoke([message])
            text = str(getattr(response, "content", "") or "").strip()
            if not text:
                return f"{ERROR_PREFIX} 模型返回了空结果"
            if window > 0:
                self._cache_focus = focus
                self._cache_ts = time.time()
                self._cache_text = text
            return text
        except Exception as exc:
            log.warning("quick-screen-view 分析失败: %s", exc)
            return f"{ERROR_PREFIX} screen-model 无效或调用失败: {exc}"

    # ── 生命周期 ──
    async def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        await ctx.register_config(CONFIG_SCHEMA)  # type: ignore[arg-type]
        await self._apply_mode()

    @hookimpl
    async def plugin_unloaded(self, ctx: PluginContext) -> None:
        del ctx
        await self._unmount_vfs()

    async def _apply_mode(self) -> None:
        if await self._mode() == "vfs":
            await self._mount_vfs()
        else:
            await self._unmount_vfs()

    async def _mount_vfs(self) -> None:
        if self.ctx is None:
            return
        self._focus_value = ""
        self._clear_cache()
        await self.ctx.vfs_write_symbolic(FOCUS_PATH, self._read_focus, writable=True)
        await self.ctx.vfs_set_write_handler(FOCUS_PATH, self._write_focus)
        await self.ctx.vfs_set_edit_handler(FOCUS_PATH, self._write_focus)
        await self.ctx.vfs_write_symbolic(
            TEXT_PATH, self._read_text, should_be_included_in_search=False
        )
        log.info("quick-screen-view: VFS 模式已挂载 %s, %s", FOCUS_PATH, TEXT_PATH)

    async def _unmount_vfs(self) -> None:
        if self.ctx is None:
            return
        for path in (FOCUS_PATH, TEXT_PATH):
            try:
                await self.ctx.vfs_delete(path)
            except Exception:
                pass

    @hookimpl
    async def config_changed(self, key: str, old: Any, new: Any, ctx: PluginContext) -> None:
        del old, new, ctx
        if key == "mode":
            await self._apply_mode()

    @hookimpl
    async def register_tools(self, ctx: PluginContext) -> list:
        self.ctx = ctx
        # 严格互斥：mode=vfs 时不注册工具
        if await self._mode() != "tool":
            return []

        @tool
        async def quickScreenView(focus: str = "") -> str:
            """截取主显示器屏幕截图，调用 screen-model 视觉模型，按照 focus 指示以结构化 Markdown 概括屏幕内容。

            Args:
                focus (str): 概括指示，例如“当前打开了哪些应用”“屏幕上有什么数字”。为空时输出通用屏幕概览。

            Returns:
                str: 结构化 Markdown 概括文本；失败时返回以 [quick-screen-view] 开头的错误说明。
            """
            return await self._analyze(str(focus or ""))

        return [
            ToolSpec(
                name="quickScreenView",
                tool=quickScreenView,
                enabled_by_default=True,
                description=quickScreenView.__doc__ or "",
            )
        ]
