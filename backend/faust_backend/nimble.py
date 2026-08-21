"""
这个文件负责实现"灵动交互"系统的核心逻辑。

设计目标：
1. Agent 非阻塞地创建一个灵动窗口；
2. 窗口生命周期与对应 trigger 绑定；
3. 窗口显示期间自动创建一个定时提醒 trigger，提醒 Agent 主动关注该窗口；
4. 双向通信走 console：前端 sendMessage → 追加 "Frontend>" 行（可选 event trigger 唤醒 Agent）；
   Agent 用 write 工具写 faustbot://nimble/{id}/console → 追加 "You>" 行并推送到前端；
5. 每个会话在 VFS 暴露 faustbot://nimble/{id}/{summary,console,code-readonly}，关闭即删；
6. trigger_manager 和 backend-main 只通过 callback_id / trigger_id 取回上下文。
"""
import json
import os
import time
import uuid
from datetime import datetime
from typing import Dict, Any, Optional
from faust_backend.logger import get_logger
from faust_backend.config_loader import CONFIG_ROOT

log = get_logger("faust.nimble")

_nimble_sessions: Dict[str, Dict[str, Any]] = {}
PERSISTENT_FILE = os.path.join(CONFIG_ROOT, "persistent_nimble.json")
PERSISTENT_EXPIRE_TRIGGER_SUFFIX = "__no_expire"
VFS_NIMBLE_DIR = "/nimble"


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
    metadata = metadata or {}
    summary_text = "\n".join([
        f"标题: {title}",
        f"用途: {recall_text}",
        f"metadata: {json.dumps(metadata, ensure_ascii=False)}",
    ])
    session = {
        "callback_id": callback_id,
        "title": title,
        "html": html,
        "metadata": metadata,
        "created_at": _now(),
        "updated_at": _now(),
        "lifespan": int(max(1, lifespan)),
        "expires_at": _now() + int(max(1, lifespan)),
        "closed": False,
        "status": "open",
        "recall_text": recall_text,
        "expire_trigger_id": f"nimble_expire::{callback_id}",
        "persistent": persistent,
        "persistent_id": persistent_id,
        "console": [],
        "summary_text": summary_text,
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


def _console_dumps(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def append_console_line(callback_id: str, line: str) -> Optional[Dict[str, Any]]:
    session = _nimble_sessions.get(callback_id)
    if not session:
        log.warning("收到未知 callback_id 的 console 消息: %s", callback_id)
        return None
    session["console"].append(line)
    session["updated_at"] = _now()
    if session.get("persistent"):
        save_persistent_session(session)
    return session


def record_frontend_message(callback_id: str, payload: Any) -> Optional[Dict[str, Any]]:
    """记录一条来自前端 sendMessage 的消息到 console。"""
    return append_console_line(callback_id, f"Frontend>{_console_dumps(payload)}")


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


def _vfs_session_dir(callback_id: str) -> str:
    return f"{VFS_NIMBLE_DIR}/{callback_id}"


async def register_session_vfs_nodes(callback_id: str) -> None:
    """在 VFS 注册 faustbot://nimble/{id}/{summary,console,code-readonly} 三个节点。"""
    from faust_backend.tools.vfs import get_faustbot_vfs

    session = _nimble_sessions.get(callback_id)
    if not session:
        raise KeyError(f"nimble session 不存在: {callback_id}")

    vfs = await get_faustbot_vfs()
    base = _vfs_session_dir(callback_id)
    await vfs.mkdir(base)

    def read_summary(_path: str) -> str:
        s = _nimble_sessions.get(callback_id)
        if not s:
            return f"[nimble session 已关闭: {callback_id}]"
        remain = max(0, int(float(s.get("expires_at", 0)) - _now()))
        status_lines = [
            "",
            "--- 动态状态 ---",
            f"callback_id: {callback_id}",
            f"status: {s.get('status')}",
            f"persistent: {s.get('persistent', False)}",
            f"created_at: {datetime.fromtimestamp(s['created_at']).isoformat(timespec='seconds')}",
            f"剩余存活时间: {remain} 秒",
            f"console 消息数: {len(s.get('console') or [])}",
        ]
        return str(s.get("summary_text") or "") + "\n".join(status_lines)

    def write_summary(_node, content) -> None:
        s = _nimble_sessions.get(callback_id)
        if not s:
            raise FileNotFoundError(f"nimble session 已关闭: {callback_id}")
        s["summary_text"] = str(content or "")
        s["updated_at"] = _now()
        if s.get("persistent"):
            save_persistent_session(s)

    def read_console(_path: str) -> str:
        s = _nimble_sessions.get(callback_id)
        if not s:
            return f"[nimble session 已关闭: {callback_id}]"
        lines = s.get("console") or []
        if not lines:
            return "(console 暂无消息)"
        return "\n".join(lines)

    def write_console(_node, content) -> None:
        import faust_backend.backend2front as backend2frontend

        s = _nimble_sessions.get(callback_id)
        if not s:
            raise FileNotFoundError(f"nimble session 已关闭: {callback_id}")
        text = str(content or "").strip()
        if not text:
            raise ValueError("console 消息不能为空")
        try:
            payload = json.loads(text)
            line_body = _console_dumps(payload)
        except (json.JSONDecodeError, ValueError):
            payload = text
            line_body = text
        append_console_line(callback_id, f"You>{line_body}")
        backend2frontend.FrontEndNimbleMessage({
            "callback_id": callback_id,
            "payload": payload,
        })

    def read_code(_path: str) -> str:
        s = _nimble_sessions.get(callback_id)
        if not s:
            return f"[nimble session 已关闭: {callback_id}]"
        return str(s.get("html") or "")

    await vfs.write_symbolic(f"{base}/summary", read_summary, writable=True)
    await vfs.set_write_handler(f"{base}/summary", write_summary)
    await vfs.set_edit_handler(f"{base}/summary", write_summary)
    await vfs.write_symbolic(f"{base}/console", read_console, writable=True)
    await vfs.set_write_handler(f"{base}/console", write_console)
    await vfs.set_edit_handler(f"{base}/console", write_console)
    await vfs.write_symbolic(f"{base}/code-readonly", read_code, writable=False)
    log.info("VFS nodes registered: faustbot:/%s", base)


async def unregister_session_vfs_nodes(callback_id: str) -> None:
    from faust_backend.tools.vfs import get_faustbot_vfs

    vfs = await get_faustbot_vfs()
    await vfs.delete(_vfs_session_dir(callback_id))
    log.info("VFS nodes removed: faustbot:/%s", _vfs_session_dir(callback_id))


def message_trigger_id(callback_id: str) -> str:
    return f"nimble_message::{callback_id}"


async def finalize_close(callback_id: str, reason: str = "closed") -> Optional[Dict[str, Any]]:
    """关闭会话的统一出口：删 trigger、通知前端、清理 VFS 与内存/磁盘记录。"""
    import faust_backend.backend2front as backend2frontend
    import faust_backend.trigger_manager as trigger_manager

    session = close_nimble_session(callback_id, reason=reason)
    if not session:
        return None
    trigger_manager.delete_trigger(session["expire_trigger_id"])
    trigger_manager.delete_trigger(message_trigger_id(callback_id))
    backend2frontend.FrontEndCloseNimbleWindow({"callback_id": callback_id, "reason": reason})
    await unregister_session_vfs_nodes(callback_id)
    cleanup_nimble_session(callback_id)
    return session


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
        "lifespan": session.get("lifespan", 1800),
        "metadata": session.get("metadata", {}),
        "console": list(session.get("console") or []),
        "summary_text": session.get("summary_text", ""),
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
                lifespan=entry.get("lifespan", 31536000),
                metadata=entry.get("metadata", {}),
                persistent=True,
                persistent_id=entry.get("persistent_id", ""),
            )
            session["console"] = list(entry.get("console") or [])
            if entry.get("summary_text"):
                session["summary_text"] = entry["summary_text"]
            await register_session_vfs_nodes(callback_id)
            backend2frontend.FrontEndShowNimbleWindow(export_window_payload(callback_id) or {})
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
            backend2frontend.FrontEndShowNimbleWindow(export_window_payload(callback_id) or {})
            count += 1
    if count:
        log.info("已推送 %d 个持久化 Nimble 窗口到前端", count)
