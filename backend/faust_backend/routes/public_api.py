import asyncio
import uuid
from typing import Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import faust_backend.trigger_manager as tm

router = APIRouter(tags=["public-api"])



class EventTriggerPushRequest(BaseModel):
    """事件触发器推送请求体。

    event_name 必填；payload 为附加数据（会透传进 Agent 的 trigger 上下文）；
    callback_id 可选，用于关联灵动窗口等会话。
    """
    event_name: str = Field(..., description="事件名，例如 nimble_message / blive_danmaku / mc_event")
    payload: Optional[dict[str, Any]] = Field(default=None, description="事件负载，透传给 Agent")
    callback_id: Optional[str] = Field(default=None, description="可选回调 ID（如灵动窗口 callback_id）")
    run_background: bool = Field(default=False, description="是否后台静默执行（不推送到前端）")


class GenericTriggerPushRequest(BaseModel):
    """通用触发器提交请求体（对齐 trigger_manager.append_trigger 的 dict 格式）。"""
    trigger: dict[str, Any] = Field(..., description="trigger dict，需含 type/id；支持 datetime/interval/py-eval/event/nimble-expire")


@router.post("/public-api/event-trigger-push")
async def event_trigger_push(req: EventTriggerPushRequest):
    """外部系统向 FaustBot 推送一个事件触发器。

    例如第三方服务检测到某事件后调用本接口，FaustBot 的 Agent 会被唤醒并处理。
    成功返回 trigger id；失败返回 400。
    """
    if not req.event_name.strip():
        raise HTTPException(status_code=400, detail="event_name 不能为空")
    trigger = {
        "id": f"public_{uuid.uuid4().hex[:12]}",
        "type": "event",
        "event_name": req.event_name,
        "payload": req.payload or {},
        "run_background": req.run_background,
    }
    if req.callback_id:
        trigger["callback_id"] = req.callback_id
    try:
        await asyncio.to_thread(tm.append_trigger, trigger)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Trigger 无效: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Trigger 推送失败: {e}")
    return {"status": "success", "type": "event", "event_name": req.event_name}


@router.post("/public-api/trigger")
async def generic_trigger_push(req: GenericTriggerPushRequest):
    """通用触发器提交：透传完整 trigger dict 给 trigger_manager.append_trigger。"""
    body = req.trigger
    if not isinstance(body, dict) or not body.get("type"):
        raise HTTPException(status_code=400, detail="trigger 必须是含 type 字段的 dict")
    try:
        await asyncio.to_thread(tm.append_trigger, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Trigger 无效: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Trigger 提交失败: {e}")
    return {"status": "success", "trigger": body}


@router.get("/public-api/health")
async def public_health():
    """公开健康检查：确认服务存活（不暴露内部状态）。"""
    return {"status": "ok", "service": "faustbot"}
