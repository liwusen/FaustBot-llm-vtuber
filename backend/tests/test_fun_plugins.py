from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.plugin_system import PluginManager
from faust_backend.tools.vfs import get_faustbot_vfs, run_coro_sync
import faust_backend.config_loader as conf


REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins'
STATE_FILE = Path(__file__).resolve().parents[2] / 'logs' / 'plugin-test-state-fun.json'


def _build_manager() -> PluginManager:
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=STATE_FILE)
    pm.reload()
    return pm


def test_fun_plugins_load():
    pm = _build_manager()
    for pid in ('emotion-engine', 'rss-watcher', 'desktop-mood'):
        pm.set_plugin_enabled(pid, True)
    pm.reload(force=True)
    ids = {item['id'] for item in pm.list_plugins()}
    assert 'emotion-engine' in ids
    assert 'rss-watcher' in ids
    assert 'desktop-mood' in ids




def test_rss_store_basic_flow():
    pm = _build_manager()
    pm.set_plugin_enabled('rss-watcher', True)
    pm.reload(force=True)
    plugin = pm._plugins['rss-watcher']['plugin']
    plugin.store.add_feed('https://example.com/feed.xml', 'Example', 'tech')
    feeds = plugin.store.list_feeds()
    assert any(feed['name'] == 'Example' for feed in feeds)
    plugin.store.insert_items(int(feeds[0]['id']), [{'title': 'Example/Item', 'link': 'https://example.com/1', 'summary': 'hello', 'published': 1721400000}], max_items=500)
    plugin._write_item_to_vfs({'title': 'Example/Item', 'link': 'https://example.com/1', 'summary': 'hello', 'published': 1721400000}, 'Example')
    plugin._write_daily_index()
    vfs = get_faustbot_vfs(refresh=True)
    index_text = run_coro_sync(vfs.read_text('/plugins/rss-watcher/index.md', default=''))
    feed_doc = run_coro_sync(vfs.read_text('/plugins/rss-watcher/RSS-FEED-ExampleItem-20240719.md', default=''))
    assert 'Example/Item' in feed_doc # type: ignore
    assert 'RSS Watcher Index' in index_text # type: ignore
    digest = plugin.store.build_digest(limit=3)
    assert 'summary' in digest


def test_desktop_context_and_vfs():
    pm = _build_manager()
    pm.set_plugin_enabled('desktop-mood', True)
    pm.reload(force=True)
    plugin = pm._plugins['desktop-mood']['plugin']
    context = plugin.collect_context()
    assert 'idle_seconds' in context
    assert 'window_title' in context
    vfs = get_faustbot_vfs(refresh=True)
    plugin.heartbeat(plugin.ctx)
    payload = json.loads(run_coro_sync(vfs.read_text('/plugins/desktop-context.json', default='{}'))) # type: ignore
    assert 'hour' in payload
    assert run_coro_sync(vfs.read_text('/plugins/desktop-mood.md', default='')).startswith('# Desktop Mood')


def test_plugin_reload_skips_when_unchanged():
    pm = _build_manager()
    for pid in ('emotion-engine', 'rss-watcher', 'desktop-mood'):
        pm.set_plugin_enabled(pid, True)
    pm.reload(force=True)
    summary = pm.reload()
    assert summary.get('skipped') is True
    pm.set_plugin_enabled('emotion-engine', False)
    summary2 = pm.reload(force=True)
    assert summary2.get('skipped') is False
    assert 'emotion-engine' in pm._plugins
    assert pm._plugins['emotion-engine']['plugin'] is None
    summary3 = pm.reload()
    assert summary3.get('skipped') is True


def test_builtin_plugin_data_dir_and_rss_feed_update():
    pm = _build_manager()
    pm.set_plugin_enabled('rss-watcher', True)
    pm.reload(force=True)
    rss_ctx = pm._plugins['rss-watcher']['ctx']
    rss_plugin = pm._plugins['rss-watcher']['plugin']
    assert rss_ctx.plugin_data_dir == Path(conf.PLUGIN_DATA_ROOT) / 'rss-watcher'
    assert rss_plugin.store._data_dir == Path(conf.PLUGIN_DATA_ROOT) / 'rss-watcher'
    feed = rss_plugin.store.add_feed('https://example.com/feed.xml', 'Example', 'tech')
    updated = rss_plugin.store.update_feed(int(feed['id']), url='https://example.com/feed-2.xml', name='Example 2', category='news')
    assert updated is not None
    assert updated['url'] == 'https://example.com/feed-2.xml'
    assert updated['name'] == 'Example 2'
    assert updated['category'] == 'news'


def test_plugin_communicate_dispatch():
    pm = _build_manager()
    for pid in ('emotion-engine', 'rss-watcher', 'desktop-mood'):
        pm.set_plugin_enabled(pid, True)
    pm.reload(force=True)

    emotion = asyncio.run(pm.communicate('emotion-engine', {'action': 'get_state'}))
    assert emotion['status'] == 'ok'
    assert 'vector' in emotion

    asyncio.run(pm.communicate('desktop-mood', {'action': 'set_mood', 'mood': 'warm'}))
    desktop_state = asyncio.run(pm.communicate('desktop-mood', {'action': 'get_state'}))
    assert desktop_state['status'] == 'ok'
    assert desktop_state['state']['manual_mood'] == 'warm'

    created = asyncio.run(pm.communicate('rss-watcher', {
        'action': 'create_feed',
        'url': 'https://example.com/feed.xml',
        'name': 'Example',
        'category': 'tech',
    }))
    assert created['status'] == 'ok'
    feed_id = int(created['item']['id'])

    updated = asyncio.run(pm.communicate('rss-watcher', {
        'action': 'update_feed',
        'feed_id': feed_id,
        'url': 'https://example.com/feed-2.xml',
        'name': 'Example 2',
        'category': 'news',
    }))
    assert updated['status'] == 'ok'
    assert updated['item']['name'] == 'Example 2'

    feeds = asyncio.run(pm.communicate('rss-watcher', {'action': 'get_feeds'}))
    assert feeds['status'] == 'ok'
    assert any(int(item['id']) == feed_id for item in feeds['items'])
