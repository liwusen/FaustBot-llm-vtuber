# backend/tests/test_memory_injector.py
"""Memory Injector 插件行为测试：低熵跳过、query 重建、lite/full 分流、片段选段、
片段预算、图片/读失败回退、n 轮去重、top_k clamp。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND = Path(__file__).resolve().parents[1]
IMPL = BACKEND / "default_plugins" / "memory-injector" / "impl.py"


def _load_module():
    """与 plugin_system.manager._load_module 一致：先注册 sys.modules 再 exec
    （dataclasses 解析字符串注解时要按 __module__ 反查模块）。"""
    name = "memory_injector_test"
    spec = importlib.util.spec_from_file_location(name, str(IMPL))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


mi = _load_module()

# 假 jieba：固定分词结果，保证测试确定且不依赖真词典
_FAKE_TOKENS = {
    "你好": ["你好"],
    "谢谢，请问LSTM是什么": ["谢谢", "，", "请问", "LSTM", "是", "什么"],
    "介绍一下记忆系统": ["介绍", "一下", "记忆", "系统"],
    " again about memory system ": ["again", "about", "memory", "system"],
    "LSTM 怎么用": ["LSTM", "怎么", "用"],
}


class FakeCtx:
    def __init__(self, values=None):
        self.values = dict(values or {})
        import tempfile
        from faust_backend.plugins.system.plugin_storage import PluginStorage
        self.storage = PluginStorage("memory-injector-test",
                                     Path(tempfile.mkdtemp()), agent_name="faust")

    async def get_config(self, key, default=None):
        return self.values.get(key, default)

    async def register_config(self, schema):
        return None


class FakeMemory:
    """只提供插件真正依赖的检索/读取接口：search_bm25 按真实实现只返回 path/score。"""

    def __init__(self, items, contents=None):
        self.items = items
        self.contents = dict(contents or {})
        self.calls = []
        self.reads = []

    async def search_bm25(self, tokens, top_k=3):
        self.calls.append(("bm25", list(tokens), top_k))
        return list(self.items)

    async def search_compact(self, query, top_k=3):
        self.calls.append(("compact", query, top_k))
        return list(self.items)

    async def file_read(self, path):
        self.reads.append(path)
        if path not in self.contents:
            raise FileNotFoundError(path)
        return {"path": path, "content": self.contents[path], "description": "", "meta": {}}


def _make_plugin(monkeypatch, fake_memory, config=None):
    plugin = mi.Plugin()
    plugin.ctx = FakeCtx(config)
    monkeypatch.setattr(mi, "get_memory", lambda: fake_memory)
    async def _tok(text):
        return _FAKE_TOKENS.get(text, text.split())
    monkeypatch.setattr(mi, "jieba_tokenize", _tok)
    return plugin


_HIT = [{"path": "/kb/lstm.md", "description": "LSTM 笔记", "line_count": 12, "score": 2.0}]

# 文档开头与查询无关，相关段落在中间——旧实现只会注入 "LSTM 笔记" 这类头部摘要
_LSTM_DOC = "# 我的笔记\n\n今天天气不错，出门走了走。\n\nLSTM 是一种循环神经网络，靠门控记忆长序列。\n"


# ── 片段选择器（纯函数） ──

def test_select_passage_prefers_matching_paragraph():
    got = mi.select_passage(_LSTM_DOC, ["LSTM"], budget=100)
    assert "LSTM 是一种循环神经网络" in got.text
    assert "今天天气" not in got.text
    assert got.score > 0
    assert got.truncated_head and not got.truncated_tail  # 相关段落在文档末尾


def test_select_passage_returns_window_inside_long_paragraph():
    doc = "前" * 300 + "LSTM" + "后" * 300
    got = mi.select_passage(doc, ["LSTM"], budget=100)
    assert len(got.text) == 100
    assert "LSTM" in got.text
    assert got.truncated_head and got.truncated_tail


def test_select_passage_expands_adjacent_matching_paragraphs():
    doc = "LSTM 简介\n\nLSTM 细节\n\n无关段落\n"
    got = mi.select_passage(doc, ["LSTM"], budget=100)
    assert "简介" in got.text and "细节" in got.text
    assert "无关段落" not in got.text


def test_select_passage_splits_by_line_without_blank_lines():
    doc = "第一行\n第二行 LSTM\n第三行\n"
    got = mi.select_passage(doc, ["LSTM"], budget=100)
    assert got.text == "第二行 LSTM"


def test_select_passage_falls_back_to_head_without_match():
    got = mi.select_passage(_LSTM_DOC, ["向量数据库"], budget=20)
    assert got.score == 0.0
    assert got.text == _LSTM_DOC.strip()[:20]


def test_select_passage_empty_text_and_budget_clamp():
    empty = mi.select_passage("   \n\n", ["LSTM"], budget=100)
    assert empty.text == "" and empty.start == empty.end == 0
    got = mi.select_passage("LSTM " + "说明" * 50, ["LSTM"], budget=0)
    assert len(got.text) == 1


# ── 插件注入行为 ──

@pytest.mark.asyncio(loop_scope="module")
async def test_pure_greeting_not_searched(monkeypatch):
    fm = FakeMemory(_HIT)
    plugin = _make_plugin(monkeypatch, fm)
    out = await plugin.message_received("你好", [], plugin.ctx)
    assert out is None
    assert fm.calls == []
    assert fm.reads == []


@pytest.mark.asyncio(loop_scope="module")
async def test_mixed_message_filters_greeting_and_hits_memory(monkeypatch):
    fm = FakeMemory(_HIT, {"/kb/lstm.md": _LSTM_DOC})
    plugin = _make_plugin(monkeypatch, fm)
    out = await plugin.message_received("谢谢，请问LSTM是什么", [], plugin.ctx)
    assert out is not None
    assert out.startswith("谢谢，请问LSTM是什么")
    assert "[Memory]" in out
    assert "/kb/lstm.md" in out
    assert "12行" in out
    # 注入的是命中段落正文，而不是文档开头/description
    assert "LSTM 是一种循环神经网络" in out
    assert "我的笔记" not in out
    assert "LSTM 笔记" not in out
    kind, query, top_k = fm.calls[0]
    assert kind == "compact"
    assert "谢谢" not in query and "请问" not in query
    assert "LSTM" in query
    assert top_k == 3


@pytest.mark.asyncio(loop_scope="module")
async def test_lite_mode_uses_bm25_tokens(monkeypatch):
    fm = FakeMemory([{"path": "/kb/m.md", "score": 1.0}],
                    {"/kb/m.md": "开头无关。\n\n记忆系统由向量与图谱组成。\n"})
    plugin = _make_plugin(monkeypatch, fm, {"MODE": "lite"})
    out = await plugin.message_received("介绍一下记忆系统", [], plugin.ctx)
    assert out is not None
    kind, tokens, top_k = fm.calls[0]
    assert kind == "bm25"
    assert "记忆" in tokens and "系统" in tokens
    assert top_k == 3
    # lite 的结果没有 description/line_count，必须由正文补齐（行数口径同 store._count_lines）
    assert "记忆系统由向量与图谱组成" in out
    assert "约3行" in out


@pytest.mark.asyncio(loop_scope="module")
async def test_passage_budget_caps_total_chars(monkeypatch):
    doc = "LSTM " + "细节" * 200
    items = [{"path": f"/kb/{i}.md", "description": "头部", "line_count": 1, "score": 1.0}
             for i in range(3)]
    fm = FakeMemory(items, {f"/kb/{i}.md": doc for i in range(3)})
    plugin = _make_plugin(monkeypatch, fm, {"PASSAGE_CHARS": 50, "MAX_TOTAL_CHARS": 60})
    out = await plugin.message_received("LSTM 怎么用", [], plugin.ctx)
    injected = [ln.strip()[2:].strip() for ln in out.splitlines() if ln.strip().startswith("> ")]
    assert len(injected) == 2  # 第三条预算耗尽，只保留路径
    assert sum(len(s) for s in injected) <= 60
    assert "/kb/2.md" in out and out.rstrip().endswith("/kb/2.md (约1行)")


@pytest.mark.asyncio(loop_scope="module")
async def test_injected_block_is_path_line_plus_single_quote_line(monkeypatch):
    """跨段片段压成单行：每条记忆固定为「路径行 + > 片段行」。"""
    doc = "LSTM 简介段落内容。\n\nLSTM 细节段落内容。\n"
    fm = FakeMemory([{"path": "/kb/l.md", "description": "头部", "line_count": 3,
                      "score": 1.0}], {"/kb/l.md": doc})
    plugin = _make_plugin(monkeypatch, fm)
    out = await plugin.message_received("LSTM 怎么用", [], plugin.ctx)
    body = out.split("[Memory]\n", 1)[1].strip().splitlines()
    assert body[0] == "- /kb/l.md (约3行)"
    assert len(body) == 2
    assert body[1].strip().startswith("> ")
    assert "简介" in body[1] and "细节" in body[1]


@pytest.mark.asyncio(loop_scope="module")
async def test_image_hit_falls_back_to_description(monkeypatch):
    fm = FakeMemory([{"path": "/images/a.png", "description": "用户头像", "line_count": 0,
                      "score": 1.0}])
    plugin = _make_plugin(monkeypatch, fm)
    out = await plugin.message_received("LSTM 怎么用", [], plugin.ctx)
    assert out is not None
    assert "用户头像" in out
    assert fm.reads == []  # 图片不读正文


@pytest.mark.asyncio(loop_scope="module")
async def test_unreadable_document_falls_back_to_description(monkeypatch):
    fm = FakeMemory(_HIT)  # contents 为空 -> file_read 抛 FileNotFoundError
    plugin = _make_plugin(monkeypatch, fm)
    out = await plugin.message_received("谢谢，请问LSTM是什么", [], plugin.ctx)
    assert out is not None
    assert "LSTM 笔记" in out
    assert fm.reads == ["/kb/lstm.md"]


@pytest.mark.asyncio(loop_scope="module")
async def test_dedup_within_n_turns(monkeypatch):
    fm = FakeMemory([{"path": "/kb/m.md", "description": "记忆系统笔记", "line_count": 5,
                      "score": 1.0}])
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


@pytest.mark.asyncio(loop_scope="module")
async def test_session_reset_clears_dedup_window(monkeypatch):
    """SESSION 存储重置（会话 clear/compact）后，去重窗口清空，同 path 可再次注入。"""
    fm = FakeMemory([{"path": "/kb/m.md", "description": "记忆系统笔记", "line_count": 5,
                      "score": 1.0}])
    plugin = _make_plugin(monkeypatch, fm, {"DEDUP_TURNS": 2})
    first = await plugin.message_received("介绍一下记忆系统", [], plugin.ctx)
    second = await plugin.message_received(" again about memory system ", [], plugin.ctx)
    assert first is not None and second is None
    # 模拟会话 clear/compact 的 SESSION 重置
    plugin.ctx.storage.reset_session()
    third = await plugin.message_received(" again about memory system ", [], plugin.ctx)
    assert third is not None
