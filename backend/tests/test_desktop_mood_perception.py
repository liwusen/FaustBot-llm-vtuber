"""desktop-mood 感知分级：等级/单源开关必须真的决定采集范围与面板视图。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.plugin_system import PluginManager

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins'


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """隔离插件数据目录与规则文件路径，避免测试写真实用户数据。"""
    import faust_backend.config_loader as conf

    monkeypatch.setattr(conf, "PLUGIN_DATA_ROOT", str(tmp_path / "plugin_data"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)


async def _plugin(tmp_path):
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(tmp_path / "plugin-state.json"))
    pm.set_plugin_enabled('desktop-mood', True)
    await pm.reload(force=True)
    plugin = pm._plugins['desktop-mood']['plugin']
    # 天气是联网调用：测试里替成固定值，保持采集链路但不发请求
    sys.modules['faust_plugin_desktop-mood']._fetch_weather = lambda city: {'text': '晴', 'temperature_c': '23'}
    plugin.store.set_rules([])  # 心跳不应在测试里触发真实规则动作
    return pm, plugin


def _load_impl_module():
    """独立加载 impl.py，用于纯函数（分类器）测试，不启插件。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        'desktop_mood_impl_under_test', REPO_PLUGIN_DIR / 'desktop-mood' / 'impl.py'
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_classify_clipboard_text():
    classify = _load_impl_module().classify_clipboard_text
    assert classify('') == 'empty'
    assert classify('   ') == 'empty'
    assert classify('https://example.com/a?b=1') == 'url'
    assert classify('https://example.com/foo bar') == 'text'
    assert classify('Traceback (most recent call last):\n  File "a.py", line 1\nValueError: bad') == 'error'
    assert classify('TypeError: unsupported operand\n  at foo (index.js:3:1)') == 'error'
    assert classify('def main():\n    import os\n    return os.getcwd()') == 'code'
    assert classify('今天午饭吃什么') == 'text'


@pytest.mark.asyncio
async def test_default_tiers_collect_green_and_yellow_only(tmp_path):
    _pm, plugin = await _plugin(tmp_path)
    context = await plugin.collect_context()

    assert context['perception']['tiers'] == {'green': True, 'yellow': True, 'red': False}
    assert 'clipboard' in context['perception']['disabled_sources']
    assert 'window_title' in context  # 黄色级
    assert 'window_process' in context  # 绿色级
    assert 'idle_seconds' in context
    assert 'hour' in context
    assert 'clipboard' not in context  # 红色级默认关闭


@pytest.mark.asyncio
async def test_green_tier_off_hides_metadata_senses(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_TIER_GREEN': False})
    context = await plugin.collect_context()

    assert context['perception']['tiers']['green'] is False
    for field in ('cpu', 'memory', 'battery', 'idle_seconds', 'window_process', 'holiday'):
        assert field not in context
    assert 'hour' in context  # 基础时钟不依赖感知源
    assert 'manual_mood' in context
    assert 'window_title' in context  # 黄色级未受影响
    assert 'system_load' in context['perception']['disabled_sources']


@pytest.mark.asyncio
async def test_yellow_tier_off_keeps_green_senses(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_TIER_YELLOW': False})
    context = await plugin.collect_context()

    for field in ('window_title', 'smtc', 'weather'):
        assert field not in context
    assert 'window_process' in context
    assert 'idle_seconds' in context


@pytest.mark.asyncio
async def test_single_source_switch_beats_enabled_tier(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_WINDOW_PROCESS': False})
    context = await plugin.collect_context()

    assert 'window_process' not in context
    assert 'idle_seconds' in context  # 同级其他源不受影响
    assert 'window_process' in context['perception']['disabled_sources']


@pytest.mark.asyncio
async def test_red_tier_enables_clipboard_reader(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_TIER_RED': True, 'ENABLE_CLIPBOARD_WATCH': True})
    context = await plugin.collect_context()

    assert context['perception']['tiers']['red'] is True
    assert 'clipboard' not in context['perception']['disabled_sources']
    clipboard = context.get('clipboard')
    if clipboard is not None:  # 无剪贴板访问权限的环境下允许为 None
        assert clipboard['kind'] in {'empty', 'image', 'files', 'url', 'error', 'code', 'text'}
        assert 'changed_at' in clipboard


@pytest.mark.asyncio
async def test_perception_report_flags_and_values(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_TIER_GREEN': False})
    await plugin.heartbeat(plugin.ctx)
    report = await plugin.perception_report()

    by_id = {source['id']: source for source in report['sources']}
    # 单源开关仍是"开"，但整级关闭 → 未在采集
    assert by_id['idle']['enabled'] is True
    assert by_id['idle']['collecting'] is False
    assert by_id['idle']['fields'][0]['value'] is None  # 未采集
    assert by_id['window_title']['collecting'] is True
    assert by_id['window_title']['fields'][0]['value'] is not None
    assert report['updated_at'] > 0
    tiers = {tier['id']: tier['enabled'] for tier in report['tiers']}
    assert tiers == {'green': False, 'yellow': True, 'red': False}


@pytest.mark.asyncio
async def test_heartbeat_keeps_idle_state_when_idle_sense_off(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    plugin.store.set_last_idle_state('idle')
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_IDLE_WATCH': False})
    await plugin.heartbeat(plugin.ctx)
    # 空闲感知关闭时不能把状态伪造成 active，否则 return_active 规则会凭空触发
    assert plugin.store.get_last_idle_state() == 'idle'
