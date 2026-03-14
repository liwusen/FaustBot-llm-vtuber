"""
这个文件负责实现“灵动交互”系统的核心逻辑。

设计目标：
1. Agent 非阻塞地创建一个灵动窗口；
2. 窗口生命周期与对应 trigger 绑定；
3. 窗口显示期间自动创建一个定时提醒 trigger，提醒 Agent 主动关注该窗口；
4. 用户提交 / 关闭窗口时，相关 trigger 一并清理；
5. trigger_manager 和 backend-main 只通过 callback_id / trigger_id 取回上下文。
"""
import time
import uuid
from typing import Dict, Any, Optional

_nimble_sessions: Dict[str, Dict[str, Any]] = {}


def _now() -> float:
    return time.time()


def build_callback_id() -> str:
    return f"nimble_{uuid.uuid4().hex}"


def create_nimble_session(
    callback_id: str,
    *,
    title: str,
    html: str,
    recall_text: str = "用户仍在处理灵动交互窗口，请检查是否需要继续引导用户。",
    reminder_interval_seconds: int = 20,
    lifespan: int = 1800,
    metadata: Optional[dict] = None,
) -> Dict[str, Any]:
    """创建或覆盖一个 nimble 会话。

    返回 session dict，包含窗口生命周期和与之绑定的 trigger id。
    """
    session = {
        "callback_id": callback_id,
        "title": title,
        "html": html,
        "metadata": metadata or {},
        "created_at": _now(),
        "updated_at": _now(),
        "lifespan": int(max(1, lifespan)),
        "expires_at": _now() + int(max(1, lifespan)),
        "closed": False,
        "result": None,
        "status": "open",
        "recall_text": recall_text,
        "reminder_interval_seconds": int(max(3, reminder_interval_seconds)),
        "result_trigger_id": f"nimble_result::{callback_id}",
        "reminder_trigger_id": f"nimble_reminder::{callback_id}",
        "expire_trigger_id": f"nimble_expire::{callback_id}",
    }
    _nimble_sessions[callback_id] = session
    print(f"[faust.backend.nimble] Session created: {callback_id}")
    return session


def get_nimble_session(callback_id: str) -> Optional[Dict[str, Any]]:
    return _nimble_sessions.get(callback_id)


def touch_nimble_session(callback_id: str) -> Optional[Dict[str, Any]]:
    session = _nimble_sessions.get(callback_id)
    if not session:
        return None
    session["updated_at"] = _now()
    session["expires_at"] = _now() + int(session.get("lifespan", 1800))
    return session


def set_nimble_result(callback_id: str, data: Any, *, closed: bool = False) -> Optional[Dict[str, Any]]:
    session = _nimble_sessions.get(callback_id)
    if not session:
        print(f"[faust.backend.nimble] Warning: Received result for unknown callback_id: {callback_id}")
        return None
    session["result"] = data
    session["updated_at"] = _now()
    session["status"] = "submitted"
    if closed:
        session["closed"] = True
        session["status"] = "closed"
    print(f"[faust.backend.nimble] Result stored for: {callback_id}")
    return session


def close_nimble_session(callback_id: str, reason: str = "closed") -> Optional[Dict[str, Any]]:
    session = _nimble_sessions.get(callback_id)
    if not session:
        return None
    session["closed"] = True
    session["status"] = reason
    session["updated_at"] = _now()
    print(f"[faust.backend.nimble] Session closed: {callback_id}, reason={reason}")
    return session


def is_nimble_session_alive(callback_id: str) -> bool:
    session = _nimble_sessions.get(callback_id)
    if not session:
        return False
    if session.get("closed"):
        return False
    return _now() < float(session.get("expires_at", 0))


def get_nimble_result(callback_id: str, *, cleanup: bool = False) -> Any:
    session = _nimble_sessions.get(callback_id)
    if not session:
        return None
    data = session.get("result")
    if cleanup:
        cleanup_nimble_session(callback_id)
    return data


def export_window_payload(callback_id: str) -> Optional[Dict[str, Any]]:
    session = _nimble_sessions.get(callback_id)
    if not session:
        return None
    return {
        "callback_id": session["callback_id"],
        "title": session["title"],
        "html": session["html"],
        "lifespan": session["lifespan"],
        "expires_at": session["expires_at"],
        "metadata": session.get("metadata") or {},
    }


def cleanup_nimble_session(callback_id: str) -> Optional[Dict[str, Any]]:
    session = _nimble_sessions.pop(callback_id, None)
    if session:
        print(f"[faust.backend.nimble] Session cleaned: {callback_id}")
    return session


def list_active_sessions() -> Dict[str, Dict[str, Any]]:
    return {k: v for k, v in _nimble_sessions.items() if is_nimble_session_alive(k)}
