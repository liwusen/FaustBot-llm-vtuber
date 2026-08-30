"""Agile Engine 插件主体。

框架本体：让 Agent 在运行期编写轻量模块（能力限于 VFS 内容/写/编辑节点、定时任务、
事件、日志），碰不到 Agent 核心（不能注册工具/改上下文/拦截消息），写坏了卸载即恢复。

- 模块文件: ~/.faustbot/agile-modules/{name}.py
- 加载/重载/卸载/启用/禁用: agileOperate 工具
- 状态与日志: faustbot://agile/status、faustbot://agile/{name}/status、log/all、log/errors
"""
import os
import sys

_PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
if _PLUGIN_DIR not in sys.path:
    sys.path.insert(0, _PLUGIN_DIR)

from faust_backend.logger import get_logger
from faust_backend.plugin_system import FaustPlugin, PluginContext, ToolSpec, hookimpl

import runner  # noqa: E402

log = get_logger("faust.plugins.agile-engine")

_PLUGIN: "Plugin | None" = None


class Plugin(FaustPlugin):
    def __init__(self):
        self.ctx: PluginContext | None = None

    async def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        runner.configure(ctx)
        await runner.register_overview_node()
        # 启动时自动加载所有 .py 模块（.disabled 跳过）；单个失败不阻塞启动
        for item in runner.list_modules():
            if item["disabled"]:
                continue
            result = await runner.load_module(item["name"])
            if not result.get("ok"):
                log.warning("agile 模块自动加载失败: %s", result.get("message"))

    @hookimpl
    def register_frontend(self) -> list[dict]:
        return [
            {"type": "js", "path": "/faust/plugins/agile-engine/frontend/panel.js"},
        ]

    @hookimpl
    async def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        """Agile 配置面板数据接口（只读状态/日志/存储）。"""
        action = str((payload or {}).get("action") or "").strip().lower()
        try:
            if action == "get_modules":
                items = []
                for item in runner.list_modules():
                    name = item["name"]
                    inst = runner.AGILE_INSTANCES.get(name)
                    storage_keys: list[str] = []
                    log_count = 0
                    if inst is not None:
                        storage = getattr(inst.get("agile"), "storage", None)
                        if storage is not None:
                            try:
                                with storage:
                                    storage.get("__agile_panel_probe__", None)  # 确保已从磁盘加载
                                    storage_keys = list(storage._cache or {})
                            except Exception:  # noqa: BLE001
                                storage_keys = []
                        logs = await runner.LM.getLog(agile_from=name)
                        log_count = len(logs)
                    items.append({
                        "name": name,
                        "disabled": item["disabled"],
                        "loaded": inst is not None and not item["disabled"],
                        "status": inst.get("status", "未加载") if inst else ("disabled" if item["disabled"] else "未加载"),
                        "last_error": (inst.get("last_error") or None) if inst else None,
                        "vfs_count": len(inst.get("vfs_paths", [])) if inst else 0,
                        "interval_count": len(inst.get("interval_handles", [])) if inst else 0,
                        "storage_keys": storage_keys,
                        "log_count": log_count,
                    })
                return {"status": "ok", "items": items}

            if action == "get_module_logs":
                name = str((payload or {}).get("name") or "").strip()
                if not name:
                    return {"status": "ok", "logs": []}
                level = str((payload or {}).get("level") or "").strip() or None
                logs = await runner.LM.getLog(agile_from=name, level=level)
                logs = logs[-50:]  # 最近 50 条
                return {"status": "ok", "logs": await runner.LM.formatLogs(logs)}
        except Exception as exc:  # noqa: BLE001
            log.warning("agile communicate_handler 失败: %s", exc)
            return {"status": "error", "message": str(exc)}
        return None

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        _PLUGIN = self

    @hookimpl
    async def plugin_unloaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        if _PLUGIN is self:
            _PLUGIN = None
        for name in list(runner.AGILE_INSTANCES.keys()):
            try:
                await runner.unload_module(name)
            except Exception as exc:  # noqa: BLE001
                log.warning("agile 模块卸载失败 %s: %s", name, exc)

    def register_tools(self, ctx: PluginContext):
        from langchain.tools import tool


        @tool
        async def agileOperate(action: str, name: str = "", value: str = "") -> str:
            """Description:
            管理 Agile 模块（Agent 可编程的轻量扩展模块）。
            Agile 模块 = 放在 ~/.faustbot/agile-modules/ 下 of .py 文件，能力限于
            VFS 内容/写/编辑节点、定时任务、事件、日志；不能注册工具或修改 Agent 上下文。
            模块状态与日志可读 faustbot://agile/status、faustbot://agile/{name}/status、
            faustbot://agile/{name}/log/all、faustbot://agile/{name}/log/errors。
            编写协议见 skill: agile-engine。
            action:
            - list: 列出所有模块文件与加载状态（无需 name）
            - load <name>: 加载模块（已加载则提示用 reload）
            - reload <name>: 卸载后重新加载（修改代码后使用）
            - unload <name>: 卸载模块，自动清理其 VFS 节点与定时任务
            - enable <name>: 启用模块（.py.disabled → .py 并加载）
            - disable <name>: 禁用模块（卸载并重命名为 .py.disabled）
            - status <name>: 查看单个模块状态
            - limit <name> <value>: 设置该模块每分钟触发 trigger 的上限（value 为整数，
              0 或负数 = 不限制；超限时模块 of event_fire 会报错并记入 log/errors）
            Args:
                action (str): 操作名
                name (str): 模块名（list 可留空）
                value (str): 仅 limit 使用，分钟触发上限数值
            Returns:
                str: 操作结果
            """
            action = str(action or "").strip().lower()
            name = str(name or "").strip()
            value = str(value or "").strip()
            try:
                if action == "list":
                    return runner.format_status_overview()
                if not name:
                    return "错误: 该操作需要指定模块名 (name)"
                if action == "load":
                    return str((await runner.load_module(name)).get("message"))
                if action == "reload":
                    return str((await runner.reload_module(name)).get("message"))
                if action == "unload":
                    return str((await runner.unload_module(name)).get("message"))
                if action == "enable":
                    return str((await runner.enable_module(name)).get("message"))
                if action == "disable":
                    return str((await runner.disable_module(name)).get("message"))
                if action == "status":
                    return runner.format_module_status(name)
                if action == "limit":
                    try:
                        limit_value = int(value)
                    except ValueError:
                        return f"错误: limit 需要整数（每分钟上限），得到 '{value}'"
                    return str(runner.set_tpm_limit(name, limit_value).get("message"))
                return f"未知操作: {action}（支持 list/load/reload/unload/enable/disable/status/limit）"
            except Exception as exc:  # noqa: BLE001
                return f"agileOperate 执行失败: {exc}"
        assert agileOperate.__doc__ is not None
        return [
            ToolSpec(name="agileOperate", tool=agileOperate, enabled_by_default=True,
                     description=agileOperate.__doc__),
        ]


def get_plugin() -> Plugin:
    return Plugin()
