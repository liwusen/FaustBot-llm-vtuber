"""路由聚合：``main.py`` 唯一需要的路由注册入口。

**为什么 import 全写在函数里**：这个包里的模块会反向 import ``app.lifespan``
（例如 ``api/system/system.py`` 要 ``_graceful_shutdown_task``），如果本文件在
模块层就把 29 个路由模块全部 import 进来，那么任何一句
``import faust_backend.api.<x>`` 都会先执行本文件 → 立刻拉进 ``system.py`` →
它又回头 import 还没执行完的 ``app.lifespan``，形成循环导入（实测在
``main.py`` 启动时炸 ``ImportError: partially initialized module``）。
因此本文件在模块层保持零业务 import。

**``ROUTER_ORDER`` 的顺序就是路由解析顺序**，会改变实际命中行为，禁止重排
（与搬迁前 ``main.py`` 里那个 27 元素列表逐项对应，araya / tts 追加在末尾）。
"""

from __future__ import annotations

from types import ModuleType

from fastapi import FastAPI

#: 路由模块的注册顺序（= 解析优先级）。名字用于文档与断言，模块对象在函数内解析。
ROUTER_ORDER: tuple[str, ...] = (
    "faust_backend.api.admin.admin_config",
    "faust_backend.api.admin.admin_runtime",
    "faust_backend.api.admin.admin_models",
    "faust_backend.api.admin.admin_services",
    "faust_backend.api.admin.admin_agents",
    "faust_backend.api.admin.admin_skills",
    "faust_backend.api.admin.admin_triggers",
    "faust_backend.api.admin.admin_plugins",
    "faust_backend.api.admin.admin_logs",
    "faust_backend.api.admin.admin_mcp",
    "faust_backend.api.admin.admin_providers",
    "faust_backend.api.processors",
    "faust_backend.api.system.ui_settings",
    "faust_backend.api.chat.chat",
    "faust_backend.api.chat.session",
    "faust_backend.api.chat.hil_nimble",
    "faust_backend.api.chat.audio",
    "faust_backend.api.system.system",
    "faust_backend.api.system.autocomplete",
    "faust_backend.api.components",
    "faust_backend.api.system.logger_ws",
    "faust_backend.api.chat.subagents",
    "faust_backend.api.system.debugging",
    "faust_backend.api.public",
    "faust_backend.api.memory",
    "faust_backend.api.update",
    "faust_backend.api.live",
    "faust_backend.api.araya",
    "faust_backend.api.tts",
)


def _import_routers() -> list[ModuleType]:
    """按 ``ROUTER_ORDER`` 逐条 import（延迟到调用时，避免上面说的循环导入）。"""
    import importlib

    return [importlib.import_module(name) for name in ROUTER_ORDER]


def register_routes(app: FastAPI) -> None:
    """把 ``ROUTER_ORDER`` 里的 router 按顺序依次挂载到 ``app``。"""
    for module in _import_routers():
        app.include_router(module.router)
