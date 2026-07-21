from __future__ import annotations
from typing import Any
import pluggy

hookspec = pluggy.HookspecMarker("faustbot")
hookimpl = pluggy.HookimplMarker("faustbot")


class CoreHooks:
    """pluggy hook specifications for the FaustBot plugin system."""

    # ── Lifecycle ──

    @hookspec
    def plugin_loaded(self, ctx: Any) -> None:
        """Called after plugin is loaded. Use for initialization."""

    @hookspec
    def plugin_unloaded(self, ctx: Any) -> None:
        """Called before plugin is unloaded. Use for cleanup."""

    @hookspec
    def heartbeat(self, ctx: Any) -> None:
        """Periodic heartbeat tick (every ~10s)."""

    @hookspec(firstresult=True)
    def health_check(self) -> dict | None:
        """Return health status dict. First non-None result wins."""

    # ── Routes ──

    @hookspec
    def register_routes(self) -> list:
        """Return list of FastAPI APIRouter objects to mount at /faust/plugins/{plugin_id}/."""

    # ── Frontend ──

    @hookspec
    def register_frontend(self) -> list[dict]:
        """Return list of frontend asset dicts: [{type: 'js'|'css', path: str, ...}]."""

    # ── Schedules ──

    @hookspec
    def register_schedules(self) -> list[dict]:
        """Return list of schedule dicts: [{id, cron?, interval?, callback, description}]."""

    # ── Dependencies ──

    @hookspec
    def register_pip_deps(self) -> list[str]:
        """Return list of pip package strings, e.g. ['pandas>=2.0']."""

    # ── Tools & Middleware ──

    @hookspec
    def register_tools(self, ctx: Any) -> list:
        """Return list of ToolSpec or callable tools."""

    @hookspec
    def register_middlewares(self, ctx: Any) -> list:
        """Return list of MiddlewareSpec or middleware objects."""

    @hookspec
    def tool_call_pre(self, name: str, args: dict, ctx: Any) -> dict | None:
        """Intercept/modify tool args before call. Return modified args or None to block."""

    @hookspec
    def tool_call_post(self, name: str, args: dict, result: Any, ctx: Any) -> Any:
        """Modify tool result after call."""

    # ── Messages ──

    @hookspec
    def message_received(self, msg: Any, history: list, ctx: Any) -> str | None:
        """Intercept/modify incoming user message."""

    @hookspec
    def message_sent(self, msg: str, response: Any, ctx: Any) -> Any:
        """Modify agent response before sending."""

    # ── Memory ──

    @hookspec
    def memory_read_pre(self, query: str, filters: dict | None, ctx: Any) -> str | None:
        """Rewrite query before memory read."""

    @hookspec
    def memory_read_post(self, query: str, results: list, ctx: Any) -> list | None:
        """Reorder/filter memory results."""

    @hookspec
    def memory_write_pre(self, content: str, metadata: dict | None, ctx: Any) -> str | None:
        """Intercept/modify content before memory write."""

    @hookspec
    def memory_write_post(self, content: str, metadata: dict | None, id: str, ctx: Any) -> None:
        """Called after memory write."""

    # ── Triggers ──

    @hookspec
    def trigger_append(self, payload: dict, ctx: Any) -> dict | None:
        """Filter/modify trigger on append."""

    @hookspec
    def trigger_fire(self, payload: dict, ctx: Any) -> dict | None:
        """Filter/modify trigger on fire."""

    # ── Prompt ──

    @hookspec
    def register_prompt_suffix(self) -> list[str]:
        """Return list of prompt suffix strings to append to the main agent's system prompt."""

    # ── Config ──

    @hookspec
    def config_changed(self, key: str, old: Any, new: Any, ctx: Any) -> None:
        """Called when a config value changes."""
