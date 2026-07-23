from __future__ import annotations

import ctypes
import json
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

import faust_backend.backend2front as backend2frontend
from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl
from faust_backend.tools.vfs import run_coro_sync

from faust_backend.logger import get_logger
log = get_logger("faust.plugins.desktop-mood")

try:
    import psutil
except Exception:
    psutil = None

try:
    from winsdk.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as _SMTCManager
except Exception:
    _SMTCManager = None

_ROUTER = APIRouter()
_PLUGIN: "Plugin | None" = None
STATE_FILE_NAME = 'desktop_mood_state.json'
RULES_FILE = Path.home() / '.faustbot' / 'desktop-mood.rules.json'
DEFAULT_RULES = [
    {"id": "idle_yawn", "label": "空闲打哈欠", "enabled": True, "cooldown_sec": 1800, "kind": "motion", "condition": {"type": "idle_over", "seconds": 600}, "action": {"motion": "yawn"}},
    {"id": "idle_voice", "label": "空闲提醒", "enabled": True, "cooldown_sec": 1800, "kind": "speech", "condition": {"type": "idle_over", "seconds": 600}, "action": {"speech": "你很久没说话了。"}},
    {"id": "return_voice", "label": "回归提醒", "enabled": True, "cooldown_sec": 1800, "kind": "speech", "condition": {"type": "return_active"}, "action": {"speech": "离开了一会，终于回来了。"}},
    {"id": "cpu_warning", "label": "高负载提醒", "enabled": True, "cooldown_sec": 1800, "kind": "speech", "condition": {"type": "cpu_over", "value": 90}, "action": {"speech": "你的电脑在哀嚎。"}},
    {"id": "memory_warning", "label": "内存提醒", "enabled": True, "cooldown_sec": 1800, "kind": "speech", "condition": {"type": "memory_over", "value": 90}, "action": {"speech": "内存快满了，要不要关掉一些东西？"}},
    {"id": "battery_note", "label": "低电量便签", "enabled": True, "cooldown_sec": 1800, "kind": "nimble", "condition": {"type": "battery_under", "value": 15}, "action": {"title": "电量提醒", "note": "充电！还剩 {battery}%!"}},
    {"id": "night_owl", "label": "深夜活动提醒", "enabled": True, "cooldown_sec": 1800, "kind": "speech", "condition": {"type": "hour_range", "start": 2, "end": 5}, "action": {"speech": "凌晨 {hour} 点了，还不睡吗。"}},
    {"id": "vscode_bless", "label": "VS Code 祝福", "enabled": True, "cooldown_sec": 1800, "kind": "speech", "condition": {"type": "window_contains", "value": "Visual Studio Code", "probability": 0.2}, "action": {"speech": "祝你写出没有 bug 的代码。"}},
    {"id": "media_playing", "label": "媒体播放提醒", "enabled": True, "cooldown_sec": 1800, "kind": "event-trigger", "condition": {"type": "smtc_playing"}, "action": {"event_name": "desktop_mood_media", "summary": "检测到系统媒体正在播放。"}},
]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def _now() -> int:
    return int(time.time())


