from fastapi import APIRouter, HTTPException
import faust_backend.admin_runtime as admin_runtime
import faust_backend.vrm_config_manager as vrm_config_manager
from faust_backend.runtime import state

router = APIRouter(tags=["admin-models"])
router.description = "Live2D / VRM 模型管理：应用 Live2D 设定、VRM 配置 CRUD、模型列表"


@router.post("/faust/admin/live2d/apply")
async def admin_apply_live2d(payload: dict | None = None):
    return admin_runtime.apply_live2d_to_frontend(payload or {})


@router.get("/faust/admin/live2d/models")
async def admin_list_live2d_models():
    return {"items": admin_runtime.list_available_models()}


@router.get("/faust/admin/vrm-config")
async def admin_get_vrm_config():
    return {"status": "ok", "config": vrm_config_manager.get_vrm_config()}


@router.post("/faust/admin/vrm-config")
async def admin_save_vrm_config(payload: dict | None = None):
    if not payload or "config" not in payload:
        raise HTTPException(status_code=400, detail="missing config field")
    merged = vrm_config_manager.save_vrm_config(payload["config"])
    return {"status": "ok", "config": merged}


@router.post("/faust/admin/vrm-config/model-state")
async def admin_save_vrm_model_state(payload: dict | None = None):
    if not payload:
        raise HTTPException(status_code=400, detail="missing payload")
    state_data = vrm_config_manager.save_vrm_model_state(payload)
    return {"status": "ok", "modelState": state_data}


@router.get("/faust/admin/vrm-config/reset")
async def admin_reset_vrm_config():
    defaults = vrm_config_manager.reset_vrm_config()
    return {"status": "ok", "config": defaults}
