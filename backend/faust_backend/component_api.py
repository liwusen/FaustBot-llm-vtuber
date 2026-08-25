"""组件管理 API 路由。

Phase C: 组件状态查询、下载/安装接口、SSE 进度流
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from faust_backend.component_manager import (
    detect_components,
    detect_gpu,
    get_mc_bridge_enabled,
    on_component_installed,
)
import faust_backend.service_manager as service_manager
from faust_backend.logger import get_logger

log = get_logger("faust.component.api")

router = APIRouter(tags=["components"])



class ComponentTaskCancelled(Exception):
    pass


# ── 任务状态模型 ──

@dataclass
class ComponentTask:
    task_id: str
    component: str
    status: str = "pending"          # pending | running | complete | error | cancelled
    progress_percent: float = 0.0    # 0-100，仅 TTS 下载有精确值
    stage: str = ""                  # 当前阶段名
    log_lines: list[str] = field(default_factory=list)  # 最近 200 行
    error: str | None = None
    started_at: float = 0.0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "component": self.component,
            "status": self.status,
            "progress_percent": self.progress_percent,
            "stage": self.stage,
            "log_lines": self.log_lines[-200:],
            "error": self.error,
        }


# 全局任务存储
_tasks: dict[str, ComponentTask] = {}


def _get_task(task_id: str) -> ComponentTask | None:
    return _tasks.get(task_id)


def _cleanup_task(task_id: str) -> None:
    _tasks.pop(task_id, None)


# ── C1: 组件状态查询 ──

class ComponentStatusResponse(BaseModel):
    gpu: dict[str, Any]
    components: dict[str, Any]
    services: dict[str, Any]



@router.get("/faust/components/status")
async def get_component_status() -> ComponentStatusResponse:
    """查询 GPU、组件安装状态和服务运行状态。"""
    gpu = detect_gpu()
    components = detect_components()

    # 填充 minecraft_bridge.enabled
    components["minecraft_bridge"]["enabled"] = get_mc_bridge_enabled()

    services = {
        "asr": service_manager.service_status("asr"),
        "tts": service_manager.service_status("tts"),
        "minecraft": service_manager.service_status("mc_operator"),
    }

    return ComponentStatusResponse(gpu=gpu, components=components, services=services)


# ── C2: 下载/安装接口 ──

class InstallRequest(BaseModel):
    component: str  # "funasr" | "tts"
    torch_variant: str | None = None      # funasr 专用
    use_aliyun_mirror: bool = False       # funasr 专用
    tts_variant: str | None = None        # tts 专用


class InstallResponse(BaseModel):
    task_id: str
    status: str


@router.post("/faust/components/install")
async def start_install(req: InstallRequest) -> InstallResponse:
    """启动组件下载/安装。返回 task_id，客户端可通过 SSE 获取进度。"""
    task_id = str(uuid.uuid4())
    task = ComponentTask(
        task_id=task_id,
        component=req.component,
        status="pending",
        started_at=__import__("time").time(),
    )
    _tasks[task_id] = task

    # 创建后台任务
    asyncio.create_task(_run_install(task, req))

    return InstallResponse(task_id=task_id, status="started")


@router.post("/faust/components/tasks/{task_id}/cancel")
async def cancel_install(task_id: str) -> dict[str, Any]:
    task = _get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status in {"complete", "error", "cancelled"}:
        return {"status": task.status, "task_id": task_id}
    task.cancel_event.set()
    task.log_lines.append("[取消] 已请求终止任务")
    task.stage = "cancel_requested"
    return {"status": "cancelling", "task_id": task_id}


async def _run_install(task: ComponentTask, req: InstallRequest) -> None:
    """后台执行安装任务，更新 task 状态并回调 on_component_installed。"""
    try:
        task.status = "running"
        task.stage = "preparing"

        def _check_cancel() -> None:
            if task.cancel_event.is_set():
                raise ComponentTaskCancelled("安装已取消")

        if req.component == "funasr":
            await _install_funasr(task, req, _check_cancel)
        elif req.component == "tts":
            await _install_tts(task, req, _check_cancel)
        else:
            task.error = f"未知组件: {req.component}"
            task.status = "error"
            return

        task.status = "complete"
        task.stage = "complete"
        task.progress_percent = 100.0
        task.log_lines.append(f"[完成] {req.component} 安装完成")

        # 触发自动启动
        await on_component_installed(req.component)
    except ComponentTaskCancelled as e:
        task.status = "cancelled"
        task.stage = "cancelled"
        task.error = None
        task.log_lines.append(f"[取消] {e}")
    except Exception as e:
        task.status = "error"
        task.error = str(e)
        task.log_lines.append(f"[错误] {e}")
        log.exception("组件 %s 安装失败", req.component)


async def _install_funasr(task: ComponentTask, req: InstallRequest, cancel_check) -> None:
    """安装 PyTorch + funasr。"""
    import download_torch
    log.info(f"Installing funasr+torch with args:{req.torch_variant},{req.use_aliyun_mirror}")
    def _progress(stage: str, percent: float | None, message: str) -> None:
        cancel_check()
        task.stage = stage
        if percent is not None:
            task.progress_percent = percent
        if message:
            task.log_lines.append(message)

    result = await asyncio.to_thread(
        download_torch.install_torch_and_funasr,
        req.torch_variant or "cpu",
        req.use_aliyun_mirror,
        _progress,
        cancel_check,
    )

    if not result.get("success"):
        raise RuntimeError(result.get("error", "安装失败"))


async def _install_tts(task: ComponentTask, req: InstallRequest, cancel_check) -> None:
    """下载并解压 TTS 包。"""
    import download_tts

    def _progress(stage: str, percent: float | None, message: str) -> None:
        cancel_check()
        task.stage = stage
        if percent is not None:
            task.progress_percent = percent
        if message:
            task.log_lines.append(message)

    await download_tts.download_tts_async(req.tts_variant or "standard", _progress, cancel_check)


# ── C3: SSE 进度流 ──

@router.get("/faust/components/tasks/{task_id}/events")
async def component_task_events(task_id: str):
    """SSE 进度事件流。"""
    task = _get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_stream():
        try:
            while True:
                if task.error:
                    yield f"event: error\ndata: {json.dumps({'error': task.error})}\n\n"
                    return
                if task.status == "cancelled":
                    yield f"event: cancelled\ndata: {json.dumps(task.to_dict())}\n\n"
                    return
                if task.status == "complete":
                    yield f"event: complete\ndata: {json.dumps(task.to_dict())}\n\n"
                    return
                yield f"event: progress\ndata: {json.dumps(task.to_dict())}\n\n"
                await asyncio.sleep(0.5)
        except GeneratorExit:
            log.info("SSE 客户端断开连接: %s", task_id)
        except ConnectionResetError:
            log.warning("SSE 连接重置: %s", task_id)
        except Exception:
            log.exception("SSE 流错误: %s", task_id)
        finally:
            _cleanup_task(task_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
    )