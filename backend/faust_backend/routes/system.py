import asyncio
from fastapi import APIRouter
import faust_backend.trigger_manager as trigger_manager
from faust_backend.runtime import state
from faust_backend.runtime.lifecycle import _graceful_shutdown_task

router = APIRouter(tags=["system"])
router.description = "系统：健康检查 & 活跃 Trigger 状态、优雅关闭"


@router.post("/faust/status")
async def status_post():
    active_tasks = trigger_manager.get_trigger_information()
    return {"status": "ok", "active_tasks": active_tasks}


@router.post("/faust/shutdown")
async def shutdown_post():
    asyncio.create_task(_graceful_shutdown_task())
    return {"status": "shutting_down"}
