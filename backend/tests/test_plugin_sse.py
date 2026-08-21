from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.plugin_system import PluginManager
from faust_backend.plugin_system.manager import PluginLoadError
from faust_backend.plugin_system.plugin_base import FaustPlugin
from faust_backend.plugin_system.interfaces import PluginManifest

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins'
STATE_FILE = Path(__file__).resolve().parents[2] / 'logs' / 'plugin-test-state-sse.json'


class SsePlugin(FaustPlugin):
    def sse_communicate_handler(self, params, ctx):
        async def gen():
            yield {"n": 1, "params": params}
            yield {"n": 2}
        return gen()


class BrokenSsePlugin(FaustPlugin):
    def sse_communicate_handler(self, params, ctx):
        return {"not": "a generator"}


def _manager_with(plugin_id: str, plugin) -> PluginManager:
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=STATE_FILE)
    pm._plugins[plugin_id] = {
        "manifest": PluginManifest(plugin_id=plugin_id, name=plugin_id, enabled=True),
        "plugin": plugin,
        "ctx": object(),
    }
    return pm


def test_open_sse_streams_events():
    pm = _manager_with('sse-test', SsePlugin())
    agen, abort = pm.open_sse('sse-test', {"a": "1"})
    assert not abort.is_set()

    async def collect():
        items = [item async for item in agen]
        return items

    items = asyncio.run(collect())
    assert items == [{"n": 1, "params": {"a": "1"}}, {"n": 2}]
    pm.close_sse('sse-test', abort)
    assert pm._sse_abort_events == {}


def test_open_sse_rejects_missing_plugin():
    pm = _manager_with('sse-test', SsePlugin())
    with pytest.raises(PluginLoadError, match='not found'):
        pm.open_sse('no-such-plugin', {})


def test_open_sse_rejects_plugin_without_handler():
    pm = _manager_with('plain', FaustPlugin())
    with pytest.raises(PluginLoadError, match='does not support'):
        pm.open_sse('plain', {})


def test_open_sse_rejects_non_generator_result():
    pm = _manager_with('broken', BrokenSsePlugin())
    with pytest.raises(PluginLoadError, match='must return an async generator'):
        pm.open_sse('broken', {})


def test_abort_all_sse_sets_events_and_clears():
    pm = _manager_with('sse-test', SsePlugin())
    _, abort1 = pm.open_sse('sse-test', {})
    _, abort2 = pm.open_sse('sse-test', {})
    assert len(pm._sse_abort_events['sse-test']) == 2
    pm.abort_all_sse()
    assert abort1.is_set()
    assert abort2.is_set()
    assert pm._sse_abort_events == {}


def test_reload_aborts_active_sse():
    import asyncio
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=STATE_FILE)
    asyncio.run(pm.reload())
    pm._plugins['sse-test'] = {
        "manifest": PluginManifest(plugin_id='sse-test', name='sse-test', enabled=True),
        "plugin": SsePlugin(),
        "ctx": object(),
    }
    _, abort = pm.open_sse('sse-test', {})
    asyncio.run(pm.reload(force=True))
    assert abort.is_set()
