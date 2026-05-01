"""
事件系统: 提供 EventBus 单例及向后兼容的模块级符号。
"""
import asyncio
from faust_backend.events.bus import EventBus

_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


# ── 向后兼容的模块级符号 ──
_b = get_bus()

backend2frontendQueue_event: asyncio.Event = _b.backend2frontendQueue_event
HIL_feedback_event: asyncio.Event = _b.HIL_feedback_event
HIL_feedback_fail_event: asyncio.Event = _b.HIL_feedback_fail_event
ignore_trigger_event: asyncio.Event = _b.ignore_trigger_event
feedback_event_pool: dict = _b.feedback_event_pool
hil_request_pool: dict = _b.hil_request_pool


def create_hil_request(request_id: str) -> asyncio.Future:
    return _b.create_hil_request(request_id)


def resolve_hil_request(request_id: str, payload) -> bool:
    return _b.resolve_hil_request(request_id, payload)


def cancel_hil_request(request_id: str, reason: str = "cancelled") -> bool:
    return _b.cancel_hil_request(request_id, reason)


def create_feedback_event(feedback_id: str) -> asyncio.Event:
    return _b.create_feedback_event(feedback_id)
