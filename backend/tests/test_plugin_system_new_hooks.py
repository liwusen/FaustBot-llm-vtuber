from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.plugin_system import PluginManager
from faust_backend.plugin_system.hooks import CoreHooks, hookimpl
from faust_backend.plugin_system.plugin_base import FaustPlugin
from faust_backend.plugin_system.interfaces import PluginManifest

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / 'default_plugins'
STATE_FILE = Path(__file__).resolve().parents[2] / 'logs' / 'plugin-test-state-hooks.json'


class LlmRewritePlugin(FaustPlugin):
    @hookimpl
    def llm_request_pre(self, messages, ctx):
        return [{"role": "system", "content": "rewritten"}] + list(messages)


class TtsRewritePlugin(FaustPlugin):
    @hookimpl
    def tts_text(self, text, ctx):
        return f"[spoken] {text}"


class TtsStartPlugin(FaustPlugin):
    def __init__(self):
        self.started: list[str] = []

    @hookimpl
    def tts_start(self, text, ctx):
        self.started.append(text)


def _manager_with(plugin_id: str, plugin) -> PluginManager:
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=STATE_FILE)
    pm._pluggy_manager.register(plugin, name=plugin_id)
    pm._pluggy_loaded = True
    pm._plugins[plugin_id] = {
        "manifest": PluginManifest(plugin_id=plugin_id, name=plugin_id, enabled=True),
        "plugin": plugin,
        "ctx": object(),
    }
    return pm


# ── Hook 定义与默认实现 ──


def test_hookspecs_defined():
    for name in ("llm_request_pre", "tts_text", "tts_start", "tts_end"):
        assert hasattr(CoreHooks, name)


def test_default_impl_noop():
    p = FaustPlugin()
    assert p.llm_request_pre([], None) is None
    assert p.tts_text("hi", None) is None
    assert p.tts_start("hi", None) is None
    assert p.tts_end("hi", None) is None


# ── pluggy 集成：llm_request_pre ──


def test_llm_request_pre_hook_rewrites_messages():
    pm = _manager_with('llm-rewrite', LlmRewritePlugin())
    results = pm._call_pluggy_hook(
        "llm_request_pre", messages=[{"role": "user", "content": "hi"}], ctx=None
    )
    rewritten = [r for r in results if isinstance(r, list) and r]
    assert len(rewritten) == 1
    assert rewritten[0][0] == {"role": "system", "content": "rewritten"}


def test_llm_request_pre_absent_plugin_returns_none():
    pm = _manager_with('plain', FaustPlugin())
    results = pm._call_pluggy_hook(
        "llm_request_pre", messages=[{"role": "user", "content": "hi"}], ctx=None
    )
    assert all(r is None for r in results)


# ── pluggy 集成：TTS hooks ──


def test_tts_text_hook_firstresult_wins():
    pm = _manager_with('tts-rewrite', TtsRewritePlugin())
    results = pm._call_pluggy_hook("tts_text", text="你好", ctx=None)
    assert results and results[0] == "[spoken] 你好"


def test_tts_start_hook_notified():
    p = TtsStartPlugin()
    pm = _manager_with('tts-start', p)
    pm._call_pluggy_hook("tts_start", text="hello", ctx=None)
    assert p.started == ["hello"]


# ── 调用点：lifecycle._apply_llm_request_pre ──


def test_lifecycle_apply_llm_request_pre(monkeypatch):
    from faust_backend.runtime import lifecycle, state

    class FakePM:
        def _call_pluggy_hook(self, name, **kw):
            if name == "llm_request_pre":
                return [[{"role": "system", "content": "injected"}]]
            return []

    monkeypatch.setattr(state, "plugin_manager", FakePM())
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    out = lifecycle._apply_llm_request_pre(payload)
    assert out["messages"][0] == {"role": "system", "content": "injected"}


def test_lifecycle_apply_llm_request_pre_passthrough(monkeypatch):
    from faust_backend.runtime import lifecycle, state

    class FakePM:
        def _call_pluggy_hook(self, name, **kw):
            return [None]

    monkeypatch.setattr(state, "plugin_manager", FakePM())
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    out = lifecycle._apply_llm_request_pre(payload)
    assert out["messages"] == [{"role": "user", "content": "hi"}]


def test_lifecycle_apply_llm_request_pre_no_pm(monkeypatch):
    from faust_backend.runtime import lifecycle, state

    monkeypatch.setattr(state, "plugin_manager", None)
    payload = {"messages": [{"role": "user", "content": "hi"}]}
    out = lifecycle._apply_llm_request_pre(payload)
    assert out is payload


# ── 调用点：synthesize tts hooks ──


def test_synthesize_apply_tts_text_hook(monkeypatch):
    from faust_backend.runtime import state
    from faust_backend.speech.tts import synthesize

    class FakePM:
        def _call_pluggy_hook(self, name, **kw):
            if name == "tts_text":
                return ["[spoken] 你好"]
            return []

    monkeypatch.setattr(state, "plugin_manager", FakePM())
    assert synthesize._apply_tts_text_hook("你好") == "[spoken] 你好"


def test_synthesize_apply_tts_text_hook_passthrough(monkeypatch):
    from faust_backend.runtime import state
    from faust_backend.speech.tts import synthesize

    class FakePM:
        def _call_pluggy_hook(self, name, **kw):
            return [None]

    monkeypatch.setattr(state, "plugin_manager", FakePM())
    assert synthesize._apply_tts_text_hook("你好") == "你好"


def test_synthesize_fire_tts_start(monkeypatch):
    from faust_backend.runtime import state
    from faust_backend.speech.tts import synthesize

    seen: list[str] = []

    class FakePM:
        def _call_pluggy_hook(self, name, **kw):
            if name == "tts_start":
                seen.append(kw["text"])
            return []

    monkeypatch.setattr(state, "plugin_manager", FakePM())
    synthesize._fire_tts_start("hello")
    assert seen == ["hello"]