def _windows_idle_seconds() -> int | None:
    try:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if user32.GetLastInputInfo(ctypes.byref(info)) == 0:
            return None
        millis = kernel32.GetTickCount() - info.dwTime
        return max(0, int(millis // 1000))
    except Exception:
        return None


def _foreground_window_title() -> str:
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ''
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buffer, 512)
        return buffer.value.strip()
    except Exception:
        return ''


def _holiday_name() -> str | None:
    now = time.localtime()
    month_day = f'{now.tm_mon:02d}-{now.tm_mday:02d}'
    mapping = {'12-25': 'Christmas', '10-31': 'Halloween', '02-10': 'Spring Festival'}
    return mapping.get(month_day)


async def _read_smtc_now() -> dict[str, Any] | None:
    if _SMTCManager is None:
        return None
    try:
        manager = await _SMTCManager.request_async()
        session = manager.get_current_session()
        if session is None:
            return None
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        status = str(getattr(info, 'playback_status', 'unknown'))
        return {
            'title': str(getattr(props, 'title', '') or ''),
            'artist': str(getattr(props, 'artist', '') or ''),
            'status': status,
        }
    except Exception:
        return None


class DesktopMoodStore:
    def __init__(self, plugin_dir: Path):
        self._lock = threading.RLock()
        self._data_dir = plugin_dir / 'data'
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = self._data_dir / STATE_FILE_NAME
        self._state = self._load_state()
        self.save()

    def _load_state(self) -> dict[str, Any]:
        base = {
            'manual_mood': 'auto',
            'weather': None,
            'weather_updated_at': 0,
            'snapshot': {},
            'rules': self._load_rules_file(),
            'rule_hits': {},
            'global_last_fire_ts': 0,
            'last_idle_state': 'active',
        }
        if not self._state_path.exists():
            return base
        try:
            raw = json.loads(self._state_path.read_text(encoding='utf-8'))
        except Exception:
            return base
        base.update(raw)
        base['rules'] = self._load_rules_file()
        return base

    def _load_rules_file(self) -> list[dict[str, Any]]:
        RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if not RULES_FILE.exists():
            RULES_FILE.write_text(json.dumps(DEFAULT_RULES, ensure_ascii=False, indent=2), encoding='utf-8')
            return list(DEFAULT_RULES)
        try:
            data = json.loads(RULES_FILE.read_text(encoding='utf-8'))
            return list(data) if isinstance(data, list) else list(DEFAULT_RULES)
        except Exception:
            return list(DEFAULT_RULES)

    def save(self) -> None:
        self._state_path.write_text(json.dumps(self._state, ensure_ascii=False, indent=2), encoding='utf-8')

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._state, ensure_ascii=False))

    def set_manual_mood(self, mood: str) -> None:
        with self._lock:
            self._state['manual_mood'] = mood
            self.save()

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._state['snapshot'] = snapshot
            self.save()

    def set_rules(self, rules: list[dict[str, Any]]) -> None:
        with self._lock:
            self._state['rules'] = list(rules)
            RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
            RULES_FILE.write_text(json.dumps(list(rules), ensure_ascii=False, indent=2), encoding='utf-8')
            self.save()

    def set_weather(self, weather: dict[str, Any] | None) -> None:
        with self._lock:
            self._state['weather'] = weather
            self._state['weather_updated_at'] = _now()
            self.save()

    def can_fire(self, rule_id: str, cooldown_sec: int, global_cooldown_sec: int) -> bool:
        with self._lock:
            last_rule = int((self._state.get('rule_hits') or {}).get(rule_id) or 0)
            last_global = int(self._state.get('global_last_fire_ts') or 0)
            now = _now()
            return (now - last_rule) >= cooldown_sec and (now - last_global) >= global_cooldown_sec

    def touch_rule_fire(self, rule_id: str) -> None:
        with self._lock:
            now = _now()
            self._state.setdefault('rule_hits', {})[rule_id] = now
            self._state['global_last_fire_ts'] = now
            self.save()

    def get_last_idle_state(self) -> str:
        return str(self._state.get('last_idle_state') or 'active')

    def set_last_idle_state(self, state: str) -> None:
        with self._lock:
            self._state['last_idle_state'] = state
            self.save()


def _fetch_weather(city: str) -> dict[str, Any] | None:
    try:
        query = urllib.parse.quote(city or '')
        url = f'https://wttr.in/{query}?format=j1' if query and city != 'auto' else 'https://wttr.in/?format=j1'
        with urllib.request.urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode('utf-8', errors='ignore'))
        current = ((payload.get('current_condition') or [{}])[0]) if isinstance(payload, dict) else {}
        return {
            'text': ((current.get('weatherDesc') or [{}])[0].get('value') or '').strip(),
            'temperature_c': current.get('temp_C'),
        }
    except Exception:
        return None


@_ROUTER.get('/state')
async def get_state():
    if _PLUGIN is None or _PLUGIN.store is None:
        return {"status": "ok", "state": {}}
    return {"status": "ok", "state": _PLUGIN.store.snapshot()}


@_ROUTER.get('/context')
async def get_context():
    if _PLUGIN is None:
        return {"status": "ok", "context": {}}
    return {"status": "ok", "context": _PLUGIN.collect_context()}


@_ROUTER.get('/rules')
async def get_rules():
    if _PLUGIN is None or _PLUGIN.store is None:
        return {"status": "ok", "items": []}
    return {"status": "ok", "items": _PLUGIN.store.snapshot().get('rules', [])}


@_ROUTER.post('/rules')
async def set_rules(payload: dict = Body(...)):
    if _PLUGIN is None or _PLUGIN.store is None:
        raise HTTPException(status_code=503, detail='plugin not loaded')
    items = payload.get('items')
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail='items must be a list')
    _PLUGIN.store.set_rules(items)
    return {"status": "ok", "items": items}


@_ROUTER.post('/mood')
async def set_mood(payload: dict = Body(...)):
    if _PLUGIN is None or _PLUGIN.store is None:
        raise HTTPException(status_code=503, detail='plugin not loaded')
    mood = str(payload.get('mood') or 'auto').strip()
    _PLUGIN.store.set_manual_mood(mood)
    return {"status": "ok", "mood": mood}


