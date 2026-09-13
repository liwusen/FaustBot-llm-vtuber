from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.plugin_system import PluginManager
from faust_backend.tools.vfs import get_faustbot_vfs
import faust_backend.config_loader as conf


REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins'
STATE_FILE = Path(__file__).resolve().parents[2] / 'logs' / 'plugin-test-state-fun.json'


@pytest.fixture(autouse=True)
def _isolate_plugin_data(tmp_path, monkeypatch):
    """隔离插件数据目录与启用状态到临时目录，避免测试往真实 ~/.faustbot 注入数据（如 RSS example.com feed）。"""
    monkeypatch.setattr(conf, "PLUGIN_DATA_ROOT", str(tmp_path / "plugin_data"))
    monkeypatch.setattr(sys.modules[__name__], "STATE_FILE", str(tmp_path / "plugin-state.json"))


async def _build_manager() -> PluginManager:
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=STATE_FILE)
    await pm.reload()
    return pm


@pytest.mark.asyncio
async def test_fun_plugins_load():
    pm = await _build_manager()
    for pid in ('emotion-engine', 'rss-watcher', 'desktop-mood'):
        pm.set_plugin_enabled(pid, True)
    await pm.reload(force=True)
    ids = {item['id'] for item in pm.list_plugins()}
    assert 'emotion-engine' in ids
    assert 'rss-watcher' in ids
    assert 'desktop-mood' in ids




@pytest.mark.asyncio
async def test_rss_store_basic_flow():
    pm = await _build_manager()
    pm.set_plugin_enabled('rss-watcher', True)
    await pm.reload(force=True)
    plugin = pm._plugins['rss-watcher']['plugin']
    plugin.store.add_feed('https://example.com/feed.xml', 'Example', 'tech')
    feeds = plugin.store.list_feeds()
    assert any(feed['name'] == 'Example' for feed in feeds)
    plugin.store.insert_items(int(feeds[0]['id']), [{'title': 'Example/Item', 'link': 'https://example.com/1', 'summary': 'hello', 'published': 1721400000}], max_items=500)
    await plugin._write_item_to_vfs({'title': 'Example/Item', 'link': 'https://example.com/1', 'summary': 'hello', 'published': 1721400000}, 'Example')
    await plugin._write_daily_index()

    vfs = await get_faustbot_vfs(refresh=True)
    index_text = await vfs.read_text('/plugins/rss-watcher/index.md', default='')
    feed_doc = await vfs.read_text('/plugins/rss-watcher/RSS-FEED-ExampleItem-20240719.md', default='')
    assert 'Example/Item' in feed_doc # type: ignore
    assert 'RSS Watcher Index' in index_text # type: ignore
    digest = plugin.store.build_digest(limit=3)
    assert 'summary' in digest


@pytest.mark.asyncio
async def test_desktop_context_and_vfs():
    pm = await _build_manager()
    pm.set_plugin_enabled('desktop-mood', True)
    await pm.reload(force=True)
    plugin = pm._plugins['desktop-mood']['plugin']

    context = await plugin.collect_context()
    assert 'idle_seconds' in context
    assert 'window_title' in context
    assert 'window_process' in context
    vfs = await get_faustbot_vfs(refresh=True)
    await plugin.heartbeat(plugin.ctx)
    payload = json.loads(await vfs.read_text('/plugins/desktop-context.json', default='{}')) # type: ignore
    assert 'hour' in payload
    assert (await vfs.read_text('/plugins/desktop-mood.md', default='')).startswith('# Desktop Mood')


