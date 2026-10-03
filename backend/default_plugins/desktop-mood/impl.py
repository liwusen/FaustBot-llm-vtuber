from __future__ import annotations

import asyncio
import ctypes
import json
import random
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

import faust_backend.backend2front as backend2frontend
from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl

from faust_backend.logger import get_logger
log = get_logger("faust.plugins.desktop-mood")

# 插件是以文件方式加载的（模块名 faust_plugin_desktop-mood），加载器不会把插件目录加进
# sys.path；同仓库 agile-engine 也是这么自举的。不这样处理，同目录的 dm_* 模块 import 不到。
_PLUGIN_DIR = Path(__file__).resolve().parent
if str(_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_DIR))

import dm_api  # noqa: E402
import dm_attach  # noqa: E402
import dm_conditions  # noqa: E402
import dm_external  # noqa: E402
import dm_sensors_a  # noqa: E402
import dm_sources  # noqa: E402
import dm_timeline  # noqa: E402
import dm_vfs  # noqa: E402

try:
    import dm_sensors_b  # noqa: E402
except Exception as exc:  # noqa: BLE001  模块级错误必须暴露，不能静默少一组感知
    log.error("desktop-mood 感知模块 dm_sensors_b 加载失败: %s", exc)
    raise

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
RHYTHM_NODE_PATH = dm_vfs.RHYTHM_PATH                  # 天级节律档案（agent 可读）

# ── 即时附加（attach）跨重启状态：都放在 store 的 kv 命名空间里 ──
ATTACH_QUEUE_KV = 'attach.queue'
ATTACH_PUSHED_KV = 'attach.pushed'
ATTACH_RING_KV = 'attach.ring'
ATTACH_LAST_KV = 'attach.last'
ATTACH_ERROR_KV = 'attach.error'
ATTACH_MAX_QUEUE = 20
ATTACH_CONFIG_KEYS = ('ENABLE_ATTACH', 'ENABLE_AUTO_ATTACH', 'ATTACH_BUDGET')
# 只有这两类入口允许随行附加：后台触发器/无前端连接时的降级执行不附加
ATTACH_ORIGINS = ('user', 'trigger_foreground')

# 感知模块按顺序执行：时间线在最后，才能看到本轮所有其它字段
SENSOR_MODULES = (dm_sensors_a, dm_sensors_b, dm_external, dm_timeline)
REGISTRY = dm_sources.build_registry(SENSOR_MODULES)

CONDITION_TYPES = dm_conditions.CONDITION_TYPES
ACTION_KINDS = ('motion', 'speech', 'nimble', 'event-trigger', 'emotion', 'attach')

CLIPBOARD_CHANGE_DEBOUNCE_SEC = 15

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


CF_UNICODETEXT = 13
CF_DIB = 8
CF_HDROP = 15

ERROR_MARKERS = ('traceback (most recent call last)', 'unhandled exception', '\n    at ')
CODE_MARKERS = (
    'def ', 'class ', 'import ', 'function ', 'const ', 'let ', 'return ',
    '=>', '});', '#!/', 'public ', 'private ', '</div>',
)


def _clipboard_sequence() -> int | None:
    """剪贴板序号：变了就说明内容变了。纯 ctypes，比读内容便宜得多。"""
    try:
        return int(ctypes.windll.user32.GetClipboardSequenceNumber())
    except Exception:
        return None


def _clipboard_formats() -> set[int]:
    """当前剪贴板有哪些格式（不读取内容，仅判断格式存在性）。"""
    formats: set[int] = set()
    try:
        user32 = ctypes.windll.user32
        for fmt in (CF_UNICODETEXT, CF_DIB, CF_HDROP):
            if user32.IsClipboardFormatAvailable(fmt):
                formats.add(fmt)
    except Exception:
        pass
    return formats


def classify_clipboard_text(text: str) -> str:
    """把剪贴板文本判成类型。只返回类型标签，正文由调用方丢弃。"""
    body = str(text or '').strip()
    if not body:
        return 'empty'
    lowered = body.lower()
    lines = [line for line in body.splitlines() if line.strip()]
    if not lines:
        return 'empty'
    if len(lines) == 1 and ' ' not in body and lowered.startswith(('http://', 'https://', 'www.')):
        return 'url'
    if any(marker in lowered for marker in ERROR_MARKERS):
        return 'error'
    if re.search(r'(?m)^[A-Za-z_.]*(Error|Exception|Warning):', body):
        return 'error'
    hits = sum(1 for marker in CODE_MARKERS if marker in body)
    if len(lines) > 1 and hits >= 2:
        return 'code'
    if body.endswith((';', '}', ');', '{')) and any(ch in body for ch in ('=', '(', '{')):
        return 'code'
    return 'text'


