"""
示例插件：演示 FaustPlugin 基类用法。
包含 1 个工具 + 1 个 API 端点 + 1 个定时任务。
"""


try:
    from langchain.tools import tool
except Exception:
    def tool(func):
        return func

from fastapi import APIRouter

from faust_backend.plugin_system import FaustPlugin, PluginContext


# ── 工具 ──

@tool
def echoTool(message: str) -> str:
    """回显消息 — 简单的测试工具。

    Args:
        message (str): 要回显的消息文本。
    Returns:
        str: 原样返回的消息。
    """
    return f"Echo: {message}"


# ── 路由 ──

example_router = APIRouter()


@example_router.get("/info")
async def info():
    """返回插件信息。"""
    return {"plugin": "example", "version": "1.0.0", "status": "ok"}


@example_router.get("/ping")
async def ping():
    """健康检查端点。"""
    return {"pong": True}


# ── 定时任务回调 ──

_poll_count = 0


def do_poll():
    """每 30 秒执行一次的示例轮询任务。"""
    global _poll_count
    _poll_count += 1
    # 实际使用时可在此处执行数据同步、清理等操作
    print(f"[Example Plugin] Poll #{_poll_count}")


# ── 插件主类 ──

class Plugin(FaustPlugin):
    def plugin_loaded(self, ctx: PluginContext) -> None:
        print(f"[Example Plugin] Loaded: {ctx.plugin_id}")

    def plugin_unloaded(self, ctx: PluginContext) -> None:
        print(f"[Example Plugin] Unloaded: {ctx.plugin_id}")

    def register_tools(self, ctx: PluginContext) -> list:
        return [echoTool]

    def register_routes(self) -> list:
        return [example_router]

    def register_schedules(self) -> list[dict]:
        return [
            {
                "id": "example_poll",
                "interval": 30,
                "callback": do_poll,
                "description": "示例轮询任务（每30秒）",
            },
        ]

    def health_check(self) -> dict | None:
        return {"status": "ok", "poll_count": _poll_count}


def get_plugin() -> Plugin:
    return Plugin()
