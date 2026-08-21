from fastapi import APIRouter
import faust_backend.backend2front as backend2frontend
import faust_backend.events as events
import faust_backend.nimble as nimble
import faust_backend.trigger_manager as trigger_manager
from faust_backend.logger import get_logger

log = get_logger("faust.hil_nimble")

router = APIRouter(tags=["hil-nimble"])



@router.post("/faust/humanInLoop/feedback")
async def human_in_loop_feedback_post(payload: dict):
    feedback = None
    request_id = None
    reason = None
    log.debug("HIL feedback payload: %s", payload)
    if isinstance(payload, dict):
        feedback = payload.get('feedback')
        request_id = payload.get('request_id') or payload.get('id')
        reason = payload.get('reason')
    if feedback is None:
        return {"error": "no feedback provided"}
    approved = bool(feedback)
    resolved = False
    if request_id:
        resolved = events.resolve_hil_request(str(request_id), {
            "approved": approved,
            "reason": reason or ("approved" if approved else "rejected"),
            "request_id": str(request_id),
        })
        backend2frontend.FrontEndCloseNimbleWindow({"callback_id": str(request_id), "reason": "approved" if approved else "rejected"})
    else:
        if approved:
            events.HIL_feedback_event.set()
        else:
            events.HIL_feedback_fail_event.set()
        resolved = True
    return {"status": "feedback received", "request_id": request_id, "resolved": resolved}


@router.post("/faust/nimble/message")
async def nimble_message_post(payload: dict):
    callback_id = None
    create_event_trigger = False
    message = None
    if isinstance(payload, dict):
        callback_id = payload.get("callback_id")
        create_event_trigger = bool(payload.get("create_event_trigger"))
        message = payload.get("payload")
    if not callback_id:
        return {"error": "no callback_id provided"}
    session = nimble.record_frontend_message(callback_id, message)
    if not session:
        return {"error": f"unknown callback_id: {callback_id}"}

    if create_event_trigger:
        trigger_manager.append_trigger({
            "id": nimble.message_trigger_id(callback_id),
            "type": "event",
            "event_name": "nimble_message",
            "callback_id": callback_id,
            "payload": {"payload": message},
            "recall_description": f"灵动窗口 {callback_id} 收到前端消息，完整对话见 faustbot://nimble/{callback_id}/console",
            "lifespan": 7200,
        })
    return {"status": "ok", "callback_id": callback_id}


@router.post("/faust/nimble/close")
async def nimble_close_post(payload: dict):
    callback_id = None
    reason = "closed_by_user"
    if isinstance(payload, dict):
        callback_id = payload.get("callback_id")
        reason = payload.get("reason") or reason
    if not callback_id:
        return {"error": "no callback_id provided"}
    session = await nimble.finalize_close(callback_id, reason=reason)
    if not session:
        return {"error": f"unknown callback_id: {callback_id}"}

    trigger_manager.append_trigger({
        "id": f"nimble_closed::{callback_id}",
        "type": "event",
        "event_name": "nimble_message",
        "callback_id": callback_id,
        "payload": {"payload": {"type": "window-closed", "reason": reason}},
        "recall_description": f"灵动窗口 {callback_id} 已被关闭（{reason}）。",
        "lifespan": 7200,
    })
    return {"status": "closed", "callback_id": callback_id}