async def _read_clipboard_text() -> str:
    """读一次剪贴板文本（pyperclip 优先，否则 powershell），仅供判类型。"""
    try:
        import pyperclip  # type: ignore
    except ImportError:
        pyperclip = None  # type: ignore[assignment]
    if pyperclip is not None:
        try:
            return str(await asyncio.to_thread(pyperclip.paste) or '')
        except Exception:
            log.warning("通过 pyperclip 读取剪贴板失败，回退 powershell")
    try:
        proc = await asyncio.create_subprocess_exec(
            'powershell', '-NoProfile', '-Command', 'Get-Clipboard -Raw',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return stdout.decode('utf-8', errors='ignore')
    except Exception as exc:  # noqa: BLE001
        log.warning("读取剪贴板失败: %s", exc)
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


# 免打扰闸门：这些进程在前台/占用麦克风时，规则里的 speech/nimble 默认不放行
MEETING_PROCESS_HINTS = {
    'zoom.exe', 'teams.exe', 'ms-teams.exe', 'webexmta.exe', 'dingtalk.exe',
    'wemeetapp.exe', 'voov.exe', 'skype.exe', 'discord.exe',
}


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
            self._state['snapshot_at'] = _now()
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

    # ── 通用 KV：给感知模块（时间线/外部上报）存放跨重启状态 ──
    def get_kv(self, key: str, default: Any = None) -> Any:
        with self._lock:
            namespace = self._state.get('kv')
            if not isinstance(namespace, dict) or key not in namespace:
                return default
            return json.loads(json.dumps(namespace[key], ensure_ascii=False))

    def set_kv(self, key: str, value: Any) -> None:
        with self._lock:
            namespace = self._state.setdefault('kv', {})
            namespace[key] = value
            self.save()

    def set_last_idle_state(self, state: str) -> None:
        with self._lock:
            self._state['last_idle_state'] = state
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


class Plugin(FaustPlugin):
    def __init__(self):
        self.ctx: PluginContext | None = None
        self.store: DesktopMoodStore | None = None
        self.registry = REGISTRY
        # SMTC 播放状态边沿检测：只在 非Playing -> Playing 时触发一次
        self._last_smtc_playing: bool | None = None
        self._smtc_rising_edge = False
        # 规则草稿是否有未提交改动
        self._draft_dirty = False
        # 感知分级：最近一次采集时各级/各源是否开启（写入上下文与面板）
        self._tier_flags: dict[str, bool] = {
            tier['id']: bool(tier['default']) for tier in dm_sources.PERCEPTION_TIERS}
        self._source_flags: dict[str, bool] = {
            source['id']: bool(source['default']) for source in self.registry.sources}
        # 感知模块运行态：上一轮字段、各源上次采样时间、模块私有 memory
        self._last_fields: dict[str, Any] = {}
        self._source_last_run: dict[str, float] = {}
        self._sensor_memory: dict[str, Any] = {}
        self._sensor_status: dict[str, str] = {}
        # 红色级剪贴板：序号变化才读一次判类型，正文不留存
        self._clipboard_seq: int | None = None
        self._clipboard_info: dict[str, Any] | None = None
        # 规则引擎：for_seconds 的持续计时
        self._condition_since: dict[str, float] = {}

    async def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        data_dir = ctx.plugin_data_dir or (ctx.plugin_dir / 'data')
        self.store = DesktopMoodStore(data_dir)
        config_schema = [
            {"key": "GLOBAL_COOLDOWN_SEC", "type": "int", "label": "全局冷却（秒）", "default": 180},
            {"key": "WEATHER_CITY", "type": "str", "label": "天气城市", "default": 'auto'},
            {"key": "CODE_WATCH_DIR", "type": "str", "label": "编码活动监视目录（git 仓库）", "default": ''},
            {"key": "EMOTION_FALLBACK", "type": "str", "label": "emotion 动作兜底情绪名", "default": 'neutral'},
            {"key": "ENABLE_ATTACH", "type": "bool", "label": "即时附加（随用户消息附带桌面信息）", "default": True},
            {"key": "ENABLE_AUTO_ATTACH", "type": "bool", "label": "即时附加：启发式自动挑选变化过的信息", "default": True},
            {"key": "ATTACH_BUDGET", "type": "int", "label": "即时附加字数上限", "default": dm_attach.BUDGET_DEFAULT},
        ]
        for tier in dm_sources.PERCEPTION_TIERS:
            config_schema.append({
                "key": dm_sources.TIER_CONFIG_KEY.format(tier['id'].upper()),
                "type": "bool",
                "label": f"感知分级：{tier['label']}",
                "default": tier['default'],
            })
        for source in self.registry.sources:
            config_schema.append({
                "key": source['key'],
                "type": "bool",
                "label": f"感知源：{source['label']}（{source['tier']}）",
                "default": source['default'],
            })
        await ctx.register_config(config_schema)
        await ctx.vfs_write(
            "/plugins/desktop-mood.md",
            "# Desktop Mood\n\n"
            "Desktop Mood 持续把桌面环境按数据域写进 faustbot://desktop-mood/：每个域一份 <域>.json"
            "（精确值/源状态/采样间隔）与一份 <域>.md（可读行），总览见 faustbot://desktop-mood/overview.md，"
            "要最新数据就 read/write faustbot://desktop-mood/refresh。\n"
            "域划分：system（负载/电量/空闲/节日/显卡/进程增减/显示器与供电）、window（前台进程与标题/窗口几何/应用停留/"
            "未保存文档/编码活动/桌面新文件）、input（鼠标/手柄/USB/外设电量）、media（媒体播放/音频输出/麦克风）、"
            "network（联网类型/SSID）、weather（天气/环境光）、narrative（时间线/场景摘要/离开期间/注意力/节律）、"
            "external（外部上报/日历）、privacy（剪贴板类型）。\n"
            "字段分三级感知：green（本机元数据）、yellow（文本与联网：窗口标题/曲名/文件名/天气）、"
            "red（屏幕内容：目前只有剪贴板类型判定，默认关闭）。被关闭的源不会出现在任何视图里，"
            "依赖它的规则也不会触发；读 overview.md 看得到「未启用」与「不可用：原因」。\n"
            "narrative 域还有：narrative（一句话场景摘要）、recent_events（最近场景变化）、"
            "away_digest（用户离开期间发生的事）、attention/activity_level（心流/碎片、平静/激烈）、rhythm_today"
            "（今日节律，7 天档案见 faustbot://desktop-mood/narrative/rhythm.md）。\n"
            "想根据用户环境主动提醒、播报、关心用户时，先读 faustbot://desktop-mood/overview.md；判断「用户现在忙不忙」用 "
            "attention 与 context.disturbed（免打扰闸门：全屏/会议/锁屏）。\n"
            "规则（自动触发动作）的编辑入口见 faustbot://plugins/desktop-mood/rules.md"
            "，改规则必须走「编辑 rules.json 草稿 → 读/写 reload 节点提交」。kind=attach 的规则不说话、不弹窗，"
            "只把文本暂存进随行附加队列，等用户开口（或前台触发器）时一起送进模型，总长 ≤100 字且只放变化过的内容。\n",
            description="Desktop Mood 插件说明：数据域视图、感知分级与规则编辑入口",
        )
        await dm_vfs.install(ctx, self)
        await self._install_rule_nodes(ctx)

    async def _install_rule_nodes(self, ctx: PluginContext) -> None:
        """注册规则草稿节点、指南节点、提交节点。

        - rules.json：草稿内容节点，write/edit handler 只暂存原文（不校验、不落盘）；
        - rules.md：symbolic 指南（schema + 工作流）；
        - reload：symbolic 节点，read 即提交草稿；write 也提交（内容被忽略）。
        每次插件启动无条件重播种草稿，丢弃上一次未提交的改动。
        """
        rules = self.store.snapshot().get('rules', []) if self.store is not None else []
        await ctx.vfs_write(
            RULES_NODE_PATH,
            json.dumps(rules, ensure_ascii=False, indent=2),
            description="桌面心情规则草稿：只改这里，读/写 reload 节点才生效",
        )
        await ctx.vfs_set_write_handler(RULES_NODE_PATH, self._on_rules_draft_write)
        await ctx.vfs_set_edit_handler(RULES_NODE_PATH, self._on_rules_draft_write)
        await ctx.vfs_write_symbolic(
            RULES_GUIDE_PATH,
            lambda _path: self._rules_guide_text(),
            should_be_included_in_search=False,
            description="规则编辑指南：条件/动作 schema 与提交流程",
        )
        await ctx.vfs_write_symbolic(
            RULES_RELOAD_PATH,
            self._on_reload_read,
            should_be_included_in_search=False,
            description="提交桌面心情规则草稿（read/write 均触发生效）",
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
        off = [sid for sid, on in (self._source_flags or {}).items() if not on]
        disabled = ', '.join(off) if off else '无'
        tiers = ', '.join(f"{tid}={'on' if on else 'off'}" for tid, on in (self._tier_flags or {}).items())
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
            f'感知分级: {tiers or "未采集"}；被关闭的感知源: {disabled}',
            '  依赖被关闭字段的条件（如窗口标题、媒体、天气、剪贴板、麦克风、显示器）永远不会命中——',
            '  写规则前先读 faustbot://desktop-mood/overview.md（细则在 <域>/<域>.json）确认字段真的存在，',
            '  或让用户在面板「感知引擎」里打开对应分级。',
            '',
            '感知数据域: faustbot://desktop-mood/<域>/<域>.{json,md}（域：system/window/input/media/network/',
            '  weather/narrative/external/privacy）；总览 overview.md；read/write refresh 立即重采。',
            '',
            '可用字段（部分）: idle_seconds, cpu, memory, battery.percent, hour, window_title, window_process.name,',
            '  window_fullscreen, app_session.seconds, context_switches_5min, mouse.velocity_px_s, mouse.jitter,',
            '  unsaved_docs, window_dynamic, gpu.temp_c, process_events.started, desktop_new_files,',
            '  display_status, mic.in_use, network.type, network.ssid, gamepad_connected, usb_devices.added,',
            '  smtc.title, locked, display_count, weather.text, calendar.work_hours, external.<source>.<field>,',
            '  narrative, recent_events, away_digest.items, attention, activity_level, rhythm_today.focus_minutes。',
            '通用条件: field_over / field_under / field_eq / field_contains / field_in / field_changed（field 支持',
            '  battery.percent、app_session.seconds 这类点号路径），可加 for_seconds（持续 N 秒才触发）、',
            '  probability（0~1）、when_not_disturbed（免打扰时不触发）。',
            '动作 kind: motion / speech / nimble / event-trigger / emotion / attach（emotion 直接改桌宠表情，如',
            '  {"kind":"emotion","action":{"emotion":"happy","intensity":0.7}}）。',
            'attach 动作: 命中时不说话、不弹窗、不打断，只把文本暂存进随行附加队列（TTL 默认 1800 秒，队列上限 20），',
            '  在下一条用户消息或前台触发器上随行送达（后台触发器不附加）；总长 ≤100 字、最多 6 行、只放相对上次',
            '  变化过的内容，最近 20 条不重复；不受免打扰闸门影响（它不是打扰）。文本支持 {battery} 等模板占位。',
            '  例：{"kind":"attach","action":{"attach":{"text":"电量只剩 {battery}%，记得插电","ttl_sec":1800}}}。',
            '  引擎还会启发式自动附加"AI 可能需要的"变化（事件优先、字段按 attach_weight 补），规则命中时一并跑。',
            '免打扰闸门: 全屏、麦克风占用、会议软件前台、锁屏时，speech/nimble/emotion 默认不发；',
            '  需要强行放行就加 action.bypass_disturb=true。调度上只受 rule.cooldown_sec 与 GLOBAL_COOLDOWN_SEC 限制。',
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
            condition_error = dm_conditions.validate(condition)
            if condition_error is not None:
                return None, f'{where} ({rule_id}): {condition_error}'
            action = rule.get('action')
            if not isinstance(action, dict):
                return None, f'{where} ({rule_id}): 缺少 action 对象'
            if kind == 'motion' and not str(action.get('motion') or '').strip():
                return None, f'{where} ({rule_id}): kind=motion 需要 action.motion'
            if kind == 'speech' and not str(action.get('speech') or '').strip():
                return None, f'{where} ({rule_id}): kind=speech 需要 action.speech'
            if kind == 'nimble' and not str(action.get('note') or '').strip():
                return None, f'{where} ({rule_id}): kind=nimble 需要 action.note'
            if kind == 'emotion' and not str(action.get('emotion') or '').strip():
                return None, f'{where} ({rule_id}): kind=emotion 需要 action.emotion（如 happy/sad/angry/surprised）'
            if kind == 'event-trigger' and not str(action.get('event_name') or '').strip():
                return None, f'{where} ({rule_id}): kind=event-trigger 需要 action.event_name'
            if kind == 'attach':
                text = action.get('attach')
                if isinstance(text, dict):
                    text = text.get('text')
                if not str(text or '').strip():
                    return None, (f'{where} ({rule_id}): kind=attach 需要 action.attach，'
                                  '可以是字符串，也可以是 {"text": "...", "ttl_sec": 1800, "priority": 50}')
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
            "桌面环境按数据域暴露在 faustbot://desktop-mood/：总览 faustbot://desktop-mood/overview.md（每个源一行，最省 token），"
            "细节读 <域>/<域>.md，精确值与源状态读 <域>/<域>.json；域有 system/window/input/media/network/weather/"
            "narrative/external/privacy；要最新数据就 read/write faustbot://desktop-mood/refresh。"
            "使用指南在 faustbot://plugins/desktop-mood.md。在用户主动发起对话时，你应该(SHOULD)读取这些内容。\n"
            "域里有：窗口/进程/全屏、应用停留与切换频率、鼠标节奏、未保存文档、进程增减、显卡、显示器与电源、"
            "麦克风占用、网络/SSID、耳机与手柄、USB 外设、媒体播放、天气、外部上报，以及叙事层 "
            "narrative（一句话场景摘要）、recent_events（最近变化）、away_digest（你不在时发生的事）、"
            "attention/activity_level（心流/碎片、平静/激烈）、rhythm_today（今日节律，档案见 "
            "faustbot://desktop-mood/narrative/rhythm.md）。\n"
            "判断「现在能不能打扰用户」看 overview.md 里的免打扰行（全屏/会议/锁屏）；被关闭的源标「未启用」、"
            "采集失败的源标「不可用：原因」，不要臆测，也不要把规则建在它们上面。\n"
            "用户消息末尾可能带一行 [桌面] 即时摘要（≤100 字，只含相对上次发生变化的内容，由 kind=attach 规则与"
            "启发式自动附上）；它只是提示，需要细节时仍以 faustbot://desktop-mood/ 为准。\n"
            "当用户要求新增/修改/关闭桌面自动规则时：edit faustbot://plugins/desktop-mood/rules.json 草稿，"
            "再 read faustbot://plugins/desktop-mood/reload 提交生效；规则支持通用字段条件"
            "（field_over/field_under/field_contains/field_changed + for_seconds/probability/when_not_disturbed）、"
            "emotion 动作，以及 attach 动作（不说话，只把文本暂存，随下一条用户消息或前台触发器送达）；"
            "schema 与工作流见 skill://desktop-mood-rules/SKILL.md 或 faustbot://plugins/desktop-mood/rules.md。\n"
        ]

    # ── 即时附加（attach）：规则暂存 → 随用户消息/前台触发器附带 ──
    def _enqueue_attach(self, rule: dict[str, Any], context: dict[str, Any]) -> None:
        """kind=attach 命中：只暂存文本，不发声、不弹窗、不打断。"""
        if self.store is None:
            return
        raw = (rule.get('action') or {}).get('attach')
        options = raw if isinstance(raw, dict) else {}
        text = self._render_template(str((raw.get('text') if isinstance(raw, dict) else raw) or ''), context).strip()
        if not text:
            log.warning('desktop-mood 规则 %s 的 attach 文本为空，已忽略', rule.get('id'))
            return
        try:
            ttl = max(30, int(options.get('ttl_sec') or 1800))
        except (TypeError, ValueError):
            ttl = 1800
        try:
            priority = max(0, min(100, int(options.get('priority') or dm_attach.RULE_PRIORITY_DEFAULT)))
        except (TypeError, ValueError):
            priority = dm_attach.RULE_PRIORITY_DEFAULT
        now = _now()
        stored = self.store.get_kv(ATTACH_QUEUE_KV, [])
        queue = [item for item in stored if isinstance(item, dict)][-ATTACH_MAX_QUEUE:]
        if len(queue) >= ATTACH_MAX_QUEUE:
            dropped = queue.pop(0)
            log.warning('desktop-mood 附加队列已满，丢弃最旧一条: %s', str(dropped.get('text'))[:40])
        queue.append({'text': text, 'rule': str(rule.get('id') or ''), 'at': now,
                      'expires_at': now + ttl, 'priority': priority})
        self.store.set_kv(ATTACH_QUEUE_KV, queue)
        log.info('desktop-mood attach 入队 %d 条: %s', len(queue), text[:60])

    async def _compose_attach_block(self) -> str:
        """规则暂存文本 + 启发式变化信息 → 预算内的附加块；没有可说的一次性返回空串。

        预算与去重的实现全在 dm_attach（纯函数，可单测）；这里只管读写 store 与配置。
        """
        if self.store is None or self.ctx is None:
            return ''
        if not bool(await self.ctx.get_config('ENABLE_ATTACH', True)):
            return ''
        try:
            budget = int(await self.ctx.get_config('ATTACH_BUDGET', dm_attach.BUDGET_DEFAULT) or dm_attach.BUDGET_DEFAULT)
        except (TypeError, ValueError):
            budget = dm_attach.BUDGET_DEFAULT
        auto = bool(await self.ctx.get_config('ENABLE_AUTO_ATTACH', True))
        now = _now()
        observed = self.store.snapshot().get('snapshot') or {}
        queue, expired = dm_attach.prune_queue(self.store.get_kv(ATTACH_QUEUE_KV, []), now)
        if expired:
            log.info('desktop-mood 附加队列丢弃 %d 条过期内容: %s', len(expired),
                     '；'.join(str(item.get('text'))[:30] for item in expired))
        ring_entries = self.store.get_kv(ATTACH_RING_KV, [])
        ring_entries = ring_entries if isinstance(ring_entries, list) else []
        ring = [str(entry.get('hash') or '') for entry in ring_entries if isinstance(entry, dict)]
        pushed = self.store.get_kv(ATTACH_PUSHED_KV, {})
        pushed = pushed if isinstance(pushed, dict) else {}
        last = self.store.get_kv(ATTACH_LAST_KV, {})
        last = last if isinstance(last, dict) else {}

        candidates = list(dm_attach.rule_candidates(queue, ring))
        if auto:
            candidates += dm_attach.event_candidates(observed.get('recent_events') or [],
                                                     int(last.get('at') or 0), ring)
            _tiers, enabled_map = await self._perception_flags()
            enabled_sources = [source for source in self.registry.sources if enabled_map.get(source['id'])]
            candidates += dm_attach.field_candidates(self.registry, enabled_sources, observed, pushed, ring)

        if not candidates:
            self.store.set_kv(ATTACH_QUEUE_KV, queue)
            return ''
        block, chosen = dm_attach.compose(candidates, budget)
        consumed_rules = {item.key for item in chosen if item.kind == 'rule'}
        remaining = [item for item in queue
                     if f"rule:{dm_attach.fingerprint(str(item.get('text') or '').strip())}" not in consumed_rules]
        self.store.set_kv(ATTACH_QUEUE_KV, remaining)
        if not block:
            # 预算装不下任何一条（例如单条规则文本被截断后仍放不下）：队列保留，等下次
            return ''
        for item in chosen:
            if item.kind == 'field':
                pushed[item.key] = item.text
        self.store.set_kv(ATTACH_PUSHED_KV, pushed)
        self.store.set_kv(ATTACH_RING_KV, dm_attach.update_ring(ring_entries, [item.text for item in chosen], now))
        self.store.set_kv(ATTACH_LAST_KV, {'at': now, 'text': block,
                                           'sent_total': int(last.get('sent_total') or 0) + 1})
        self.store.set_kv(ATTACH_ERROR_KV, None)
        log.info('desktop-mood attach 送出 %d 条 / %d 字: %s', len(chosen), len(block), block)
        return block

    @hookimpl
    async def message_received(self, msg: Any, history: list, ctx: Any, origin: str = 'user') -> str | None:
        """把暂存的规则文本与"变化过的桌面信息"附到这条消息后面。

        只对用户消息与前台触发器生效（后台触发器/无前端连接时的降级执行不附加）；
        这里绝不分析 msg 的内容——附加内容只取决于桌面状态与上次附加的基线。
        """
        if str(origin or 'user') not in ATTACH_ORIGINS:
            return None
        try:
            block = await self._compose_attach_block()
        except Exception as exc:  # noqa: BLE001 附加失败不能拖垮这一轮对话，但必须留下痕迹
            log.warning('desktop-mood 附加失败: %s', exc)
            if self.store is not None:
                self.store.set_kv(ATTACH_ERROR_KV, {'at': _now(), 'detail': str(exc)})
            return None
        if not block:
            return None
        return f'{msg}\n\n{block}'

    def _attach_view(self) -> dict[str, Any]:
        """面板/报告用的附加状态（不触发采集）。"""
        snapshot = self.store.snapshot() if self.store is not None else {}
        queue, expired = dm_attach.prune_queue(self.store.get_kv(ATTACH_QUEUE_KV, []) if self.store else [], _now())
        last = self.store.get_kv(ATTACH_LAST_KV, {}) if self.store else {}
        last = last if isinstance(last, dict) else {}
        error = self.store.get_kv(ATTACH_ERROR_KV, None) if self.store else None
        return {
            'queued': len(queue),
            'expired': len(expired),
            'last_text': last.get('text'),
            'last_at': int(last.get('at') or 0) or None,
            'sent_total': int(last.get('sent_total') or 0),
            'last_error': (error or {}).get('detail') if isinstance(error, dict) else None,
            'snapshot_at': int(snapshot.get('snapshot_at') or 0) or None,
        }

    async def refresh_now(self) -> str:
        """立即采集一次并写入快照（VFS refresh 节点与面板「立即采集」同一条通路）。"""
        if self.store is None or self.ctx is None:
            return '插件未加载，无法采集。'
        context = await self.collect_context()
        self.store.update_snapshot(context)
        await self._write_rhythm_node(context)
        _tiers, enabled_map = await self._perception_flags()
        enabled = [source['id'] for source in self.registry.sources if enabled_map.get(source['id'])]
        lines = [f'已启用源 {len(enabled)}/{len(self.registry.sources)} 个，本轮上下文 {len(context)} 个字段。']
        unavailable = [f'{source_id}: {reason}' for source_id, reason in self._sensor_status.items() if reason]
        if unavailable:
            lines.append('不可用: ' + '；'.join(unavailable[:6]))
        return '\n'.join(lines)

    async def _perception_flags(self) -> tuple[dict[str, bool], dict[str, bool]]:
        """返回 (分级开关, 感知源开关)；源开启 = 所属分级开启 且 该源开关开启。"""
        if self.ctx is None:
            tiers = {tier['id']: bool(tier['default']) for tier in dm_sources.PERCEPTION_TIERS}
            sources = {source['id']: bool(source['default']) for source in self.registry.sources}
        else:
            tiers = {}
            for tier in dm_sources.PERCEPTION_TIERS:
                tiers[tier['id']] = bool(await self.ctx.get_config(
                    dm_sources.TIER_CONFIG_KEY.format(tier['id'].upper()), tier['default']))
            sources = {}
            for source in self.registry.sources:
                own = bool(await self.ctx.get_config(source['key'], source['default']))
                sources[source['id']] = own and tiers.get(source['tier'], False)
        self._tier_flags = tiers
        self._source_flags = sources
        return tiers, sources

    def _due_sources(self, now: float) -> dict[str, bool]:
        """按每个源的 cadence 判断本轮是否到采样时间。"""
        due: dict[str, bool] = {}
        for source in self.registry.sources:
            last = float(self._source_last_run.get(source['id']) or 0.0)
            if last and now - last < self.registry.cadence_of(source):
                due[source['id']] = False
                continue
            due[source['id']] = True
            self._source_last_run[source['id']] = now
        return due

    async def _collect_base(self, context: dict[str, Any], sources: dict[str, bool],
                            due: dict[str, bool]) -> None:
        """基础源（collector 留在本文件）：系统负载/电池/空闲/窗口/节日/媒体/剪贴板。"""
        def add(source_id: str, field: str, value: Any) -> None:
            if sources.get(source_id) and due.get(source_id, True):
                context[field] = value

        if sources.get('system_load') and psutil is not None and due.get('system_load', True):
            try:
                context['cpu'] = float(psutil.cpu_percent(interval=None))
            except Exception:
                context['cpu'] = None
            try:
                context['memory'] = float(psutil.virtual_memory().percent)
            except Exception:
                context['memory'] = None
            try:
                disk = psutil.disk_io_counters()
                if disk is not None:
                    context['disk_io'] = {'read_bytes': int(disk.read_bytes), 'write_bytes': int(disk.write_bytes)}
            except Exception:
                pass
            try:
                usage = psutil.disk_usage(str(Path.home().anchor or '/'))
                context['disk_free'] = {'percent': float(usage.percent), 'free_bytes': int(usage.free)}
            except Exception:
                pass
        if sources.get('battery') and psutil is not None and due.get('battery', True):
            battery_percent = None
            charging = None
            minutes_left = None
            try:
                battery = psutil.sensors_battery()
                if battery is not None:
                    battery_percent = float(battery.percent)
                    charging = bool(battery.power_plugged)
                    if battery.secsleft not in (psutil.POWER_TIME_UNKNOWN, psutil.POWER_TIME_UNLIMITED):
                        minutes_left = max(0, int(battery.secsleft) // 60)
            except Exception:
                pass
            context['battery'] = {'percent': battery_percent, 'charging': charging, 'minutes_left': minutes_left}
        if sources.get('idle') and due.get('idle', True):
            context['idle_seconds'] = _windows_idle_seconds()
        if sources.get('window_process') and due.get('window_process', True):
            context['window_process'] = _foreground_window_process()
        if sources.get('window_title') and due.get('window_title', True):
            context['window_title'] = _foreground_window_title()
        if sources.get('holiday') and due.get('holiday', True):
            context['holiday'] = _holiday_name()
        if sources.get('smtc') and due.get('smtc', True):
            smtc = None
            try:
                smtc = await _read_smtc_now()
            except Exception:
                smtc = None
            context['smtc'] = smtc
        if sources.get('clipboard') and due.get('clipboard', True):
            try:
                context['clipboard'] = await self._collect_clipboard()
            except Exception as exc:  # noqa: BLE001
                log.warning('剪贴板类型判定失败: %s', exc)
                self._sensor_status['clipboard'] = f'判定失败: {exc}'
        del add

    async def collect_context(self) -> dict[str, Any]:
        tiers, sources = await self._perception_flags()
        now = time.time()
        due = self._due_sources(now)
        context: dict[str, Any] = {
            'hour': time.localtime().tm_hour,
            'manual_mood': self.store.snapshot().get('manual_mood') if self.store is not None else 'auto',
            'perception': {
                'tiers': dict(tiers),
                'disabled_sources': [sid for sid, enabled in sources.items() if not enabled],
            },
        }
        status: dict[str, str] = {}
        await self._collect_base(context, sources, due)

        sctx = dm_api.SensorContext(
            now=now,
            observed=self._last_fields,
            context=context,
            memory=self._sensor_memory,
            external=context.get('external') or {},
            store=self.store,
            enabled=lambda source_id: bool(sources.get(source_id)),
            log=log,
            get_config=self.ctx.get_config if self.ctx is not None else None,
            due=lambda source_id: bool(due.get(source_id, True)) and bool(sources.get(source_id)),
        )
        for module in SENSOR_MODULES:
            module_sources = [source for source in self.registry.sources
                              if any(source['id'] == item['id'] for item in getattr(module, 'SOURCES', ()))]
            if not any(sources.get(source['id']) and due.get(source['id'], True) for source in module_sources):
                continue
            try:
                result = await module.collect(sctx)
            except Exception as exc:  # noqa: BLE001  模块整体失败也要显式记录
                log.warning('%s 采集失败: %s', module.__name__, exc)
                for source in module_sources:
                    status[source['id']] = f'模块采集失败: {exc}'
                continue
            for field_name, value in result.fields.items():
                if self._field_source_enabled(field_name, sources, due):
                    context[field_name] = value
            status.update(result.status)

        # cadence 未到或本轮未采的源：沿用上一轮的值，避免条件在两次采样之间抖动
        for source in self.registry.sources:
            if not sources.get(source['id']):
                continue
            for field_name in self.registry.fields_of(source):
                if field_name not in context and field_name in self._last_fields:
                    context[field_name] = self._last_fields[field_name]

        context['disturb_reasons'] = self._disturb_reasons(context)
        context['disturbed'] = bool(context['disturb_reasons'])
        self._sensor_status = status
        self._last_fields = {key: value for key, value in context.items()
                             if not key.startswith('_') and key not in ('perception', 'disturb_reasons')}
        return context

    def _field_source_enabled(self, field_name: str, sources: dict[str, bool],
                              due: dict[str, bool]) -> bool:
        for source in self.registry.sources:
            if field_name in self.registry.fields_of(source):
                return bool(sources.get(source['id'])) and bool(due.get(source['id'], True))
        return True

    def _disturb_reasons(self, context: dict[str, Any]) -> list[str]:
        reasons: list[str] = []
        if context.get('window_fullscreen'):
            reasons.append('全屏中')
        if (context.get('mic') or {}).get('in_use'):
            reasons.append('麦克风占用（会议/语音）')
        if context.get('locked'):
            reasons.append('屏幕已锁定')
        process_name = str((context.get('window_process') or {}).get('name') or '').lower()
        if process_name in MEETING_PROCESS_HINTS:
            reasons.append(f'会议软件前台（{process_name}）')
        return reasons

    async def _collect_clipboard(self) -> dict[str, Any] | None:
        """红色级：按剪贴板序号变化读一次判类型；正文用后即弃，不落任何存储。"""
        seq = _clipboard_sequence()
        if seq is None:
            return self._clipboard_info
        if seq == self._clipboard_seq and self._clipboard_info is not None:
            return self._clipboard_info
        if self._clipboard_info is not None and _now() - int(self._clipboard_info.get('changed_at') or 0) < CLIPBOARD_CHANGE_DEBOUNCE_SEC:
            # 节流：不更新序号，等下一轮心跳再判，避免高频复制时反复起进程
            return self._clipboard_info
        formats = _clipboard_formats()
        self._clipboard_seq = seq
        info: dict[str, Any] = {'changed_at': _now()}
        if CF_DIB in formats:
            info['kind'] = 'image'
        elif CF_HDROP in formats:
            info['kind'] = 'files'
        elif CF_UNICODETEXT in formats:
            text = await _read_clipboard_text()  # 只此一次，用于判类型
            info['kind'] = classify_clipboard_text(text)
            info['length'] = len(text)
            del text
        else:
            info['kind'] = 'empty'
        self._clipboard_info = info
        return info

    async def perception_report(self) -> dict[str, Any]:
        """面板数据视图：分级/源的开关状态 + 各源最近采集到的字段值 + 时间线/免打扰状态。"""
        tiers, sources = await self._perception_flags()
        snapshot = self.store.snapshot() if self.store is not None else {}
        observed = snapshot.get('snapshot') or {}
        sources_payload = []
        for source in self.registry.sources:
            own = bool(source['default']) if self.ctx is None else bool(
                await self.ctx.get_config(source['key'], source['default']))
            sources_payload.append({
                'id': source['id'],
                'key': source['key'],
                'tier': source['tier'],
                'group': self.registry.group_of(source),
                'label': source['label'],
                'note': source['note'],
                'cadence': self.registry.cadence_of(source),
                'enabled': own,
                'collecting': bool(sources.get(source['id'])),
                'status': self._sensor_status.get(source['id']),
                'fields': [
                    {'field': field, 'label': self.registry.labels.get(field, field),
                     'value': self.registry.format_field(field, observed)}
                    for field in self.registry.fields_of(source)
                ],
            })
        try:
            attach_budget = int(await self.ctx.get_config('ATTACH_BUDGET', dm_attach.BUDGET_DEFAULT)) if self.ctx else dm_attach.BUDGET_DEFAULT
        except (TypeError, ValueError):
            attach_budget = dm_attach.BUDGET_DEFAULT
        attach_payload = self._attach_view()
        attach_payload.update({
            'enabled': bool(await self.ctx.get_config('ENABLE_ATTACH', True)) if self.ctx else True,
            'auto': bool(await self.ctx.get_config('ENABLE_AUTO_ATTACH', True)) if self.ctx else True,
            'budget': attach_budget,
        })
        return {
            'updated_at': int(snapshot.get('snapshot_at') or 0),
            'tiers': [
                {'id': tier['id'], 'label': tier['label'], 'note': tier['note'],
                 'enabled': bool(tiers.get(tier['id']))}
                for tier in dm_sources.PERCEPTION_TIERS
            ],
            'sources': sources_payload,
            'narrative': observed.get('narrative'),
            'recent_events': observed.get('recent_events') or [],
            'away_digest': observed.get('away_digest'),
            'rhythm_today': observed.get('rhythm_today'),
            'attention': observed.get('attention'),
            'activity_level': observed.get('activity_level'),
            'disturbed': bool(observed.get('disturbed')),
            'disturb_reasons': observed.get('disturb_reasons') or [],
            'attach': attach_payload,
        }

    async def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        action = str((payload or {}).get('action') or '').strip().lower()
        if action == 'get_state':
            return {"status": "ok", "state": self.store.snapshot() if self.store is not None else {}}
        if action == 'get_context':
            # 读上一次心跳的采集结果：面板/浮窗轮询不应该触发整轮感知采集
            snapshot = self.store.snapshot() if self.store is not None else {}
            context = snapshot.get('snapshot')
            if not context:
                context = await self.collect_context()
            return {"status": "ok", "context": context}
        if action == 'get_perception':
            return {"status": "ok", "perception": await self.perception_report()}
        if action == 'get_rules':
            items = []
            for rule in (self.store.snapshot().get('rules', []) if self.store is not None else []):
                if not isinstance(rule, dict):
                    continue
                items.append({**rule, 'summary': dm_conditions.describe(rule.get('condition') or {})})
            return {"status": "ok", "items": items}
        if action == 'report':
            accepted, detail = dm_external.ingest(payload or {}, self.store)
            if not accepted:
                return {"status": "error", "detail": detail}
            return {"status": "ok", "detail": detail}
        if action == 'set_rules':
            items = (payload or {}).get('items')
            if self.store is None:
                return {"status": "error", "detail": 'plugin not loaded'}
            if not isinstance(items, list):
                return {"status": "error", "detail": 'items must be a list'}
            error = self._validate_rules(items)[1]
            if error is not None:
                return {"status": "error", "detail": error}
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
        session = context.get('app_session') or {}
        smtc = context.get('smtc') or {}
        fields = {
            'hour': context.get('hour'),
            'battery': int(battery) if battery is not None else '?',
            'idle': int(context.get('idle_seconds') or 0),
            'app': session.get('process') or (context.get('window_process') or {}).get('name') or '?',
            'window': str(context.get('window_title') or '')[:40],
            'media': smtc.get('title') or '',
            'attention': context.get('attention') or '',
            'rhythm_awake_minutes': (context.get('rhythm_today') or {}).get('awake_minutes') or 0,
        }
        try:
            return str(template or '').format(**fields)
        except (KeyError, IndexError, ValueError):
            return str(template or '')

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
        elif kind == 'emotion':
            # 感知 → 情绪闭环：直接驱动前端表演层（与 avatar-performance 的 setAvatarEmotion 同一条通路）
            fallback = 'neutral'
            if self.ctx is not None:
                fallback = str(await self.ctx.get_config('EMOTION_FALLBACK', 'neutral') or 'neutral')
            emotion = str(action.get('emotion') or fallback).strip()
            if emotion:
                try:
                    intensity = float(action.get('intensity') or 0.6)
                except (TypeError, ValueError):
                    intensity = 0.6
                backend2frontend.frontendAvatarCommand(
                    'AVATAR_EMOTION', {'emotion': emotion, 'intensity': round(max(0.0, min(1.0, intensity)), 4)})
                log.info('desktop-mood emotion action: %s (%.2f)', emotion, intensity)
        elif kind == 'attach' and self.store is not None:
            self._enqueue_attach(rule, context)
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
            except Exception as exc:  # noqa: BLE001
                log.warning('event-trigger 创建失败: %s', exc)

    def _suppressed_by_disturb(self, rule: dict[str, Any], context: dict[str, Any]) -> bool:
        """免打扰闸门：全屏/会议/锁屏时，打断型动作默认不发。

        规则可用 action.bypass_disturb=true 强行放行，或用 condition.when_not_disturbed 只在安静时触发。
        """
        if not context.get('disturbed'):
            return False
        if bool((rule.get('action') or {}).get('bypass_disturb')):
            return False
        return str(rule.get('kind') or '') in ('speech', 'nimble', 'emotion')

    def _match_rule(self, rule: dict[str, Any], context: dict[str, Any], edges: dict[str, Any]) -> bool:
        """条件匹配 + for_seconds 持续门控 + probability 概率门控。"""
        condition = rule.get('condition') or {}
        rule_id = str(rule.get('id') or '')
        if condition.get('when_not_disturbed') and context.get('disturbed'):
            return False
        hit = dm_conditions.evaluate(condition, context, edges)
        for_seconds = condition.get('for_seconds')
        if for_seconds:
            try:
                window = max(0, int(float(for_seconds)))
            except (TypeError, ValueError):
                window = 0
            if window > 0:
                since = self._condition_since.get(rule_id)
                if not hit:
                    self._condition_since.pop(rule_id, None)
                    return False
                if since is None:
                    self._condition_since[rule_id] = time.time()
                    return False
                return time.time() - since >= window
        self._condition_since.pop(rule_id, None)
        if not hit:
            return False
        probability = condition.get('probability')
        if probability is None:
            return True
        try:
            p = float(probability)
        except (TypeError, ValueError):
            return True
        return random.random() < max(0.0, min(1.0, p))

    async def heartbeat(self, ctx: PluginContext) -> None:
        if self.store is None or self.ctx is None:
            return
        context = await self.collect_context()
        # SMTC 播放状态边沿检测只在心跳里做：collect_context 还会被面板查询调用，
        # 若在那里消费边沿，轮询会把 smtc_playing 规则的触发机会吃掉。
        if 'smtc' not in context:
            self._smtc_rising_edge = False
            self._last_smtc_playing = None
        else:
            playing_now = str((context.get('smtc') or {}).get('status_name') or '').lower() == 'playing'
            self._smtc_rising_edge = playing_now and self._last_smtc_playing is False
            self._last_smtc_playing = playing_now
        self.store.update_snapshot(context)
        await self._write_rhythm_node(context)
        idle_seconds = context.get('idle_seconds')
        last_idle_state = self.store.get_last_idle_state()
        if idle_seconds is None:
            # 空闲感知被关闭：保持上一次状态，别用 0 伪造"刚活动"
            next_idle_state = last_idle_state
        else:
            next_idle_state = 'idle' if int(idle_seconds) >= 600 else 'active'
            if next_idle_state != last_idle_state:
                self.store.set_last_idle_state(next_idle_state)

        edges = {'idle_prev': last_idle_state, 'idle_next': next_idle_state,
                 'smtc_playing': self._smtc_rising_edge}
        global_cooldown = int(await self.ctx.get_config('GLOBAL_COOLDOWN_SEC', 180) or 180)
        rules = self.store.snapshot().get('rules', [])
        for rule in rules:
            if not bool(rule.get('enabled', True)):
                continue
            rule_id = str(rule.get('id') or '')
            if not self.store.can_fire(rule_id, int(rule.get('cooldown_sec') or 1800), global_cooldown):
                continue
            if not self._match_rule(rule, context, edges):
                continue
            if self._suppressed_by_disturb(rule, context):
                log.info('desktop-mood 规则 %s 命中但被免打扰闸门拦住: %s',
                         rule_id, '、'.join(context.get('disturb_reasons') or []))
                continue
            await self._execute_rule(rule, context)
            self.store.touch_rule_fire(rule_id)
            break

    async def _write_rhythm_node(self, context: dict[str, Any]) -> None:
        """把天级节律档案写成 agent 可读的 markdown（感知 → 记忆的通道）。"""
        if self.ctx is None or self.store is None:
            return
        today = context.get('rhythm_today') or {}
        archive = self.store.get_kv('timeline.rhythm', {})
        if not isinstance(archive, dict) or not archive:
            return
        lines = ['# Desktop Mood 节律档案', '',
                 '| 日期 | 清醒 | 首次活动 | 最后活动 | 专注 | 游戏 | 离开 | 窗口切换 |',
                 '|---|---|---|---|---|---|---|---|']
        for day in sorted(archive.keys())[-7:]:
            entry = archive.get(day) or {}
            lines.append('| {} | {} 分钟 | {} | {} | {} 分钟 | {} 分钟 | {} 次 | {} 次 |'.format(
                day,
                int(float(entry.get('awake_minutes') or 0)),
                entry.get('awake_first') or '-',
                entry.get('awake_last') or '-',
                int(float(entry.get('focus_minutes') or 0)),
                int(float(entry.get('game_minutes') or 0)),
                int(entry.get('leaves') or 0),
                int(entry.get('switches') or 0),
            ))
        if today:
            lines += ['', f"今日注意力状态：{context.get('attention') or '未知'}；活动强度：{context.get('activity_level') or '未知'}。"]
        try:
            await self.ctx.vfs_write(RHYTHM_NODE_PATH, '\n'.join(lines) + '\n',
                                     description="最近 7 天的节律档案（清醒/专注/游戏/离开次数）")
        except Exception as exc:  # noqa: BLE001
            log.warning('节律档案写入失败: %s', exc)

    def health_check(self) -> dict | None:
        snapshot = self.store.snapshot() if self.store else {}
        return {"status": "ok", "plugin": "desktop-mood", "rules": len(snapshot.get('rules', []))}


def get_plugin() -> Plugin:
    return Plugin()
