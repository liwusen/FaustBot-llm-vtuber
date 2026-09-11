from __future__ import annotations

import asyncio
import ctypes
import json
import random
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import faust_backend.backend2front as backend2frontend
from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl

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

_PLUGIN: "Plugin | None" = None
STATE_FILE_NAME = 'desktop_mood_state.json'
RULES_FILE = Path.home() / '.faustbot' / 'desktop-mood.rules.json'

# ── 规则编辑 VFS 三节点（草稿 → 提交 → 指南） ──
RULES_NODE_PATH = '/plugins/desktop-mood/rules.json'   # 草稿节点：AI 只改这里，改完不生效
RULES_GUIDE_PATH = '/plugins/desktop-mood/rules.md'    # 指南节点：schema + 工作流（symbolic）
RULES_RELOAD_PATH = '/plugins/desktop-mood/reload'     # 提交节点：read/write 都触发草稿生效

CONDITION_TYPES = (
    'idle_over', 'return_active', 'cpu_over', 'memory_over',
    'battery_under', 'hour_range', 'window_contains', 'smtc_playing',
)
ACTION_KINDS = ('motion', 'speech', 'nimble', 'event-trigger')
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

SMTC_STATUS_MAP = {
            '1': 'changing',
            '0': 'closed',
            '4': 'paused',
            '3': 'playing',
            '2': 'stopped',
#            '5': 'seems to be paused?',#我的电脑上有时会返回 5，可能是文档的错误?
        }

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


def _foreground_window_process() -> dict[str, str] | None:
    """前台窗口所属进程（名/路径），用于稳定识别用户正在运行的程序（如游戏 exe）。

    窗口标题易变（游戏可能显示当前地图/加载界面等），可执行名才是稳定信号。
    """
    if psutil is None:
        return None
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        proc = psutil.Process(pid.value)
        try:
            exe = proc.exe()
        except Exception:
            exe = ''
        return {"name": proc.name(), "path": exe}
    except Exception:
        return None


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
        # 排除 Faust/Electron 自身的媒体会话（如前端 TTS 播放）：
        # 否则 Faust 自己播语音时会被当作"系统媒体播放"触发 smtc_playing 规则。
        try:
            app_id = getattr(session, 'source_app_user_model_id', None)
            if asyncio.iscoroutine(app_id):
                app_id = await app_id
        except Exception:
            app_id = None
        if app_id and any(k in str(app_id).lower() for k in ('faust', 'electron')):
            return None
        props = await session.try_get_media_properties_async()
        info = session.get_playback_info()
        status = str(getattr(info, 'playback_status', 'unknown'))
        """
        Changing	1	
        媒体正在更改。

        Closed	0	
        媒体已关闭。

        Paused	4	
        媒体已暂停。

        Playing	3	
        媒体正在播放。

        Stopped	2	
        媒体已停止。

        FROM https://learn.microsoft.com/zh-cn/uwp/api/windows.media.mediaplaybackstatus?view=winrt-26100
        """
        
            
        return {
            'title': str(getattr(props, 'title', '') or ''),
            'artist': str(getattr(props, 'artist', '') or ''),
            'album': str(getattr(props, 'album_title', '') or ''),
            'status': str(int(status)-1),
            'status_name': SMTC_STATUS_MAP.get(str(int(status)-1), 'unknown'),#Fuck,Winsdk返回的是实际值+1,和文档上不一样!
        }
    except Exception:
        return None


class DesktopMoodStore:
    def __init__(self, data_dir: Path):
        self._lock = threading.RLock()
        self._data_dir = data_dir
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


