"""
桥接模块: 事件系统委托给 events/ 包中的 EventBus。
原有消费者无需修改。
"""
import asyncio
from faust_backend.events import get_bus

_bus = get_bus()

# 重新导出 EventBus 上的 Event 对象
backend2frontendQueue_event: asyncio.Event = _bus.backend2frontendQueue_event
HIL_feedback_event: asyncio.Event = _bus.HIL_feedback_event
HIL_feedback_fail_event: asyncio.Event = _bus.HIL_feedback_fail_event
ignore_trigger_event: asyncio.Event = _bus.ignore_trigger_event
feedback_event_pool: dict = _bus.feedback_event_pool
hil_request_pool: dict = _bus.hil_request_pool


def create_hil_request(request_id: str) -> asyncio.Future:
    return _bus.create_hil_request(request_id)


def resolve_hil_request(request_id: str, payload) -> bool:
    return _bus.resolve_hil_request(request_id, payload)


def cancel_hil_request(request_id: str, reason: str = "cancelled") -> bool:
    return _bus.cancel_hil_request(request_id, reason)


def create_feedback_event(feedback_id: str) -> asyncio.Event:
    return _bus.create_feedback_event(feedback_id)
