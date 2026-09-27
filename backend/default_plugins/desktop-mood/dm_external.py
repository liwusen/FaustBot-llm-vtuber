"""外部上报与联网/日历感知。

- `external`：Electron 主进程（锁屏/睡眠/AC/显示器）、渲染进程（宠物被点击/窗口可见）、
  以及其它插件（RSS/直播）推来的信号，统一走 communicate(action='report') 入库，
  按 `EXTERNAL_SOURCE_TIERS` 受感知分级约束；
- `weather`：wttr.in 扩展字段（日出日落/降雨/体感），低频缓存；
- `calendar`：星期/是否周末/工作时间/时段，纯本地推算。
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

from dm_api import SensorContext, SensorResult

SOURCES = (
    {'id': 'external', 'key': 'ENABLE_EXTERNAL_REPORT', 'tier': 'green', 'default': True,
     'cadence': 10, 'label': '外部上报', 'note': '前端/其它插件推来的信号（锁屏、睡眠、显示器增减、宠物被点击、订阅更新等）',
     'fields': ('external', 'locked', 'display_count', 'pet_interaction_at')},
    {'id': 'weather', 'key': 'ENABLE_WEATHER_WATCH', 'tier': 'yellow', 'default': True,
     'cadence': 60, 'label': '天气（联网）', 'note': '经 wttr.in 查询，会把城市/出口 IP 交给第三方',
     'fields': ('weather',)},
    {'id': 'calendar', 'key': 'ENABLE_CALENDAR', 'tier': 'green', 'default': True,
     'cadence': 60, 'label': '日历时段', 'note': '星期、是否周末、是否工作时间、上午/下午/深夜',
     'fields': ('calendar',)},
)

FIELD_LABELS = {
    'external': '外部上报', 'locked': '屏幕锁定', 'display_count': '显示器数量',
    'pet_interaction_at': '最近互动', 'weather': '天气', 'calendar': '日历时段',
}

# 上报源 → 感知分级；未登记的上报源一律拒绝（避免任意插件往上下文里塞东西）
EXTERNAL_SOURCE_TIERS = {
    'electron': 'green',
    'electron_main': 'green',
    'faust_ui': 'green',
    'rss': 'yellow',
    'live': 'yellow',
}
EXTERNAL_SOURCE_ALIASES = {'electron_main': 'electron'}
REPORTS_KV = 'external.reports'
WEATHER_KV = 'external.weather'
WEATHER_AT_KV = 'external.weather_at'
WEATHER_TTL_SEC = 600
REPORT_TTL_SEC = 3600
MAX_FIELDS_PER_REPORT = 16
MAX_VALUE_CHARS = 400


def canonical_source(source: str) -> str:
    name = str(source or '').strip().lower()
    return EXTERNAL_SOURCE_ALIASES.get(name, name)


def sanitize_fields(fields: Any) -> dict[str, Any]:
    """只收标量/短列表，限制字段数与字符串长度；其余丢弃。"""
    if not isinstance(fields, dict):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in list(fields.items())[:MAX_FIELDS_PER_REPORT]:
        name = str(key or '').strip()
        if not name or len(name) > 48:
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            cleaned[name] = value
        elif isinstance(value, str):
            cleaned[name] = value[:MAX_VALUE_CHARS]
        elif isinstance(value, (list, tuple)):
            cleaned[name] = [str(item)[:MAX_VALUE_CHARS] for item in list(value)[:12]]
    return cleaned


def ingest(payload: dict[str, Any], store: Any) -> tuple[bool, str]:
    """把一条上报写进 KV。返回 (是否接受, 说明)——拒绝原因要能显示给用户，不静默丢弃。"""
    if store is None:
        return False, '插件未加载'
    source = canonical_source((payload or {}).get('source'))
    tier = EXTERNAL_SOURCE_TIERS.get(source)
    if tier is None:
        return False, f'未登记的上报源: {source or "(空)"}'
    fields = sanitize_fields((payload or {}).get('fields'))
    if not fields:
        return False, 'fields 为空或格式非法'
    reports = store.get_kv(REPORTS_KV, {})
    if not isinstance(reports, dict):
        reports = {}
    now = int(time.time())
    previous = reports.get(source) if isinstance(reports.get(source), dict) else {}
    merged = dict(previous.get('fields') or {})
    merged.update(fields)
    reports[source] = {'fields': merged, 'at': now, 'tier': tier}
    cutoff = now - REPORT_TTL_SEC
    store.set_kv(REPORTS_KV, {key: value for key, value in reports.items()
                              if isinstance(value, dict) and int(value.get('at') or 0) >= cutoff})
    return True, f'已接收 {source}: {", ".join(fields.keys())}'


def fresh_reports(store: Any, now: float) -> dict[str, Any]:
    reports = store.get_kv(REPORTS_KV, {}) if store is not None else {}
    if not isinstance(reports, dict):
        return {}
    fresh: dict[str, Any] = {}
    for source, value in reports.items():
        if not isinstance(value, dict):
            continue
        age = max(0, int(now - int(value.get('at') or 0)))
        if age > REPORT_TTL_SEC:
            continue
        fresh[str(source)] = {**(value.get('fields') or {}), 'age_sec': age}
    return fresh


def build_calendar(ts: float) -> dict[str, Any]:
    """星期/周末/工作时间/时段（纯函数）。"""
    local = time.localtime(ts)
    hour = local.tm_hour
    weekday = int(local.tm_wday)  # 0 = 周一
    names = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')
    if 5 <= hour < 9:
        daypart = '清晨'
    elif 9 <= hour < 12:
        daypart = '上午'
    elif 12 <= hour < 14:
        daypart = '午间'
    elif 14 <= hour < 18:
        daypart = '下午'
    elif 18 <= hour < 23:
        daypart = '晚上'
    else:
        daypart = '深夜'
    return {
        'weekday': weekday,
        'weekday_name': names[weekday],
        'is_weekend': weekday >= 5,
        'work_hours': weekday < 5 and 9 <= hour < 18,
        'daypart': daypart,
        'date': time.strftime('%Y-%m-%d', local),
    }


def fetch_weather(city: str) -> dict[str, Any] | None:
    """wttr.in j1：解析扩展字段（日出日落/体感/降雨）。失败返回 None。"""
    try:
        query = urllib.parse.quote(city or '')
        url = f'https://wttr.in/{query}?format=j1' if query and city != 'auto' else 'https://wttr.in/?format=j1'
        with urllib.request.urlopen(url, timeout=8) as response:
            payload = json.loads(response.read().decode('utf-8', errors='ignore'))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    current = ((payload.get('current_condition') or [{}])[0]) or {}
    today = ((payload.get('weather') or [{}])[0]) or {}
    astronomy = ((today.get('astronomy') or [{}])[0]) or {}
    hourly = (today.get('hourly') or [{}])[0] or {}
    area = ((payload.get('nearest_area') or [{}])[0]) or {}
    return {
        'text': ((current.get('weatherDesc') or [{}])[0].get('value') or '').strip(),
        'temperature_c': current.get('temp_C'),
        'feels_like_c': current.get('FeelsLikeC'),
        'humidity': current.get('humidity'),
        'wind_kmph': current.get('windspeedKmph'),
        'precip_mm': hourly.get('precipMM'),
        'chance_of_rain': hourly.get('chanceofrain'),
        'sunrise': astronomy.get('sunrise'),
        'sunset': astronomy.get('sunset'),
        'area': ((area.get('areaName') or [{}])[0].get('value') or '').strip() or None,
    }


async def collect(sctx: SensorContext) -> SensorResult:
    result = SensorResult()
    if sctx.enabled('external'):
        try:
            reports = fresh_reports(sctx.store, sctx.now)
            result.fields['external'] = reports
            electron = reports.get('electron') or {}
            ui = reports.get('faust_ui') or {}
            # 拍平成顶层字段：规则与免打扰闸门要按 locked/display_count 直接判断
            result.fields['locked'] = electron.get('locked') if 'locked' in electron else None
            result.fields['display_count'] = electron.get('display_count') if 'display_count' in electron else None
            result.fields['pet_interaction_at'] = ui.get('pet_interaction_at') if 'pet_interaction_at' in ui else None
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('外部上报读取失败: %s', exc)
            result.status['external'] = f'采集失败: {exc}'
    if sctx.enabled('calendar'):
        try:
            result.fields['calendar'] = build_calendar(sctx.now)
        except Exception as exc:  # noqa: BLE001
            sctx.log.warning('日历时段计算失败: %s', exc)
            result.status['calendar'] = f'计算失败: {exc}'
    if sctx.enabled('weather') and sctx.store is not None and sctx.is_due('weather'):
        cached = sctx.store.get_kv(WEATHER_KV, None)
        cached_at = int(sctx.store.get_kv(WEATHER_AT_KV, 0) or 0)
        needs_refresh = (cached is None and cached_at == 0) or (sctx.now - cached_at >= WEATHER_TTL_SEC)
        if needs_refresh:
            city = str(await sctx.config('WEATHER_CITY', 'auto') or 'auto')
            # 联网查询是阻塞 IO：必须挪线程，否则心跳/管理接口会卡住事件循环
            weather = await sctx.to_thread(fetch_weather, city)
            if weather is None:
                if cached is not None:
                    result.status['weather'] = '联网查询失败，沿用上次结果'
                else:
                    result.status['weather'] = '联网查询失败'
            else:
                sctx.store.set_kv(WEATHER_KV, weather)
                sctx.store.set_kv(WEATHER_AT_KV, int(sctx.now))
                cached = weather
        result.fields['weather'] = cached if cached is not None else None
    return result


# ── 面板显示 ─────────────────────────────────────────────────

def _fmt_external(value: Any) -> str:
    if not value:
        return '暂无上报'
    parts: list[str] = []
    for source, fields in value.items():
        text = '、'.join(f'{key}={fields[key]}' for key in list(fields)[:3] if key != 'age_sec')
        parts.append(f'{source}（{int(fields.get("age_sec") or 0)} 秒前）{text}')
    return '；'.join(parts)


def _fmt_locked(value: Any) -> str:
    return '已锁屏' if value else '未锁屏'


def _fmt_display_count(value: Any) -> str:
    return f'{int(value or 0)} 台'


def _fmt_pet_interaction(value: Any) -> str:
    if not value:
        return '暂无'
    return time.strftime('%H:%M', time.localtime(int(value))) + ' 有过互动'


def _fmt_weather(value: Any) -> str:
    if not value:
        return '获取失败'
    parts = [str(value.get('text') or '').strip()]
    if value.get('temperature_c') is not None:
        parts.append(f'{value["temperature_c"]}°C')
    if value.get('feels_like_c') is not None and value.get('feels_like_c') != value.get('temperature_c'):
        parts.append(f'体感 {value["feels_like_c"]}°C')
    if value.get('chance_of_rain') not in (None, '0'):
        parts.append(f'降雨概率 {value["chance_of_rain"]}%')
    if value.get('sunrise') and value.get('sunset'):
        parts.append(f'日出 {value["sunrise"]} / 日落 {value["sunset"]}')
    if value.get('area'):
        parts.append(str(value['area']))
    return ' · '.join(part for part in parts if part)


def _fmt_calendar(value: Any) -> str:
    if not value:
        return '未知'
    text = f'{value.get("weekday_name")} {value.get("daypart")}'
    if value.get('work_hours'):
        text += ' · 工作时间'
    elif value.get('is_weekend'):
        text += ' · 周末'
    return text


FORMATTERS = {
    'external': _fmt_external,
    'locked': _fmt_locked,
    'display_count': _fmt_display_count,
    'pet_interaction_at': _fmt_pet_interaction,
    'weather': _fmt_weather,
    'calendar': _fmt_calendar,
}
