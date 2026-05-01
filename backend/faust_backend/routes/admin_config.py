from fastapi import APIRouter
import faust_backend.admin_runtime as admin_runtime
from faust_backend.runtime import state
from faust_backend.runtime.lifecycle import rebuild_runtime

router = APIRouter(tags=["admin-config"])
router.description = "Config 管理：获取/保存/重载 FaustBot 配置（含 public + private 配置视图）"


@router.get("/faust/admin/config")
async def admin_get_config():
    return admin_runtime.get_config_view()


@router.post("/faust/admin/config")
async def admin_save_config(payload: dict):
    return admin_runtime.save_config(payload or {})


@router.post("/faust/admin/config/reload")
async def admin_reload_config(payload: dict | None = None):
    info = await rebuild_runtime(
        reset_dialog=bool((payload or {}).get("reset_dialog", False)),
        no_initial_chat=bool((payload or {}).get("no_initial_chat", True)),
    )
    return {
        "status": "ok",
        "runtime": info,
        "summary": admin_runtime.runtime_summary(),
        "callback": {
            "type": "runtime_reloaded",
            "scope": "config",
            "agent_name": info.get("agent_name"),
            "reset_dialog": bool((payload or {}).get("reset_dialog", False)),
            "no_initial_chat": bool((payload or {}).get("no_initial_chat", True)),
        }
    }
