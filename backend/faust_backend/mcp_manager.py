from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import Field, create_model

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
except Exception:
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    sse_client = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]

log = get_logger("faust.mcp")


def _schema_type(schema: dict[str, Any]) -> type[Any]:
    type_name = str(schema.get("type") or "string").lower()
    if type_name == "string":
        return str
    if type_name == "number":
        return float
    if type_name == "integer":
        return int
    if type_name == "boolean":
        return bool
    if type_name == "array":
        return list
    if type_name == "object":
        return dict
    return Any


def _json_schema_to_args_model(name: str, schema: dict[str, Any] | None):
    if not schema or not isinstance(schema, dict):
        return None
    properties = schema.get("properties")
    if not isinstance(properties, dict) or not properties:
        return None
    required = set(schema.get("required") or [])
    fields: dict[str, tuple[type[Any], Any]] = {}
    for field_name, field_schema in properties.items():
        if not isinstance(field_schema, dict):
            field_schema = {}
        field_type = _schema_type(field_schema)
        default = ... if field_name in required else None
        fields[str(field_name)] = (
            field_type,
            Field(default=default, description=str(field_schema.get("description") or "")),
        )
    model_name = "MCPArgs_" + "".join(ch if ch.isalnum() else "_" for ch in name)
    return create_model(model_name, **fields)


def _find_nodejs() -> str:
    env = str(os.environ.get("FAUST_NODEJS") or "").strip()
    if env and os.path.isfile(env):
        return env
    candidate = Path(conf.PROJECT_ROOT).parent / ".nodejs" / "node.exe"
    if candidate.exists():
        return str(candidate)
    return str(os.environ.get("NODE") or "node")


def _replace_bundled_command(command: str, node_path: str) -> str:
    token = str(command or "").strip()
    lowered = token.lower()
    node_root = Path(node_path).parent
    if lowered in {"", "node", "node.exe"}:
        return node_path
    if lowered in {"npx", "npx.cmd"}:
        return str(node_root / "npx.cmd")
    return token


def _tool_result_to_text(result: Any) -> str:
    if result is None:
        return ""
    texts: list[str] = []
    for block in list(getattr(result, "content", []) or []):
        text = getattr(block, "text", None)
        if text:
            texts.append(str(text))
            continue
        resource = getattr(block, "resource", None)
        if resource is not None:
            resource_text = getattr(resource, "text", None)
            resource_uri = getattr(resource, "uri", None)
            if resource_text:
                texts.append(str(resource_text))
            elif resource_uri:
                texts.append(f"[resource] {resource_uri}")
            continue
        mime_type = getattr(block, "mimeType", None)
        if mime_type:
            texts.append(f"[{mime_type} content omitted]")
    structured = getattr(result, "structuredContent", None)
    if structured not in (None, "", []):
        payload = json.dumps(structured, ensure_ascii=False, indent=2)
        if texts:
            texts.append(payload)
        else:
            return payload
    if getattr(result, "isError", False) and texts:
        return "MCP tool error:\n" + "\n".join(texts)
    if texts:
        return "\n".join(texts)
    return json.dumps({"result": str(result)}, ensure_ascii=False)


