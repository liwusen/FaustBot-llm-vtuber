"""message_received 洋葱模型：多个插件的注入必须逐层叠加，不能互相覆盖。

回归背景：原先消费点用 `for r in results: text = r; break` 只取 pluggy 返回的
第一条非 None 结果。由于每个实现拿到的都是同一个原始 msg，其余插件的注入
（记忆、情绪向量…）被静默丢弃，且谁赢取决于插件注册顺序。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.plugin_system import PluginManager
from faust_backend.plugin_system.hooks import hookimpl
from faust_backend.plugin_system.interfaces import PluginManifest
from faust_backend.plugin_system.plugin_base import FaustPlugin

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins'
STATE_FILE = Path(__file__).resolve().parents[2] / 'logs' / 'plugin-test-state-onion.json'


def _run(coro):
    return asyncio.run(coro)


class SuffixPlugin(FaustPlugin):
    """追加式注入层：返回「上一层文本 + 自己的标记」。"""

    def __init__(self, tag: str):
        self.tag = tag

    @hookimpl
    def message_received(self, msg, history, ctx):
        return f"{msg}\n[{self.tag}]"


class AsyncSuffixPlugin(FaustPlugin):
    def __init__(self, tag: str):
        self.tag = tag

    @hookimpl
    async def message_received(self, msg, history, ctx):
        await asyncio.sleep(0)
        return f"{msg}\n[{self.tag}]"


class SilentPlugin(FaustPlugin):
    """只做副作用、不改文本（rss-watcher 那种）。"""

    def __init__(self):
        self.seen: list[str] = []

    @hookimpl
    def message_received(self, msg, history, ctx):
        self.seen.append(msg)
        return None


class IgnorePlugin(FaustPlugin):
    @hookimpl
    def message_received(self, msg, history, ctx):
        return "__IGNORED__"


class BoomPlugin(FaustPlugin):
    @hookimpl
    def message_received(self, msg, history, ctx):
        raise RuntimeError("插件炸了")


class RecordingPlugin(FaustPlugin):
    """记录自己被调用时收到的文本，用于验证串联顺序。"""

    def __init__(self):
        self.received: list[str] = []

    @hookimpl
    def message_received(self, msg, history, ctx):
        self.received.append(msg)
        return f"{msg}<{len(self.received)}>"


class PartialArgsPlugin(FaustPlugin):
    """只声明部分参数（pluggy 允许实现只取 spec 的子集）。"""

    def __init__(self):
        self.got: list[str] = []

    @hookimpl
    def message_received(self, msg):
        self.got.append(msg)
        return f"{msg}\n[partial]"


def _manager_with(*plugins: tuple[str, FaustPlugin, int]) -> PluginManager:
    """注册多个插件（plugin_id、实例、priority），模仿真实加载路径的记录结构。"""
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=STATE_FILE)
    for plugin_id, plugin, priority in plugins:
        pm._pluggy_manager.register(plugin, name=plugin_id)
        pm._plugins[plugin_id] = {
            "manifest": PluginManifest(plugin_id=plugin_id, name=plugin_id, enabled=True, priority=priority),
            "plugin": plugin,
            "ctx": object(),
        }
    pm._pluggy_loaded = True
    return pm


def test_all_plugins_inject_into_same_message():
    """核心回归：两个追加式插件必须都生效，而不是只留一条。"""
    pm = _manager_with(
        ("a-emotion", SuffixPlugin("情绪"), 100),
        ("b-memory", SuffixPlugin("记忆"), 100),
    )
    out = _run(pm.apply_message_received("你好"))
    assert "[情绪]" in out
    assert "[记忆]" in out
    assert out.startswith("你好")


def test_registration_order_does_not_change_result():
    """结果只由 priority/插件 id 决定，与注册顺序无关。"""
    forward = _manager_with(
        ("a-emotion", SuffixPlugin("情绪"), 100),
        ("b-memory", SuffixPlugin("记忆"), 100),
    )
    backward = _manager_with(
        ("b-memory", SuffixPlugin("记忆"), 100),
        ("a-emotion", SuffixPlugin("情绪"), 100),
    )
    assert _run(forward.apply_message_received("你好")) == _run(backward.apply_message_received("你好"))


def test_priority_decides_layer_order():
    """priority 小的在外层：先拿到原始文本，priority 大的看到前者的输出。"""
    inner = RecordingPlugin()
    outer = RecordingPlugin()
    pm = _manager_with(
        ("outer", outer, 10),
        ("inner", inner, 200),
    )
    out = _run(pm.apply_message_received("原始"))
    assert outer.received == ["原始"]
    assert inner.received == ["原始<1>"]
    assert out == "原始<1><1>"


def test_silent_layer_passes_through_and_later_layers_still_apply():
    """返回 None 的层不阻断后续层（rss-watcher 这类副作用插件不能吃掉注入）。"""
    silent = SilentPlugin()
    pm = _manager_with(
        ("a-silent", silent, 10),
        ("b-memory", SuffixPlugin("记忆"), 100),
    )
    out = _run(pm.apply_message_received("你好"))
    assert out == "你好\n[记忆]"
    assert silent.seen == ["你好"]


def test_ignored_short_circuits_remaining_layers():
    """__IGNORED__ 立即拦截：后续层不再执行，文本原样返回。"""
    later = SuffixPlugin("不该出现")
    pm = _manager_with(
        ("a-block", IgnorePlugin(), 10),
        ("b-later", later, 100),
    )
    assert _run(pm.apply_message_received("你好")) == "__IGNORED__"


def test_failing_layer_is_skipped_without_breaking_others():
    """单层异常只跳过该层，不影响其它插件的注入。"""
    pm = _manager_with(
        ("a-boom", BoomPlugin(), 10),
        ("b-memory", SuffixPlugin("记忆"), 100),
    )
    assert _run(pm.apply_message_received("你好")) == "你好\n[记忆]"


def test_async_and_sync_layers_compose():
    pm = _manager_with(
        ("a-async", AsyncSuffixPlugin("异步"), 10),
        ("b-sync", SuffixPlugin("同步"), 100),
    )
    assert _run(pm.apply_message_received("你好")) == "你好\n[异步]\n[同步]"


def test_layer_declaring_subset_of_args_is_supported():
    """实现只声明 (msg) 时不应因缺少 history/ctx 而报错。"""
    plugin = PartialArgsPlugin()
    pm = _manager_with(("partial", plugin, 100))
    assert _run(pm.apply_message_received("你好")) == "你好\n[partial]"
    assert plugin.got == ["你好"]


def test_no_plugins_returns_text_unchanged():
    pm = _manager_with()
    assert _run(pm.apply_message_received("你好")) == "你好"


# ── 路由接缝：chat.py 的所有入口必须走洋葱派发器 ──


def test_chat_route_helper_uses_onion_dispatcher(monkeypatch):
    """用户消息与触发器共用的入口必须走洋葱派发器。

    若路由层退回「只取 pluggy 第一条非 None」，这条断言会失败。
    """
    from faust_backend.routes import chat
    from faust_backend.runtime import state

    pm = _manager_with(
        ("a-emotion", SuffixPlugin("情绪"), 10),
        ("b-memory", SuffixPlugin("记忆"), 20),
    )
    monkeypatch.setattr(state, "plugin_manager", pm)
    assert _run(chat._apply_plugin_message_hooks("你好")) == "你好\n[情绪]\n[记忆]"


def test_chat_route_helper_passthrough_without_manager(monkeypatch):
    from faust_backend.routes import chat
    from faust_backend.runtime import state

    monkeypatch.setattr(state, "plugin_manager", None)
    assert _run(chat._apply_plugin_message_hooks("你好")) == "你好"
