from fastapi import APIRouter
import faust_backend.service_manager as service_manager
from faust_backend.runtime import state

router = APIRouter(tags=["admin-services"])
router.description = "服务管理：列出/查看/启停/重启后端子进程服务"


@router.get("/faust/admin/services")
async def admin_list_services(include_log: bool = False):
    return {"status": "ok", "items": service_manager.list_services(include_log=include_log)}


@router.get("/faust/admin/services/{service_key}")
async def admin_get_service(service_key: str, include_log: bool = True):
    return {"status": "ok", "item": service_manager.service_status(service_key, include_log=include_log)}


@router.post("/faust/admin/services/{service_key}/start")
async def admin_start_service(service_key: str):
    item = service_manager.start_service(service_key)
    return {"status": "ok", "item": item, "callback": {"type": "service_action", "action": "start", "service_key": service_key}}


@router.post("/faust/admin/services/{service_key}/stop")
async def admin_stop_service(service_key: str):
    item = service_manager.stop_service(service_key)
    return {"status": "ok", "item": item, "callback": {"type": "service_action", "action": "stop", "service_key": service_key}}


@router.post("/faust/admin/services/{service_key}/restart")
async def admin_restart_service(service_key: str):
    item = service_manager.restart_service(service_key)
    return {"status": "ok", "item": item, "callback": {"type": "service_action", "action": "restart", "service_key": service_key}}
