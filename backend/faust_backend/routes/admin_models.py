from fastapi import APIRouter, HTTPException
import faust_backend.admin_runtime as admin_runtime
import faust_backend.vrm_config_manager as vrm_config_manager
import faust_backend.vrm_pose_manager as vrm_pose_manager
from faust_backend.runtime import state

router = APIRouter(tags=["admin-models"])



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


@router.get("/faust/admin/vrm-poses")
async def admin_get_vrm_poses():
    return {"status": "ok", "poses": vrm_pose_manager.get_vrm_poses()}


@router.post("/faust/admin/vrm-poses")
async def admin_save_vrm_pose(payload: dict | None = None):
    if not payload or "name" not in payload:
        raise HTTPException(status_code=400, detail="missing name field")
    err = vrm_pose_manager.validate_pose_name(payload["name"])
    if err:
        raise HTTPException(status_code=400, detail=err)
    entry = vrm_pose_manager.save_vrm_pose(payload["name"], payload.get("pose") or {})
    return {"status": "ok", "pose": entry}


@router.delete("/faust/admin/vrm-poses/{name}")
async def admin_delete_vrm_pose(name: str):
    if not vrm_pose_manager.delete_vrm_pose(name):
        raise HTTPException(status_code=404, detail="preset not found")
    return {"status": "ok"}
