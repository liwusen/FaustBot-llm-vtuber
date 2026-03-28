from __future__ import annotations

from typing import Any

try:
    from langchain.tools import tool
except Exception:
    def tool(func):
        return func

from faust_backend.plugin_system import MiddlewareSpec, PluginContext, PluginManifest, ToolSpec


class EchoTraceMiddleware:
    """示例 middleware：仅保留占位，供主链路插入验证。"""

    def __repr__(self) -> str:
        return "EchoTraceMiddleware()"


@tool
def plugin_echo_tool(text: str) -> str:
    """Echo 插件工具：回显输入文本。"""
    print("debug: plugin_echo_tool called with text:", text)
    return f"[example_echo] {text}"


class Plugin:
    manifest = PluginManifest(
        plugin_id="example_echo",
        name="Example Echo Plugin",
        version="1.0.0",
        enabled=False,
        permissions=["tool:echo", "trigger:control"],
        priority=200,
    )

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self, ctx: PluginContext) -> None:
        pass

    def register_tools(self, ctx: PluginContext):
        return [
            ToolSpec(
                name="plugin_echo_tool",
                tool=plugin_echo_tool,
                enabled_by_default=True,
                description="回显文本",
            )
        ]

    def register_middlewares(self, ctx: PluginContext):
        return []

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "plugin": "example_echo"}

    def filter_trigger_append(self, trigger_payload: dict) -> dict | None:
        # 示例：拒绝没有 id 的触发器
        if not isinstance(trigger_payload, dict):
            return None
        if not str(trigger_payload.get("id") or "").strip():
            return None
        return trigger_payload

    def filter_trigger_fire(self, trigger_payload: dict) -> dict | None:
        # 示例：在触发阶段附加插件追踪信息
        if not isinstance(trigger_payload, dict):
            return None
        payload = dict(trigger_payload)
        payload.setdefault("plugin_trace", {})
        payload["plugin_trace"]["example_echo"] = "fired"
        return payload

    def Heartbeat(self, ctx):
        print("Heartbeat received in example_echo plugin",type(ctx))

def get_plugin() -> Plugin:
    return Plugin()
