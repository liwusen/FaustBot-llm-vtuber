from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body
from faust_backend.routes.subagents import subagent_status_overrides
from faust_backend.runtime import state

from faust_backend.skill_manager import list_skills
from faust_backend.logger import get_logger
import faust_backend.logger as faust_logger
from faust_backend.runtime import state
import sys
from faust_backend.tools.vfs import get_faustbot_vfs
from faust_backend.config_loader import CONFIG_ROOT
import faust_backend.trigger_manager as trigger_manager
log = get_logger("faust.debugging")

router = APIRouter(tags=["debugging"])
router.description = "调试接口：手动聊天、覆写 Subagent 状态"


@router.get("/faust/debugging/vfs-read/{path:path}")
async def vfs_read(path: str):
    """调试接口：读取虚拟文件系统内容"""
    if await get_faustbot_vfs().is_dir(path):
        return await get_faustbot_vfs().list_dir(path)
    else:
        return await get_faustbot_vfs().read(path)

async def status_get():
    all_tasks = asyncio.all_tasks()
    tasks=[]
    for task in all_tasks:
        tasks.append({
            "name": task.get_name(),
            "done": task.done(),
            "cancelled": task.cancelled(),
            "coro": str(task.get_coro()),
        })

    active_triggers = trigger_manager.get_trigger_information()
    return {"active_tasks": tasks, "active_triggers": active_triggers}

@router.get("/faust/debugging/snapshot")
async def snapshot():
    """调试接口：获取当前状态快照"""
    return {
        "subagents": state.SubagentManager.list_statuses_light(),
        "runtime_ready": state.RUNTIME_READY,
        "runtime_status": state.RUNTIME_STATUS,
        "runtime_error": state.RUNTIME_ERROR,
        "agent_name": state.AGENT_NAME,
        "agent_root": state.AGENT_ROOT,
        "prompt": state.PROMPT,
        "detailed_status": state.runtime_status_payload(),
        "plugin_states": state.plugin_manager.list_plugins(),
        "forward_queue_size": state.forward_queue.qsize(),
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "python_executable": f"{sys.executable}",
        "last_logs": faust_logger.get_recent_logs(50),
        "last_errors": faust_logger.get_recent_errors(20),
        "config_raw": Path(CONFIG_ROOT).read_text(encoding="utf-8",errors="ignore") if Path(CONFIG_ROOT).exists() else "",
        "private_config_missing": bool(state.conf.PRIVATE_CONFIG_WAS_MISSING),
        "private_config_auto_created": bool(state.conf.PRIVATE_CONFIG_AUTO_CREATED),
        "skills": list_skills(),
        "async_tasks": (await status_get())["active_tasks"],
        "active_triggers": (await status_get())["active_triggers"]
    }