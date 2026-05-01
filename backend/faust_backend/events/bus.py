import asyncio


class EventBus:
    def __init__(self):
        self.backend2frontendQueue_event = asyncio.Event()
        self.HIL_feedback_event = asyncio.Event()
        self.HIL_feedback_fail_event = asyncio.Event()
        self.ignore_trigger_event = asyncio.Event()

        self.feedback_event_pool: dict[str, asyncio.Event] = {}
        self.hil_request_pool: dict[str, asyncio.Future] = {}

    def create_hil_request(self, request_id: str) -> asyncio.Future:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.hil_request_pool[request_id] = future
        return future

    def resolve_hil_request(self, request_id: str, payload) -> bool:
        future = self.hil_request_pool.pop(request_id, None)
        if future and not future.done():
            future.set_result(payload)
            return True
        return False

    def cancel_hil_request(self, request_id: str, reason: str = "cancelled") -> bool:
        future = self.hil_request_pool.pop(request_id, None)
        if future and not future.done():
            future.set_result({"approved": False, "reason": reason, "request_id": request_id})
            return True
        return False

    def create_feedback_event(self, feedback_id: str) -> asyncio.Event:
        event = asyncio.Event()
        self.feedback_event_pool[feedback_id] = event
        return event
