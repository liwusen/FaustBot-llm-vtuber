import logging

from fastapi import APIRouter

import faust_backend.config_loader as conf
import faust_backend.backend2front as backend2front
import faust_backend.trigger_manager as trigger_manager
from faust_backend.live_mode import (
    is_live_mode,
    set_live_mode,
    get_danmaku_blacklist,
    set_danmaku_blacklist,
    get_tts_blacklist,
    set_tts_blacklist,
)
from faust_backend.blive_manager import get_blive_manager

log = logging.getLogger("faust.live_api")

router = APIRouter(prefix="/faust/live", tags=["live"])

_rebuild_callback = None


def set_rebuild_callback(callback):
    global _rebuild_callback
    _rebuild_callback = callback


@router.post("/start")
async def live_start():
    if is_live_mode():
        return {"status": "already_in_live_mode"}
    set_live_mode(True)
    if _rebuild_callback:
        try:
            await _rebuild_callback()
        except Exception as e:
            log.error("直播模式重建运行时失败: %s", e)
            set_live_mode(False)
            return {"status": "error", "error": f"重建运行时失败: {e}"}
    blive = get_blive_manager(refresh=True)
    try:
        ok = await blive.start()
    except Exception as e:
        log.error("启动 B站弹幕失败: %s", e)
        ok = False
    return {
        "status": "ok",
        "live_mode": True,
        "blive_started": ok,
    }


@router.post("/stop")
async def live_stop():
    if not is_live_mode():
        return {"status": "not_in_live_mode"}
    blive = get_blive_manager(refresh=False)
    try:
        await blive.stop()
    except Exception as e:
        log.warning("停止 B站弹幕时出错: %s", e)
    set_live_mode(False)
    if _rebuild_callback:
        try:
            await _rebuild_callback()
        except Exception as e:
            log.error("退出直播模式重建运行时失败: %s", e)
            return {"status": "error", "error": f"重建运行时失败: {e}"}
    return {"status": "ok", "live_mode": False}


@router.get("/status")
async def live_status():
    blive = get_blive_manager(refresh=False)
    return {
        "live_mode": is_live_mode(),
        "blive": blive.get_status(),
        "danmaku_blacklist": get_danmaku_blacklist(),
        "tts_blacklist": get_tts_blacklist(),
    }


@router.post("/blive/settings")
async def blive_settings(payload: dict):
    room_id = int(payload.get("room_id", 0))
    sessdata = str(payload.get("sessdata", "") or "")
    enabled = bool(payload.get("enabled", False))
    blive = get_blive_manager(refresh=True)
    blive.save_config(room_id, sessdata, enabled)
    if is_live_mode():
        await blive.stop()
        await blive.start()
    return {"status": "ok"}


@router.get("/triggers")
async def list_live_triggers():
    tasks = list(trigger_manager.trigger_queue.queue)
    live_tasks = [t for t in tasks if isinstance(t, dict) and t.get("event_name") == "blive_danmaku"]
    return {"triggers": live_tasks}


@router.delete("/triggers/{trigger_id}")
async def delete_live_trigger(trigger_id: str):
    removed = False
    with trigger_manager.trigger_queue.mutex:
        items = list(trigger_manager.trigger_queue.queue)
        trigger_manager.trigger_queue.queue.clear()
        for item in items:
            if isinstance(item, dict) and item.get("event_name") == "blive_danmaku" and item.get("payload", {}).get("uid") == trigger_id:
                removed = True
                continue
            trigger_manager.trigger_queue.put(item)
    return {"status": "ok" if removed else "not_found", "removed": removed}


@router.post("/blacklist/danmaku")
async def update_danmaku_blacklist(payload: dict):
    words = payload.get("words", [])
    if isinstance(words, str):
        words = [w.strip() for w in words.replace("\n", ",").split(",") if w.strip()]
    set_danmaku_blacklist(words)
    return {"status": "ok", "blacklist": get_danmaku_blacklist()}


@router.post("/blacklist/tts")
async def update_tts_blacklist(payload: dict):
    words = payload.get("words", [])
    if isinstance(words, str):
        words = [w.strip() for w in words.replace("\n", ",").split(",") if w.strip()]
    set_tts_blacklist(words)
    return {"status": "ok", "blacklist": get_tts_blacklist()}