class Plugin(FaustPlugin):
    def __init__(self):
        self.ctx: PluginContext | None = None
        self.store: DesktopMoodStore | None = None
        self._weather_refresh_started = False

    def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.store = DesktopMoodStore(ctx.plugin_dir)
        ctx.register_config([
            {"key": "GLOBAL_COOLDOWN_SEC", "type": "int", "label": "全局冷却（秒）", "default": 180},
            {"key": "WEATHER_CITY", "type": "str", "label": "天气城市", "default": 'auto'},
            {"key": "ENABLE_WINDOW_WATCH", "type": "bool", "label": "窗口监控开关", "default": True},
            {"key": "ENABLE_IDLE_WATCH", "type": "bool", "label": "空闲检测开关", "default": True},
            {"key": "ENABLE_HOLIDAY_EGG", "type": "bool", "label": "节日彩蛋开关", "default": True},
            {"key": "ENABLE_SMTC_WATCH", "type": "bool", "label": "媒体监控开关", "default": True},
        ])
        ctx.vfs_write(
            "/plugins/desktop-mood.md",
            "# Desktop Mood\n\n"
            "Desktop Mood 会持续把桌面环境写入 faustbot://plugins/desktop-context.json。\n"
            "当你想根据用户环境主动提醒、播报、关心用户时，请先读取这个上下文文件。\n"
            "规则文件位于 ~/.faustbot/desktop-mood.rules.json，插件会按规则自动触发动作或 event-trigger。\n",
        )

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        _PLUGIN = self

    @hookimpl
    def plugin_unloaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        if _PLUGIN is self:
            _PLUGIN = None

    def register_routes(self) -> list:
        return [_ROUTER]

    def register_frontend(self) -> list[dict]:
        return [
            {"type": "js", "path": "/faust/plugins/desktop-mood/frontend/panel-v2.js"},
            {"type": "js", "path": "/faust/plugins/desktop-mood/frontend/app-hook-v2.js"},
            {"type": "css", "path": "/faust/plugins/desktop-mood/frontend/panel-v2.css"},
        ]

    def register_prompt_suffix(self) -> list[str]:
        return [
            "\n[Desktop Mood 情景感知]\n"
            "桌面环境实时快照在 faustbot://plugins/desktop-context.json，使用指南在 faustbot://plugins/desktop-mood.md。"
            "在需要关心用户环境、主动提醒或理解 event-trigger 时，请优先读取这些内容。\n"
        ]

    def _maybe_refresh_weather(self) -> None:
        if self.store is None or self.ctx is None:
            return
        state = self.store.snapshot()
        if _now() - int(state.get('weather_updated_at') or 0) < 600:
            return
        city = str(self.ctx.get_config('WEATHER_CITY', 'auto') or 'auto')
        weather = _fetch_weather(city)
        self.store.set_weather(weather)

    def collect_context(self) -> dict[str, Any]:
        if self.store is not None:
            self._maybe_refresh_weather()
        cpu = None
        memory = None
        battery_percent = None
        charging = None
        disk_io = None
        if psutil is not None:
            try:
                cpu = float(psutil.cpu_percent(interval=None))
            except Exception:
                cpu = None
            try:
                memory = float(psutil.virtual_memory().percent)
            except Exception:
                memory = None
            try:
                battery = psutil.sensors_battery()
                if battery is not None:
                    battery_percent = float(battery.percent)
                    charging = bool(battery.power_plugged)
            except Exception:
                pass
            try:
                disk = psutil.disk_io_counters()
                if disk is not None:
                    disk_io = {'read_bytes': int(disk.read_bytes), 'write_bytes': int(disk.write_bytes)}
            except Exception:
                pass
        idle = _windows_idle_seconds() if self.ctx is None or bool(self.ctx.get_config('ENABLE_IDLE_WATCH', True)) else None
        window_title = _foreground_window_title() if self.ctx is None or bool(self.ctx.get_config('ENABLE_WINDOW_WATCH', True)) else ''
        weather = self.store.snapshot().get('weather') if self.store is not None else None
        holiday = _holiday_name() if self.ctx is not None and bool(self.ctx.get_config('ENABLE_HOLIDAY_EGG', True)) else None
        smtc = None
        if self.ctx is not None and bool(self.ctx.get_config('ENABLE_SMTC_WATCH', True)):
            try:
                smtc = run_coro_sync(_read_smtc_now())
            except Exception:
                smtc = None
        return {
            'cpu': cpu,
            'memory': memory,
            'battery': {'percent': battery_percent, 'charging': charging},
            'disk_io': disk_io,
            'idle_seconds': idle,
            'window_title': window_title,
            'weather': weather,
            'hour': time.localtime().tm_hour,
            'manual_mood': self.store.snapshot().get('manual_mood') if self.store is not None else 'auto',
            'holiday': holiday,
            'smtc': smtc,
        }

    def _render_template(self, template: str, context: dict[str, Any]) -> str:
        battery = (context.get('battery') or {}).get('percent')
        return str(template or '').format(hour=context.get('hour'), battery=int(battery) if battery is not None else '?')

    def _show_nimble_note(self, title: str, note: str) -> None:
        html = '<div style="padding:18px;font-family:Segoe UI;color:#fff;background:rgba(20,20,30,.85);border-radius:16px;">' + note + '</div>'
        backend2frontend.FrontEndShowNimbleWindow({
            'callback_id': 'desktop_mood_' + str(_now()),
            'title': title,
            'html': html,
            'lifespan': 25,
            'expires_at': _now() + 25,
            'metadata': {'source': 'desktop-mood'},
            'persistent': False,
            'persistent_id': '',
        })

    def _execute_rule(self, rule: dict[str, Any], context: dict[str, Any]) -> None:
        action = rule.get('action') or {}
        kind = str(rule.get('kind') or '')
        if kind == 'motion':
            motion = str(action.get('motion') or '').strip()
            if motion:
                backend2frontend.frontendSetMotion(motion)
        elif kind == 'speech':
            speech = self._render_template(str(action.get('speech') or ''), context)
            if speech:
                backend2frontend.FrontEndSay(speech)
        elif kind == 'nimble':
            title = str(action.get('title') or '桌面提醒')
            note = self._render_template(str(action.get('note') or ''), context)
            self._show_nimble_note(title, note)
        elif kind == 'event-trigger' and self.ctx is not None:
            event_name = str(action.get('event_name') or 'desktop_mood_event')
            summary = self._render_template(str(action.get('summary') or '桌面情景触发。'), context)
            try:
                self.ctx.trigger_create({
                    'id': f'desktop_mood::{rule.get("id") or event_name}::{_now()}',
                    'type': 'event',
                    'event_name': event_name,
                    'payload': {'summary': summary, 'context': context, 'rule': rule},
                    'recall_description': summary,
                    'lifespan': 7200,
                })
            except Exception:
                pass

    def _match_rule(self, rule: dict[str, Any], context: dict[str, Any], last_idle_state: str, next_idle_state: str) -> bool:
        condition = rule.get('condition') or {}
        ctype = str(condition.get('type') or '')
        if ctype == 'idle_over':
            return int(context.get('idle_seconds') or 0) >= int(condition.get('seconds') or 0)
        if ctype == 'return_active':
            return last_idle_state == 'idle' and next_idle_state == 'active'
        if ctype == 'cpu_over':
            cpu = context.get('cpu')
            return cpu is not None and float(cpu) >= float(condition.get('value') or 0)
        if ctype == 'memory_over':
            memory = context.get('memory')
            return memory is not None and float(memory) >= float(condition.get('value') or 0)
        if ctype == 'battery_under':
            battery = (context.get('battery') or {}).get('percent')
            charging = (context.get('battery') or {}).get('charging')
            return battery is not None and float(battery) <= float(condition.get('value') or 0) and not bool(charging)
        if ctype == 'hour_range':
            hour = int(context.get('hour') or 0)
            return int(condition.get('start') or 0) <= hour <= int(condition.get('end') or 23)
        if ctype == 'window_contains':
            return str(condition.get('value') or '').lower() in str(context.get('window_title') or '').lower()
        if ctype == 'smtc_playing':
            smtc = context.get('smtc') or {}
            status = str(smtc.get('status') or '').lower()
            return 'play' in status
        return False

    def heartbeat(self, ctx: PluginContext) -> None:
        if self.store is None or self.ctx is None:
            return
        context = self.collect_context()
        self.store.update_snapshot(context)
        self.ctx.vfs_write('/plugins/desktop-context.json', json.dumps(context, ensure_ascii=False, indent=2))
        idle_seconds = int(context.get('idle_seconds') or 0)
        last_idle_state = self.store.get_last_idle_state()
        next_idle_state = 'idle' if idle_seconds >= 600 else 'active'
        self.store.set_last_idle_state(next_idle_state)
        global_cooldown = int(self.ctx.get_config('GLOBAL_COOLDOWN_SEC', 180) or 180)
        rules = self.store.snapshot().get('rules', [])
        for rule in rules:
            if not bool(rule.get('enabled', True)):
                continue
            rule_id = str(rule.get('id') or '')
            cooldown = int(rule.get('cooldown_sec') or 1800)
            if not self.store.can_fire(rule_id, cooldown, global_cooldown):
                continue
            if not self._match_rule(rule, context, last_idle_state, next_idle_state):
                continue
            self._execute_rule(rule, context)
            self.store.touch_rule_fire(rule_id)
            break

    def health_check(self) -> dict | None:
        snapshot = self.store.snapshot() if self.store else {}
        return {"status": "ok", "plugin": "desktop-mood", "rules": len(snapshot.get('rules', []))}


def get_plugin() -> Plugin:
    return Plugin()
