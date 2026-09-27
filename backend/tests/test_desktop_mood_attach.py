"""即时附加（attach）与数据域 VFS 视图的测试。

覆盖用户提的三条硬约束：不做用户输入分析、追加总长 ≤100 字、不重复且只放变化过的内容；
以及 attach 只为用户消息/前台触发器生效。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins' / 'desktop-mood'
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dm_attach  # noqa: E402
import dm_sources  # noqa: E402
import faust_backend.config_loader as conf  # noqa: E402
from faust_backend.plugin_system import PluginManager  # noqa: E402
from faust_backend.tools.vfs import get_faustbot_vfs  # noqa: E402

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins'
QUEUE_KV = 'attach.queue'
RING_KV = 'attach.ring'


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """隔离插件数据目录与 Path.home()，绝不碰真实的 ~/.faustbot。"""
    monkeypatch.setattr(conf, 'PLUGIN_DATA_ROOT', str(tmp_path / 'plugin_data'))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)


async def _plugin(tmp_path):
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(tmp_path / 'state.json'))
    pm.set_plugin_enabled('desktop-mood', True)
    await pm.reload(force=True)
    plugin = pm._plugins['desktop-mood']['plugin']
    sys.modules['faust_plugin_desktop-mood'].dm_external.fetch_weather = lambda city: None
    plugin.store.set_rules([])
    return pm, plugin


def _attach_rule(text: str, ttl_sec: int = 1800, priority: int = 90):
    """条件挂在必然存在的 manual_mood 上，只考验 attach 本身。"""
    return {'id': 'battery_attach', 'label': '低电量随行提醒', 'enabled': True, 'cooldown_sec': 60,
            'kind': 'attach', 'condition': {'type': 'field_eq', 'field': 'manual_mood', 'value': 'auto'},
            'action': {'attach': {'text': text, 'ttl_sec': ttl_sec, 'priority': priority}}}


def _attach_block(text: str) -> str:
    """取出桌面附加块（desktop-mood 的 priority 最大，永远附在最后）。"""
    marker = '\n\n[桌面] '
    return '[桌面] ' + text.rsplit(marker, 1)[1] if marker in text else ''


def _queue_item(text: str, ttl_sec: int = 600, priority: int = 90) -> dict:
    now = int(time.time())
    return {'text': text, 'at': now, 'expires_at': now + ttl_sec, 'priority': priority}


# ── 纯函数：预算 / 排序 / 去重 ─────────────────────────────────

def test_compose_never_exceeds_budget_and_truncates_oversized_line():
    candidates = [dm_attach.Candidate(f'field:f{index}', '字段内容' * 30, 50, 0, 'field')
                  for index in range(10)]
    block, chosen = dm_attach.compose(candidates, 100)
    assert 0 < len(block) <= 100
    assert len(chosen) == 1
    assert chosen[0].text.endswith('…')


def test_compose_orders_rule_then_event_then_field():
    candidates = [
        dm_attach.Candidate('field:cpu', 'CPU: 90%', 100, 0, 'field'),
        dm_attach.Candidate('event:1', '10:00 切到 VS Code', 10, 1, 'event'),
        dm_attach.Candidate('rule:x', '电量低了', 1, 2, 'rule'),
    ]
    block, chosen = dm_attach.compose(candidates, 100)
    assert [item.kind for item in chosen] == ['rule', 'event', 'field']
    assert block.startswith('[桌面] 电量低了')


def test_compose_packs_many_lines_within_budget():
    candidates = [dm_attach.Candidate(f'field:{index}', f'x{index}', 50, 0, 'field')
                  for index in range(50)]
    block, chosen = dm_attach.compose(candidates, 20)
    assert len(block) <= 20
    assert 1 <= len(chosen) <= dm_attach.MAX_LINES


def test_compose_drops_duplicate_lines():
    candidates = [dm_attach.Candidate('a', '同样的话', 90, 0, 'rule'),
                  dm_attach.Candidate('b', '同样的话', 10, 0, 'field')]
    block, chosen = dm_attach.compose(candidates, 100)
    assert block == '[桌面] 同样的话'
    assert len(chosen) == 1


def test_field_candidates_only_report_changed_values():
    class _Registry:
        labels = {'battery': '电池'}

        def attach_weight(self, source):
            return 95

        def fields_of(self, source):
            return tuple(source['fields'])

        def attach_fields_of(self, source):
            return tuple(source['fields'])

        def format_field(self, field, observed):
            return f'{observed[field]}'

    sources = [{'id': 'battery', 'fields': ('battery',)}]
    candidate = dm_attach.field_candidates(_Registry(), sources, {'battery': '18%'}, {}, [])
    assert [item.text for item in candidate] == ['电池: 18%']
    # 值没变 → 不再成为候选
    again = dm_attach.field_candidates(_Registry(), sources, {'battery': '18%'},
                                       {'field:battery': '电池: 18%'}, [])
    assert again == []
    # 已经发过同样的行（去重环）→ 跳过
    ring = [{'hash': dm_attach.fingerprint('电池: 18%'), 'at': 0}]
    skipped = dm_attach.field_candidates(_Registry(), sources, {'battery': '18%'}, {},
                                         [entry['hash'] for entry in ring])
    assert skipped == []


def test_event_candidates_skip_old_and_repeat():
    now = int(time.time())
    events = [{'kind': 'app', 'text': '切到 VS Code', 'at': now - 100},
              {'kind': 'app', 'text': '切到 Chrome', 'at': now - 5}]
    fresh = dm_attach.event_candidates(events, now - 60, [])
    assert [item.text.split(' ', 1)[1] for item in fresh] == ['切到 Chrome']
    # 发过的行进环后不再出现，但更旧、没发过的仍可作为候选
    ring = [dm_attach.fingerprint(fresh[0].text)]
    assert [item.text.split(' ', 1)[1] for item in dm_attach.event_candidates(events, 0, ring)] == ['切到 VS Code']


def test_prune_queue_drops_expired_entries():
    now = int(time.time())
    kept, dropped = dm_attach.prune_queue(
        [_queue_item('新'), {'text': '旧', 'at': 1, 'expires_at': now - 1, 'priority': 50}], now)
    assert [item['text'] for item in kept] == ['新']
    assert [item['text'] for item in dropped] == ['旧']


def test_update_ring_keeps_bounded_history():
    ring: list[dict] = []
    for index in range(dm_attach.RING_SIZE + 5):
        ring = dm_attach.update_ring(ring, [f'第 {index} 行'], index)
    assert len(ring) == dm_attach.RING_SIZE
    assert ring[-1]['hash'] == dm_attach.fingerprint(f'第 {dm_attach.RING_SIZE + 4} 行')


# ── 规则 kind=attach：只暂存，不发声 ──────────────────────────

@pytest.mark.asyncio
async def test_attach_rule_stages_without_speaking(tmp_path, monkeypatch):
    pm, plugin = await _plugin(tmp_path)
    said: list[str] = []
    monkeypatch.setattr(sys.modules['faust_plugin_desktop-mood'].backend2frontend, 'FrontEndSay',
                        lambda text: said.append(text))
    plugin.store.set_rules([_attach_rule('电量只剩 {battery}%，记得插电')])

    await plugin.heartbeat(plugin.ctx)
    assert said == []
    queued = plugin.store.get_kv(QUEUE_KV) or []
    assert len(queued) == 1
    assert '记得插电' in queued[0]['text']
    assert queued[0]['expires_at'] > int(time.time())


@pytest.mark.asyncio
async def test_attach_rides_next_user_message_then_stops(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_AUTO_ATTACH': False})
    plugin.store.set_rules([_attach_rule('电量只剩 {battery}%，记得插电')])
    await plugin.heartbeat(plugin.ctx)

    out = await pm.apply_message_received('在吗', origin='user')
    assert out.startswith('在吗')
    block = _attach_block(out)
    assert len(block) <= 100
    assert '记得插电' in block
    assert plugin.store.get_kv(QUEUE_KV) == []          # 已消费
    assert plugin.store.get_kv('attach.last')['text'] == block

    assert _attach_block(await pm.apply_message_received('还在吗', origin='user')) == ''

    # 同样的文本再次入队也不重复发（去重环）
    plugin.store.set_kv(QUEUE_KV, [_queue_item(block[len('[桌面] '):])])
    assert _attach_block(await pm.apply_message_received('又来了', origin='user')) == ''


@pytest.mark.asyncio
async def test_attach_only_for_user_and_foreground_trigger(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    plugin.store.set_kv(QUEUE_KV, [_queue_item('规则暂存的提醒')])

    assert _attach_block(await pm.apply_message_received('后台', origin='trigger_background')) == ''
    assert plugin.store.get_kv(QUEUE_KV)                # 后台触发器不动队列
    foreground = await pm.apply_message_received('前台触发', origin='trigger_foreground')
    assert _attach_block(foreground) == '[桌面] 规则暂存的提醒'


@pytest.mark.asyncio
async def test_attach_ignores_user_text_content(tmp_path):
    """同样队列 + 不同用户输入 → 完全相同的附加块（证明不分析用户输入）。"""
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_AUTO_ATTACH': False})
    outputs = []
    for text in ('你好', '帮我看看这个报错 关键词 alert(1) 屏幕'):
        plugin.store.set_kv(RING_KV, [])
        plugin.store.set_kv(QUEUE_KV, [_queue_item('规则暂存的提醒')])
        outputs.append(await pm.apply_message_received(text, origin='user'))
    assert outputs[0].startswith('你好')
    assert outputs[1].startswith('帮我看看这个报错 关键词 alert(1) 屏幕')
    assert _attach_block(outputs[0]) == _attach_block(outputs[1]) == '[桌面] 规则暂存的提醒'


@pytest.mark.asyncio
async def test_attach_drops_expired_and_respects_switch(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_AUTO_ATTACH': False})
    missing = {'text': '过期的提醒', 'at': 1, 'expires_at': int(time.time()) - 1, 'priority': 90}
    plugin.store.set_kv(QUEUE_KV, [missing])
    assert _attach_block(await pm.apply_message_received('你好', origin='user')) == ''
    assert plugin.store.get_kv(QUEUE_KV) == []           # 过期项被清掉，不静默堆积

    plugin.store.set_kv(QUEUE_KV, [_queue_item('开关关掉就不该出现')])
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_ATTACH': False})
    assert _attach_block(await pm.apply_message_received('你好', origin='user')) == ''
    assert plugin.store.get_kv(QUEUE_KV)                 # 关掉开关不动队列


@pytest.mark.asyncio
async def test_attach_budget_holds_and_never_repeats_lines(tmp_path):
    """把一整轮感知都当成"变化过"：每次附加 ≤100 字，行不重复，攒够了就停。"""
    pm, plugin = await _plugin(tmp_path)
    await plugin.heartbeat(plugin.ctx)
    plugin.store.set_kv('attach.pushed', {})
    plugin.store.set_kv(RING_KV, [])

    seen_lines: list[str] = []
    for index in range(4):
        block = _attach_block(await pm.apply_message_received(f'第 {index} 句', origin='user'))
        assert len(block) <= 100
        lines = [part for part in block[len('[桌面] '):].split(' · ') if block] if block else []
        assert not set(lines) & set(seen_lines)          # 与之前发过的行不重复
        seen_lines.extend(lines)
    assert seen_lines                                  # 至少真的附加过内容


@pytest.mark.asyncio
async def test_attach_state_exposed_for_panel(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    plugin.store.set_kv(QUEUE_KV, [_queue_item('排队中的提醒')])
    report = await plugin.perception_report()
    attach = report['attach']
    assert attach['enabled'] is True and attach['auto'] is True
    assert attach['budget'] == dm_attach.BUDGET_DEFAULT
    assert attach['queued'] == 1
    assert {entry['group'] for entry in report['sources']} <= set(dm_sources.GROUP_IDS)


# ── 数据域 VFS 视图 ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_vfs_domain_views_and_refresh(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    await plugin.heartbeat(plugin.ctx)
    vfs = await get_faustbot_vfs(refresh=True)

    overview = await vfs.read_text('/desktop-mood/overview.md', default='')
    assert overview.startswith('# 桌面上下文总览')
    assert '数据时间' in overview and '免打扰' in overview

    for group in dm_sources.GROUP_IDS:
        if not plugin.registry.sources_of(group):
            continue
        assert await vfs.exists(f'/desktop-mood/{group}/{group}.json'), group
        assert await vfs.exists(f'/desktop-mood/{group}/{group}.md'), group

    payload = json.loads(await vfs.read_text('/desktop-mood/window/window.json', default='{}'))
    assert payload['group'] == 'window'
    assert payload['group_label'] == '窗口与应用'
    assert payload['generated_at']
    entry = payload['sources']['window_process']
    assert entry['label'] == '前台进程' and entry['cadence_sec'] == 10
    assert entry['state'] in ('ok', 'off') or entry['state']

    markdown = await vfs.read_text('/desktop-mood/window/window.md', default='')
    assert markdown.startswith('# 窗口与应用 · window')
    assert '前台进程' in markdown

    # read refresh = 立即采集一次（有副作用，对标规则 reload 节点）
    refreshed = await vfs.read_text('/desktop-mood/refresh', default='')
    assert '已重新采集' in refreshed
    assert int(plugin.store.snapshot().get('snapshot_at') or 0) >= int(time.time()) - 5

    assert await vfs.exists('/desktop-mood/narrative/rhythm.md')
    assert not await vfs.exists('/plugins/desktop-context.json')


@pytest.mark.asyncio
async def test_vfs_marks_disabled_and_unavailable_sources(tmp_path):
    pm, plugin = await _plugin(tmp_path)
    pm.set_plugin_config_values('desktop-mood', {'ENABLE_SYSTEM_LOAD': False})
    await plugin.heartbeat(plugin.ctx)
    vfs = await get_faustbot_vfs(refresh=True)

    payload = json.loads(await vfs.read_text('/desktop-mood/system/system.json', default='{}'))
    assert payload['sources']['system_load']['state'] == 'off'
    overview = await vfs.read_text('/desktop-mood/overview.md', default='')
    assert '系统负载: 未启用' in overview
