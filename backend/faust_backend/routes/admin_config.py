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
    result = admin_runtime.save_config(payload or {})
    pm = getattr(state, 'plugin_manager', None)
    if pm:
        pm._call_pluggy_hook('config_changed', key='all', old=None, new=payload, ctx=None)
    return result


@router.post("/faust/admin/config/reload")
async def admin_reload_config(payload: dict | None = None):
    # 捕获旧配置用于服务变更检测
    from faust_backend.config_loader import config as old_config
    old_config_copy = dict(old_config)

    info = await rebuild_runtime(
        reset_dialog=bool((payload or {}).get("reset_dialog", False)),
        no_initial_chat=bool((payload or {}).get("no_initial_chat", True)),
    )

    # 配置变更后检查服务启停
    from faust_backend.config_loader import config as new_config
    from faust_backend.component_manager import check_and_manage_services
    await check_and_manage_services(old_config_copy, dict(new_config))
    pm = getattr(state, 'plugin_manager', None)
    if pm:
        pm._call_pluggy_hook('config_changed', key='all', old=old_config_copy, new=dict(new_config), ctx=None)

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
