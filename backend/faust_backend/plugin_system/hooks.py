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

    # ── Frontend ──

    @hookspec
    def register_frontend(self) -> list[dict]:
        """Return list of frontend asset dicts: [{type: 'js'|'css', path: str, ...}]."""

    @hookspec(firstresult=True)
    def communicate_handler(self, payload: dict, ctx: Any) -> dict | None:
        """Handle plugin frontend/backend communication via POST /faust/plugins/{plugin_id}/communicate."""

    @hookspec(firstresult=True)
    def sse_communicate_handler(self, params: dict, ctx: Any) -> Any:
        """Handle GET /faust/plugins/{plugin_id}/sse-communicate?... as a Server-Sent-Events stream.

        Must return an async generator: each yielded dict is sent as one SSE
        event (`data: <json>`). When the generator returns (or raises), the
        connection is closed. On client disconnect / plugin reload the
        generator receives GeneratorExit for cleanup."""

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
    def agent_event_sent(self, event: dict, current_history: list, ctx: Any) -> dict | None:
        """Called before each agent event is sent to the frontend WebSocket.
        Return a modified event dict, or None to suppress the event.
        current_history is the list of events already sent in this turn."""

    # ── LLM ──

    @hookspec
    def llm_request_pre(self, messages: list, ctx: Any) -> list | None:
        """Called right before each agent LLM invocation (ainvoke / astream_events).
        Return a modified messages list to replace the payload, or None to pass through.
        Use for prompt injection, message rewriting, or observing the exact
        system/user/assistant/tool messages sent to the model."""

    # ── TTS ──

    @hookspec(firstresult=True)
    def tts_text(self, text: str, ctx: Any) -> str | None:
        """Rewrite the text before TTS synthesis. First non-None result wins.
        Only affects synthesized speech, not the subtitle."""

    @hookspec
    def tts_start(self, text: str, ctx: Any) -> None:
        """Called when a TTS segment finishes synthesis and is about to be
        delivered to the frontend for playback."""

    @hookspec
    def tts_end(self, text: str, ctx: Any) -> None:
        """Called when TTS playback ends (requires a frontend playback-report
        channel; currently defined for API completeness)."""

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
