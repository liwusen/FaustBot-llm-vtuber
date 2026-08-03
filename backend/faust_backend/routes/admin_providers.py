"""Provider 模型管理路由：provider CRUD、自动加载模型、主/Subagent 模型选择。"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import faust_backend.config_loader as conf
from faust_backend.runtime import state

router = APIRouter(tags=["admin-providers"])
router.description = "AI Provider 管理：provider CRUD、模型自动加载、主/Subagent 模型选择"


class ProviderCreate(BaseModel):
    name: str
    base_url: str
    key: Optional[str] = None


class ModelSelectPayload(BaseModel):
    main_model: Optional[str] = None
    subagent_models: Optional[list[str]] = None


def _get_mp():
    return state.get_model_providers()


def _rebuild_after_change():
    from faust_backend.runtime.lifecycle import rebuild_runtime
    return rebuild_runtime(reset_dialog=False, no_initial_chat=True)


@router.get("/faust/admin/providers")
async def admin_list_providers():
    mp = _get_mp()
    return {
        "providers": [p.model_dump() for p in mp.providers],
        "main_model": mp.main_model,
        "subagent_models": mp.subagent_models or [],
    }


@router.post("/faust/admin/providers")
async def admin_add_provider(payload: ProviderCreate):
    from faust_backend.provider import new_provider
    mp = _get_mp()
    name = str(payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name 不能为空")
    if any(p.name == name for p in mp.providers):
        raise HTTPException(status_code=400, detail=f"provider '{name}' 已存在")
    new_provider(mp, name, str(payload.base_url or "").strip(), payload.key)
    conf.save_model_providers()
    return {"status": "ok", "provider": next(p for p in mp.providers if p.name == name).model_dump()}


@router.delete("/faust/admin/providers/{name}")
async def admin_remove_provider(name: str):
    from faust_backend.provider import remove_provider
    mp = _get_mp()
    if not remove_provider(mp, name):
        raise HTTPException(status_code=404, detail=f"provider '{name}' 未找到")
    conf.save_model_providers()
    return {"status": "ok", "removed": name}


@router.post("/faust/admin/providers/{name}/load-models")
async def admin_auto_load_models(name: str):
    """自动调用 auto_load_model_for_provider 拉取模型列表（前端向导第 2 步）。"""
    from faust_backend.provider import resolve_provider, auto_load_model_for_provider
    mp = _get_mp()
    try:
        provider = resolve_provider(mp, name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        models = await auto_load_model_for_provider(provider)
    except Exception as exc:  # noqa: BLE001 - 网络/API 错误转 502
        raise HTTPException(status_code=502, detail=f"模型加载失败: {exc}")
    conf.save_model_providers()
    return {"status": "ok", "provider": name, "models": models}


@router.post("/faust/admin/model/select")
async def admin_select_model(payload: ModelSelectPayload):
    """设置主/Subagent 模型并触发运行时重建（双通道之一）。"""
    mp = _get_mp()
    if payload.main_model is not None:
        mp.main_model = payload.main_model
    if payload.subagent_models is not None:
        mp.subagent_models = list(payload.subagent_models)
    conf.save_model_providers()
    try:
        info = await _rebuild_after_change()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"运行时重建失败: {exc}")
    return {"status": "ok", "runtime": info, "main_model": mp.main_model, "subagent_models": mp.subagent_models or []}
