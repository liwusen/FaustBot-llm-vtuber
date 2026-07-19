from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from faust_backend.runtime import state

router = APIRouter(tags=["subagents"])
router.description = "Subagent 状态与控制：查看状态、停止与移除 Subagent"

# Debug override: set by POST /faust/debugging/subagent-override
subagent_status_overrides: list[dict[str, Any]] = []


@router.get("/faust/subagents-status")
async def subagents_status_api():
    if subagent_status_overrides:
        return {"status": "ok", "items": list(subagent_status_overrides)}
    manager = state.subagent_manager
    if manager is None:
        return {"status": "ok", "items": []}
    return {"status": "ok", "items": manager.list_statuses()}


@router.delete("/faust/subagents/{name}")
async def delete_subagent_api(name: str):
    manager = state.subagent_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="subagent manager not ready")
    removed = await manager.removeSubagent(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"subagent not found: {name}")
    return {"status": "ok", "name": name, "removed": True}
