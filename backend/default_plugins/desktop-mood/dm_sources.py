"""感知分级 + 源注册表 + 字段显示格式化。

一个源真正采集 = 所属分级开关 ∧ 该源开关。
`build_registry()` 把基础源（collector 仍在 impl.py）和各感知模块的 SOURCES 合并成
单一注册表，供配置 schema、上下文提示、面板数据视图三处共用。
"""

from __future__ import annotations

import json
from typing import Any, Callable, Iterable, Sequence

TIER_CONFIG_KEY = 'ENABLE_TIER_{}'

PERCEPTION_TIERS = (
    {'id': 'green', 'label': '绿色 · 本机元数据', 'default': True,
     'note': '进程名、CPU/内存/电量、空闲时长、时间等本机计数：不出本机、不含文本内容'},
    {'id': 'yellow', 'label': '黄色 · 文本与联网', 'default': True,
     'note': '窗口标题、媒体曲名、文件名等文本内容；天气会联网查询所在城市'},
    {'id': 'red', 'label': '红色 · 屏幕内容', 'default': False,
     'note': '需要读取内容本身（如剪贴板正文判类型）；正文只在内存中判类型，不写入上下文'},
)

# 数据域：VFS 视图（faustbot://desktop-mood/<group>/）与附加权重都按域组织
GROUPS: tuple[dict[str, str], ...] = (
    {'id': 'system', 'label': '系统与电源', 'note': '负载/电量/空闲/节日/显卡/进程增减/显示器与供电'},
    {'id': 'window', 'label': '窗口与应用', 'note': '前台进程与标题、窗口几何、应用停留、未保存文档、编码活动、桌面文件'},
    {'id': 'input', 'label': '输入设备', 'note': '鼠标节奏、手柄、USB、蓝牙外设电量'},
    {'id': 'media', 'label': '媒体与音频', 'note': '媒体播放、音频输出设备、麦克风占用'},
    {'id': 'network', 'label': '网络', 'note': '联网类型、是否计量、VPN、SSID'},
    {'id': 'weather', 'label': '天气与环境', 'note': '天气（联网查询）、环境光'},
    {'id': 'narrative', 'label': '叙事与节律', 'note': '事件时间线、场景摘要、离开期间、活动强度、注意力、今日节律'},
    {'id': 'external', 'label': '外部上报', 'note': '前端与其它插件推送的信号、日历时段'},
    {'id': 'privacy', 'label': '隐私内容', 'note': '剪贴板类型判定（正文从不写入上下文）'},
)
GROUP_IDS: tuple[str, ...] = tuple(group['id'] for group in GROUPS)
GROUP_LABELS: dict[str, str] = {group['id']: group['label'] for group in GROUPS}

# 基础源：采集逻辑在 impl.py，这里只登记元数据（字段/tier/开关/采样间隔/数据域/附加权重）
BASE_SOURCES: tuple[dict[str, Any], ...] = (
    {'id': 'system_load', 'key': 'ENABLE_SYSTEM_LOAD', 'tier': 'green', 'default': True, 'cadence': 10,
     'label': '系统负载', 'note': 'CPU / 内存 / 磁盘 IO / 磁盘余量', 'group': 'system', 'attach_weight': 35,
     'attach_fields': ('cpu', 'memory'),
     'fields': ('cpu', 'memory', 'disk_io', 'disk_free')},
    {'id': 'battery', 'key': 'ENABLE_BATTERY_WATCH', 'tier': 'green', 'default': True, 'cadence': 10,
     'label': '电池', 'note': '电量、充电状态与剩余续航', 'group': 'system', 'attach_weight': 95,
     'fields': ('battery',)},
    {'id': 'window_process', 'key': 'ENABLE_WINDOW_PROCESS', 'tier': 'green', 'default': True, 'cadence': 10,
     'label': '前台进程', 'note': '活动窗口所属进程名/路径（不含标题文本）', 'group': 'window', 'attach_weight': 70,
     'fields': ('window_process',)},
    {'id': 'idle', 'key': 'ENABLE_IDLE_WATCH', 'tier': 'green', 'default': True, 'cadence': 10,
     'label': '空闲时长', 'note': '键鼠多久没动，用于判断离开/回来', 'group': 'system', 'attach_weight': 0,
     'fields': ('idle_seconds',)},
    {'id': 'holiday', 'key': 'ENABLE_HOLIDAY_EGG', 'tier': 'green', 'default': True, 'cadence': 600,
     'label': '节日彩蛋', 'note': '节日名称（圣诞/万圣/春节），仅本地日期推算', 'group': 'system', 'attach_weight': 20,
     'fields': ('holiday',)},
    {'id': 'window_title', 'key': 'ENABLE_WINDOW_WATCH', 'tier': 'yellow', 'default': True, 'cadence': 10,
     'label': '窗口标题', 'note': '活动窗口标题文本（文件名、网页标题等）', 'group': 'window', 'attach_weight': 30,
     'fields': ('window_title',)},
    {'id': 'smtc', 'key': 'ENABLE_SMTC_WATCH', 'tier': 'yellow', 'default': True, 'cadence': 10,
     'label': '媒体播放', 'note': '系统媒体播放状态、曲名/艺人、播放进度', 'group': 'media', 'attach_weight': 50,
     'fields': ('smtc',)},
    {'id': 'clipboard', 'key': 'ENABLE_CLIPBOARD_WATCH', 'tier': 'red', 'default': False, 'cadence': 10,
     'label': '剪贴板类型', 'note': '仅在内容变化时读一次判定类型（代码/链接/报错/图片），正文不写入上下文',
     'group': 'privacy', 'attach_weight': 0,
     'fields': ('clipboard',)},
)

