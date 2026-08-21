"""Dev Debugger 插件 — 在 Configer 页面查看/调用 AI 的激活工具。

能力：
- list_tools：列出当前注册的全部工具及其参数 schema（供前端动态生成表单）
- invoke_tool：按 name + args 调用指定工具，返回结果

仅用于开发调试，不注册任何 Agent 工具。
"""

from __future__ import annotations

import json
import re
from typing import Any

from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl
from faust_backend.logger import get_logger

log = get_logger("faust.plugins.dev-debugger")


def _type_label(annotation: Any) -> str:
    """把 pydantic 类型注解映射为前端可用的类型标签（str/int/float/bool/list…）。"""
    name = getattr(annotation, "__name__", None)
    if name:
        return name.lower()
    # typing 泛型（list[int] 等）
    origin = getattr(annotation, "__origin__", None)
    if origin is not None:
        inner = getattr(annotation, "__args__", ())
        base = getattr(origin, "__name__", str(origin)).lower()
        if inner:
            subtypes = [_type_label(i) for i in inner]
            return f"{base} of {','.join(subtypes)}"
        return base
    return str(annotation)


def _parse_schema(tool) -> dict:
    """从工具对象的 args_schema 提取参数 schema。"""
    fields = {}
    try:
        model_fields = getattr(tool.args_schema, "model_fields", {})
    except Exception:
        model_fields = {}
    for key, f in model_fields.items():
        required = f.is_required() if hasattr(f, "is_required") else True
        default = None
        if not required:
            try:
                default = _jsonable(f.default)
            except Exception:
                default = None
        fields[key] = {
            "name": key,
            "type": _type_label(f.annotation),
            "required": required,
            "default": default,
        }
    return fields


def _jsonable(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except Exception:
        return str(v)


def _iter_tools():
    from faust_backend.tools import _registry as registry
    seen = set()
    for t in registry.toollist:
        name = getattr(t, "name", None) or getattr(t, "__name__", "")
        if name in seen:
            continue
        seen.add(name)
        yield name, t


def _invoke_tool(name: str, args: dict) -> Any:
    """按名称查找注册的工具并同步调用。

    在独立线程里执行 tool.invoke：插件 communicate_handler 常运行在事件循环中，
    而不少工具内部使用 asyncio.run(...)（如 writeDiaryFileTool），在 running loop 里
    直接调用会抛 "asyncio.run() cannot be called from a running event loop"。
    换到无 running loop 的新线程执行，与 LangChain Agent 的线程池路径行为一致。
    """
    from faust_backend.tools import _registry as registry
    tool = None
    for t in registry.toollist:
        if (getattr(t, "name", None) or "") == name:
            tool = t
            break
    if tool is None:
        tool = registry.ORIGINAL_TOOL_FUNCS.get(name)
    if tool is None:
        raise KeyError(f"工具不存在: {name}")

    result_box: dict = {}
    exc_box: dict = {}

    def _run() -> None:
        try:
            result_box["value"] = tool.invoke(args)
        except BaseException as e:  # noqa: BLE001
            exc_box["error"] = e

    import threading
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join()
    if "error" in exc_box:
        raise exc_box["error"]

    result = result_box.get("value")
    # invoke 返回往往是字符串；若是结构化结果则转文本
    if isinstance(result, str):
        return result
    if isinstance(result, (dict, list)):
        return json.dumps(result, ensure_ascii=False, default=str)
    return str(result)


class Plugin(FaustPlugin):
    def startup(self, ctx: PluginContext) -> None:
        ctx.register_config([
            {"key": "DEV_DEBUGGER_ENABLED", "type": "bool", "label": "启用 Dev Debugger", "default": True},
        ])

    @hookimpl
    def register_frontend(self) -> list[dict]:
        return [
            {
                "type": "js",
                "path": "/faust/plugins/dev-debugger/frontend/app-hook.js",
            },
        ]

    @hookimpl
    def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        action = str((payload or {}).get("action") or "").strip().lower()
        if action == "list_tools":
            tools = []
            for name, t in _iter_tools():
                tools.append({
                    "name": name,
                    "description": str(getattr(t, "description", "") or ""),
                    "schema": list(_parse_schema(t).values()),
                })
            tools.sort(key=lambda x: x["name"])
            return {"status": "ok", "tools": tools, "count": len(tools)}

        if action == "invoke_tool":
            name = str((payload or {}).get("name") or "").strip()
            args = (payload or {}).get("args") or {}
            if not name:
                return {"status": "error", "detail": "缺少工具名 name"}
            if not isinstance(args, dict):
                args = json.loads(str(args))
            try:
                result = _invoke_tool(name, args)
                return {"status": "ok", "tool": name, "result": result}
            except Exception as e:
                log.warning("dev-debugger invoke_tool error: %s", e)
                return {"status": "error", "tool": name, "detail": f"{type(e).__name__}: {e}"}
        return {"status": "error", "detail": f"unknown action: {action}"}
