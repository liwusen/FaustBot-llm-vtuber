# backend/tests/test_memory_injector.py
"""Memory Injector 插件行为测试：低熵跳过、query 重建、lite/full 分流、n 轮去重、top_k clamp。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND = Path(__file__).resolve().parents[1]
IMPL = BACKEND / "default_plugins" / "memory-injector" / "impl.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("memory_injector_test", str(IMPL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


mi = _load_module()

# 假 jieba：固定分词结果，保证测试确定且不依赖真词典
_FAKE_TOKENS = {
    "你好": ["你好"],
    "谢谢，请问LSTM是什么": ["谢谢", "，", "请问", "LSTM", "是", "什么"],
    "介绍一下记忆系统": ["介绍", "一下", "记忆", "系统"],
    " again about memory system ": ["again", "about", "memory", "system"],
}


class FakeCtx:
    def __init__(self, values=None):
        self.values = dict(values or {})

    async def get_config(self, key, default=None):
        return self.values.get(key, default)

    async def register_config(self, schema):
        return None


class FakeMemory:
    def __init__(self, items):
        self.items = items
        self.calls = []

    async def search_bm25(self, tokens, top_k=3):
        self.calls.append(("bm25", list(tokens), top_k))
        return list(self.items)

    async def search_compact(self, query, top_k=3):
        self.calls.append(("compact", query, top_k))
        return list(self.items)


def _make_plugin(monkeypatch, fake_memory, config=None):
    plugin = mi.Plugin()
    plugin.ctx = FakeCtx(config)
    monkeypatch.setattr(mi, "get_memory", lambda: fake_memory)
    async def _tok(text):
        return _FAKE_TOKENS.get(text, text.split())
    monkeypatch.setattr(mi, "jieba_tokenize", _tok)
    return plugin


_HIT = [{"path": "/kb/lstm.md", "description": "LSTM 笔记", "line_count": 12, "score": 2.0}]


@pytest.mark.asyncio(loop_scope="module")
async def test_pure_greeting_not_searched(monkeypatch):
    fm = FakeMemory(_HIT)
    plugin = _make_plugin(monkeypatch, fm)
    out = await plugin.message_received("你好", [], plugin.ctx)
    assert out is None
    assert fm.calls == []


@pytest.mark.asyncio(loop_scope="module")
async def test_mixed_message_filters_greeting_and_hits_memory(monkeypatch):
    fm = FakeMemory(_HIT)
    plugin = _make_plugin(monkeypatch, fm)
    out = await plugin.message_received("谢谢，请问LSTM是什么", [], plugin.ctx)
    assert out is not None
    assert out.startswith("谢谢，请问LSTM是什么")
    assert "[Memory]" in out
    assert "/kb/lstm.md" in out
    assert "12行" in out
    kind, query, top_k = fm.calls[0]
    assert kind == "compact"
    assert "谢谢" not in query and "请问" not in query
    assert "LSTM" in query
    assert top_k == 3


@pytest.mark.asyncio(loop_scope="module")
async def test_lite_mode_uses_bm25_tokens(monkeypatch):
    fm = FakeMemory([{"path": "/kb/m.md", "description": "记忆系统笔记", "line_count": 5, "score": 1.0}])
    plugin = _make_plugin(monkeypatch, fm, {"MODE": "lite"})
    out = await plugin.message_received("介绍一下记忆系统", [], plugin.ctx)
    assert out is not None
    kind, tokens, top_k = fm.calls[0]
    assert kind == "bm25"
    assert "记忆" in tokens and "系统" in tokens
    assert top_k == 3


@pytest.mark.asyncio(loop_scope="module")
async def test_dedup_within_n_turns(monkeypatch):
    fm = FakeMemory([{"path": "/kb/m.md", "description": "记忆系统笔记", "line_count": 5, "score": 1.0}])
    plugin = _make_plugin(monkeypatch, fm, {"DEDUP_TURNS": 2})
    first = await plugin.message_received("介绍一下记忆系统", [], plugin.ctx)
    second = await plugin.message_received(" again about memory system ", [], plugin.ctx)
    assert first is not None
    assert second is None  # 2 轮内同 path 不重复注入


@pytest.mark.asyncio(loop_scope="module")
async def test_top_k_clamped_to_3(monkeypatch):
    fm = FakeMemory([])
    plugin = _make_plugin(monkeypatch, fm, {"TOP_K": "99"})
    await plugin.message_received("介绍一下记忆系统", [], plugin.ctx)
    assert fm.calls[0][2] == 3


@pytest.mark.asyncio(loop_scope="module")
async def test_no_hits_returns_original_message_untouched(monkeypatch):
    fm = FakeMemory([])
    plugin = _make_plugin(monkeypatch, fm)
    out = await plugin.message_received("介绍一下记忆系统", [], plugin.ctx)
    assert out is None  # 无命中不注入，消息保持原样
