"""时间线：把瞬时字段变成"发生了什么"。

- `recent_events`：最近 30 条场景变化（切窗口/全屏/播放/锁屏/耳机/外设…），持久化便于重启后连续；
- `narrative`：一句话场景摘要，AI 不必逐个理解数字；
- `away_digest`：离开（空闲/锁屏）期间发生的事，回来时汇报——"你不在的时候编译跑完了"；
- `attention` / `activity_level`：心流 / 碎片 / 狂乱的粗略判断（供免打扰与情绪映射使用）；
- `rhythm_today`：天级节律档案（清醒时段、专注分钟、游戏分钟、离开次数），面板可看、agent 可读。
"""

from __future__ import annotations

import time
from typing import Any

from dm_api import SensorContext, SensorResult

SOURCES = (
    {'id': 'timeline', 'key': 'ENABLE_TIMELINE', 'tier': 'green', 'default': True,
     'cadence': 10, 'label': '事件时间线', 'note': '场景变化事件流、一句话场景摘要、活动强度与注意力状态',
     'fields': ('recent_events', 'narrative', 'activity_level', 'attention')},
    {'id': 'away_digest', 'key': 'ENABLE_AWAY_DIGEST', 'tier': 'green', 'default': True,
     'cadence': 10, 'label': '离开期间', 'note': '用户离开/锁屏时发生的事，回来时一次性汇报',
     'fields': ('away_digest',)},
    {'id': 'rhythm', 'key': 'ENABLE_RHYTHM', 'tier': 'green', 'default': True,
     'cadence': 60, 'label': '今日节律', 'note': '今天的清醒时段、专注/游戏累计、离开次数（天级档案）',
     'fields': ('rhythm_today',)},
)

FIELD_LABELS = {
    'recent_events': '最近事件', 'narrative': '场景摘要', 'activity_level': '活动强度',
    'attention': '注意力状态', 'away_digest': '离开期间', 'rhythm_today': '今日节律',
}

EVENTS_KV = 'timeline.events'
AWAY_KV = 'timeline.away'
RHYTHM_KV = 'timeline.rhythm'
MAX_EVENTS = 30
MAX_AWAY_ITEMS = 12
AWAY_IDLE_SEC = 600
HEARTBEAT_SEC = 10
FOCUS_MIN_MINUTES = 25
CHAOS_SWITCHES_PER_MIN = 20

# 值得写进时间线的进程名关键词（游戏/编译/下载/会议），用于离开期间的"值得一提"
NOTABLE_PROCESS_HINTS = {
    'steam.exe': 'Steam', 'epicgameslauncher.exe': 'Epic', 'obs64.exe': 'OBS',
    'msedge.exe': 'Edge', 'chrome.exe': 'Chrome', 'discord.exe': 'Discord',
    'zoom.exe': 'Zoom', 'teams.exe': 'Teams', 'wechat.exe': '微信', 'qq.exe': 'QQ',
}
GAME_HINTS = ('genshinimpact.exe', 'yuanshen.exe', 'starrail.exe', 'cs2.exe', 'valorant.exe',
              'league of legends.exe', 'dota2.exe', 'eldenring.exe', 'steam.exe', 'epicgameslauncher.exe')
DEV_HINTS = ('code.exe', 'code - insiders.exe', 'devenv.exe', 'pycharm64.exe', 'idea64.exe',
             'cursor.exe', 'windowsterminal.exe', 'nvim.exe', 'vim.exe', 'sublime_text.exe')


def _now_ts() -> float:
    return time.time()


def _day_key(ts: float | None = None) -> str:
    return time.strftime('%Y-%m-%d', time.localtime(ts or _now_ts()))


def describe_process(name: str | None) -> str:
    """把进程名翻译成用户能懂的场景词。"""
    lowered = str(name or '').lower()
    if not lowered:
        return '未知程序'
    if lowered in GAME_HINTS:
        return f'游戏（{name}）'
    if lowered in DEV_HINTS:
        return f'开发工具（{name}）'
    return NOTABLE_PROCESS_HINTS.get(lowered, str(name))


