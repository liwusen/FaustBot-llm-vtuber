from __future__ import annotations

from typing import Any

try:
    from langchain.tools import tool
except Exception:
    def tool(func):
        return func
import sys
import subprocess
from faust_backend.plugin_system import MiddlewareSpec, PluginContext, PluginManifest, ToolSpec


class EchoTraceMiddleware:
    """示例 middleware：仅保留占位，供主链路插入验证。"""

    def __repr__(self) -> str:
        return "EchoTraceMiddleware()"

class CrossPlatformClipboard:
    def __init__(self):
        self.system = sys.platform

    def copy(self, text):
        """跨平台复制文本到剪切板"""
        if self.system == "win32":
            try:
                import pyperclip
                pyperclip.copy(text)
            except ImportError:
                # 使用Windows命令行工具
                subprocess.run(['clip'], input=text, text=True, check=True)
        elif self.system == "darwin":  # macOS
            subprocess.run(['pbcopy'], input=text, text=True, check=True)
        elif self.system.startswith("linux"):  # Linux
            try:
                subprocess.run(['xclip', '-selection', 'clipboard'], 
                             input=text, text=True, check=True)
            except FileNotFoundError:
                subprocess.run(['xsel', '--clipboard', '--input'], 
                             input=text, text=True, check=True)

    def paste(self):
        """跨平台从剪切板粘贴文本"""
        if self.system == "win32":
            try:
                import pyperclip
                return pyperclip.paste()
            except ImportError:
                # 使用PowerShell
                result = subprocess.run(['powershell', '-command', 'Get-Clipboard'], 
                                      capture_output=True, text=True)
                return result.stdout.strip()
        elif self.system == "darwin":  # macOS
            result = subprocess.run(['pbpaste'], capture_output=True, text=True)
            return result.stdout
        elif self.system.startswith("linux"):  # Linux
            try:
                result = subprocess.run(['xclip', '-selection', 'clipboard', '-o'], 
                                      capture_output=True, text=True)
                return result.stdout
            except FileNotFoundError:
                result = subprocess.run(['xsel', '--clipboard', '--output'], 
                                      capture_output=True, text=True)
                return result.stdout
clipboard = CrossPlatformClipboard()
@tool
def getClipboardContent() -> str:
    """
    Description:
        获取系统剪贴板的文本内容。
    Args:
        None
    Returns:
        str: 剪贴板文本内容，或者错误信息。
    """
    try:
        return clipboard.paste()
    except Exception as e:
        return f"获取剪贴板内容出错: {str(e)}"
@tool
def setClipboardContent(text: str) -> str:
    """
    Description:
        设置系统剪贴板的文本内容。
    Args:
        text (str): 要设置到剪贴板的文本内容。
    Returns:
        str: 操作结果信息。
    """
    try:
        clipboard.copy(text)
        return "剪贴板内容已更新。"
    except Exception as e:
        return f"设置剪贴板内容出错: {str(e)}"


class Plugin:
    manifest = PluginManifest(
        plugin_id="clipboard",
        name="Clipboard Plugin",
        version="1.0.0",
        enabled=False,
        permissions=["tool:clipboard"],
        priority=200,
    )

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self, ctx: PluginContext) -> None:
        pass

    def register_tools(self, ctx: PluginContext):
        @tool
        def getClipboardContentManaged() -> str:
            """
            Description:
                获取系统剪贴板的文本内容。
            Args:
                None
            Returns:
                str: 剪贴板文本内容，或者错误信息。
            """
            if not bool(ctx.get_config("ENABLE_CLIPBOARD_READ", True)):
                return "剪贴板读取已在插件配置中禁用。"
            return getClipboardContent()

        @tool
        def setClipboardContentManaged(text: str) -> str:
            """
            Description:
                设置系统剪贴板的文本内容。
            Args:
                text (str): 要设置到剪贴板的文本内容。
            Returns:
                str: 操作结果信息。
            """
            if not bool(ctx.get_config("ENABLE_CLIPBOARD_WRITE", True)):
                return "剪贴板写入已在插件配置中禁用。"
            return setClipboardContent(text)

        # 覆盖为对外稳定工具名，保持兼容
        getClipboardContentManaged.name = "getClipboardContent"
        setClipboardContentManaged.name = "setClipboardContent"

        return [
            ToolSpec(
                name="getClipboardContent",
                tool=getClipboardContentManaged,
                enabled_by_default=True,
                description=getClipboardContentManaged.__doc__,
            ),
            ToolSpec(
                name="setClipboardContent",
                tool=setClipboardContentManaged,
                enabled_by_default=True,
                description=setClipboardContentManaged.__doc__,
            )
        ]

    def register_middlewares(self, ctx: PluginContext):
        return []

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "plugin": "clipboard"}

    def filter_trigger_append(self, trigger_payload: dict) -> dict | None:
        return trigger_payload

    def filter_trigger_fire(self, trigger_payload: dict) -> dict | None:
        return trigger_payload

    def Heartbeat(self, ctx):
        pass

    def startup(self, ctx: PluginContext) -> None:
        ctx.register_config(
            """
            ENABLE_CLIPBOARD_READ:bool:启用剪贴板读取=true
            ENABLE_CLIPBOARD_WRITE:bool:启用剪贴板写入=true
            """
        )
        legacy_enable = ctx.get_config("Enable", None)
        if legacy_enable is not None:
            if ctx.get_config("ENABLE_CLIPBOARD_READ", None) is None:
                ctx.set_config("ENABLE_CLIPBOARD_READ", bool(legacy_enable))
            if ctx.get_config("ENABLE_CLIPBOARD_WRITE", None) is None:
                ctx.set_config("ENABLE_CLIPBOARD_WRITE", bool(legacy_enable))

def get_plugin() -> Plugin:
    return Plugin()
