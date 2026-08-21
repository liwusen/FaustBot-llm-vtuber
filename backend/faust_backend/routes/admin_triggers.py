import asyncio
from fastapi import APIRouter, HTTPException
import faust_backend.trigger_manager as trigger_manager
from faust_backend.runtime import state

router = APIRouter(tags=["admin-triggers"])



@router.get("/faust/admin/triggers")
async def admin_list_triggers():
    items = await asyncio.to_thread(trigger_manager.list_triggers)
    return {"status": "ok", "items": items}


@router.get("/faust/admin/triggers/{trigger_id}")
async def admin_get_trigger(trigger_id: str):
    item = await asyncio.to_thread(trigger_manager.get_trigger, trigger_id)
    if not item:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")
    return {"status": "ok", "item": item}


@router.post("/faust/admin/triggers")
async def admin_create_or_upsert_trigger(payload: dict | None = None):
    body = payload or {}
    try:
        await asyncio.to_thread(trigger_manager.append_trigger, body)
        tid = str(body.get("id") or "")
        item = await asyncio.to_thread(trigger_manager.get_trigger, tid) if tid else body
        return {"status": "ok", "item": item}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Trigger 保存失败: {e}")


@router.put("/faust/admin/triggers/{trigger_id}")
async def admin_update_trigger(trigger_id: str, payload: dict | None = None):
    body = payload or {}
    try:
        await asyncio.to_thread(trigger_manager.update_trigger, trigger_id, body)
        item = await asyncio.to_thread(trigger_manager.get_trigger, trigger_id)
        return {"status": "ok", "item": item}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Trigger 更新失败: {e}")


@router.delete("/faust/admin/triggers/{trigger_id}")
async def admin_delete_trigger(trigger_id: str):
    existed = await asyncio.to_thread(trigger_manager.get_trigger, trigger_id)
    existed = existed is not None
    await asyncio.to_thread(trigger_manager.delete_trigger, trigger_id)
    if not existed:
        raise HTTPException(status_code=404, detail=f"Trigger not found: {trigger_id}")
    return {"status": "ok", "deleted": trigger_id}