def classify_attention(context: dict[str, Any]) -> tuple[str, str]:
    """返回 (attention, activity_level)；都是粗略启发式，用于免打扰与情绪映射。"""
    session = context.get('app_session') or {}
    seconds = int(session.get('seconds') or 0)
    switches = int(context.get('context_switches_5min') or 0)
    mouse = context.get('mouse') or {}
    velocity = float(mouse.get('velocity_px_s') or 0.0)
    jitter = float(mouse.get('jitter') or 0.0)
    idle = context.get('idle_seconds')
    if seconds >= FOCUS_MIN_MINUTES * 60 and switches <= 2:
        attention = 'focused'
    elif switches >= CHAOS_SWITCHES_PER_MIN:
        attention = 'chaotic'
    elif switches >= 6:
        attention = 'fragmented'
    else:
        attention = 'neutral'
    if idle is not None and int(idle or 0) >= 300:
        level = 'idle'
    elif velocity >= 900 or jitter >= 0.35:
        level = 'intense'
    elif velocity >= 200:
        level = 'active'
    else:
        level = 'calm'
    return attention, level


def build_narrative(context: dict[str, Any], away_seconds: int | None = None) -> str:
    """由当前字段拼一句人话场景摘要。"""
    parts: list[str] = []
    session = context.get('app_session') or {}
    process_name = (context.get('window_process') or {}).get('name') or session.get('process')
    title = str(context.get('window_title') or '').strip()
    if session.get('seconds'):
        parts.append(f'用户在 {describe_process(process_name)} 上已停留 {_duration_text(int(session["seconds"]))}')
    elif process_name:
        parts.append(f'用户当前在 {describe_process(process_name)}')
    if title:
        short = title if len(title) <= 40 else title[:40] + '…'
        parts.append(f'窗口是「{short}」')
    if context.get('window_fullscreen'):
        parts.append('处于全屏')
    smtc = context.get('smtc') or {}
    if str(smtc.get('status_name') or '').lower() == 'playing':
        playing = str(smtc.get('title') or '').strip()
        parts.append(f'正在播放「{playing}」' if playing else '正在播放媒体')
    attention = context.get('attention')
    if attention == 'focused':
        parts.append('看起来进入心流，别打扰')
    elif attention == 'chaotic':
        parts.append('窗口切得很碎，可能卡住了')
    mouse = context.get('mouse') or {}
    if mouse.get('corner'):
        parts.append(f'鼠标停在{mouse["corner"]}角')
    idle = context.get('idle_seconds')
    if idle is not None and int(idle or 0) >= 60:
        parts.append(f'已 {_duration_text(int(idle))} 没有输入')
    elif away_seconds:
        parts.append(f'刚回来（离开约 {_duration_text(away_seconds)}）')
    rhythm = context.get('rhythm_today') or {}
    if rhythm.get('awake_minutes'):
        parts.append(f'今天已清醒约 {_duration_text(rhythm["awake_minutes"] * 60)}')
    return '，'.join(parts) + '。' if parts else '暂无足够信息描述当前场景。'


def _duration_text(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds} 秒'
    if seconds < 3600:
        return f'{seconds // 60} 分钟'
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    return f'{hours} 小时' + (f' {minutes} 分钟' if minutes else '')


# ── 事件识别 ─────────────────────────────────────────────────

