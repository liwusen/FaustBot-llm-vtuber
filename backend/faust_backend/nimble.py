"""
这个文件负责实现"灵动交互"系统的核心逻辑。

设计目标：
1. Agent 非阻塞地创建一个灵动窗口；
2. 窗口生命周期与对应 trigger 绑定；
3. 窗口显示期间自动创建一个定时提醒 trigger，提醒 Agent 主动关注该窗口；
4. 用户提交 / 关闭窗口时，相关 trigger 一并清理；
5. trigger_manager 和 backend-main 只通过 callback_id / trigger_id 取回上下文。
"""
import json
import os
import time
import uuid
from typing import Dict, Any, Optional
from faust_backend.logger import get_logger
from faust_backend.config_loader import CONFIG_ROOT

log = get_logger("faust.nimble")

_nimble_sessions: Dict[str, Dict[str, Any]] = {}
PERSISTENT_FILE = os.path.join(CONFIG_ROOT, "persistent_nimble.json")
PERSISTENT_EXPIRE_TRIGGER_SUFFIX = "__no_expire"


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
    persistent: bool = False,
    persistent_id: str = "",
) -> Dict[str, Any]:
    """创建或覆盖一个 nimble 会话。

    返回 session dict，包含窗口生命周期和与之绑定的 trigger id。
    """
    if persistent:
        lifespan = max(lifespan, 31536000)  # at least 1 year
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
        "persistent": persistent,
        "persistent_id": persistent_id,
    }
    if persistent:
        session["expire_trigger_id"] = f"nimble_expire::{callback_id}{PERSISTENT_EXPIRE_TRIGGER_SUFFIX}"
    _nimble_sessions[callback_id] = session
    log.info("Session created: %s (persistent=%s)", callback_id, persistent)
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
        log.warning("收到未知 callback_id 的结果: %s", callback_id)
        return None
    session["result"] = data
    session["updated_at"] = _now()
    session["status"] = "submitted"
    if closed:
        session["closed"] = True
        session["status"] = "closed"
    log.info("Result stored for: %s", callback_id)
    return session


def close_nimble_session(callback_id: str, reason: str = "closed") -> Optional[Dict[str, Any]]:
    session = _nimble_sessions.get(callback_id)
    if not session:
        return None
    session["closed"] = True
    session["status"] = reason
    session["updated_at"] = _now()
    log.info("Session closed: %s, reason=%s", callback_id, reason)
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
        "persistent": session.get("persistent", False),
        "persistent_id": session.get("persistent_id", ""),
    }


def cleanup_nimble_session(callback_id: str) -> Optional[Dict[str, Any]]:
    session = _nimble_sessions.pop(callback_id, None)
    if session:
        log.info("Session cleaned: %s", callback_id)
        if session.get("persistent") or is_persistent_session(callback_id):
            remove_persistent_session(callback_id)
    return session


