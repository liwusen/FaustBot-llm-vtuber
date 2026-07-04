"""
Frontend Example Plugin: 演示前端注入功能（pluginUI API）。
包含 register_frontend() 注入 JS/CSS 资源 + 1 个 API 端点。
"""

from fastapi import APIRouter

from faust_backend.plugin_system import FaustPlugin, PluginContext


# ── 路由 ──

fe_router = APIRouter()


@fe_router.get("/hello")
async def hello():
    """返回插件状态信息。"""
    return {
        "plugin": "frontend-example",
        "version": "1.0.0",
        "status": "ok",
        "message": "Hello from Frontend Example Plugin!",
    }


# ── 插件主类 ──

class Plugin(FaustPlugin):
    def plugin_loaded(self, ctx: PluginContext) -> None:
        print(f"[Frontend Example] Loaded: {ctx.plugin_id}")

    def plugin_unloaded(self, ctx: PluginContext) -> None:
        print(f"[Frontend Example] Unloaded: {ctx.plugin_id}")

    def register_routes(self) -> list:
        return [fe_router]

    def register_frontend(self) -> list[dict]:
        return [
            {
                "type": "js",
                "path": "/faust/plugins/frontend-example/frontend/panel.js",
            },
            {
                "type": "css",
                "path": "/faust/plugins/frontend-example/frontend/panel.css",
            },
        ]

    def health_check(self) -> dict | None:
        return {
            "status": "ok",
            "plugin": "frontend-example",
            "version": "1.0.0",
        }


def get_plugin() -> Plugin:
    return Plugin()