FIELD_LABELS: dict[str, str] = {
    # 基础字段
    'cpu': 'CPU 使用率', 'memory': '内存占用', 'disk_io': '磁盘 IO', 'disk_free': '磁盘余量',
    'battery': '电池', 'window_process': '前台进程', 'idle_seconds': '空闲时长',
    'hour': '当前小时', 'holiday': '节日', 'window_title': '窗口标题',
    'smtc': '媒体播放', 'clipboard': '剪贴板',
    # dm_sensors_a
    'window_fullscreen': '全屏', 'window_rect': '窗口尺寸',
    'app_session': '当前应用停留', 'context_switches_5min': '窗口切换(5分钟)',
    'mouse': '鼠标活动', 'unsaved_docs': '未保存文档', 'window_dynamic': '画面动态',
    'gpu': '显卡', 'code_activity': '编码活动', 'desktop_new_files': '桌面新文件',
    # dm_sensors_b
    'display_status': '显示器状态', 'power': '电源', 'audio_output': '音频输出',
    'mic': '麦克风', 'gamepad_connected': '手柄', 'network': '网络',
    'ambient_light': '环境光', 'usb_devices': 'USB 设备', 'peripherals': '外设电量',
    # dm_timeline
    'narrative': '场景摘要', 'recent_events': '最近事件', 'away_digest': '离开期间',
    'activity_level': '活动强度', 'attention': '注意力状态', 'rhythm_today': '今日节律',
    # dm_external
    'weather': '天气', 'calendar': '日历', 'external': '外部上报',
    'locked': '屏幕锁定', 'display_count': '显示器数量', 'pet_interaction_at': '最近互动',
}


