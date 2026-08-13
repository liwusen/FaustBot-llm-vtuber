from __future__ import annotations

import asyncio
import inspect
import importlib.util
import json
import os
import shutil
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pluggy

import faust_backend.trigger_manager as trigger_manager
from faust_backend.tools.vfs import get_faustbot_vfs, run_coro_sync

from .hooks import CoreHooks, hookimpl
from .interfaces import MiddlewareSpec, PluginContext, PluginManifest, ToolSpec
from .plugin_base import FaustPlugin

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger

log = get_logger(__name__)

class PluginLoadError(RuntimeError):
    pass


def _ensure_default_plugins() -> None:
    dst = _plugin_user_dir()
    if not dst.exists():
        dst.mkdir(parents=True, exist_ok=True)
    candidates = [
        Path(conf.PROJECT_ROOT) / "default_plugins",
        Path(conf.PROJECT_ROOT).parent / "backend" / "default_plugins",
        Path(__file__).resolve().parents[2] / "default_plugins",
        Path(__file__).resolve().parents[2] / "plugins",
    ]
    for src in candidates:
        if src.exists() and src.is_dir():
            for item in src.iterdir():
                dst_item = dst / item.name
                if item.is_dir():
                    shutil.copytree(item, dst_item, dirs_exist_ok=True)
                    if dst_item.exists():
                        src_children = {child.name for child in item.iterdir()}
                        for stale in list(dst_item.iterdir()):
                            if stale.name not in src_children:
                                if stale.is_dir():
                                    shutil.rmtree(stale, ignore_errors=True)
                                else:
                                    try:
                                        stale.unlink()
                                    except OSError:
                                        pass
                elif item.is_file():
                    shutil.copy(item, dst_item)
            return


def _plugin_user_dir() -> Path:
    if conf:
        return Path(conf.CONFIG_ROOT) / "plugins"
    return Path(__file__).resolve().parents[2] / "plugins"


