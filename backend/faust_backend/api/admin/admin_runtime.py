from fastapi import APIRouter
import faust_backend.services.admin as admin_runtime
from faust_backend.agent.runtime import state
from faust_backend.agent.runtime.rebuild import rebuild_runtime

router = APIRouter(tags=["admin-runtime"])



@router.get("/faust/admin/runtime")
async def admin_runtime_summary_api():
    return {"status": "ok", "runtime": {**admin_runtime.runtime_summary(), **state.runtime_status_payload()}}


@router.post("/faust/admin/runtime/reload-agent")
async def admin_reload_agent():
    info = await rebuild_runtime(reset_dialog=False, no_initial_chat=True)
    return {
        "status": "ok",
        "runtime": info,
        "callback": {
            "type": "runtime_reloaded",
            "scope": "agent",
            "agent_name": info.get("agent_name"),
            "reset_dialog": False,
            "no_initial_chat": True,
        }
    }


@router.post("/faust/admin/runtime/reload-all")
async def admin_reload_all():
    info = await rebuild_runtime(reset_dialog=True, no_initial_chat=False)
    return {
        "status": "ok",
        "runtime": info,
        "callback": {
            "type": "runtime_reloaded",
            "scope": "all",
            "agent_name": info.get("agent_name"),
            "reset_dialog": True,
            "no_initial_chat": False,
        }
    }