def _format_bytes(value: Any) -> str:
    size = float(value or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024:
            return f'{size:.0f} {unit}'
        size /= 1024
    return f'{size:.1f} TB'


def _format_seconds(value: Any) -> str:
    secs = int(value or 0)
    if secs < 60:
        return f'{secs} 秒'
    if secs < 3600:
        return f'{secs // 60} 分 {secs % 60} 秒'
    return f'{secs // 3600} 小时 {(secs % 3600) // 60} 分'


def _format_cpu(value: Any) -> str:
    return '未知' if value is None else f'{float(value):.0f}%'


def _format_disk_io(value: Any) -> str:
    if value is None:
        return '未知'
    return f'读 {_format_bytes(value.get("read_bytes"))} / 写 {_format_bytes(value.get("write_bytes"))}'


def _format_disk_free(value: Any) -> str:
    if value is None:
        return '未知'
    if isinstance(value, dict):
        return f'{float(value.get("percent") or 0):.0f}% 可用（{_format_bytes(value.get("free_bytes"))}）'
    return str(value)


def _format_battery(value: Any) -> str:
    percent = (value or {}).get('percent')
    if percent is None:
        return '无电池数据'
    text = f'{float(percent):.0f}%（{"充电中" if value.get("charging") else "放电中"}）'
    minutes = value.get('minutes_left')
    if minutes:
        text += f' 约 {int(minutes)} 分钟'
    return text


def _format_window_process(value: Any) -> str:
    if not value:
        return '未知'
    name = str(value.get('name') or '?')
    path = str(value.get('path') or '')
    return f'{name}（{path}）' if path else name


def _format_window_title(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return '（无标题）'
    return text[:80] + '…' if len(text) > 80 else text


def _format_smtc(value: Any) -> str:
    if not value:
        return '无媒体会话'
    status = str(value.get('status_name') or 'unknown')
    title = str(value.get('title') or '').strip()
    artist = str(value.get('artist') or '').strip()
    if not title:
        return status
    text = f'{status} · {title}' + (f' — {artist}' if artist else '')
    position, duration = value.get('position_sec'), value.get('duration_sec')
    if position is not None and duration:
        text += f'（{_format_seconds(position)} / {_format_seconds(duration)}）'
    return text


def _format_clipboard(value: Any) -> str:
    if not value:
        return '未知'
    kind = str(value.get('kind') or 'unknown')
    length = value.get('length')
    return f'{kind} · {length} 字' if length is not None else kind


def _format_json(value: Any) -> str:
    if isinstance(value, (list, tuple)) and value and isinstance(value[0], dict):
        return '；'.join(str(item.get('text') or item) for item in value[:3])
    return json.dumps(value, ensure_ascii=False)


FORMATTERS: dict[str, Callable[[Any], str]] = {
    'cpu': _format_cpu, 'memory': _format_cpu, 'disk_io': _format_disk_io, 'disk_free': _format_disk_free,
    'battery': _format_battery, 'window_process': _format_window_process,
    'idle_seconds': _format_seconds, 'window_title': _format_window_title,
    'smtc': _format_smtc, 'clipboard': _format_clipboard, 'hour': lambda v: f'{int(v or 0)} 点',
    'holiday': lambda v: '今日无节日' if v is None else str(v),
}


class Registry:
    """合并后的注册表（基础源 + 各模块源）。"""

    def __init__(self, sources: Sequence[dict[str, Any]], labels: dict[str, str],
                 formatters: dict[str, Callable[[Any], str]]):
        self.sources = tuple(sources)
        self.labels = dict(labels)
        self.formatters = dict(formatters)

    def fields_of(self, source: dict[str, Any]) -> tuple[str, ...]:
        return tuple(source.get('fields') or ())

    def cadence_of(self, source: dict[str, Any]) -> int:
        return int(source.get('cadence') or 10)

    def group_of(self, source: dict[str, Any]) -> str:
        return str(source.get('group') or '')

    def sources_of(self, group: str) -> tuple[dict[str, Any], ...]:
        return tuple(source for source in self.sources if self.group_of(source) == group)

    def attach_weight(self, source: dict[str, Any]) -> int:
        return int(source.get('attach_weight') or 0)

    def attach_fields_of(self, source: dict[str, Any]) -> tuple[str, ...]:
        """参与"即时附加"的字段：默认全部，噪声大的源用 attach_fields 收窄。"""
        declared = source.get('attach_fields')
        return tuple(declared) if declared else self.fields_of(source)

    def format_field(self, field: str, observed: dict[str, Any]) -> str | None:
        """未采集（源关闭/字段缺失）返回 None，面板据此显示"未采集"。"""
        if field not in observed:
            return None
        value = observed.get(field)
        formatter = self.formatters.get(field)
        if formatter is not None:
            try:
                return formatter(value)
            except Exception:
                return str(value)
        if value is None:
            return '未知'
        if isinstance(value, bool):
            return '是' if value else '否'
        if isinstance(value, (int, float, str)):
            return str(value)
        return _format_json(value)


def build_registry(modules: Iterable[Any]) -> Registry:
    """合并基础源与模块自述的 SOURCES / FIELD_LABELS / FORMATTERS。"""
    sources: list[dict[str, Any]] = list(BASE_SOURCES)
    labels = dict(FIELD_LABELS)
    formatters = dict(FORMATTERS)
    seen_ids = {source['id'] for source in sources}
    field_owner: dict[str, str] = {}
    for source in sources:
        for field_name in source.get('fields') or ():
            field_owner[field_name] = source['id']
    for module in modules:
        module_name = getattr(module, '__name__', str(module))
        for source in getattr(module, 'SOURCES', ()) or ():
            if source['id'] in seen_ids:
                raise ValueError(f'duplicate source id: {source["id"]}')
            seen_ids.add(source['id'])
            for field_name in source.get('fields') or ():
                owner = field_owner.get(field_name)
                if owner is not None:
                    raise ValueError(f'{module_name} 的源 {source["id"]} 字段 {field_name} 已被 {owner} 占用')
                field_owner[field_name] = source['id']
            sources.append(source)
        labels.update(getattr(module, 'FIELD_LABELS', {}) or {})
        formatters.update(getattr(module, 'FORMATTERS', {}) or {})
    missing = [field_name for field_name in field_owner if field_name not in labels]
    if missing:
        raise ValueError(f'缺少字段中文名: {", ".join(sorted(missing))}')
    for source in sources:
        group = str(source.get('group') or '')
        if group not in GROUP_LABELS:
            raise ValueError(f'源 {source["id"]} 的数据域未知: {group or "（空）"}')
        weight = source.get('attach_weight')
        if not isinstance(weight, int) or isinstance(weight, bool) or not 0 <= weight <= 100:
            raise ValueError(f'源 {source["id"]} 的 attach_weight 必须是 0-100 的整数，当前是 {weight!r}')
    return Registry(sources, labels, formatters)