def _strip_none_values(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            if item is None:
                continue
            cleaned[str(key)] = _strip_none_values(item)
        return cleaned
    if isinstance(value, list):
        return [_strip_none_values(item) for item in value]
    return value


def _build_langchain_tool(server_id: str, tool_name: str, description: str, args_schema, manager: "McpManager"):
    exposed_name = tool_name if tool_name.startswith(f"{server_id}_") else f"{server_id}_{tool_name}"

    async def _call_tool(**kwargs) -> str:
        log.debug(f"Calling MCP tool {exposed_name} with args: {kwargs}")
        return await manager.call_tool(server_id, tool_name, kwargs)

    _call_tool.__doc__ = description or f"MCP tool {tool_name} from {server_id}"
    return StructuredTool.from_function(
        coroutine=_call_tool,
        name=exposed_name,
        description=description or f"MCP tool {tool_name} from {server_id}",
        args_schema=args_schema,
        return_direct=False,
    )


@dataclass
class McpServerHandle:
    server_id: str
    config: dict[str, Any]
    transport: str
    session: Any = None
    transport_ctx: Any = None
    server_params: Any = None
    logs: list[str] = field(default_factory=list)
    last_error: str = ""
    tool_specs: list[dict[str, Any]] = field(default_factory=list)
    initialized: bool = False

    def add_log(self, message: str) -> None:
        line = str(message or "").strip()
        if not line:
            return
        self.logs.append(line)
        if len(self.logs) > 200:
            self.logs = self.logs[-200:]


class McpManager:
    def __init__(self) -> None:
        self._config: dict[str, dict[str, Any]] = {}
        self._servers: dict[str, McpServerHandle] = {}
        self._tool_cache: list[Any] = []
        self._nodejs: str | None = None
        self._lock = asyncio.Lock()

    def load_config(self, raw: dict[str, Any] | None) -> None:
        cfg = raw or {}
        self._config = {str(k): dict(v or {}) for k, v in cfg.items() if isinstance(v, dict)}

    def _require_sdk(self) -> None:
        if ClientSession is None or StdioServerParameters is None or sse_client is None or stdio_client is None:
            raise RuntimeError("未安装 MCP Python SDK，请先安装依赖 mcp>=1.28,<2")

    def _get_nodejs(self) -> str:
        if self._nodejs:
            return self._nodejs
        self._nodejs = _find_nodejs()
        return self._nodejs

    def _builtin_stdio_params(self, server_id: str, cfg: dict[str, Any]):
        node_path = self._get_nodejs()
        node_root = Path(node_path).parent
        if server_id == "playwright":
            cli_path = node_root / "mcp-server" / "node_modules" / "@playwright" / "mcp" / "cli.js"
            if not cli_path.exists():
                raise RuntimeError(f"MCP server {server_id} 未安装: {cli_path}")
            log.debug(f"Using Playwright MCP CLI at: {cli_path}")
            log.debug(f"Playwright MCP args: {cfg.get('args')}")
            log.debug(f"node path: {node_path}")
            log.debug(f"real args{[str(cli_path), *list(cfg.get('args') or [])]}")
            return StdioServerParameters(
                command=node_path,
                args=[str(cli_path), *list(cfg.get("args") or [])],
                env=dict(os.environ),
            )
        raise RuntimeError(f"未知内建 MCP server: {server_id}")

    def _custom_stdio_params(self, cfg: dict[str, Any]):
        node_path = self._get_nodejs()
        return StdioServerParameters(
            command=_replace_bundled_command(str(cfg.get("command") or "node"), node_path),
            args=[str(x) for x in list(cfg.get("args") or [])],
            env=dict(os.environ),
        )

    async def _on_log(self, handle: McpServerHandle, params: Any) -> None:
        handle.add_log(f"[{getattr(params, 'level', 'info')}] {getattr(params, 'data', '')}")

    async def _list_tools(self, handle: McpServerHandle) -> list[dict[str, Any]]:
        result = await handle.session.list_tools()
        items = []
        for tool_info in list(getattr(result, "tools", []) or []):
            items.append({
                "name": str(getattr(tool_info, "name", "") or "").strip(),
                "title": str(getattr(tool_info, "title", "") or "").strip(),
                "description": str(getattr(tool_info, "description", "") or "").strip(),
                "inputSchema": getattr(tool_info, "inputSchema", None),
            })
        return items

    async def _stop_handle(self, handle: McpServerHandle) -> None:
        session = handle.session
        transport_ctx = handle.transport_ctx
        handle.session = None
        handle.transport_ctx = None
        if session is not None:
            try:
                await session.__aexit__(None, None, None)
            except Exception as exc:
                handle.add_log(f"session close error: {exc}")
        if transport_ctx is not None:
            try:
                await transport_ctx.__aexit__(None, None, None)
            except Exception as exc:
                handle.add_log(f"transport close error: {exc}")
        handle.initialized = False
        handle.tool_specs = []

    async def start_server(self, server_id: str) -> dict[str, Any]:
        self._require_sdk()
        async with self._lock:
            cfg = self._config.get(server_id)
            if not cfg:
                raise RuntimeError(f"未知 MCP server: {server_id}")
            existing = self._servers.get(server_id)
            if existing and existing.initialized and existing.session is not None:
                return self.get_server_status(server_id, include_log=True)

            transport = str(cfg.get("transport") or "stdio").strip().lower()
            handle = existing or McpServerHandle(server_id=server_id, config=dict(cfg), transport=transport)
            handle.config = dict(cfg)
            handle.transport = transport
            handle.last_error = ""
            handle.tool_specs = []
            handle.initialized = False
            handle.add_log("starting server")

            try:
                if transport == "sse":
                    url = str(cfg.get("url") or "").strip()
                    if not url:
                        raise RuntimeError("SSE 模式缺少 url")
                    handle.transport_ctx = sse_client(url)
                    read_stream, write_stream = await handle.transport_ctx.__aenter__()
                else:
                    server_params = self._custom_stdio_params(cfg) if cfg.get("custom") else self._builtin_stdio_params(server_id, cfg)
                    handle.server_params = server_params
                    handle.transport_ctx = stdio_client(server_params)
                    read_stream, write_stream = await handle.transport_ctx.__aenter__()

                handle.session = ClientSession(
                    read_stream,
                    write_stream,
                    logging_callback=lambda params, _handle=handle: self._on_log(_handle, params),
                )
                await handle.session.__aenter__()
                await handle.session.initialize()
                handle.tool_specs = await self._list_tools(handle)
                handle.initialized = True
                handle.add_log(f"initialized with {len(handle.tool_specs)} tools")
                self._servers[server_id] = handle
                self._rebuild_tool_cache()
                return self.get_server_status(server_id, include_log=True)
            except Exception as exc:
                handle.last_error = str(exc)
                handle.add_log(f"error: {exc}")
                self._servers[server_id] = handle
                await self._stop_handle(handle)
                self._rebuild_tool_cache()
                raise

    async def stop_server(self, server_id: str) -> dict[str, Any]:
        async with self._lock:
            handle = self._servers.get(server_id)
            if handle is None:
                return self.get_server_status(server_id, include_log=True)
            handle.add_log("stopping server")
            await self._stop_handle(handle)
            self._rebuild_tool_cache()
            return self.get_server_status(server_id, include_log=True)

    async def stop_all(self) -> None:
        async with self._lock:
            for handle in list(self._servers.values()):
                await self._stop_handle(handle)
            self._rebuild_tool_cache()

    async def sync_servers(self) -> None:
        desired_ids = set(self._config.keys())
        for server_id in list(self._servers.keys()):
            if server_id not in desired_ids:
                await self.stop_server(server_id)
                self._servers.pop(server_id, None)
        for server_id, cfg in self._config.items():
            if bool(cfg.get("enabled", False)):
                await self.start_server(server_id)
            else:
                await self.stop_server(server_id)
        self._rebuild_tool_cache()

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        handle = self._servers.get(server_id)
        if handle is None or handle.session is None or not handle.initialized:
            raise RuntimeError(f"MCP server 未运行: {server_id}")
        clean_arguments = _strip_none_values(arguments or {})
        result = await handle.session.call_tool(tool_name, arguments=clean_arguments)
        handle.add_log(f"call {tool_name}")
        return _tool_result_to_text(result)

    def _rebuild_tool_cache(self) -> None:
        tools = []
        for server_id in sorted(self._servers.keys()):
            handle = self._servers[server_id]
            if not handle.initialized or handle.session is None:
                continue
            for spec in handle.tool_specs:
                tool_name = str(spec.get("name") or "").strip()
                if not tool_name:
                    continue
                args_schema = _json_schema_to_args_model(f"{server_id}_{tool_name}", spec.get("inputSchema"))
                tools.append(_build_langchain_tool(server_id, tool_name, str(spec.get("description") or ""), args_schema, self))
        self._tool_cache = tools

    def get_langchain_tools(self) -> list[Any]:
        return list(self._tool_cache)

    def get_server_status(self, server_id: str, *, include_log: bool = False) -> dict[str, Any]:
        cfg = dict(self._config.get(server_id) or {})
        handle = self._servers.get(server_id)
        item = {
            "server_id": server_id,
            "id": server_id,
            "enabled": bool(cfg.get("enabled", False)),
            "description": str(cfg.get("description") or ""),
            "custom": bool(cfg.get("custom", False)),
            "transport": str(cfg.get("transport") or "stdio"),
            "command": cfg.get("command") or ("node" if bool(cfg.get("custom", False)) else "builtin"),
            "args": list(cfg.get("args") or []),
            "url": str(cfg.get("url") or ""),
            "status": "stopped",
            "running": False,
            "tool_count": 0,
            "tools": [],
            "error": "",
        }
        if handle is not None:
            item["running"] = bool(handle.initialized and handle.session is not None)
            item["status"] = "running" if item["running"] else ("error" if handle.last_error else "stopped")
            item["tool_count"] = len(handle.tool_specs)
            item["tools"] = [dict(x) for x in handle.tool_specs]
            item["error"] = handle.last_error
            if include_log:
                item["log_tail"] = list(handle.logs[-100:])
        return item

    def list_server_statuses(self, *, include_log: bool = False) -> list[dict[str, Any]]:
        ids = sorted(set(self._config.keys()) | set(self._servers.keys()))
        return [self.get_server_status(server_id, include_log=include_log) for server_id in ids]


_manager: McpManager | None = None


def get_mcp_manager() -> McpManager:
    global _manager
    if _manager is None:
        _manager = McpManager()
    return _manager