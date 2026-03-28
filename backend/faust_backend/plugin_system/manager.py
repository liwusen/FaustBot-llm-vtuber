from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import faust_backend.trigger_manager as trigger_manager

from .interfaces import MiddlewareSpec, PluginContext, PluginManifest, ToolSpec


class PluginLoadError(RuntimeError):
    pass


class PluginManager:
    def __init__(self, plugins_dir: Path | None = None, state_file: Path | None = None):
        backend_root = Path(__file__).resolve().parents[2]
        self.plugins_dir = Path(plugins_dir) if plugins_dir else backend_root / "plugins"
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = Path(state_file) if state_file else self.plugins_dir / "plugins.state.json"

        self._state: dict[str, Any] = {"plugins": {}, "tools": {}, "middlewares": {}, "trigger_controls": {}}
        self._plugins: dict[str, dict[str, Any]] = {}
        self._hot_reload_enabled = False
        self._hot_reload_interval_sec = 2.0
        self._last_reload_ts = 0.0
        self._plugin_fingerprint: dict[str, float] = {}
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_file.exists():
            self._save_state()
            return
        try:
            self._state = json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception:
            self._state = {"plugins": {}, "tools": {}, "middlewares": {}, "trigger_controls": {}}

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_plugins_fingerprint(self) -> dict[str, float]:
        fp: dict[str, float] = {}
        for plugin_dir in sorted(self.plugins_dir.iterdir()):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
                continue
            for p in plugin_dir.rglob("*"):
                if not p.is_file():
                    continue
                if "__pycache__" in p.parts:
                    continue
                if p.suffix not in {".py", ".json", ".yaml", ".yml", ".txt", ".md"}:
                    continue
                try:
                    fp[str(p.resolve())] = p.stat().st_mtime
                except Exception:
                    pass
        return fp

    def _load_manifest(self, plugin_dir: Path) -> PluginManifest:
        manifest_path = plugin_dir / "plugin.json"
        raw: dict[str, Any] = {}
        if manifest_path.exists():
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))

        plugin_id = str(raw.get("id") or plugin_dir.name)
        return PluginManifest(
            plugin_id=plugin_id,
            name=str(raw.get("name") or plugin_id),
            version=str(raw.get("version") or "0.1.0"),
            enabled=bool(raw.get("enabled", True)),
            entry=str(raw.get("entry") or "main.py"),
            permissions=list(raw.get("permissions") or []),
            priority=int(raw.get("priority") or 100),
        )

    def _build_plugin_context(self, plugin_id: str, plugin_dir: Path) -> PluginContext:
        return PluginContext(
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            config={
                "trigger_create": trigger_manager.append_trigger,
                "trigger_list": trigger_manager.list_triggers,
                "trigger_get": trigger_manager.get_trigger,
                "trigger_update": trigger_manager.update_trigger,
                "trigger_delete": trigger_manager.delete_trigger,
            },
        )

    def _load_module(self, plugin_id: str, entry_file: Path) -> ModuleType:
        if not entry_file.exists():
            raise PluginLoadError(f"Plugin entry not found: {entry_file}")
        module_name = f"faust_plugin_{plugin_id}"
        spec = importlib.util.spec_from_file_location(module_name, str(entry_file))
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Cannot create import spec for {entry_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _create_plugin_instance(self, module: ModuleType) -> Any:
        if hasattr(module, "get_plugin") and callable(module.get_plugin):
            return module.get_plugin()
        if hasattr(module, "Plugin"):
            return module.Plugin()
        raise PluginLoadError("Plugin module must expose get_plugin() or Plugin class")

    def _normalize_tool_specs(self, plugin_id: str, tools: list[Any] | None) -> list[ToolSpec]:
        out: list[ToolSpec] = []
        for item in tools or []:
            if isinstance(item, ToolSpec):
                out.append(item)
                continue
            if callable(item):
                name = getattr(item, "name", None) or getattr(item, "__name__", "tool")
                out.append(ToolSpec(name=str(name), tool=item))
                continue
            if isinstance(item, dict) and item.get("tool") is not None:
                out.append(
                    ToolSpec(
                        name=str(item.get("name") or getattr(item.get("tool"), "__name__", "tool")),
                        tool=item.get("tool"),
                        enabled_by_default=bool(item.get("enabled_by_default", True)),
                        description=str(item.get("description") or ""),
                    )
                )
        # 冲突处理：同一个插件内按 name 去重，后者覆盖前者
        dedup: dict[str, ToolSpec] = {t.name: t for t in out}
        return list(dedup.values())

    def _normalize_middleware_specs(self, middlewares: list[Any] | None) -> list[MiddlewareSpec]:
        out: list[MiddlewareSpec] = []
        for item in middlewares or []:
            if isinstance(item, MiddlewareSpec):
                out.append(item)
                continue
            if isinstance(item, dict) and item.get("middleware") is not None:
                out.append(
                    MiddlewareSpec(
                        name=str(item.get("name") or type(item.get("middleware")).__name__),
                        middleware=item.get("middleware"),
                        priority=int(item.get("priority") or 100),
                        enabled_by_default=bool(item.get("enabled_by_default", True)),
                        description=str(item.get("description") or ""),
                    )
                )
                continue
            # 直接对象形式
            out.append(MiddlewareSpec(name=type(item).__name__, middleware=item))

        dedup: dict[str, MiddlewareSpec] = {m.name: m for m in out}
        return list(dedup.values())

    def _plugin_enabled(self, plugin_id: str, default: bool) -> bool:
        p_state = self._state.setdefault("plugins", {}).setdefault(plugin_id, {})
        return bool(p_state.get("enabled", default))

    def _tool_enabled(self, plugin_id: str, tool_name: str, default: bool) -> bool:
        key = f"{plugin_id}:{tool_name}"
        t_state = self._state.setdefault("tools", {}).setdefault(key, {})
        return bool(t_state.get("enabled", default))

    def _middleware_enabled(self, plugin_id: str, middleware_name: str, default: bool) -> bool:
        key = f"{plugin_id}:{middleware_name}"
        m_state = self._state.setdefault("middlewares", {}).setdefault(key, {})
        return bool(m_state.get("enabled", default))

    def _trigger_control_enabled(self, plugin_id: str, default: bool = True) -> bool:
        t_state = self._state.setdefault("trigger_controls", {}).setdefault(plugin_id, {})
        return bool(t_state.get("enabled", default))

    def reload(self) -> dict[str, Any]:
        # unload old plugins
        for plugin_id, record in list(self._plugins.items()):
            plugin = record.get("plugin")
            ctx = record.get("ctx")
            try:
                if plugin and hasattr(plugin, "on_unload"):
                    plugin.on_unload(ctx)
            except Exception:
                pass

        self._plugins = {}
        errors: list[dict[str, str]] = []

        for plugin_dir in sorted(self.plugins_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            if plugin_dir.name.startswith("_"):
                continue

            manifest = self._load_manifest(plugin_dir)
            ctx = self._build_plugin_context(manifest.plugin_id, plugin_dir)

            try:
                module = self._load_module(manifest.plugin_id, plugin_dir / manifest.entry)
                plugin = self._create_plugin_instance(module)

                # 允许插件在代码里覆盖 manifest（可选）
                if getattr(plugin, "manifest", None) is None:
                    plugin.manifest = manifest
                else:
                    manifest = plugin.manifest

                if hasattr(plugin, "on_load"):
                    plugin.on_load(ctx)

                tools = self._normalize_tool_specs(manifest.plugin_id, plugin.register_tools(ctx) if hasattr(plugin, "register_tools") else [])
                middlewares = self._normalize_middleware_specs(plugin.register_middlewares(ctx) if hasattr(plugin, "register_middlewares") else [])

                self._plugins[manifest.plugin_id] = {
                    "manifest": manifest,
                    "ctx": ctx,
                    "plugin": plugin,
                    "tools": tools,
                    "middlewares": middlewares,
                }
            except Exception as e:
                errors.append({"plugin": manifest.plugin_id, "error": str(e)})

        self._save_state()
        self._plugin_fingerprint = self._build_plugins_fingerprint()
        self._last_reload_ts = time.time()
        return {
            "loaded": len(self._plugins),
            "errors": errors,
            "plugins": [pid for pid in self._plugins.keys()],
        }

    def configure_hot_reload(self, *, enabled: bool | None = None, interval_sec: float | None = None) -> dict[str, Any]:
        if enabled is not None:
            self._hot_reload_enabled = bool(enabled)
        if interval_sec is not None:
            try:
                self._hot_reload_interval_sec = max(0.5, float(interval_sec))
            except Exception:
                self._hot_reload_interval_sec = 2.0
        return self.hot_reload_status()

    def hot_reload_status(self) -> dict[str, Any]:
        return {
            "enabled": self._hot_reload_enabled,
            "interval_sec": self._hot_reload_interval_sec,
            "last_reload_ts": self._last_reload_ts,
        }

    def hot_reload_tick(self) -> dict[str, Any]:
        if not self._hot_reload_enabled:
            return {"changed": False, "enabled": False}
        new_fp = self._build_plugins_fingerprint()
        if not self._plugin_fingerprint:
            self._plugin_fingerprint = new_fp
            return {"changed": False, "enabled": True}
        if new_fp == self._plugin_fingerprint:
            return {"changed": False, "enabled": True}
        summary = self.reload()
        return {"changed": True, "enabled": True, "reload": summary}

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> None:
        self._state.setdefault("plugins", {}).setdefault(plugin_id, {})["enabled"] = bool(enabled)
        self._save_state()

    def set_tool_enabled(self, plugin_id: str, tool_name: str, enabled: bool) -> None:
        key = f"{plugin_id}:{tool_name}"
        self._state.setdefault("tools", {}).setdefault(key, {})["enabled"] = bool(enabled)
        self._save_state()

    def set_middleware_enabled(self, plugin_id: str, middleware_name: str, enabled: bool) -> None:
        key = f"{plugin_id}:{middleware_name}"
        self._state.setdefault("middlewares", {}).setdefault(key, {})["enabled"] = bool(enabled)
        self._save_state()

    def set_trigger_control_enabled(self, plugin_id: str, enabled: bool) -> None:
        self._state.setdefault("trigger_controls", {}).setdefault(plugin_id, {})["enabled"] = bool(enabled)
        self._save_state()

    def filter_trigger_on_append(self, trigger_payload: dict | None) -> dict | None:
        if not isinstance(trigger_payload, dict):
            return None
        payload = dict(trigger_payload)
        for plugin_id, record in self._plugins.items():
            manifest: PluginManifest = record["manifest"]
            if not self._plugin_enabled(plugin_id, manifest.enabled):
                continue
            if not self._trigger_control_enabled(plugin_id, True):
                continue
            plugin = record.get("plugin")
            if plugin is None or not hasattr(plugin, "filter_trigger_append"):
                continue
            try:
                payload = plugin.filter_trigger_append(payload)
                if payload is None:
                    return None
                if not isinstance(payload, dict):
                    return None
            except Exception:
                return None
        return payload

    def filter_trigger_on_fire(self, trigger_payload: dict | None) -> dict | None:
        if not isinstance(trigger_payload, dict):
            return None
        payload = dict(trigger_payload)
        for plugin_id, record in self._plugins.items():
            manifest: PluginManifest = record["manifest"]
            if not self._plugin_enabled(plugin_id, manifest.enabled):
                continue
            if not self._trigger_control_enabled(plugin_id, True):
                continue
            plugin = record.get("plugin")
            if plugin is None or not hasattr(plugin, "filter_trigger_fire"):
                continue
            try:
                payload = plugin.filter_trigger_fire(payload)
                if payload is None:
                    return None
                if not isinstance(payload, dict):
                    return None
            except Exception:
                return None
        return payload

    def compose_tools(self, base_tools: list[Any], agent_name: str | None = None) -> list[Any]:
        merged: list[Any] = list(base_tools or [])
        existing_names = {
            str(getattr(t, "name", None) or getattr(t, "__name__", ""))
            for t in merged
        }

        for plugin_id, record in self._plugins.items():
            manifest: PluginManifest = record["manifest"]
            if not self._plugin_enabled(plugin_id, manifest.enabled):
                continue

            for spec in record["tools"]:
                if not self._tool_enabled(plugin_id, spec.name, spec.enabled_by_default):
                    continue
                tool_name = str(getattr(spec.tool, "name", None) or getattr(spec.tool, "__name__", spec.name))
                if tool_name in existing_names:
                    # 命名冲突：跳过插件工具，避免覆盖内置
                    continue
                merged.append(spec.tool)
                existing_names.add(tool_name)

        return merged

    def compose_middlewares(self, agent_name: str | None = None) -> list[Any]:
        candidates: list[tuple[int, str, Any]] = []
        for plugin_id, record in self._plugins.items():
            manifest: PluginManifest = record["manifest"]
            if not self._plugin_enabled(plugin_id, manifest.enabled):
                continue

            for spec in record["middlewares"]:
                if not self._middleware_enabled(plugin_id, spec.name, spec.enabled_by_default):
                    continue
                priority = int(spec.priority if spec.priority is not None else manifest.priority)
                candidates.append((priority, f"{plugin_id}:{spec.name}", spec.middleware))

        candidates.sort(key=lambda x: (x[0], x[1]))
        return [item[2] for item in candidates]

    def list_plugins(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for plugin_id, record in self._plugins.items():
            manifest: PluginManifest = record["manifest"]
            plugin = record.get("plugin")
            tools: list[ToolSpec] = record.get("tools", [])
            middlewares: list[MiddlewareSpec] = record.get("middlewares", [])

            tool_items = []
            for t in tools:
                tool_items.append(
                    {
                        "name": t.name,
                        "enabled": self._tool_enabled(plugin_id, t.name, t.enabled_by_default),
                        "description": t.description,
                    }
                )

            middleware_items = []
            for m in middlewares:
                middleware_items.append(
                    {
                        "name": m.name,
                        "priority": m.priority,
                        "enabled": self._middleware_enabled(plugin_id, m.name, m.enabled_by_default),
                        "description": m.description,
                    }
                )

            health = {"status": "unknown"}
            if plugin and hasattr(plugin, "health_check"):
                try:
                    health = plugin.health_check() or {"status": "ok"}
                except Exception as e:
                    health = {"status": "error", "error": str(e)}

            out.append(
                {
                    "id": plugin_id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "enabled": self._plugin_enabled(plugin_id, manifest.enabled),
                    "permissions": list(manifest.permissions),
                    "priority": manifest.priority,
                    "tools": tool_items,
                    "middlewares": middleware_items,
                    "trigger_control": {
                        "enabled": self._trigger_control_enabled(plugin_id, True),
                        "supports_append_filter": bool(hasattr(plugin, "filter_trigger_append")),
                        "supports_fire_filter": bool(hasattr(plugin, "filter_trigger_fire")),
                    },
                    "health": health,
                }
            )

        return out

    def heartbeat_tick(self) -> dict[str, Any]:
        called = 0
        errors: list[dict[str, str]] = []
        for plugin_id, record in self._plugins.items():
            manifest: PluginManifest = record["manifest"]
            if not self._plugin_enabled(plugin_id, manifest.enabled):
                continue

            plugin = record.get("plugin")
            ctx = record.get("ctx")
            if plugin is None:
                continue

            hb = None
            if hasattr(plugin, "Heartbeat"):
                hb = getattr(plugin, "Heartbeat")
            elif hasattr(plugin, "heartbeat"):
                hb = getattr(plugin, "heartbeat")
            elif hasattr(plugin, "on_heartbeat"):
                hb = getattr(plugin, "on_heartbeat")

            if not callable(hb):
                continue

            try:
                try:
                    hb(ctx)
                except TypeError:
                    hb()
                called += 1
            except Exception as e:
                errors.append({"plugin": plugin_id, "error": str(e)})

        return {"called": called, "errors": errors}
