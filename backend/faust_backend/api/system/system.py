import asyncio
from fastapi import APIRouter
from fastapi.responses import RedirectResponse
import faust_backend.agent.trigger as trigger_manager
from faust_backend.agent.runtime import state
from faust_backend.app.lifespan import _graceful_shutdown_task

router = APIRouter(tags=["system"])



@router.get("/faust/status")
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
    return {"status": "ok", "active_tasks": tasks, "active_triggers": active_triggers}

@router.post("/faust/shutdown")
async def shutdown_post():
    asyncio.create_task(_graceful_shutdown_task())
    return {"status": "shutting_down"}

@router.get("/")
async def about_page():
    return RedirectResponse(url="/docs")