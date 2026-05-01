from fastapi import APIRouter
import faust_backend.backend2front as backend2frontend
import faust_backend.events as events
import faust_backend.nimble as nimble
import faust_backend.trigger_manager as trigger_manager
from faust_backend.logger import get_logger

log = get_logger("faust.hil_nimble")

router = APIRouter(tags=["hil-nimble"])
router.description = "HIL / Nimble 交互：人工审批反馈、Nimble 窗口提交回调和关闭"


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


@router.post("/faust/nimble/callback")
async def nimble_callback_post(payload: dict):
    callback_id = None
    data = None
    should_close = False
    if isinstance(payload, dict):
        callback_id = payload.get("callback_id")
        data = payload.get("data")
        should_close = bool(payload.get("close"))
    if not callback_id:
        return {"error": "no callback_id provided"}
    session = nimble.set_nimble_result(callback_id, data, closed=should_close)
    if not session:
        return {"error": f"unknown callback_id: {callback_id}"}
    if should_close:
        trigger_manager.delete_trigger(session["reminder_trigger_id"])
        trigger_manager.delete_trigger(session["expire_trigger_id"])
        backend2frontend.FrontEndCloseNimbleWindow({"callback_id": callback_id, "reason": "submitted"})
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
    session = nimble.close_nimble_session(callback_id, reason=reason)
    if not session:
        return {"error": f"unknown callback_id: {callback_id}"}
    trigger_manager.delete_trigger(session["result_trigger_id"])
    trigger_manager.delete_trigger(session["reminder_trigger_id"])
    trigger_manager.delete_trigger(session["expire_trigger_id"])
    backend2frontend.FrontEndCloseNimbleWindow({"callback_id": callback_id, "reason": reason})
    nimble.cleanup_nimble_session(callback_id)
    return {"status": "closed", "callback_id": callback_id}