class Plugin(FaustPlugin):
    def __init__(self):
        self.ctx: PluginContext | None = None
        self.store: DesktopMoodStore | None = None
        self._weather_refresh_started = False
        # SMTC 播放状态边沿检测：只在 非Playing -> Playing 时触发一次
        self._last_smtc_playing: bool | None = None
        self._smtc_rising_edge = False
        # 规则草稿是否有未提交改动
        self._draft_dirty = False

    async def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        data_dir = ctx.plugin_data_dir or (ctx.plugin_dir / 'data')
        self.store = DesktopMoodStore(data_dir)
        await ctx.register_config([
            {"key": "GLOBAL_COOLDOWN_SEC", "type": "int", "label": "全局冷却（秒）", "default": 180},
            {"key": "WEATHER_CITY", "type": "str", "label": "天气城市", "default": 'auto'},
            {"key": "ENABLE_WINDOW_WATCH", "type": "bool", "label": "窗口监控开关", "default": True},
            {"key": "ENABLE_IDLE_WATCH", "type": "bool", "label": "空闲检测开关", "default": True},
            {"key": "ENABLE_HOLIDAY_EGG", "type": "bool", "label": "节日彩蛋开关", "default": True},
            {"key": "ENABLE_SMTC_WATCH", "type": "bool", "label": "媒体监控开关", "default": True},
        ])
        await ctx.vfs_write(
            "/plugins/desktop-mood.md",
            "# Desktop Mood\n\n"
            "Desktop Mood 会持续把桌面环境写入 faustbot://plugins/desktop-context.json。\n"
            "其中 window_title / window_process 是当前活动窗口标题与所属进程（如游戏 exe），可用来感知用户在做什么。\n"
            "当你想根据用户环境主动提醒、播报、关心用户时，请先读取这个上下文文件。\n"
            "规则（自动触发动作）的编辑入口见 faustbot://plugins/desktop-mood/rules.md"
            "，改规则必须走「编辑 rules.json 草稿 → 读/写 reload 节点提交」。\n",
        )
        await self._install_rule_nodes(ctx)

    async def _install_rule_nodes(self, ctx: PluginContext) -> None:
        """注册规则草稿节点、指南节点、提交节点。

        - rules.json：草稿内容节点，write/edit handler 只暂存原文（不校验、不落盘）；
        - rules.md：symbolic 指南（schema + 工作流）；
        - reload：symbolic 节点，read 即提交草稿；write 也提交（内容被忽略）。
        每次插件启动无条件重播种草稿，丢弃上一次未提交的改动。
        """
        rules = self.store.snapshot().get('rules', []) if self.store is not None else []
        await ctx.vfs_write(RULES_NODE_PATH, json.dumps(rules, ensure_ascii=False, indent=2))
        await ctx.vfs_set_write_handler(RULES_NODE_PATH, self._on_rules_draft_write)
        await ctx.vfs_set_edit_handler(RULES_NODE_PATH, self._on_rules_draft_write)
        await ctx.vfs_write_symbolic(
            RULES_GUIDE_PATH,
            lambda _path: self._rules_guide_text(),
            should_be_included_in_search=False,
        )
        await ctx.vfs_write_symbolic(
            RULES_RELOAD_PATH,
            self._on_reload_read,
            should_be_included_in_search=False,
        )
        await ctx.vfs_set_write_handler(RULES_RELOAD_PATH, self._on_reload_write)
        self._draft_dirty = False

    async def _on_rules_draft_write(self, node, content) -> None:
        """草稿写入：只替换草稿原文并标记 dirty，不做任何校验，不落盘。"""
        text = content.decode('utf-8', errors='replace') if isinstance(content, bytes) else str(content)
        node.content = text
        self._draft_dirty = True
        log.info("desktop-mood draft staged: %d chars", len(text))

    def _rules_guide_text(self) -> str:
        snapshot = self.store.snapshot() if self.store is not None else {}
        rules = snapshot.get('rules', []) or []
        ids = [str(rule.get('id') or '?') for rule in rules]
        return '\n'.join([
            '# Desktop Mood 规则编辑',
            '',
            '| 节点 | 作用 |',
            '|---|---|',
            f'| faustbot://{RULES_NODE_PATH.lstrip("/")} | 草稿 JSON。write/edit 只改这里的草稿，不生效、不落盘 |',
            f'| faustbot://{RULES_RELOAD_PATH.lstrip("/")} | 提交节点。**read 一次**即把草稿提交生效（内存+磁盘） |',
            f'| faustbot://{RULES_GUIDE_PATH.lstrip("/")} | 本指南 |',
            '',
            '工作流：',
            f'1. read("faustbot://{RULES_NODE_PATH.lstrip("/")}") 查看当前草稿。',
            f'2. edit/write 该草稿节点，改成想要的规则；不生效也没关系，可反复改。',
            f'3. read("faustbot://{RULES_RELOAD_PATH.lstrip("/")}") 提交；返回「成功/失败 + 原因」。',
            '   失败时草稿保留，改完再提交即可；失败不会污染已生效规则。',
            f'4. write("faustbot://{RULES_RELOAD_PATH.lstrip("/")}", "apply") 与 read 等价，写入内容被忽略。',
            '',
            f'当前生效规则: {len(rules)} 条 ({", ".join(ids) if ids else "无"})',
            '',
            '详细 schema、条件类型、动作类型与示例见 skill://desktop-mood-rules/SKILL.md。',
            '磁盘文件 ~/.faustbot/desktop-mood.rules.json 由提交动作写入，不要直接改它。',
        ])

    def _validate_rules(self, data: Any) -> tuple[list[dict[str, Any]] | None, str | None]:
        """校验规则结构；返回 (rules, None) 或 (None, 错误说明)。"""
        if not isinstance(data, list):
            return None, f'顶层必须是 JSON 数组，当前是 {type(data).__name__}'
        seen: set[str] = set()
        for index, rule in enumerate(data):
            where = f'规则 #{index + 1}'
            if not isinstance(rule, dict):
                return None, f'{where}: 必须是 JSON 对象'
            rule_id = rule.get('id')
            if not isinstance(rule_id, str) or not rule_id.strip():
                return None, f'{where}: 缺少非空字符串字段 id'
            if rule_id in seen:
                return None, f'{where}: id 重复: {rule_id}'
            seen.add(rule_id)
            kind = rule.get('kind')
            if kind not in ACTION_KINDS:
                return None, f'{where} ({rule_id}): kind 非法: {kind!r}（支持: {", ".join(ACTION_KINDS)}）'
            condition = rule.get('condition')
            if not isinstance(condition, dict):
                return None, f'{where} ({rule_id}): 缺少 condition 对象'
            ctype = condition.get('type')
            if ctype not in CONDITION_TYPES:
                return None, f'{where} ({rule_id}): condition.type 非法: {ctype!r}（支持: {", ".join(CONDITION_TYPES)}）'
            action = rule.get('action')
            if not isinstance(action, dict):
                return None, f'{where} ({rule_id}): 缺少 action 对象'
            if kind == 'motion' and not str(action.get('motion') or '').strip():
                return None, f'{where} ({rule_id}): kind=motion 需要 action.motion'
            if kind == 'speech' and not str(action.get('speech') or '').strip():
                return None, f'{where} ({rule_id}): kind=speech 需要 action.speech'
            if kind == 'nimble' and not str(action.get('note') or '').strip():
                return None, f'{where} ({rule_id}): kind=nimble 需要 action.note'
        return list(data), None

    async def _commit_draft(self) -> str:
        """解析草稿并提交生效（内存 + 磁盘）。返回给 AI 看的三段式状态文本。"""
        current = len(self.store.snapshot().get('rules', []) or []) if self.store is not None else 0
        draft_text = await self.ctx.vfs_read_text(RULES_NODE_PATH, default='') if self.ctx is not None else ''
        try:
            data = json.loads(draft_text)
        except Exception as exc:  # noqa: BLE001
            return '\n'.join([
                'Desktop Mood 规则提交: 失败',
                f'原因: JSON 解析错误: {exc}',
                f'生效规则: {current} 条(未变更)',
                '草稿: 保留未提交',
            ])
        rules, error = self._validate_rules(data)
        if error is not None:
            return '\n'.join([
                'Desktop Mood 规则提交: 失败',
                f'原因: {error}',
                f'生效规则: {current} 条(未变更)',
                '草稿: 保留未提交',
            ])
        assert rules is not None
        had_changes = self._draft_dirty
        self.store.set_rules(rules)
        self._draft_dirty = False
        log.info("desktop-mood rules committed: %d rules (changed=%s)", len(rules), had_changes)
        return '\n'.join([
            'Desktop Mood 规则提交: 成功',
            f'生效规则: {len(rules)} 条',
            f'草稿与生效: 已同步（本次提交: {"有改动" if had_changes else "无改动"}）',
        ])

    async def _on_reload_read(self, _path: str) -> str:
        """读取 reload 节点 = 提交草稿。"""
        return await self._commit_draft()

    async def _on_reload_write(self, _node, _content) -> None:
        """写入 reload 节点 = 提交草稿，写入内容被忽略。"""
        report = await self._commit_draft()
        log.info("desktop-mood reload write -> %s", report.splitlines()[0])

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        _PLUGIN = self

    @hookimpl
    def plugin_unloaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        if _PLUGIN is self:
            _PLUGIN = None

    def register_frontend(self) -> list[dict]:
        return [
            {"type": "js", "path": "/faust/plugins/desktop-mood/frontend/panel-v2.js"},
            {"type": "js", "path": "/faust/plugins/desktop-mood/frontend/app-hook-v2.js"},
            {"type": "css", "path": "/faust/plugins/desktop-mood/frontend/panel-v2.css"},
        ]

    def register_prompt_suffix(self) -> list[str]:
        return [
            "\n[Desktop Mood 情景感知]\n"
            "桌面环境实时快照在 faustbot://plugins/desktop-context.json( 包含天气,前台窗口标题/进程,播放的媒体 等有用信息)，使用指南在 faustbot://plugins/desktop-mood.md。"
            "在用户主动发起对话时，你应该(SHOULD)读取这些内容。\n"
            "当用户要求新增/修改/关闭桌面自动规则时：edit faustbot://plugins/desktop-mood/rules.json 草稿，"
            "再 read faustbot://plugins/desktop-mood/reload 提交生效；规则 schema 与工作流见 "
            "skill://desktop-mood-rules/SKILL.md 或 faustbot://plugins/desktop-mood/rules.md。\n"
        ]

    async def _maybe_refresh_weather(self) -> None:
        if self.store is None or self.ctx is None:
            return
        state = self.store.snapshot()
        if _now() - int(state.get('weather_updated_at') or 0) < 600:
            return
        city = str(await self.ctx.get_config('WEATHER_CITY', 'auto') or 'auto')
        weather = _fetch_weather(city)
        self.store.set_weather(weather)

    async def collect_context(self) -> dict[str, Any]:
        if self.store is not None:
            await self._maybe_refresh_weather()
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
        idle = _windows_idle_seconds() if self.ctx is None or bool(await self.ctx.get_config('ENABLE_IDLE_WATCH', True)) else None
        window_title = ''
        window_process = None
        if self.ctx is None or bool(await self.ctx.get_config('ENABLE_WINDOW_WATCH', True)):
            window_title = _foreground_window_title()
            window_process = _foreground_window_process()
        weather = self.store.snapshot().get('weather') if self.store is not None else None
        holiday = _holiday_name() if self.ctx is not None and bool(await self.ctx.get_config('ENABLE_HOLIDAY_EGG', True)) else None
        smtc = None
        if self.ctx is not None and bool(await self.ctx.get_config('ENABLE_SMTC_WATCH', True)):
            try:
                smtc = await _read_smtc_now()
            except Exception:
                smtc = None
        return {
            'cpu': cpu,
            'memory': memory,
            'battery': {'percent': battery_percent, 'charging': charging},
            'disk_io': disk_io,
            'idle_seconds': idle,
            'window_title': window_title,
            'window_process': window_process,
            'weather': weather,
            'hour': time.localtime().tm_hour,
            'manual_mood': self.store.snapshot().get('manual_mood') if self.store is not None else 'auto',
            'holiday': holiday,
            'smtc': smtc,
        }

    async def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        action = str((payload or {}).get('action') or '').strip().lower()
        if action == 'get_state':
            return {"status": "ok", "state": self.store.snapshot() if self.store is not None else {}}
        if action == 'get_context':
            return {"status": "ok", "context": await self.collect_context()}
        if action == 'get_rules':
            items = self.store.snapshot().get('rules', []) if self.store is not None else []
            return {"status": "ok", "items": items}
        if action == 'set_rules':
            items = (payload or {}).get('items')
            if self.store is None:
                return {"status": "error", "detail": 'plugin not loaded'}
            if not isinstance(items, list):
                return {"status": "error", "detail": 'items must be a list'}
            self.store.set_rules(items)
            return {"status": "ok", "items": items}
        if action == 'set_mood':
            if self.store is None:
                return {"status": "error", "detail": 'plugin not loaded'}
            mood = str((payload or {}).get('mood') or 'auto').strip()
            self.store.set_manual_mood(mood)
            return {"status": "ok", "mood": mood}
        return {"status": "error", "detail": f"unknown action: {action}"}

    def _render_template(self, template: str, context: dict[str, Any]) -> str:
        battery = (context.get('battery') or {}).get('percent')
        return str(template or '').format(hour=context.get('hour'), battery=int(battery) if battery is not None else '?')

    def _show_nimble_note(self, title: str, note: str) -> None:
        import faust_backend.nimble as nimble
        import asyncio

        html = '<div style="padding:18px;font-family:Segoe UI;color:#fff;background:rgba(20,20,30,.85);border-radius:16px;">' + note + '</div>'
        callback_id = nimble.build_callback_id()
        lifespan = 25
        nimble.create_nimble_session(
            callback_id,
            title=title,
            html=html,
            recall_text='桌面情景便签，无需回应。',
            lifespan=lifespan,
            metadata={'source': 'desktop-mood'},
        )

        async def run_async():
            await nimble.register_session_vfs_nodes(callback_id)
            backend2frontend.FrontEndShowNimbleWindow(nimble.export_window_payload(callback_id))
            await asyncio.sleep(lifespan)
            try:
                await nimble.finalize_close(callback_id, reason='expired')
            except Exception:
                pass

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(run_async(), loop)
            else:
                loop.run_until_complete(run_async())
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop_policy().get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(run_async(), loop)
                else:
                    loop.run_until_complete(run_async())
            except Exception:
                asyncio.run(run_async())

    async def _execute_rule(self, rule: dict[str, Any], context: dict[str, Any]) -> None:
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
                await self.ctx.trigger_create({
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
        """条件匹配 + 可选 probability 概率门控。

        condition.probability（0~1）存在时，条件命中后还要过一次概率：
        probability=0.2 表示命中后只有 20% 概率真正触发。缺省/非法值视为必然触发。
        """
        if not self._match_condition(rule, context, last_idle_state, next_idle_state):
            return False
        probability = (rule.get('condition') or {}).get('probability')
        if probability is None:
            return True
        try:
            p = float(probability)
        except (TypeError, ValueError):
            return True
        return random.random() < max(0.0, min(1.0, p))

    def _match_condition(self, rule: dict[str, Any], context: dict[str, Any], last_idle_state: str, next_idle_state: str) -> bool:
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
            # 播放状态边沿：只在 非Playing -> Playing 时触发一次（由 heartbeat 计算 _smtc_rising_edge）
            return self._smtc_rising_edge
        return False

    async def heartbeat(self, ctx: PluginContext) -> None:
        if self.store is None or self.ctx is None:
            return
        context = await self.collect_context()
        # SMTC 播放状态边沿检测：状态名判定（数字 status 无法判断播放），
        # 只在 非Playing -> Playing 变化时置位一次，供 smtc_playing 规则使用
        smtc = context.get('smtc') or {}
        playing_now = str(smtc.get('status_name') or '').lower() == 'playing'
        self._smtc_rising_edge = playing_now and self._last_smtc_playing is False
        self._last_smtc_playing = playing_now
        self.store.update_snapshot(context)
        await self.ctx.vfs_write('/plugins/desktop-context.json', json.dumps(context, ensure_ascii=False, indent=2))
        idle_seconds = int(context.get('idle_seconds') or 0)
        last_idle_state = self.store.get_last_idle_state()
        next_idle_state = 'idle' if idle_seconds >= 600 else 'active'
        self.store.set_last_idle_state(next_idle_state)
        global_cooldown = int(await self.ctx.get_config('GLOBAL_COOLDOWN_SEC', 180) or 180)
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
            await self._execute_rule(rule, context)
            self.store.touch_rule_fire(rule_id)
            break

    def health_check(self) -> dict | None:
        snapshot = self.store.snapshot() if self.store else {}
        return {"status": "ok", "plugin": "desktop-mood", "rules": len(snapshot.get('rules', []))}


def get_plugin() -> Plugin:
    return Plugin()
