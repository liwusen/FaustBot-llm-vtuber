from __future__ import annotations

from typing import Any

from .hooks import hookimpl
from .interfaces import PluginContext, PluginManifest


class FaustPlugin:
    """Base class for pluggy-style plugins.

    Subclass this and override the hooks you need. Each method
    is automatically registered as @hookimpl via inheritance.
    """

    # Plugin metadata — override in subclass or set via plugin.json
    manifest: PluginManifest | None = None

    # ── Lifecycle ──

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        pass

    @hookimpl
    def plugin_unloaded(self, ctx: PluginContext) -> None:
        pass

    @hookimpl
    def heartbeat(self, ctx: PluginContext) -> None:
        pass

    @hookimpl
    def health_check(self) -> dict | None:
        return None

    # ── Frontend ──

    @hookimpl
    def register_frontend(self) -> list[dict]:
        return []

    @hookimpl
    def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        return None

    # ── Schedules ──

    @hookimpl
    def register_schedules(self) -> list[dict]:
        return []

    # ── Dependencies ──

    @hookimpl
    def register_pip_deps(self) -> list[str]:
        return []

    # ── Tools & Middleware ──

    @hookimpl
    def register_tools(self, ctx: PluginContext) -> list:
        return []

    @hookimpl
    def register_middlewares(self, ctx: PluginContext) -> list:
        return []

    @hookimpl
    def tool_call_pre(self, name: str, args: dict, ctx: PluginContext) -> dict | None:
        return None

    @hookimpl
    def tool_call_post(self, name: str, args: dict, result: Any, ctx: PluginContext) -> Any:
        return None

    # ── Messages ──

    @hookimpl
    def message_received(self, msg: Any, history: list, ctx: PluginContext) -> str | None:
        return None

    @hookimpl
    def agent_event_sent(self, event: dict, current_history: list, ctx: PluginContext) -> dict | None:
        return None

    # ── Memory ──

    @hookimpl
    def memory_read_pre(self, query: str, filters: dict | None, ctx: PluginContext) -> str | None:
        return None

    @hookimpl
    def memory_read_post(self, query: str, results: list, ctx: PluginContext) -> list | None:
        return None

    @hookimpl
    def memory_write_pre(self, content: str, metadata: dict | None, ctx: PluginContext) -> str | None:
        return None

    @hookimpl
    def memory_write_post(self, content: str, metadata: dict | None, id: str, ctx: PluginContext) -> None:
        pass

    # ── Triggers ──

    @hookimpl
    def trigger_append(self, payload: dict, ctx: PluginContext) -> dict | None:
        return None

    @hookimpl
    def trigger_fire(self, payload: dict, ctx: PluginContext) -> dict | None:
        return None

    # ── Prompt ──

    @hookimpl
    def register_prompt_suffix(self) -> list[str]:
        return []

    # ── Config ──

    @hookimpl
    def config_changed(self, key: str, old: Any, new: Any, ctx: PluginContext) -> None:
        pass
