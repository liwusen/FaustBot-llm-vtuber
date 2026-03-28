from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass
class PluginContext:
    plugin_id: str
    plugin_dir: Path
    config: dict[str, Any] = field(default_factory=dict)


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
    enabled: bool = True
    entry: str = "main.py"
    permissions: list[str] = field(default_factory=list)
    priority: int = 100


class PluginProtocol(Protocol):
    manifest: PluginManifest

    def on_load(self, ctx: PluginContext) -> None:
        ...

    def on_unload(self, ctx: PluginContext) -> None:
        ...

    def register_tools(self, ctx: PluginContext) -> list[ToolSpec] | list[Any]:
        ...

    def register_middlewares(self, ctx: PluginContext) -> list[MiddlewareSpec] | list[Any]:
        ...

    def health_check(self) -> dict[str, Any]:
        ...