def _load_persistent_file() -> list:
    if not os.path.exists(PERSISTENT_FILE):
        return []
    try:
        with open(PERSISTENT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else data.get("sessions", [])
    except Exception as e:
        log.warning("读取持久化 Nimble 窗口文件失败: %s", e)
        return []


def _save_persistent_file(sessions: list) -> None:
    try:
        os.makedirs(os.path.dirname(PERSISTENT_FILE), exist_ok=True)
        with open(PERSISTENT_FILE, "w", encoding="utf-8") as f:
            json.dump({"sessions": sessions}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("写入持久化 Nimble 窗口文件失败: %s", e)


def save_persistent_session(session: dict) -> None:
    """保存一个持久化 nimble 窗口到磁盘。"""
    entry = {
        "persistent_id": session.get("persistent_id"),
        "callback_id": session["callback_id"],
        "title": session["title"],
        "html": session["html"],
        "recall_text": session.get("recall_text", ""),
        "reminder_interval_seconds": session.get("reminder_interval_seconds", 120),
        "lifespan": session.get("lifespan", 1800),
        "metadata": session.get("metadata", {}),
    }
    sessions = _load_persistent_file()
    # 替换同 persistent_id 的已有记录
    pid = session.get("persistent_id")
    sessions = [s for s in sessions if s.get("persistent_id") != pid]
    sessions.append(entry)
    _save_persistent_file(sessions)
    log.info("持久化 Nimble 窗口已保存: %s", session["callback_id"])


def remove_persistent_session(callback_id: str) -> None:
    """从磁盘移除一个持久化 nimble 窗口。"""
    sessions = _load_persistent_file()
    before = len(sessions)
    sessions = [s for s in sessions if s.get("callback_id") != callback_id]
    if len(sessions) != before:
        _save_persistent_file(sessions)
        log.info("持久化 Nimble 窗口已移除: %s", callback_id)


def is_persistent_session(callback_id: str) -> bool:
    return callback_id.startswith("persistent_")


def get_all_persistent_session_data() -> list:
    return _load_persistent_file()


async def restore_persistent_sessions():
    """在启动时恢复所有持久化 Nimble 窗口。"""
    import faust_backend.backend2front as backend2frontend
    import faust_backend.trigger_manager as trigger_manager
    from datetime import datetime, timedelta

    entries = get_all_persistent_session_data()
    if not entries:
        log.info("没有需要恢复的持久化 Nimble 窗口")
        return
    restored = 0
    for entry in entries:
        callback_id = entry["callback_id"]
        if callback_id in _nimble_sessions:
            continue
        try:
            session = create_nimble_session(
                callback_id,
                title=entry.get("title", "灵动交互"),
                html=entry.get("html", ""),
                recall_text=entry.get("recall_text", ""),
                reminder_interval_seconds=entry.get("reminder_interval_seconds", 120),
                lifespan=entry.get("lifespan", 31536000),
                metadata=entry.get("metadata", {}),
                persistent=True,
                persistent_id=entry.get("persistent_id", ""),
            )
            trigger_manager.append_trigger({
                "id": session["result_trigger_id"],
                "type": "event",
                "event_name": "nimble_result",
                "callback_id": callback_id,
                "recall_description": f"持久化窗口 {callback_id} 收到了用户提交结果。",
                "lifespan": session["lifespan"],
            })
            trigger_manager.append_trigger({
                "id": session["reminder_trigger_id"],
                "type": "nimble-reminder",
                "callback_id": callback_id,
                "interval_seconds": entry.get("reminder_interval_seconds", 120),
                "recall_description": entry.get("recall_text", ""),
                "lifespan": session["lifespan"],
            })
            backend2frontend.FrontEndShowNimbleWindow(export_window_payload(callback_id))
            restored += 1
            log.info("持久化 Nimble 窗口已恢复: %s", callback_id)
        except Exception as e:
            log.warning("恢复持久化 Nimble 窗口失败 %s: %s", callback_id, e)
    log.info("持久化 Nimble 窗口恢复完成: %d/%d", restored, len(entries))


def cleanup_expired_sessions() -> int:
    """移除所有已过期/已关闭的 session，返回清理数量。"""
    now = _now()
    expired_ids = [
        cid for cid, s in list(_nimble_sessions.items())
        if s.get("closed") or now >= float(s.get("expires_at", 0))
    ]
    for cid in expired_ids:
        _nimble_sessions.pop(cid, None)
    if expired_ids:
        log.info("Cleaned %s expired/closed sessions", len(expired_ids))
    return len(expired_ids)


def list_active_sessions() -> Dict[str, Dict[str, Any]]:
    # 在列出前先清理过期 session，防止内存泄漏
    cleanup_expired_sessions()
    return {k: v for k, v in _nimble_sessions.items() if is_nimble_session_alive(k)}


def push_persistent_sessions_to_frontend():
    """将所有活跃的持久化 Nimble 窗口推送到前端（用于 WS 重连/新连接时恢复显示）。"""
    import faust_backend.backend2front as backend2frontend
    count = 0
    for callback_id, session in list(_nimble_sessions.items()):
        if session.get("persistent") and not session.get("closed") and _now() < float(session.get("expires_at", 0)):
            backend2frontend.FrontEndShowNimbleWindow(export_window_payload(callback_id))
            count += 1
    if count:
        log.info("已推送 %d 个持久化 Nimble 窗口到前端", count)