class PluginManager:
    def __init__(self, plugins_dir: Path | None = None, state_file: Path | None = None):
        self.plugins_dir = Path(plugins_dir) if plugins_dir else _plugin_user_dir()
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = Path(state_file) if state_file else self.plugins_dir / "plugins.state.json"
        _ensure_default_plugins()

        self._state: dict[str, Any] = {"plugins": {}, "configs": {}}
        self._plugins: dict[str, dict[str, Any]] = {}
        self._hot_reload_enabled = False
        self._hot_reload_interval_sec = 2.0
        self._last_reload_ts = 0.0
        self._plugin_fingerprint: dict[str, float] = {}
        self._plugin_enable_fingerprint: str = ""
        # ── pluggy integration ──
        self._pluggy_manager = pluggy.PluginManager("faustbot")
        self._pluggy_manager.add_hookspecs(CoreHooks)
        self._faust_plugins: dict[str, FaustPlugin] = {}
        self._pluggy_loaded = False
        self._sse_abort_events: dict[str, set[asyncio.Event]] = {}

        # ── Scheduler ──
        self._scheduler_task: asyncio.Task | None = None
        self._scheduled_jobs: dict[str, dict] = {}
        self._schedule_lock = asyncio.Lock()
        self._load_state()

    def _load_state(self) -> None:
        if not self.state_file.exists():
            self._save_state()
            return
        try:
            raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            self._state = {
                "plugins": dict(raw.get("plugins") or {}),
                "configs": dict(raw.get("configs") or {}),
            }
        except Exception:
            self._state = {"plugins": {}, "configs": {}}

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

    def _build_enabled_fingerprint(self) -> str:
        states: dict[str, bool] = {}
        for plugin_dir in sorted(self.plugins_dir.iterdir()):
            if not plugin_dir.is_dir() or plugin_dir.name.startswith("_"):
                continue
            manifest = self._load_manifest(plugin_dir)
            states[manifest.plugin_id] = self._plugin_enabled(manifest.plugin_id, manifest.enabled)
        return json.dumps(states, ensure_ascii=False, sort_keys=True)

    def needs_reload(self) -> bool:
        if not self._plugins or not self._plugin_fingerprint:
            return True
        if self._build_plugins_fingerprint() != self._plugin_fingerprint:
            return True
        if self._build_enabled_fingerprint() != self._plugin_enable_fingerprint:
            return True
        return False

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
            description=str(raw.get("description") or ""),
            author=str(raw.get("author") or ""),
            homepage=str(raw.get("homepage") or ""),
            enabled=bool(raw.get("enabled", True)),
            entry=str(raw.get("entry") or "main.py"),
            permissions=list(raw.get("permissions") or []),
            priority=int(raw.get("priority") or 100),
        )

    def _build_plugin_context(self, plugin_id: str, plugin_dir: Path) -> PluginContext:
        vfs = get_faustbot_vfs(refresh=True)
        return PluginContext(
            plugin_id=plugin_id,
            plugin_dir=plugin_dir,
            plugin_data_dir=Path(conf.PLUGIN_DATA_ROOT) / plugin_id,
            config={
                "trigger_create": trigger_manager.append_trigger,
                "trigger_list": trigger_manager.list_triggers,
                "trigger_get": trigger_manager.get_trigger,
                "trigger_update": trigger_manager.update_trigger,
                "trigger_delete": trigger_manager.delete_trigger,
                "plugin_config_register": lambda schema: self._register_plugin_config_schema(plugin_id, schema),
                "plugin_config_get": lambda key, default=None: self._plugin_config_get(plugin_id, key, default),
                "plugin_config_set": lambda key, value: self._plugin_config_set(plugin_id, key, value),
                "plugin_config_list": lambda: self._plugin_config_list(plugin_id),
                "vfs_read_text": lambda path, default="": run_coro_sync(vfs.read_text(path, default=default)),
                "vfs_write": lambda path, content: run_coro_sync(vfs.write(path, content)),
                "vfs_write_symbolic": lambda path, func, should_be_included_in_search=True: run_coro_sync(vfs.write_symbolic(path, func, should_be_included_in_search=should_be_included_in_search)),
                "vfs_delete": lambda path: run_coro_sync(vfs.delete(path)),
                "vfs_list": lambda path="/": run_coro_sync(vfs.list_dir(path)),
            },
        )

    def _ensure_plugin_config_state(self, plugin_id: str) -> dict[str, Any]:
        return self._state.setdefault("configs", {}).setdefault(plugin_id, {"schema": [], "values": {}})

    def _normalize_config_schema(self, schema: str | dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
        items: list[Any]
        if isinstance(schema, str):
            items = []
            for raw_line in schema.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(":", 2)]
                if len(parts) < 3:
                    continue
                key, typ = parts[0], parts[1]
                label_part = parts[2].strip()
                default_value: Any = None
                label = label_part
                # 支持 KEY:type:label=default 语法
                if "=" in label_part:
                    label, default_raw = label_part.rsplit("=", 1)
                    label = label.strip() or key
                    default_raw = default_raw.strip()
                    default_value = None if default_raw == "" else default_raw
                items.append({"key": key, "type": typ, "label": label, "default": default_value})
        elif isinstance(schema, dict):
            items = list(schema.get("fields") or schema.get("items") or [])
        elif isinstance(schema, list):
            items = list(schema)
        else:
            return []

        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("key") or "").strip()
            if not key:
                continue
            typ = str(item.get("type") or "str").strip().lower()
            if typ not in {"str", "string", "int", "float", "bool", "json", "text"}:
                typ = "str"
            default_value = item.get("default")
            if default_value is not None:
                try:
                    default_value = self._coerce_config_value(typ, default_value)
                except Exception:
                    # default 非法时忽略，避免影响插件加载
                    default_value = None
            out.append(
                {
                    "key": key,
                    "type": typ,
                    "label": str(item.get("label") or key),
                    "description": str(item.get("description") or ""),
                    "default": default_value,
                }
            )
        return out

    def _coerce_config_value(self, typ: str, value: Any) -> Any:
        t = (typ or "str").lower()
        if t in {"str", "string", "text"}:
            return "" if value is None else str(value)
        if t == "int":
            return int(value)
        if t == "float":
            return float(value)
        if t == "bool":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                return bool(value)
            return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}
        if t == "json":
            if isinstance(value, (dict, list)):
                return value
            if value is None or str(value).strip() == "":
                return None
            return json.loads(str(value))
        return value

    def _register_plugin_config_schema(self, plugin_id: str, schema: str | dict[str, Any] | list[Any]) -> dict[str, Any]:
        normalized = self._normalize_config_schema(schema)
        state = self._ensure_plugin_config_state(plugin_id)
        state["schema"] = normalized
        values = state.setdefault("values", {})
        for item in normalized:
            key = item["key"]
            if key not in values and item.get("default") is not None:
                try:
                    values[key] = self._coerce_config_value(str(item.get("type") or "str"), item.get("default"))
                except Exception:
                    values[key] = item.get("default")
        self._save_state()
        return {"schema": normalized, "values": dict(values)}

    def _plugin_config_get(self, plugin_id: str, key: str, default: Any = None) -> Any:
        state = self._ensure_plugin_config_state(plugin_id)
        values = state.setdefault("values", {})
        if key in values:
            return values.get(key)
        for item in state.get("schema") or []:
            if str(item.get("key")) == key and item.get("default") is not None:
                return item.get("default")
        return default

    def _plugin_config_set(self, plugin_id: str, key: str, value: Any) -> Any:
        state = self._ensure_plugin_config_state(plugin_id)
        schema = state.get("schema") or []
        value_type = "str"
        for item in schema:
            if str(item.get("key")) == key:
                value_type = str(item.get("type") or "str")
                break
        coerced = self._coerce_config_value(value_type, value)
        state.setdefault("values", {})[key] = coerced
        self._save_state()
        return coerced

    def _plugin_config_list(self, plugin_id: str) -> dict[str, Any]:
        state = self._ensure_plugin_config_state(plugin_id)
        return dict(state.get("values") or {})

    def get_plugin_config_snapshot(self, plugin_id: str) -> dict[str, Any]:
        state = self._ensure_plugin_config_state(plugin_id)
        schema = list(state.get("schema") or [])
        raw_values = dict(state.get("values") or {})
        schema_keys = [str(item.get("key") or "") for item in schema if str(item.get("key") or "")]
        values: dict[str, Any] = {k: raw_values.get(k) for k in schema_keys if k in raw_values}
        for item in schema:
            key = str(item.get("key") or "")
            if key and key not in values and item.get("default") is not None:
                try:
                    values[key] = self._coerce_config_value(str(item.get("type") or "str"), item.get("default"))
                except Exception:
                    values[key] = item.get("default")
        return {"plugin_id": plugin_id, "schema": schema, "values": values}

    def set_plugin_config_values(self, plugin_id: str, values: dict[str, Any]) -> dict[str, Any]:
        state = self._ensure_plugin_config_state(plugin_id)
        schema = {str(item.get("key")): item for item in (state.get("schema") or [])}
        target = state.setdefault("values", {})
        for key, raw_value in (values or {}).items():
            field = schema.get(str(key))
            if field is None:
                continue
            value_type = str(field.get("type") if field else "str")
            target[str(key)] = self._coerce_config_value(value_type, raw_value)
        self._save_state()
        return self.get_plugin_config_snapshot(plugin_id)

    def _load_module(self, plugin_id: str, entry_file: Path) -> ModuleType:
        if not entry_file.exists():
            raise PluginLoadError(f"Plugin entry not found: {entry_file}")
        module_name = f"faust_plugin_{plugin_id}"
        spec = importlib.util.spec_from_file_location(module_name, str(entry_file))
        if spec is None or spec.loader is None:
            raise PluginLoadError(f"Cannot create import spec for {entry_file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def _create_plugin_instance(self, module: ModuleType) -> Any:
        if hasattr(module, "get_plugin") and callable(module.get_plugin):
            return module.get_plugin()
        if hasattr(module, "Plugin"):
            return module.Plugin()
        raise PluginLoadError("Plugin module must expose get_plugin() or Plugin class")

    def _call_plugin_startup(self, plugin: Any, ctx: PluginContext) -> None:
        startup_fn = None
        if hasattr(plugin, "startup"):
            startup_fn = getattr(plugin, "startup")
        elif hasattr(plugin, "Startup"):
            startup_fn = getattr(plugin, "Startup")

        if not callable(startup_fn):
            return

        try:
            startup_fn(ctx)
        except TypeError:
            startup_fn()

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

    def reload(self, *, force: bool = False) -> dict[str, Any]:
        if not force and not self.needs_reload():
            return {
                "loaded": len(self._plugins),
                "errors": [],
                "plugins": [pid for pid in self._plugins.keys()],
                "skipped": True,
            }
        # force-disconnect active SSE streams tied to old plugin instances
        self.abort_all_sse()
        # unload old plugins
        for plugin_id, record in list(self._plugins.items()):
            plugin = record.get("plugin")
            ctx = record.get("ctx")
            try:
                if isinstance(plugin, FaustPlugin):
                    self._pluggy_manager.hook.plugin_unloaded(ctx=ctx)
                    self._pluggy_manager.unregister(plugin)
                if plugin and hasattr(plugin, "on_unload"):
                    plugin.on_unload(ctx) # type: ignore
            except Exception:
                pass

        self._plugins = {}
        self._faust_plugins = {}
        self._pluggy_loaded = False
        errors: list[dict[str, str]] = []

        for plugin_dir in sorted(self.plugins_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            if plugin_dir.name.startswith("_"):
                continue

            manifest = self._load_manifest(plugin_dir)
            if not self._plugin_enabled(manifest.plugin_id, manifest.enabled):
                self._plugins[manifest.plugin_id] = {
                    "manifest": manifest,
                    "ctx": None,
                    "plugin": None,
                    "tools": [],
                    "middlewares": [],
                }
                continue
            ctx = self._build_plugin_context(manifest.plugin_id, plugin_dir)

            try:
                module = self._load_module(manifest.plugin_id, plugin_dir / manifest.entry)
                plugin = self._create_plugin_instance(module)

                # 允许插件在代码里覆盖 manifest（可选）
                if getattr(plugin, "manifest", None) is None:
                    plugin.manifest = manifest
                else:
                    manifest = plugin.manifest

                # ── pluggy registration for FaustPlugin instances ──
                if isinstance(plugin, FaustPlugin):
                    self._pluggy_manager.register(plugin, name=manifest.plugin_id)
                    self._faust_plugins[manifest.plugin_id] = plugin
                    self._pluggy_loaded = True
                    # Call plugin_loaded hook
                    log.debug(f"Calling plugin_loaded hook for plugin: {manifest.plugin_id}")
                    self._pluggy_manager.hook.plugin_loaded(ctx=ctx)
                else:
                    # Old-style plugins
                    if hasattr(plugin, "on_load"):
                        plugin.on_load(ctx)

                self._call_plugin_startup(plugin, ctx)

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
                log.error("加载插件失败 %s: %s", manifest.plugin_id, e)
                errors.append({"plugin": manifest.plugin_id, "error": str(e)})

        # ── Load schedules from pluggy plugins ──
        self._load_schedules()

        # ── Check pip deps ──
        self._install_pip_deps()

        self._save_state()
        self._plugin_fingerprint = self._build_plugins_fingerprint()
        self._plugin_enable_fingerprint = self._build_enabled_fingerprint()
        self._last_reload_ts = time.time()
        return {
            "loaded": len(self._plugins),
            "errors": errors,
            "plugins": [pid for pid in self._plugins.keys()],
            "skipped": False,
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

    def filter_trigger_on_append(self, trigger_payload: dict | None) -> dict | None:
        if not isinstance(trigger_payload, dict):
            return None
        payload = dict(trigger_payload)
        for plugin_id, record in self._plugins.items():
            manifest: PluginManifest = record["manifest"]
            if not self._plugin_enabled(plugin_id, manifest.enabled):
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
                        "enabled": True,
                        "description": t.description,
                    }
                )

            middleware_items = []
            for m in middlewares:
                middleware_items.append(
                    {
                        "name": m.name,
                        "priority": m.priority,
                        "enabled": True,
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
                    "description": manifest.description,
                    "author": manifest.author,
                    "homepage": manifest.homepage,
                    "enabled": self._plugin_enabled(plugin_id, manifest.enabled),
                    "permissions": list(manifest.permissions),
                    "priority": manifest.priority,
                    "tools": tool_items,
                    "middlewares": middleware_items,
                    "trigger_control": {
                        "enabled": True,
                        "supports_append_filter": bool(hasattr(plugin, "filter_trigger_append")),
                        "supports_fire_filter": bool(hasattr(plugin, "filter_trigger_fire")),
                    },
                    "config": self.get_plugin_config_snapshot(plugin_id),
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

    async def communicate(self, plugin_id: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        record = self._plugins.get(plugin_id)
        if not record:
            raise PluginLoadError(f"Plugin not found: {plugin_id}")
        manifest: PluginManifest = record["manifest"]
        if not self._plugin_enabled(plugin_id, manifest.enabled):
            raise PluginLoadError(f"Plugin is disabled: {plugin_id}")
        plugin = record.get("plugin")
        ctx = record.get("ctx")
        if plugin is None or ctx is None:
            raise PluginLoadError(f"Plugin is not loaded: {plugin_id}")
        handler = getattr(plugin, "communicate_handler", None)
        if not callable(handler):
            raise PluginLoadError(f"Plugin does not support communicate: {plugin_id}")
        body = payload if isinstance(payload, dict) else {}
        result = handler(body, ctx)
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            return {"status": "ok"}
        if not isinstance(result, dict):
            raise PluginLoadError(f"Plugin communicate result must be dict: {plugin_id}")
        return result

    def open_sse(self, plugin_id: str, params: dict[str, Any]):
        """Open an SSE stream for a plugin. Returns (async_generator, abort_event)."""
        record = self._plugins.get(plugin_id)
        if not record:
            raise PluginLoadError(f"Plugin not found: {plugin_id}")
        manifest: PluginManifest = record["manifest"]
        if not self._plugin_enabled(plugin_id, manifest.enabled):
            raise PluginLoadError(f"Plugin is disabled: {plugin_id}")
        plugin = record.get("plugin")
        ctx = record.get("ctx")
        if plugin is None or ctx is None:
            raise PluginLoadError(f"Plugin is not loaded: {plugin_id}")
        handler = getattr(plugin, "sse_communicate_handler", None)
        if not callable(handler):
            raise PluginLoadError(f"Plugin does not support sse-communicate: {plugin_id}")
        agen = handler(dict(params or {}), ctx)
        if agen is None:
            raise PluginLoadError(f"Plugin does not support sse-communicate: {plugin_id}")
        if not hasattr(agen, "__anext__"):
            raise PluginLoadError(
                f"sse_communicate_handler must return an async generator: {plugin_id}")
        abort = asyncio.Event()
        self._sse_abort_events.setdefault(plugin_id, set()).add(abort)
        return agen, abort

    def close_sse(self, plugin_id: str, abort: asyncio.Event) -> None:
        events = self._sse_abort_events.get(plugin_id)
        if events:
            events.discard(abort)
            if not events:
                self._sse_abort_events.pop(plugin_id, None)

    def abort_all_sse(self) -> None:
        for events in self._sse_abort_events.values():
            for event in events:
                event.set()
        self._sse_abort_events.clear()

    # ── pluggy hook dispatch helpers ──

    def _call_pluggy_hook(self, hook_name: str, **kwargs) -> list:
        if not self._pluggy_loaded:
            return []
        try:
            hook = getattr(self._pluggy_manager.hook, hook_name, None)
            if hook is None:
                return []
            result = hook(**kwargs)
            return result if isinstance(result, list) else [result]
        except Exception as exc:
            log.warning("插件 hook 调用失败 %s: %s", hook_name, exc)
            return []

    def _load_schedules(self) -> None:
        """Load schedules from pluggy-registered plugins."""
        if not self._pluggy_loaded:
            return
        for plugin_id, plugin in self._faust_plugins.items():
            try:
                schedules = plugin.register_schedules()
                if not schedules:
                    continue
                for s in schedules:
                    s_id = str(s.get("id") or f"{plugin_id}_{id(s)}")
                    s["_plugin"] = plugin_id
                    self._scheduled_jobs[s_id] = s
            except Exception:
                pass

    def _install_pip_deps(self) -> dict[str, Any]:
        """Install pip dependencies declared by plugins."""
        if not self._pluggy_loaded:
            return {"installed": [], "errors": []}
        installed: list[str] = []
        errors: list[dict[str, str]] = []
        import importlib.metadata as importlib_metadata
        import subprocess
        import sys

        for plugin_id, plugin in self._faust_plugins.items():
            try:
                deps = plugin.register_pip_deps()
                if not deps:
                    continue
                to_install: list[str] = []
                for dep in deps:
                    pkg_name = dep.split(">=")[0].split("==")[0].split("!=")[0].strip()
                    try:
                        importlib_metadata.version(pkg_name)
                    except importlib_metadata.PackageNotFoundError:
                        to_install.append(dep)
                if not to_install:
                    continue
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "install", *to_install],
                    capture_output=True, text=True, timeout=120,
                )
                if result.returncode == 0:
                    installed.extend(to_install)
                else:
                    errors.append({"plugin": plugin_id, "deps": to_install, "error": result.stderr[:200]}) # type: ignore
            except Exception as e:
                errors.append({"plugin": plugin_id, "error": str(e)})
        return {"installed": installed, "errors": errors}

    def collect_prompt_suffixes(self) -> list[str]:
        """Collect prompt suffixes from pluggy-registered plugins."""
        if not self._pluggy_loaded:
            return []
        results: list[str] = []
        for plugin_id, plugin in self._faust_plugins.items():
            record = self._plugins.get(plugin_id, {})
            manifest = record.get("manifest")
            default_enabled = getattr(manifest, "enabled", True) if manifest else True
            if not self._plugin_enabled(plugin_id, default_enabled):
                continue
            try:
                suffixes = plugin.register_prompt_suffix()
                if suffixes:
                    for s in suffixes:
                        if s and isinstance(s, str):
                            results.append(s)
            except Exception:
                pass
        return results

    def collect_frontend_assets(self) -> list[dict]:
        """Collect frontend assets from pluggy-registered plugins."""
        if not self._pluggy_loaded:
            return []
        assets: list[dict] = []
        for plugin_id, plugin in self._faust_plugins.items():
            # Skip disabled plugins
            record = self._plugins.get(plugin_id, {})
            manifest = record.get("manifest")
            default_enabled = getattr(manifest, "enabled", True) if manifest else True
            if not self._plugin_enabled(plugin_id, default_enabled):
                continue
            try:
                plugin_assets = plugin.register_frontend()
                if plugin_assets:
                    for a in plugin_assets:
                        a.setdefault("plugin_id", plugin_id)
                        assets.append(a)
            except Exception:
                pass
        return assets

    # ── Scheduler ──

    async def _scheduler_loop(self) -> None:
        """Asyncio task that runs scheduled callbacks."""
        while True:
            try:
                await asyncio.sleep(5.0)
                async with self._schedule_lock:
                    for s_id, s in list(self._scheduled_jobs.items()):
                        try:
                            callback = s.get("callback")
                            if not callable(callback):
                                continue
                            # interval-based scheduling
                            interval = s.get("interval")
                            if not isinstance(interval, (int, float)) or interval <= 0:
                                continue
                            now = time.time()
                            last_run = float(s.get("last_run") or 0.0)
                            if now - last_run < float(interval):
                                continue
                            s["last_run"] = now
                            if asyncio.iscoroutinefunction(callback):
                                asyncio.create_task(callback())
                            else:
                                asyncio.create_task(asyncio.to_thread(callback))
                        except Exception as exc:
                            log.warning("调度任务执行失败 %s: %s", s_id, exc)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.warning("调度循环异常: %s", exc)
                await asyncio.sleep(1.0)

    def start_scheduler(self) -> None:
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    def stop_scheduler(self) -> None:
        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            self._scheduler_task = None