def detect_events(context: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, Any]]:
    """对比上一轮字段，产出场景变化事件（纯函数，可单测）。"""
    events: list[dict[str, Any]] = []

    def add(kind: str, text: str) -> None:
        events.append({'kind': kind, 'text': text, 'at': int(_now_ts())})

    process_now = (context.get('window_process') or {}).get('name')
    process_was = (previous.get('window_process') or {}).get('name')
    if process_now and process_now != process_was:
        add('window', f'切到 {describe_process(process_now)}')

    if context.get('window_fullscreen') and not previous.get('window_fullscreen'):
        add('fullscreen', '进入全屏（游戏/视频）')
    elif previous.get('window_fullscreen') and context.get('window_fullscreen') is False:
        add('fullscreen', '退出全屏')

    playing_now = str((context.get('smtc') or {}).get('status_name') or '').lower() == 'playing'
    playing_was = str((previous.get('smtc') or {}).get('status_name') or '').lower() == 'playing'
    if playing_now and not playing_was:
        title = str((context.get('smtc') or {}).get('title') or '').strip()
        add('media', f'开始播放「{title}」' if title else '开始播放媒体')
    elif playing_was and not playing_now:
        add('media', '停止播放')

    locked_now = context.get('locked')
    if locked_now is True and previous.get('locked') is not True:
        add('lock', '锁屏')
    elif locked_now is False and previous.get('locked') is True:
        add('lock', '解锁')

    display_now = context.get('display_status')
    if display_now == 'on' and previous.get('display_status') == 'off':
        add('display', '显示器点亮')
    elif display_now == 'off' and previous.get('display_status') == 'on':
        add('display', '显示器熄灭')

    kind_now = (context.get('clipboard') or {}).get('kind')
    kind_was = (previous.get('clipboard') or {}).get('kind')
    if kind_now and kind_now != kind_was:
        add('clipboard', f'剪贴板内容变为 {kind_now}')

    mic_now = (context.get('mic') or {}).get('in_use')
    mic_was = (previous.get('mic') or {}).get('in_use')
    if mic_now and not mic_was:
        app = (context.get('mic') or {}).get('app')
        add('mic', f'麦克风被占用（{app or "未知程序"}），可能在开会/语音')
    elif mic_was and mic_now is False:
        add('mic', '麦克风释放，会议/语音结束')

    headphones_now = (context.get('audio_output') or {}).get('headphones')
    headphones_was = (previous.get('audio_output') or {}).get('headphones')
    if headphones_now is not None and headphones_was is not None and headphones_now != headphones_was:
        add('audio', '戴上耳机' if headphones_now else '摘下耳机')

    network_now = (context.get('network') or {}).get('ssid') or (context.get('network') or {}).get('type')
    network_was = (previous.get('network') or {}).get('ssid') or (previous.get('network') or {}).get('type')
    if network_now and network_now != network_was:
        add('network', f'网络切换到 {network_now}')

    gamepad_now = context.get('gamepad_connected')
    if gamepad_now and not previous.get('gamepad_connected'):
        add('gamepad', '接入手柄')

    usb_now = context.get('usb_devices') or {}
    added = usb_now.get('added') or []
    if added and usb_now.get('last_event_at') != previous.get('usb_devices', {}).get('last_event_at'):
        add('usb', '接入设备 ' + '、'.join(str(item) for item in added[:3]))

    files = context.get('desktop_new_files') or []
    known = {item.get('name') for item in (previous.get('desktop_new_files') or [])}
    fresh = [item.get('name') for item in files if item.get('name') not in known]
    if fresh:
        add('desktop', '桌面新增文件 ' + '、'.join(str(name) for name in fresh[:3]))

    if context.get('window_dynamic') and not previous.get('window_dynamic'):
        add('dynamic', '画面在动但用户没动鼠标（看视频/挂机？）')

    session_seconds = int((context.get('app_session') or {}).get('seconds') or 0)
    session_was = int((previous.get('app_session') or {}).get('seconds') or 0)
    for milestone in (1800, 3600, 7200):
        if session_was < milestone <= session_seconds:
            add('session', f'已连续使用 {describe_process((context.get("window_process") or {}).get("name"))} '
                           f'{_duration_text(milestone)}')

    attention = context.get('attention')
    if attention in ('focused', 'chaotic') and attention != previous.get('attention'):
        add('attention', '进入心流状态' if attention == 'focused' else '注意力开始碎片化')

    return events


def summarize_away(events: list[dict[str, Any]]) -> list[str]:
    """离开期间只保留值得一提的事件行。"""
    interesting = {'lock', 'display', 'usb', 'media', 'mic', 'desktop'}
    lines: list[str] = []
    for event in events:
        if event.get('kind') in interesting:
            lines.append(str(event.get('text') or ''))
    return lines[-MAX_AWAY_ITEMS:]


# ── 节律档案 ─────────────────────────────────────────────────

