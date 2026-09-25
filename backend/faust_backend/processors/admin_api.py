"""Processor 管理接口（由 main.py 注册，风格对齐 routes/admin_services.py）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger

from .errors import ProcessorNotFoundError
from .manager import get_processor_manager

log = get_logger("faust.processor.admin")

router = APIRouter(tags=["processors"])
router.description = "重计算子进程（Processor）：列出/查看/强制停止/空闲回收"


@router.get("/faust/admin/processors")
async def processors_list(include_log: bool = False):
    """全部 Processor 状态快照。"""
    manager = get_processor_manager()
    items = manager.status()
    if include_log:
        for item in items:
            item["logs"] = await manager.handle(item["name"]).get_log(limit=50)
    return {"status": "ok", "items": items}


@router.get("/faust/admin/processors/{name}")
async def processors_get(name: str, include_log: bool = True):
    """单个 Processor 状态 + 日志尾巴。"""
    try:
        handle = get_processor_manager().handle(name)
    except ProcessorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    item = handle.status()
    if include_log:
        item["logs"] = await handle.get_log(limit=50)
    return {"status": "ok", "item": item}


@router.post("/faust/admin/processors/{name}/stop")
async def processors_stop(name: str):
    """强制停止（忽略引用计数，manager 会记日志说明原因）。"""
    try:
        await get_processor_manager().stop(name)
    except ProcessorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", "name": name}


@router.post("/faust/admin/processors/prune")
async def processors_prune(
    timeout: float | None = Query(default=None, description="空闲秒数阈值，缺省用 PROCESSOR_IDLE_TIMEOUT"),
    whitelist: str | None = Query(default=None, description="逗号分隔的 Processor 名字；缺省处理全部"),
):
    """回收空闲 Processor；返回每项的处理结果。"""
    manager = get_processor_manager()
    names = [item.strip() for item in whitelist.split(",") if item.strip()] if whitelist else None
    idle_timeout = float(timeout) if timeout is not None else float(conf.PROCESSOR_IDLE_TIMEOUT or 0)
    try:
        report = await manager.prune(timeout=idle_timeout, whitelist=names)
    except ProcessorNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok", **report.as_dict()}
