from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class PluginContext:
    plugin_id: str
    plugin_dir: Path
    config: dict[str, Any] = field(default_factory=dict)

    def trigger_create(self, payload: dict | str) -> Any:
        fn = self.config.get("trigger_create")
        if not callable(fn):
            raise RuntimeError("trigger_create is not available")
        return fn(payload)

    def trigger_list(self) -> list[dict]:
        fn = self.config.get("trigger_list")
        if not callable(fn):
            raise RuntimeError("trigger_list is not available")
        return fn()

    def trigger_get(self, trigger_id: str) -> dict | None:
        fn = self.config.get("trigger_get")
        if not callable(fn):
            raise RuntimeError("trigger_get is not available")
        return fn(trigger_id)

    def trigger_update(self, trigger_id: str, payload: dict | str) -> Any:
        fn = self.config.get("trigger_update")
        if not callable(fn):
            raise RuntimeError("trigger_update is not available")
        return fn(trigger_id, payload)

    def trigger_delete(self, trigger_id: str) -> Any:
        fn = self.config.get("trigger_delete")
        if not callable(fn):
            raise RuntimeError("trigger_delete is not available")
        return fn(trigger_id)

    def register_config(self, schema: str | dict[str, Any]) -> Any:
        fn = self.config.get("plugin_config_register")
        if not callable(fn):
            raise RuntimeError("plugin_config_register is not available")
        return fn(schema)

    def get_config(self, key: str, default: Any = None) -> Any:
        fn = self.config.get("plugin_config_get")
        if not callable(fn):
            raise RuntimeError("plugin_config_get is not available")
        return fn(key, default)

    def set_config(self, key: str, value: Any) -> Any:
        fn = self.config.get("plugin_config_set")
        if not callable(fn):
            raise RuntimeError("plugin_config_set is not available")
        return fn(key, value)

    def list_configs(self) -> dict[str, Any]:
        fn = self.config.get("plugin_config_list")
        if not callable(fn):
            raise RuntimeError("plugin_config_list is not available")
        return fn()


@dataclass
class ToolSpec:
    name: str
    tool: Any
    enabled_by_default: bool = True
    description: str = ""


@dataclass
class MiddlewareSpec:
    name: str
    middleware: Any
    priority: int = 100
    enabled_by_default: bool = True
    description: str = ""


@dataclass
class PluginManifest:
    plugin_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: str = ""
    homepage: str = ""
    enabled: bool = True
    entry: str = "main.py"
    permissions: list[str] = field(default_factory=list)
    priority: int = 100


class PluginProtocol(Protocol):
    manifest: PluginManifest

    # ── Lifecycle ──
    def plugin_loaded(self, ctx: PluginContext) -> None:
        ...

    def plugin_unloaded(self, ctx: PluginContext) -> None:
        ...

    def startup(self, ctx: PluginContext) -> None:
        ...

    def on_load(self, ctx: PluginContext) -> None:
        ...

    def on_unload(self, ctx: PluginContext) -> None:
        ...

    def heartbeat(self, ctx: PluginContext) -> None:
        ...

    def health_check(self) -> dict[str, Any] | None:
        ...

    # ── Routes & Frontend ──
    def register_routes(self) -> list:
        ...

    def register_frontend(self) -> list[dict]:
        ...

    def register_schedules(self) -> list[dict]:
        ...

    def register_pip_deps(self) -> list[str]:
        ...

    # ── Tools & Middleware ──
    def register_tools(self, ctx: PluginContext) -> list[ToolSpec] | list[Any]:
        ...

    def register_middlewares(self, ctx: PluginContext) -> list[MiddlewareSpec] | list[Any]:
        ...

    def tool_call_pre(self, name: str, args: dict, ctx: PluginContext) -> dict | None:
        ...

    def tool_call_post(self, name: str, args: dict, result: Any, ctx: PluginContext) -> Any:
        ...

    # ── Messages ──
    def message_received(self, msg: Any, history: list, ctx: PluginContext) -> str | None:
        ...

    def message_sent(self, msg: str, response: Any, ctx: PluginContext) -> Any:
        ...

    # ── Memory ──
    def memory_read_pre(self, query: str, filters: dict | None, ctx: PluginContext) -> str | None:
        ...

    def memory_read_post(self, query: str, results: list, ctx: PluginContext) -> list | None:
        ...

    def memory_write_pre(self, content: str, metadata: dict | None, ctx: PluginContext) -> str | None:
        ...

    def memory_write_post(self, content: str, metadata: dict | None, id: str, ctx: PluginContext) -> None:
        ...

    # ── Triggers ──
    def filter_trigger_append(self, payload: dict) -> dict | None:
        ...

    def filter_trigger_fire(self, payload: dict) -> dict | None:
        ...

    def trigger_append(self, payload: dict, ctx: PluginContext) -> dict | None:
        ...

    def trigger_fire(self, payload: dict, ctx: PluginContext) -> dict | None:
        ...

    # ── Config ──
    def config_changed(self, key: str, old: Any, new: Any, ctx: PluginContext) -> None:
        ...
