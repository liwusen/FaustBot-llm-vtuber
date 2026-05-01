import asyncio
import http.cookies
import json
import logging
import os
import sys
import threading
from pathlib import Path

_blivedm_pkg = os.path.join(os.path.dirname(__file__), "blivedm")
if _blivedm_pkg not in sys.path:
    sys.path.insert(0, _blivedm_pkg)
import blivedm
from blivedm.models import web as web_models
BLiveClient = blivedm.BLiveClient
BaseHandler = blivedm.BaseHandler

import faust_backend.config_loader as conf
import faust_backend.backend2front as backend2front
import faust_backend.trigger_manager as trigger_manager
from faust_backend.live_mode import is_danmaku_blacklisted

log = logging.getLogger("faust.blive")

_BLIVE_CONFIG_FILE = "blive_config.json"


class BLiveManager:
    def __init__(self):
        self._client: "BLiveClient | None" = None
        self._handler: "DanmakuHandler | None" = None
        self._room_id: int = 0
        self._sessdata: str = ""
        self._enabled: bool = False
        self._started: bool = False
        self._lock = threading.Lock()

    def load_config(self) -> dict:
        path = Path(conf.CONFIG_ROOT) / _BLIVE_CONFIG_FILE
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                log.warning("读取 B站直播配置失败: %s", e)
        return {"room_id": 0, "sessdata": "", "enabled": False}

    def save_config(self, room_id: int, sessdata: str, enabled: bool) -> None:
        self._room_id = room_id
        self._sessdata = sessdata
        self._enabled = enabled
        data = {"room_id": room_id, "sessdata": sessdata, "enabled": enabled}
        path = Path(conf.CONFIG_ROOT) / _BLIVE_CONFIG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        log.info("B站直播配置已保存: room_id=%d, enabled=%s", room_id, enabled)

    async def start(self) -> bool:
        if self._started:
            log.warning("B站直播客户端已在运行")
            return True
        cfg = self.load_config()
        self._room_id = int(cfg.get("room_id", 0))
        self._sessdata = str(cfg.get("sessdata", "") or "")
        self._enabled = bool(cfg.get("enabled", False))
        if not self._enabled or self._room_id <= 0:
            log.info("B站直播未启用或 room_id 无效，跳过启动")
            return False
        try:
            self._client = BLiveClient(self._room_id)
            if self._sessdata:
                cookies = http.cookies.SimpleCookie()
                cookies["SESSDATA"] = self._sessdata
                cookies["SESSDATA"]["domain"] = "bilibili.com"
                self._client._session.cookie_jar.update_cookies(cookies)
            self._handler = DanmakuHandler()
            self._client.set_handler(self._handler)
            self._client.start()
            self._started = True
            log.info("B站直播客户端已启动: room_id=%d, sessdata=%s",
                     self._room_id, "已设置" if self._sessdata else "未设置")
            return True
        except Exception as e:
            log.error("启动 B站直播客户端失败: %s", e)
            await self._cleanup()
            return False

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        try:
            if self._client is not None:
                self._client.stop()
                await self._client.join()
        except Exception as e:
            log.warning("停止 B站客户端时出错: %s", e)
        await self._cleanup()
        log.info("B站直播客户端已停止")

    async def _cleanup(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
        self._handler = None

    def get_status(self) -> dict:
        return {
            "started": self._started,
            "room_id": self._room_id,
            "enabled": self._enabled,
            "has_sessdata": bool(self._sessdata),
        }


class DanmakuHandler(BaseHandler):
    def _on_danmaku(self, client: "BLiveClient", message: web_models.DanmakuMessage):
        uname = str(message.uname or "匿名")
        msg = str(message.msg or "")
        uid = int(message.uid or 0)
        if not msg:
            return
        if is_danmaku_blacklisted(msg):
            log.debug("弹幕被黑名单过滤: %s: %s", uname, msg[:50])
            return
        log.info("B站弹幕: %s: %s", uname, msg[:80])
        payload = {"uname": uname, "msg": msg, "uid": uid}
        trigger_manager._emit_trigger({
            "type": "event",
            "event_name": "blive_danmaku",
            "payload": payload,
        })
        display_text = f"[弹幕]{uname}: {msg}"
        try:
            backend2front.FrontEndSay(display_text)
        except Exception as e:
            log.warning("推送弹幕到前端失败: %s", e)


_instance: BLiveManager | None = None
_instance_lock = threading.Lock()


def get_blive_manager(refresh: bool = False) -> BLiveManager:
    global _instance
    if refresh or _instance is None:
        with _instance_lock:
            if refresh and _instance is not None:
                was_started = _instance._started
                if was_started:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(_instance.stop())
                    except RuntimeError:
                        pass
            if refresh or _instance is None:
                _instance = BLiveManager()
    return _instance