def update_rhythm(archive: dict[str, Any], context: dict[str, Any], now: float) -> dict[str, Any]:
    """把本轮状态累加进今天的档案（纯函数，便于单测）。按心跳 10 秒折算分钟。"""
    entry = archive.setdefault(_day_key(now), {
        'awake_first': None, 'awake_last': None, 'awake_minutes': 0,
        'focus_minutes': 0, 'game_minutes': 0, 'leaves': 0,
        'switches': 0, 'max_session_minutes': 0,
    })
    hour_text = time.strftime('%H:%M', time.localtime(now))
    idle = context.get('idle_seconds')
    away = (idle is not None and int(idle) >= AWAY_IDLE_SEC) or context.get('locked') is True
    if not away:
        if entry['awake_first'] is None:
            entry['awake_first'] = hour_text
        entry['awake_last'] = hour_text
    session = context.get('app_session') or {}
    minutes = int(session.get('seconds') or 0) // 60
    entry['max_session_minutes'] = max(int(entry.get('max_session_minutes') or 0), minutes)
    entry['switches'] = int(entry.get('switches') or 0) + int(context.get('context_switches_5min') or 0)
    process_name = str((context.get('window_process') or {}).get('name') or '').lower()
    elapsed_minutes = HEARTBEAT_SEC / 60
    if not away:
        entry['awake_minutes'] = round(float(entry.get('awake_minutes') or 0) + elapsed_minutes, 2)
    if context.get('attention') == 'focused':
        entry['focus_minutes'] = round(float(entry.get('focus_minutes') or 0) + elapsed_minutes, 2)
    if process_name in GAME_HINTS:
        entry['game_minutes'] = round(float(entry.get('game_minutes') or 0) + elapsed_minutes, 2)
    return archive


def rhythm_summary(entry: dict[str, Any] | None, previous_days: list[dict[str, Any]]) -> dict[str, Any]:
    """给面板/agent 的精简节律视图。"""
    entry = entry or {}
    return {
        'awake_first': entry.get('awake_first'),
        'awake_last': entry.get('awake_last'),
        'awake_minutes': int(float(entry.get('awake_minutes') or 0)),
        'focus_minutes': int(float(entry.get('focus_minutes') or 0)),
        'game_minutes': int(float(entry.get('game_minutes') or 0)),
        'leaves': int(entry.get('leaves') or 0),
        'switches': int(entry.get('switches') or 0),
        'max_session_minutes': int(entry.get('max_session_minutes') or 0),
        'days': previous_days,
    }


def _events(sctx: SensorContext) -> list[dict[str, Any]]:
    stored = sctx.store.get_kv(EVENTS_KV, []) if sctx.store is not None else []
    return list(stored) if isinstance(stored, list) else []


