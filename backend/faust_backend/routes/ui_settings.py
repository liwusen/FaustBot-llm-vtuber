from __future__ import annotations

from fastapi import APIRouter

from faust_backend.ui_settings import load_ui_settings, save_ui_settings


router = APIRouter(tags=["ui-settings"])


@router.get("/faust/ui-setting")
async def get_ui_setting():
    return {"status": "ok", "settings": load_ui_settings()}


@router.post("/faust/ui-setting")
async def post_ui_setting(payload: dict | None = None):
    settings = save_ui_settings(payload or {})
    return {"status": "ok", "settings": settings}