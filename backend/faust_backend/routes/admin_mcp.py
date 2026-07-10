from __future__ import annotations

from fastapi import APIRouter, HTTPException

import faust_backend.admin_runtime as admin_runtime
import faust_backend.config_loader as conf
from faust_backend.mcp_manager import get_mcp_manager

router = APIRouter(tags=["admin-mcp"])
router.description = "MCP server 管理：配置、启停、状态与日志查看"


def _normalize_server_id(server_id: str) -> str:
    candidate = str(server_id or "").strip()
    if not candidate:
        raise HTTPException(status_code=400, detail="server_id 不能为空")
    return candidate


def _load_mcp_config() -> dict:
    public_cfg = admin_runtime.get_public_config()
    return dict(public_cfg.get("mcp_servers") or {})


def _save_mcp_config(mcp_servers: dict) -> None:
    admin_runtime.save_config({"public": {"mcp_servers": mcp_servers}})
    conf.reload_configs()
    mgr = get_mcp_manager()
    mgr.load_config(getattr(conf, "MCP_SERVERS", {}) or {})


@router.get("/faust/admin/mcp/servers")
async def admin_list_mcp_servers(include_log: bool = False):
    mgr = get_mcp_manager()
    mgr.load_config(getattr(conf, "MCP_SERVERS", {}) or {})
    return {"status": "ok", "items": mgr.list_server_statuses(include_log=include_log)}


@router.post("/faust/admin/mcp/{server_id}/start")
async def admin_start_mcp_server(server_id: str):
    server_id = _normalize_server_id(server_id)
    mcp_servers = _load_mcp_config()
    if server_id not in mcp_servers:
        raise HTTPException(status_code=404, detail=f"MCP server 不存在: {server_id}")
    mcp_servers[server_id]["enabled"] = True
    _save_mcp_config(mcp_servers)
    mgr = get_mcp_manager()
    try:
        item = await mgr.start_server(server_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "item": item}


@router.post("/faust/admin/mcp/{server_id}/stop")
async def admin_stop_mcp_server(server_id: str):
    server_id = _normalize_server_id(server_id)
    mcp_servers = _load_mcp_config()
    if server_id not in mcp_servers:
        raise HTTPException(status_code=404, detail=f"MCP server 不存在: {server_id}")
    mcp_servers[server_id]["enabled"] = False
    _save_mcp_config(mcp_servers)
    mgr = get_mcp_manager()
    item = await mgr.stop_server(server_id)
    return {"status": "ok", "item": item}


@router.put("/faust/admin/mcp/{server_id}")
async def admin_put_mcp_server(server_id: str, payload: dict):
    server_id = _normalize_server_id(server_id)
    body = dict(payload or {})
    body.pop("server_id", None)
    body.pop("id", None)
    transport = str(body.get("transport") or "stdio").strip().lower()
    item = {
        "enabled": bool(body.get("enabled", False)),
        "description": str(body.get("description") or ""),
        "custom": bool(body.get("custom", False)),
        "transport": transport,
        "command": str(body.get("command") or "node").strip(),
        "args": [str(x) for x in list(body.get("args") or [])],
        "url": str(body.get("url") or "").strip(),
    }
    if transport == "sse":
        item["custom"] = True
        item.pop("command", None)
    elif not item["custom"]:
        item.pop("command", None)
        item.pop("url", None)
    mcp_servers = _load_mcp_config()
    mcp_servers[server_id] = item
    _save_mcp_config(mcp_servers)
    mgr = get_mcp_manager()
    try:
        if item["enabled"]:
            result = await mgr.start_server(server_id)
        else:
            result = await mgr.stop_server(server_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "item": result}


@router.delete("/faust/admin/mcp/{server_id}")
async def admin_delete_mcp_server(server_id: str):
    server_id = _normalize_server_id(server_id)
    mcp_servers = _load_mcp_config()
    if server_id not in mcp_servers:
        raise HTTPException(status_code=404, detail=f"MCP server 不存在: {server_id}")
    mcp_servers.pop(server_id, None)
    _save_mcp_config(mcp_servers)
    mgr = get_mcp_manager()
    await mgr.stop_server(server_id)
    return {"status": "ok", "server_id": server_id}