def _push_events(sctx: SensorContext, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = (_events(sctx) + events)[-MAX_EVENTS:]
    if sctx.store is not None:
        sctx.store.set_kv(EVENTS_KV, merged)
    return merged


def _collect_timeline(sctx: SensorContext, context: dict[str, Any]) -> dict[str, Any]:
    previous = sctx.observed
    events = detect_events(context, previous)
    if events:
        _push_events(sctx, events)
    attention, level = classify_attention(context)
    context['attention'] = attention
    return {
        'recent_events': _events(sctx),
        'narrative': build_narrative(context),
        'activity_level': level,
        'attention': attention,
    }


def _collect_away(sctx: SensorContext, context: dict[str, Any]) -> dict[str, Any]:
    idle = context.get('idle_seconds')
    away = (idle is not None and int(idle) >= AWAY_IDLE_SEC) or context.get('locked') is True
    state = sctx.memory.setdefault('timeline.away_state', {'away': False, 'since': None, 'started': None})
    buffer = sctx.store.get_kv(AWAY_KV, []) if sctx.store is not None else []
    if not isinstance(buffer, list):
        buffer = []
    if away and not state['away']:
        state.update({'away': True, 'since': _now_ts(), 'started': int(_now_ts())})
    if away:
        recent = detect_events(context, sctx.observed)
        if recent:
            buffer = (buffer + recent)[-MAX_AWAY_ITEMS:]
            if sctx.store is not None:
                sctx.store.set_kv(AWAY_KV, buffer)
    digest = sctx.memory.get('timeline.last_digest')
    if not away and state['away']:
        left_at = int(state.get('since') or 0)
        items = summarize_away(buffer)
        digest = {
            'left_at': left_at,
            'returned_at': int(_now_ts()),
            'away_seconds': max(0, int(_now_ts() - left_at)) if left_at else None,
            'items': items,
        }
        sctx.memory['timeline.last_digest'] = digest
        if sctx.store is not None:
            sctx.store.set_kv(AWAY_KV, [])
        archive = sctx.store.get_kv(RHYTHM_KV, {}) if sctx.store is not None else {}
        if isinstance(archive, dict) and archive:
            entry = archive.get(_day_key()) or {}
            entry['leaves'] = int(entry.get('leaves') or 0) + 1
    if state['away'] and not away:
        state['away'] = False
    return {'away_digest': digest}


def _collect_rhythm(sctx: SensorContext, context: dict[str, Any]) -> dict[str, Any]:
    archive = sctx.store.get_kv(RHYTHM_KV, {}) if sctx.store is not None else {}
    if not isinstance(archive, dict):
        archive = {}
    archive = update_rhythm(archive, context, sctx.now)
    for key in sorted(archive.keys())[:-7]:  # 只留最近 7 天
        archive.pop(key, None)
    if sctx.store is not None:
        sctx.store.set_kv(RHYTHM_KV, archive)
    today = archive.get(_day_key())
    previous_days = [{'date': key, **{k: v for k, v in value.items() if k in (
        'awake_first', 'awake_last', 'focus_minutes', 'game_minutes', 'away_count')}}
        for key, value in sorted(archive.items())[-6:-1]]
    return {'rhythm_today': rhythm_summary(today, previous_days)}


async def collect(sctx: SensorContext) -> SensorResult:
    result = SensorResult()
    timeline_enabled = sctx.enabled('timeline')
    away_enabled = sctx.enabled('away_digest')
    rhythm_enabled = sctx.enabled('rhythm')
    if not (timeline_enabled or away_enabled or rhythm_enabled):
        return result
    context = sctx.context
    if not isinstance(context, dict):
        context = dict(sctx.observed)
    if timeline_enabled:
        try:
            result.fields.update(_collect_timeline(sctx, context))
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('时间线采集失败: %s', exc)
            result.status['timeline'] = f'采集失败: {exc}'
    if away_enabled:
        try:
            result.fields.update(_collect_away(sctx, context))
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('离开摘要采集失败: %s', exc)
            result.status['away_digest'] = f'采集失败: {exc}'
    if rhythm_enabled:
        try:
            result.fields.update(_collect_rhythm(sctx, context))
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('节律档案采集失败: %s', exc)
            result.status['rhythm'] = f'采集失败: {exc}'
    return result


# ── 面板显示 ─────────────────────────────────────────────────

def _fmt_events(value: Any) -> str:
    if not value:
        return '暂无事件'
    lines = []
    for event in value[-3:]:
        at = time.strftime('%H:%M', time.localtime(int(event.get('at') or 0)))
        lines.append(f'{at} {event.get("text")}')
    return '；'.join(lines)


def _fmt_away(value: Any) -> str:
    if not value:
        return '无（用户一直在）'
    items = value.get('items') or []
    if not items:
        return f'离开 {_duration_text(int(value.get("away_seconds") or 0))}，期间无特别事件'
    return f'离开 {_duration_text(int(value.get("away_seconds") or 0))}：' + '；'.join(items[-3:])


def _fmt_rhythm(value: Any) -> str:
    if not value:
        return '无数据'
    parts = []
    if value.get('awake_first'):
        parts.append(f'{value["awake_first"]}~{value.get("awake_last")}')
    parts.append(f'清醒 {value.get("awake_minutes") or 0} 分钟')
    if value.get('focus_minutes'):
        parts.append(f'专注 {value["focus_minutes"]} 分钟')
    if value.get('game_minutes'):
        parts.append(f'游戏 {value["game_minutes"]} 分钟')
    if value.get('leaves'):
        parts.append(f'离开 {value["leaves"]} 次')
    return ' · '.join(parts)


FORMATTERS = {
    'recent_events': _fmt_events,
    'narrative': lambda value: str(value or '暂无'),
    'away_digest': _fmt_away,
    'rhythm_today': _fmt_rhythm,
    'activity_level': lambda value: {'idle': '离开', 'calm': '平静', 'active': '活跃',
                                     'intense': '激烈'}.get(str(value), str(value or '未知')),
    'attention': lambda value: {'focused': '心流', 'fragmented': '碎片', 'chaotic': '狂乱',
                               'neutral': '一般'}.get(str(value), str(value or '未知')),
}
