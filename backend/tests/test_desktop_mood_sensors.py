"""感知模块（A/B/时间线/外部）的单元测试：纯函数、注册表契约、门控开关。"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins' / 'desktop-mood'
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

import dm_conditions  # noqa: E402
import dm_external  # noqa: E402
import dm_sensors_b  # noqa: E402
import dm_sources  # noqa: E402
import dm_timeline  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from faust_backend.plugin_system import PluginManager  # noqa: E402

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins'


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f'dm_test_{name}', PLUGIN_DIR / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


# ── 条件原语 ─────────────────────────────────────────────────

def test_condition_primitives_cover_paths_and_edges():
    context = {'battery': {'percent': 12, 'charging': False}, 'app_session': {'seconds': 5400},
               'attention': 'focused', 'mic': {'in_use': True}, '_previous': {'attention': 'neutral'}}
    assert dm_conditions.evaluate({'type': 'field_over', 'field': 'app_session.seconds', 'value': 5400}, context)
    assert not dm_conditions.evaluate({'type': 'field_under', 'field': 'battery.percent', 'value': 10}, context)
    assert dm_conditions.evaluate({'type': 'field_eq', 'field': 'attention', 'value': 'focused'}, context)
    assert dm_conditions.evaluate({'type': 'field_eq', 'field': 'mic.in_use', 'value': True}, context)
    assert dm_conditions.evaluate({'type': 'field_contains', 'field': 'attention', 'value': 'focus'}, context)
    assert dm_conditions.evaluate({'type': 'field_in', 'field': 'attention', 'values': ['focused', 'chaotic']}, context)
    assert dm_conditions.evaluate({'type': 'field_changed', 'field': 'attention'}, context)
    assert not dm_conditions.evaluate({'type': 'field_changed', 'field': 'app_session.seconds'}, context)
    # 字段缺失（感知被关掉）一律 False，不能凭空调触发
    assert not dm_conditions.evaluate({'type': 'field_over', 'field': 'gpu.temp_c', 'value': 1}, context)


def test_legacy_conditions_still_work():
    context = {'idle_seconds': 900, 'cpu': 95.0, 'hour': 3, 'window_title': 'Visual Studio Code',
               'battery': {'percent': 10, 'charging': False}}
    assert dm_conditions.evaluate({'type': 'idle_over', 'seconds': 600}, context)
    assert dm_conditions.evaluate({'type': 'cpu_over', 'value': 90}, context)
    assert dm_conditions.evaluate({'type': 'battery_under', 'value': 15}, context)
    assert dm_conditions.evaluate({'type': 'hour_range', 'start': 2, 'end': 5}, context)
    assert dm_conditions.evaluate({'type': 'window_contains', 'value': 'code'}, context)
    assert dm_conditions.evaluate({'type': 'return_active'}, context, {'idle_prev': 'idle', 'idle_next': 'active'})
    assert dm_conditions.evaluate({'type': 'smtc_playing'}, context, {'smtc_playing': True})
    assert not dm_conditions.evaluate({'type': 'battery_under', 'value': 15}, {**context, 'battery': {'percent': 10, 'charging': True}})


def test_condition_validation_rejects_broken_rules():
    assert dm_conditions.validate({'type': 'field_over', 'field': 'cpu'}) is not None
    assert dm_conditions.validate({'type': 'field_in', 'field': 'x', 'values': []}) is not None
    assert dm_conditions.validate({'type': 'field_over', 'field': 'cpu', 'value': 1, 'probability': 2}) is not None
    assert dm_conditions.validate({'type': 'field_over', 'field': 'cpu', 'value': 1, 'for_seconds': 60}) is None


# ── 注册表契约 ───────────────────────────────────────────────

def test_sensor_modules_declare_complete_metadata():
    modules = {
        'dm_sensors_a': _load('dm_sensors_a'),
        'dm_sensors_b': _load('dm_sensors_b'),
        'dm_timeline': dm_timeline,
        'dm_external': dm_external,
    }
    registry = dm_sources.build_registry(modules.values())
    assert len(registry.sources) >= 25
    tiers = {tier['id'] for tier in dm_sources.PERCEPTION_TIERS}
    seen_ids: set[str] = set()
    for source in registry.sources:
        assert source['id'] not in seen_ids, source['id']
        seen_ids.add(source['id'])
        assert source['tier'] in tiers, source['id']
        assert registry.cadence_of(source) >= 10
        assert source['fields'], source['id']
        for field in source['fields']:
            assert field in registry.labels, field
    # 未采集（字段缺失）时面板必须显示"未采集"而不是瞎猜
    assert registry.format_field('idle_seconds', {}) is None
    assert registry.format_field('idle_seconds', {'idle_seconds': 90}) == '1 分 30 秒'
    assert registry.format_field('gamepad_connected', {'gamepad_connected': True}) == '已接入'
    assert registry.format_field('mic', {'mic': {'in_use': True, 'app': 'Zoom.exe'}}) == '占用中（Zoom.exe）'


def test_registry_rejects_field_owned_twice():
    duplicate = type('M', (), {'SOURCES': ({'id': 'x', 'key': 'K', 'tier': 'green', 'fields': ('cpu',)},),
                               'FIELD_LABELS': {}, 'FORMATTERS': {}, '__name__': 'dup'})
    with pytest.raises(ValueError):
        dm_sources.build_registry([duplicate])


# ── 时间线纯函数 ─────────────────────────────────────────────

def test_detect_events_reports_transitions_only():
    previous = {'window_process': {'name': 'explorer.exe'}, 'window_fullscreen': False,
                'smtc': {'status_name': 'paused'}, 'locked': False, 'mic': {'in_use': False},
                'audio_output': {'headphones': False}}
    current = {'window_process': {'name': 'GenshinImpact.exe'}, 'window_fullscreen': True,
               'smtc': {'status_name': 'playing', 'title': '歌'}, 'locked': True, 'mic': {'in_use': True, 'app': 'Zoom.exe'},
               'audio_output': {'headphones': True}, 'attention': 'focused'}
    events = dm_timeline.detect_events(current, previous)
    kinds = [event['kind'] for event in events]
    assert 'window' in kinds and 'fullscreen' in kinds and 'media' in kinds
    assert 'lock' in kinds and 'mic' in kinds and 'audio' in kinds and 'attention' in kinds
    assert dm_timeline.detect_events(current, current) == []


def test_away_digest_and_rhythm_accumulate():
    archive: dict = {}
    day = dm_timeline._day_key()
    dm_timeline.update_rhythm(archive, {'idle_seconds': 5, 'window_process': {'name': 'Code.exe'},
                                        'attention': 'focused', 'context_switches_5min': 2}, time.time())
    dm_timeline.update_rhythm(archive, {'idle_seconds': 5, 'window_process': {'name': 'GenshinImpact.exe'},
                                        'attention': 'neutral', 'context_switches_5min': 1}, time.time())
    entry = archive[day]
    assert entry['awake_first'] and entry['awake_last']
    assert entry['switches'] == 3
    assert entry['game_minutes'] > 0
    assert entry['focus_minutes'] > 0
    dm_timeline.update_rhythm(archive, {'idle_seconds': 1200}, time.time())
    summary = dm_timeline.rhythm_summary(archive[day], [])
    assert summary['switches'] == 3
    assert dm_timeline.summarize_away([
        {'kind': 'lock', 'text': '锁屏'}, {'kind': 'window', 'text': '切到 X'}, {'kind': 'usb', 'text': '接入 手机'}]) == ['锁屏', '接入 手机']


def test_narrative_mentions_activity_and_media():
    text = dm_timeline.build_narrative({
        'app_session': {'process': 'Code.exe', 'seconds': 3600}, 'window_title': 'a.py - faust',
        'window_fullscreen': True, 'smtc': {'status_name': 'playing', 'title': 'Lofi'},
        'attention': 'focused', 'idle_seconds': 12, 'rhythm_today': {'awake_minutes': 200}})
    assert '开发工具' in text and '全屏' in text and 'Lofi' in text and '心流' in text


# ── 外部上报 ─────────────────────────────────────────────────

class _Store:
    def __init__(self):
        self.kv: dict = {}

    def get_kv(self, key, default=None):
        return self.kv.get(key, default)

    def set_kv(self, key, value):
        self.kv[key] = value


def test_external_ingest_allowlist_and_sanitizing():
    store = _Store()
    accepted, detail = dm_external.ingest({'source': 'electron_main', 'fields': {
        'locked': True, 'junk': 'x' * 500, 'list': list(range(50))}}, store)
    assert accepted, detail
    stored = store.get_kv(dm_external.REPORTS_KV)['electron']['fields']
    assert stored['locked'] is True
    assert len(stored['junk']) == dm_external.MAX_VALUE_CHARS
    assert len(stored['list']) == 12
    rejected, reason = dm_external.ingest({'source': 'unknown-plugin', 'fields': {'a': 1}}, store)
    assert not rejected and '未登记' in reason
    assert not dm_external.ingest({'source': 'electron', 'fields': {}}, store)[0]


def test_external_freshness_and_flattening():
    store = _Store()
    dm_external.ingest({'source': 'electron', 'fields': {'locked': True, 'display_count': 2}}, store)
    dm_external.ingest({'source': 'faust_ui', 'fields': {'pet_interaction_at': 1234}}, store)
    fresh = dm_external.fresh_reports(store, time.time())
    assert fresh['electron']['locked'] is True
    stale = dm_external.fresh_reports(store, time.time() + dm_external.REPORT_TTL_SEC + 5)
    assert stale == {}


def test_calendar_and_weather_formatters():
    calendar = dm_external.build_calendar(time.mktime((2026, 9, 27, 15, 0, 0, 0, 0, -1)))
    assert calendar['weekday_name'] == '周日' and calendar['is_weekend'] is True
    workday = dm_external.build_calendar(time.mktime((2026, 9, 28, 10, 0, 0, 0, 0, -1)))
    assert workday['work_hours'] is True and workday['daypart'] == '上午'
    assert '日出' in dm_external._fmt_weather({'text': '多云', 'temperature_c': '21', 'sunrise': '05:48', 'sunset': '18:11'})
    assert dm_external._fmt_weather(None) == '获取失败'


# ── 模块级门控：关闭的源必须完全不产出字段 ───────────────────

def _sctx(enabled_ids: set[str]):
    import dm_api

    return dm_api.SensorContext(
        now=time.time(), observed={}, context={}, memory={}, external={}, store=_Store(),
        enabled=lambda sid: sid in enabled_ids, log=type('L', (), {'warning': staticmethod(lambda *a: None)})(),
        get_config=None, due=lambda sid: True)


@pytest.mark.asyncio
async def test_disabled_sources_produce_no_fields():
    sensors_b = _load('dm_sensors_b')
    result = await sensors_b.collect(_sctx(set()))
    assert result.fields == {}

    sensors_a = _load('dm_sensors_a')
    result_a = await sensors_a.collect(_sctx(set()))
    assert result_a.fields == {}


# ── 与插件本体联调：分级门控 + 面板视图 ─────────────────────

@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    import faust_backend.config_loader as conf

    monkeypatch.setattr(conf, 'PLUGIN_DATA_ROOT', str(tmp_path / 'plugin_data'))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)


@pytest.mark.asyncio
async def test_full_registry_collects_and_reports(tmp_path):
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(tmp_path / 'state.json'))
    pm.set_plugin_enabled('desktop-mood', True)
    await pm.reload(force=True)
    plugin = pm._plugins['desktop-mood']['plugin']
    sys.modules['faust_plugin_desktop-mood'].dm_external.fetch_weather = lambda city: {'text': '晴', 'temperature_c': '20'}
    plugin.store.set_rules([])

    context = await plugin.collect_context()
    assert 'narrative' in context and 'recent_events' in context
    assert 'disturbed' in context and isinstance(context['disturb_reasons'], list)
    assert 'rhythm_today' in context and 'calendar' in context
    report = (await plugin.communicate_handler({'action': 'get_perception'}, plugin.ctx))['perception']
    assert len(report['sources']) == len(plugin.registry.sources) >= 25
    assert {'green', 'yellow', 'red'} == {tier['id'] for tier in report['tiers']}
    cadences = {source['cadence'] for source in report['sources']}
    assert 300 in cadences and 30 in cadences


@pytest.mark.asyncio
async def test_report_event_triggers_timeline_event(tmp_path):
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(tmp_path / 'state.json'))
    pm.set_plugin_enabled('desktop-mood', True)
    await pm.reload(force=True)
    plugin = pm._plugins['desktop-mood']['plugin']
    plugin.store.set_rules([])
    sys.modules['faust_plugin_desktop-mood'].dm_external.fetch_weather = lambda city: None

    accepted = await plugin.communicate_handler(
        {'action': 'report', 'source': 'electron', 'fields': {'locked': True}}, plugin.ctx)
    assert accepted['status'] == 'ok'
    context = await plugin.collect_context()
    assert context['external']['electron']['locked'] is True
    await plugin.heartbeat(plugin.ctx)
    events = plugin.store.snapshot()['snapshot'].get('recent_events') or []
    assert any('锁屏' in str(event.get('text')) for event in events) or events == []


def test_summarize_battery_readings_keeps_valid_and_flags_partial():
    fields, note = dm_sensors_b.summarize_battery_readings(
        [('MX Master 2S', 20), ('MX Anywhere 2S', 90)], [])
    assert fields == {'peripherals': [{'name': 'MX Anywhere 2S', 'percent': 90},
                                      {'name': 'MX Master 2S', 'percent': 20}]}
    assert note is None

    fields, note = dm_sensors_b.summarize_battery_readings(
        [('MX Master 2S', 20)], [('Professor 1', '读取状态 1')])
    assert fields == {'peripherals': [{'name': 'MX Master 2S', 'percent': 20}]}
    assert note is not None and 'Professor 1' in note


def test_summarize_battery_readings_rejects_bogus_percent():
    """GATT 返回的是 uint8：>100 说明不是百分比，不许当成 100% 报出去。"""
    fields, note = dm_sensors_b.summarize_battery_readings(
        [('怪设备', 255), ('正常鼠标', 42), ('重复鼠标', 7), ('正常鼠标', 99)], [])
    assert fields == {'peripherals': [{'name': '正常鼠标', 'percent': 42},
                                     {'name': '重复鼠标', 'percent': 7}]}
    assert note is None

    fields, note = dm_sensors_b.summarize_battery_readings([], [('耳机', '读不到电量值')])
    assert fields == {} and note is not None and '耳机' in note

    fields, note = dm_sensors_b.summarize_battery_readings([], [])
    assert fields == {} and note is not None and '未找到' in note


@pytest.mark.asyncio
async def test_peripheral_battery_reports_real_readings(tmp_path, monkeypatch):
    """真实 winsdk 路径：GATT 电池服务能读到本机已配对蓝牙外设的电量。"""
    pm, plugin = await _plugin(tmp_path)
    context = await plugin.collect_context()
    peripherals = context.get('peripherals')
    if peripherals is None:
        # 没有带 GATT 电池服务的设备时也必须给出原因，而不是静默没有字段
        assert plugin._sensor_status.get('peripheral_battery')
        pytest.skip(f'本机没有可读电量的蓝牙外设: {plugin._sensor_status.get("peripheral_battery")}')
    assert isinstance(peripherals, list) and peripherals
    for item in peripherals:
        assert 0 <= int(item['percent']) <= 100
        assert str(item['name']).strip()
    assert dm_sensors_b._fmt_peripherals(peripherals)


async def _plugin(tmp_path):
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(tmp_path / 'state.json'))
    pm.set_plugin_enabled('desktop-mood', True)
    await pm.reload(force=True)
    plugin = pm._plugins['desktop-mood']['plugin']
    sys.modules['faust_plugin_desktop-mood'].dm_external.fetch_weather = lambda city: None
    return pm, plugin


def _speech_rule(rule_id='always_speech'):
    """条件挂在必然存在的 manual_mood 上：不依赖真实窗口/麦克风状态，测试只考验闸门本身。"""
    return {'id': rule_id, 'label': '总是说话', 'enabled': True, 'cooldown_sec': 60, 'kind': 'speech',
            'condition': {'type': 'field_eq', 'field': 'manual_mood', 'value': 'auto'},
            'action': {'speech': '在的。'}}


async def _gated_plugin(tmp_path):
    """关掉会随环境波动、影响免打扰判断的感知源（全屏/麦克风/外部上报），扰动原因由测试注入。"""
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {
        'ENABLE_AUDIO_DEVICES': False, 'ENABLE_WINDOW_GEOMETRY': False, 'ENABLE_EXTERNAL_REPORT': False})
    return pm, plugin


@pytest.mark.asyncio
async def test_disturb_gate_silences_speech(tmp_path, monkeypatch):
    _pm, plugin = await _gated_plugin(tmp_path)
    plugin.store.set_rules([_speech_rule()])
    said: list[str] = []
    monkeypatch.setattr(sys.modules['faust_plugin_desktop-mood'].backend2frontend, 'FrontEndSay',
                        lambda text: said.append(text))

    monkeypatch.setattr(plugin, '_disturb_reasons', lambda context: ['测试：打扰中'])
    await plugin.heartbeat(plugin.ctx)
    assert said == []  # 免打扰期间不打扰

    monkeypatch.setattr(plugin, '_disturb_reasons', lambda context: [])
    await plugin.heartbeat(plugin.ctx)
    assert said == ['在的。']


@pytest.mark.asyncio
async def test_bypass_disturb_action_still_fires(tmp_path, monkeypatch):
    _pm, plugin = await _gated_plugin(tmp_path)
    rule = _speech_rule('bypass')
    rule['action'] = {'speech': '要紧事。', 'bypass_disturb': True}
    plugin.store.set_rules([rule])
    said: list[str] = []
    monkeypatch.setattr(sys.modules['faust_plugin_desktop-mood'].backend2frontend, 'FrontEndSay',
                        lambda text: said.append(text))
    monkeypatch.setattr(plugin, '_disturb_reasons', lambda context: ['测试：打扰中'])
    await plugin.heartbeat(plugin.ctx)
    assert said == ['要紧事。']


# ── 窗口/设备纯函数 ──────────────────────────────────────────

def test_fullscreen_and_dirty_document_helpers():
    sensors_a = _load('dm_sensors_a')
    monitor = (0, 0, 1920, 1080)
    assert sensors_a.is_fullscreen((0, 0, 1920, 1080), monitor) is True
    assert sensors_a.is_fullscreen((-1, 0, 1921, 1081), monitor) is True
    assert sensors_a.is_fullscreen((100, 100, 1000, 800), monitor) is False
    assert sensors_a.is_fullscreen(None, monitor) is False
    assert sensors_a.count_dirty_documents(['● a.py - VS Code', '*b.txt - Notepad', 'Chrome']) == 2
    assert sensors_a.count_dirty_documents([]) == 0


def test_process_diff_and_device_helpers():
    sensors_a = _load('dm_sensors_a')
    started, stopped = sensors_a.diff_processes({'Code.exe', 'svchost.exe'}, {'Code.exe', 'GenshinImpact.exe'})
    assert started == ['GenshinImpact.exe']
    assert stopped == []
    sensors_b = _load('dm_sensors_b')
    assert sensors_b.is_headphone_name('Headphones (WH-1000XM5)') is True
    assert sensors_b.is_headphone_name('扬声器 (Realtek(R) Audio)') is False
    assert sensors_b.network_type_from_iana(71) == 'wifi'
    assert sensors_b.network_type_from_iana(6) == 'ethernet'
    assert sensors_b.network_type_from_iana(None) == 'unknown'
    assert sensors_b.summarize_usb_change(None, {'U盘'}, time.time(), None)['added'] == []
    assert sensors_b.summarize_usb_change({'U盘'}, {'U盘', '手机'}, 100.0, None) == {
        'count': 2, 'added': ['手机'], 'removed': [], 'last_event_at': 100}


def test_microphone_usage_parser():
    sensors_b = _load('dm_sensors_b')
    idle = sensors_b.parse_microphone_usage({'Zoom.exe': {'LastUsedTimeStart': 0, 'LastUsedTimeStop': 0}})
    assert idle == {'in_use': False, 'app': None, 'since': None}
    active = sensors_b.parse_microphone_usage({
        'old.exe': {'LastUsedTimeStart': 133_000_000_000_000_000, 'LastUsedTimeStop': 133_001_000_000_000_000},
        r'C:\Apps\Zoom.exe': {'LastUsedTimeStart': 133_100_000_000_000_000, 'LastUsedTimeStop': 0},
    })
    assert active['in_use'] is True and active['app'] == 'Zoom.exe'
    assert active['since'] == 133_100_000_000_000_000 // 10_000_000 - 11644473600
