import os
from fastapi import APIRouter, HTTPException
from os.path import join as pjoin
import faust_backend.config_loader as conf
import faust_backend.admin_runtime as admin_runtime
from faust_backend.runtime import state
from faust_backend.runtime.lifecycle import rebuild_runtime

router = APIRouter(tags=["admin-agents"])
router.description = "Agent 管理：列出/创建/查看/编辑/删除 Agent，切换 Agent，删除 Checkpoint"


@router.get("/faust/admin/agents")
async def admin_list_agents():
    return {"items": admin_runtime.list_agents()}


@router.post("/faust/admin/agents")
async def admin_create_agent(payload: dict):
    agent_name = (payload or {}).get("agent_name")
    template_agent = (payload or {}).get("template_agent")
    detail = admin_runtime.create_agent(agent_name, template_agent=template_agent)
    return {"status": "ok", "detail": detail}


@router.get("/faust/admin/agents/{agent_name}")
async def admin_get_agent(agent_name: str):
    return {"status": "ok", "detail": admin_runtime.get_agent_detail(agent_name)}


@router.put("/faust/admin/agents/{agent_name}/files")
async def admin_save_agent_files(agent_name: str, payload: dict):
    files = (payload or {}).get("files") or {}
    updated = admin_runtime.save_agent_files(agent_name, files)
    return {"status": "ok", "files": updated}


@router.delete("/faust/admin/agents/{agent_name}")
async def admin_delete_agent(agent_name: str):
    admin_runtime.delete_agent(agent_name)
    return {"status": "ok", "deleted": agent_name}


@router.post("/faust/admin/agents/switch")
async def admin_switch_agent(payload: dict):
    agent_name = (payload or {}).get("agent_name")
    result = await admin_runtime.switch_agent(agent_name)
    info = await rebuild_runtime(reset_dialog=True, no_initial_chat=False)
    return {
        "status": "ok",
        "switch": result,
        "runtime": info,
        "callback": {
            "type": "runtime_reloaded",
            "scope": "agent_switch",
            "agent_name": info.get("agent_name"),
            "reset_dialog": True,
            "no_initial_chat": False,
        }
    }


@router.delete("/faust/admin/agents/{agent_name}/checkpoint")
async def admin_delete_agent_checkpoint(agent_name: str):
    if agent_name == state.AGENT_NAME:
        raise HTTPException(status_code=400, detail=f"不能删除当前正在使用的 Agent '{state.AGENT_NAME}' 的 checkpoint")
    os.remove(pjoin(conf.CONFIG_ROOT, "agents", agent_name, "faust_checkpoint.db"))
    for ext in ("faust_store.db", "faust_checkpoint.db-shm", "faust_checkpoint.db-wal"):
        fpath = pjoin(conf.CONFIG_ROOT, "agents", agent_name, ext)
        if os.path.exists(fpath):
            os.remove(fpath)
    for extra in ("artifact.json", "subagents.json"):
        extra_path = pjoin(conf.CONFIG_ROOT, "agents", agent_name, extra)
        if os.path.exists(extra_path):
            os.remove(extra_path)
    return {
        "status": "ok",
        "detail": f"Agent '{agent_name}' 的 checkpoint 已删除，下一次重启或切换 Agent 将会重新创建一个新的 checkpoint 文件。",
    }
