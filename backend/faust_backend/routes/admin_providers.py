"""Provider 模型管理路由：provider CRUD、自动加载模型、主/Subagent 模型选择。

另有 models.dev 目录相关接口：目录状态/强制刷新、Provider 目录匹配（可 pin）。
这些接口只做「读目录 + 写 Provider 缓存字段」，不直接构建 LLM。
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import faust_backend.config_loader as conf
from faust_backend.runtime import state

router = APIRouter(tags=["admin-providers"])



class ProviderCreate(BaseModel):
    name: str
    base_url: str
    key: Optional[str] = None


class ModelSelectPayload(BaseModel):
    main_model: Optional[str] = None
    subagent_models: Optional[list[str]] = None


class CatalogMatchPayload(BaseModel):
    provider_id: Optional[str] = None


def _get_mp():
    return state.get_model_providers()


def _rebuild_after_change():
    from faust_backend.runtime.lifecycle import rebuild_runtime
    return rebuild_runtime(reset_dialog=False, no_initial_chat=True)


def _formats_payload(mp) -> dict[str, dict[str, dict]]:
    """每个 Provider 每个模型的格式决策（供前端 Models 表格展示）。"""
    from faust_backend.llm_formats import describe_all_formats

    return describe_all_formats(mp)


def _catalog_payload(mp) -> dict[str, dict]:
    """每个 Provider 的目录匹配概览。"""
    from faust_backend.models_dev import summarize_formats

    payload: dict[str, dict] = {}
    for provider in mp.providers:
        payload[provider.name] = {
            "provider_id": provider.catalog_provider_id,
            "host_matched": all(
                bool(route.get("host_matched"))
                for route in (provider.model_catalog_routes or {}).values()
            )
            if provider.model_catalog_routes
            else False,
            "synced_at": provider.catalog_synced_at,
            "routed": len(provider.model_catalog_routes or {}),
            "formats": summarize_formats(provider.model_catalog_routes or {}),
        }
    return payload


@router.get("/faust/admin/providers")
async def admin_list_providers():
    mp = _get_mp()
    return {
        "providers": [p.model_dump() for p in mp.providers],
        "main_model": mp.main_model,
        "subagent_models": mp.subagent_models or [],
        "resolved_formats": _formats_payload(mp),
        "catalog": _catalog_payload(mp),
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
    """自动调用 auto_load_model_for_provider 拉取模型列表（前端向导第 2 步）。

    一并返回该 provider 的 model_context_lengths / model_max_output_tokens /
    model_api_capabilities：auto_load 会用 /models 元数据自动填充，前端需要立刻
    反映这些值。随后用 models.dev 目录补全格式路由与缺失的上下文/输出长度
    （目录不可用时只用 /models 与通用启发式，不报错）。
    """
    import faust_backend.models_dev as models_dev
    from faust_backend.provider import resolve_provider, auto_load_model_for_provider

    mp = _get_mp()
    try:
        provider = resolve_provider(mp, name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        # 用户显式点击「自动加载模型」：清空该 provider 已有模型后重新拉取
        models = await auto_load_model_for_provider(provider, force=True)
    except Exception as exc:  # noqa: BLE001 - 网络/API 错误转 502
        raise HTTPException(status_code=502, detail=f"模型加载失败: {exc}")
    catalog_info: dict[str, Any] = {}
    try:
        catalog = await models_dev.ensure_catalog(allow_network=True)
        if catalog:
            catalog_info = models_dev.enrich_provider(provider, catalog)
        else:
            catalog_info = {"provider_id": None, "routed": 0, "missing": models}
    except Exception as exc:  # noqa: BLE001 - 目录失败不影响模型列表
        catalog_info = {"error": str(exc)}
    conf.save_model_providers()
    from faust_backend.llm_formats import describe_provider_formats

    return {
        "status": "ok",
        "provider": name,
        "models": models,
        "model_context_lengths": provider.model_context_lengths or {},
        "model_max_output_tokens": provider.model_max_output_tokens or {},
        "model_api_capabilities": provider.model_api_capabilities or {},
        "formats": describe_provider_formats(provider),
        "catalog": catalog_info,
    }


@router.get("/faust/admin/models-dev/status")
async def admin_models_dev_status():
    """models.dev 目录缓存状态（年龄、provider 数、最后错误）。"""
    import faust_backend.models_dev as models_dev

    return models_dev.catalog_status()


@router.post("/faust/admin/models-dev/refresh")
async def admin_models_dev_refresh():
    """强制刷新 models.dev 目录并应用到全部 Provider。"""
    from faust_backend.runtime.lifecycle import refresh_models_dev_catalog

    try:
        return await refresh_models_dev_catalog(force=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"刷新 models.dev 目录失败: {exc}")


@router.post("/faust/admin/providers/{name}/catalog-match")
async def admin_catalog_match(name: str, payload: CatalogMatchPayload | None = None):
    """重新把 Provider 匹配到 models.dev 目录（可选 pin 到某个 provider id）。

    `provider_id` 为空表示取消 pin 并自动重新匹配。
    """
    import faust_backend.models_dev as models_dev
    from faust_backend.provider import resolve_provider

    mp = _get_mp()
    try:
        provider = resolve_provider(mp, name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if payload is not None and payload.provider_id is not None:
        pinned = str(payload.provider_id).strip()
        provider.catalog_provider_id = pinned or None
    try:
        catalog = await models_dev.ensure_catalog(allow_network=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"models.dev 目录不可用: {exc}")
    if not catalog:
        raise HTTPException(
            status_code=503,
            detail="models.dev 目录尚未加载：请先刷新目录（网络不可用时只能手动设置格式）",
        )
    result = models_dev.enrich_provider(provider, catalog)
    conf.save_model_providers()
    from faust_backend.llm_formats import describe_provider_formats

    return {
        "status": "ok",
        "provider": name,
        "result": result,
        "formats": describe_provider_formats(provider),
    }



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