@pytest.fixture
def _isolate_home(tmp_path, monkeypatch):
    """把 Path.home() 指到 tmp_path，避免 desktop-mood 规则文件写入真实 ~/.faustbot。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


async def _desktop_mood_plugin(pm: PluginManager):
    pm.set_plugin_enabled('desktop-mood', True)
    await pm.reload(force=True)
    return pm._plugins['desktop-mood']['plugin']


@pytest.mark.asyncio
async def test_desktop_mood_rule_draft_then_reload(_isolate_home):
    pm = await _build_manager()
    plugin = await _desktop_mood_plugin(pm)
    vfs = await get_faustbot_vfs(refresh=True)
    rules_file = Path.home() / '.faustbot' / 'desktop-mood.rules.json'
    draft_path = '/plugins/desktop-mood/rules.json'
    reload_path = '/plugins/desktop-mood/reload'

    draft = json.loads(await vfs.read_text(draft_path, default='[]'))
    assert draft and any(rule['id'] == 'idle_yawn' for rule in draft)
    disk_before = rules_file.read_text(encoding='utf-8')

    new_rule = {
        "id": "test_hydrate", "label": "测试喝水", "enabled": True, "cooldown_sec": 900,
        "kind": "speech",
        "condition": {"type": "window_contains", "value": "Code"},
        "action": {"speech": "喝水"},
    }
    await vfs.write(draft_path, json.dumps(list(draft) + [new_rule], ensure_ascii=False, indent=2))

    # 草稿阶段：内存与磁盘都还没变
    assert not any(rule['id'] == 'test_hydrate' for rule in plugin.store.snapshot()['rules'])
    assert rules_file.read_text(encoding='utf-8') == disk_before

    # 读 reload 节点 → 提交生效
    report = await vfs.read(reload_path)
    assert 'Desktop Mood 规则提交: 成功' in report
    assert '草稿与生效: 已同步' in report
    assert any(rule['id'] == 'test_hydrate' for rule in plugin.store.snapshot()['rules'])
    assert any(rule['id'] == 'test_hydrate' for rule in json.loads(rules_file.read_text(encoding='utf-8')))

    # 非法草稿：提交失败且不污染已生效规则
    await vfs.write(draft_path, '{ not json')
    failed = await vfs.read(reload_path)
    assert 'Desktop Mood 规则提交: 失败' in failed
    assert '草稿: 保留未提交' in failed
    assert any(rule['id'] == 'test_hydrate' for rule in plugin.store.snapshot()['rules'])

    # 写入 reload 节点同样提交（内容被忽略）
    await vfs.write(draft_path, json.dumps(draft, ensure_ascii=False, indent=2))
    await vfs.write(reload_path, 'apply')
    assert not any(rule['id'] == 'test_hydrate' for rule in plugin.store.snapshot()['rules'])
    assert not any(rule['id'] == 'test_hydrate' for rule in json.loads(rules_file.read_text(encoding='utf-8')))

    # edit 草稿节点走 edit_handler，同样只暂存
    text = await vfs.read_text(draft_path)
    await vfs.edit(draft_path, text.replace('idle_yawn', 'idle_yawn_renamed'))
    assert any(rule['id'] == 'idle_yawn_renamed' for rule in json.loads(await vfs.read_text(draft_path)))
    assert not any(rule['id'] == 'idle_yawn_renamed' for rule in plugin.store.snapshot()['rules'])


@pytest.mark.asyncio
async def test_desktop_mood_probability_gate(_isolate_home):
    pm = await _build_manager()
    plugin = await _desktop_mood_plugin(pm)
    context = {'window_title': 'Visual Studio Code'}

    def rule(probability=None):
        condition = {"type": "window_contains", "value": "code"}
        if probability is not None:
            condition["probability"] = probability
        return {"id": "p", "kind": "speech", "condition": condition, "action": {"speech": "hi"}}

    assert plugin._match_rule(rule(0.0), context, 'active', 'active') is False
    assert plugin._match_rule(rule(1.0), context, 'active', 'active') is True
    assert plugin._match_rule(rule(), context, 'active', 'active') is True
    assert plugin._match_rule(rule(0.5), {'window_title': 'Notepad'}, 'active', 'active') is False


@pytest.mark.asyncio
async def test_plugin_reload_skips_when_unchanged():
    pm = await _build_manager()
    for pid in ('emotion-engine', 'rss-watcher', 'desktop-mood'):
        pm.set_plugin_enabled(pid, True)
    await pm.reload(force=True)
    summary = await pm.reload()
    assert summary.get('skipped') is True
    pm.set_plugin_enabled('emotion-engine', False)
    summary2 = await pm.reload(force=True)
    assert summary2.get('skipped') is False
    assert 'emotion-engine' in pm._plugins
    assert pm._plugins['emotion-engine']['plugin'] is None
    summary3 = await pm.reload()
    assert summary3.get('skipped') is True


@pytest.mark.asyncio
async def test_builtin_plugin_data_dir_and_rss_feed_update():
    pm = await _build_manager()
    pm.set_plugin_enabled('rss-watcher', True)
    await pm.reload(force=True)
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


@pytest.mark.asyncio
async def test_plugin_communicate_dispatch():
    pm = await _build_manager()
    for pid in ('emotion-engine', 'rss-watcher', 'desktop-mood'):
        pm.set_plugin_enabled(pid, True)
    await pm.reload(force=True)

    emotion = await pm.communicate('emotion-engine', {'action': 'get_state'})
    assert emotion['status'] == 'ok'
    assert 'vector' in emotion

    await pm.communicate('desktop-mood', {'action': 'set_mood', 'mood': 'warm'})
    desktop_state = await pm.communicate('desktop-mood', {'action': 'get_state'})
    assert desktop_state['status'] == 'ok'
    assert desktop_state['state']['manual_mood'] == 'warm'

    created = await pm.communicate('rss-watcher', {
        'action': 'create_feed',
        'url': 'https://example.com/feed.xml',
        'name': 'Example',
        'category': 'tech',
    })
    assert created['status'] == 'ok'
    feed_id = int(created['item']['id'])

    updated = await pm.communicate('rss-watcher', {
        'action': 'update_feed',
        'feed_id': feed_id,
        'url': 'https://example.com/feed-2.xml',
        'name': 'Example 2',
        'category': 'news',
    })
    assert updated['status'] == 'ok'
    assert updated['item']['name'] == 'Example 2'

    feeds = await pm.communicate('rss-watcher', {'action': 'get_feeds'})
    assert feeds['status'] == 'ok'
    assert any(int(item['id']) == feed_id for item in feeds['items'])


@pytest.mark.asyncio
async def test_builtin_plugin_vfs_nodes_have_description(_isolate_home):
    """内置插件注册的 VFS 节点都带 description，且 read(with_metadata=True) 列举可见。"""
    from faust_backend.tools.read import read

    pm = await _build_manager()
    for pid in ('emotion-engine', 'rss-watcher', 'desktop-mood'):
        pm.set_plugin_enabled(pid, True)
    await pm.reload(force=True)

    vfs = await get_faustbot_vfs(refresh=True)
    expected = {
        '/plugins/desktop-mood.md': 'Desktop Mood',
        '/plugins/desktop-context.json': '桌面上下文',
        '/plugins/desktop-mood/rules.json': '草稿',
        '/plugins/desktop-mood/rules.md': '指南',
        '/plugins/desktop-mood/reload': '提交',
        '/plugins/emotion-engine.md': 'Emotion Engine',
        '/plugins/emotion-engine-state.json': '情绪向量',
        '/plugins/rss-watcher.md': 'RSS Watcher',
        '/plugins/rss-watcher/index.md': '索引',
    }
    for path, snippet in expected.items():
        node = await vfs.get_node(path)
        assert node is not None, path
        assert snippet in node.description, (path, node.description)

    listing = await read.ainvoke(
        {"uri": "faustbot://plugins/desktop-mood/", "with_metadata": True}
    )
    assert "草稿" in listing
    assert "提交" in listing


@pytest.mark.asyncio
async def test_desktop_mood_context_keeps_description_across_writes(_isolate_home):
    """上下文节点持续重写，描述不被后续写入清空。"""
    pm = await _build_manager()
    plugin = await _desktop_mood_plugin(pm)
    vfs = await get_faustbot_vfs(refresh=True)

    await plugin.heartbeat(plugin.ctx)
    node = await vfs.get_node('/plugins/desktop-context.json')
    assert node is not None
    assert '桌面上下文' in node.description


@pytest.mark.asyncio
async def test_rss_feed_node_describes_item(_isolate_home):
    """每条 RSS 正文节点带来源描述（正文节点名含标题/日期，无法静态描述）。"""
    pm = await _build_manager()
    pm.set_plugin_enabled('rss-watcher', True)
    await pm.reload(force=True)
    plugin = pm._plugins['rss-watcher']['plugin']

    await plugin._write_item_to_vfs(
        {'title': 'ExampleItem', 'link': 'https://example.com/a', 'published': 1721361600,
         'summary': 'text'},
        'Example/Item',
    )
    vfs = await get_faustbot_vfs(refresh=True)
    node = await vfs.get_node('/plugins/rss-watcher/RSS-FEED-ExampleItem-20240719.md')
    assert node is not None
    assert 'Example/Item' in node.description
