import asyncio
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import config_loader as conf
except ImportError:
    import faust_backend.config_loader as conf

import websocket
import faust_backend.trigger_manager as trigger_manager
from faust_backend.logger import get_logger

log = get_logger("faust.mc")

MC_OPERATOR_URL = conf.config.get("MC_OPERATOR_URL", "ws://127.0.0.1:18901") if hasattr(conf, "config") else "ws://127.0.0.1:18901"
MC_EVENT_TRIGGER_ENABLED = conf.config.get("MC_EVENT_TRIGGER_ENABLED", True) if hasattr(conf, "config") else True

_ws_app = None
_ws_thread = None
_connected = threading.Event()
_send_lock = threading.Lock()
_pending: dict[str, dict[str, Any]] = {}
_pending_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_started = False
_event_rate_limit_lock = threading.Lock()
_hurted_last_trigger_ts = 0.0

_verbosity = True

def _make_trigger_for_event(event_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    recall = f"Minecraft事件触发：{event_name}。相关信息：{json.dumps(payload, ensure_ascii=False)}。请根据游戏状态决定是否调用Minecraft工具继续操作。"
    return {
        "id": f"mc_event_{uuid.uuid4().hex}",
        "type": "event",
        "event_name": "mc_event",
        "payload": {
            "mc_event_type": event_name,
            **payload,
        },
        "recall_description": recall,
        "lifespan": 300,
    }


def _resolve_pending(request_id: str, ok: bool, data: dict[str, Any]) -> None:
    with _pending_lock:
        waiter = _pending.pop(request_id, None)
    if not waiter:
        return
    future = waiter["future"]
    loop = waiter["loop"]
    if future.done():
        return
    if ok:
        loop.call_soon_threadsafe(future.set_result, data)
    else:
        loop.call_soon_threadsafe(future.set_exception, RuntimeError(data.get("error", "unknown mc error")))


def _on_open(ws):
    _connected.set()
    log.info("已连接到 mc-operator: %s", MC_OPERATOR_URL)


def _on_message(ws, message: str):
    try:
        payload = json.loads(message)
    except Exception as exc:
        log.error("无效消息: %s", exc)
        return

    msg_type = payload.get("type")
    if msg_type == "command_result":
        _resolve_pending(payload.get("request_id", ""), bool(payload.get("ok")), payload)
    elif msg_type == "event":
        event_name = payload.get("event_name", "unknown")
        event_payload = payload.get("payload", {})
        log.info("收到事件: %s, payload: %s", event_name, event_payload)
        if MC_EVENT_TRIGGER_ENABLED:
            if event_name == "hurted":
                global _hurted_last_trigger_ts
                now = time.monotonic()
                with _event_rate_limit_lock:
                    if now - _hurted_last_trigger_ts < 13:
                        return
                    _hurted_last_trigger_ts = now
            trigger_manager.append_trigger(_make_trigger_for_event(event_name, event_payload))
    elif msg_type == "hello":
        pass


def _on_error(ws, error):
    log.error("WebSocket 错误: %s", error)


def _on_close(ws, status_code, msg):
    _connected.clear()
    log.info("与 mc-operator 断开: %s %s", status_code, msg)


async def _ensure_started() -> None:
    global _ws_app, _ws_thread, _started
    if _started:
        return
    _started = True
    _ws_app = websocket.WebSocketApp(
        MC_OPERATOR_URL,
        on_open=_on_open,
        on_message=_on_message,
        on_error=_on_error,
        on_close=_on_close,
    )

    def runner():
        while True:
            try:
                _ws_app.run_forever(reconnect=5)
            except Exception as exc:
                log.error("run_forever 错误: %s", exc)
            time.sleep(2)

    _ws_thread = threading.Thread(target=runner, daemon=True)
    _ws_thread.start()

    for _ in range(50):
        if _connected.is_set():
            return
        await asyncio.sleep(0.1)
    raise RuntimeError(f"mc-operator not reachable: {MC_OPERATOR_URL}")


async def ensure_started() -> None:
    await _ensure_started()


async def send_command(name: str, args: Optional[Dict[str, Any]] = None, timeout: float = 60.0) -> Dict[str, Any]:
    await _ensure_started()
    if not _connected.is_set() or _ws_app is None:
        raise RuntimeError("mc-operator websocket is not connected")
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    request_id = uuid.uuid4().hex
    with _pending_lock:
        _pending[request_id] = {"future": future, "loop": loop}
    message = {
        "type": "command",
        "request_id": request_id,
        "name": name,
        "args": args or {},
    }
    with _send_lock:
        _ws_app.send(json.dumps(message, ensure_ascii=False))
    return await asyncio.wait_for(future, timeout=timeout)


async def get_status() -> Dict[str, Any]:
    return await send_command("get-status", {})


async def connect_server(host: str, port: int, username: str, version: str | None = None) -> Dict[str, Any]:
    payload = {"host": host, "port": port, "username": username}
    if version:
        payload["version"] = version
    return await send_command("connect-server", payload, timeout=120.0)


async def disconnect_server(reason: str = "disconnect requested") -> Dict[str, Any]:
    return await send_command("disconnect-server", {"reason": reason})
