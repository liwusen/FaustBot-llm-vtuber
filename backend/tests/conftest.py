"""backend/tests 共享 pytest fixture。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


@pytest.fixture
def read_memory_store(tmp_path, monkeypatch):
    """隔离的 GraphStore：不联网、不写用户数据，并把 `get_memory()` 指向它。

    供 read 工具的 memory:// 测试使用；禁用向量索引，写入不触发 embedding。
    """
    import faust_backend.config_loader as conf
    import faust_backend.memory as memory_pkg
    import faust_backend.memory.store as store

    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "AGENT_NAME", "test_agent")
    monkeypatch.setattr(conf, "EMBED_API_KEY", "test-key")
    monkeypatch.setattr(conf, "EMBED_API_BASE", "http://test.example/v1")
    monkeypatch.setattr(conf, "EMBED_MODEL", "text-embed-model")
    monkeypatch.setattr(conf, "CHAT_API_KEY", "test-key")
    monkeypatch.setattr(conf, "CHAT_API_BASE", "http://test.example/v1")
    monkeypatch.setattr(conf, "CHAT_MODEL", "gpt-4o")
    gs = store.GraphStore("test_agent")

    async def _noop_embed_index(chunk_items):
        pass

    monkeypatch.setattr(gs, "_embed_and_index", _noop_embed_index)
    monkeypatch.setattr(memory_pkg, "get_memory", lambda: gs)
    yield gs
    gs.flush()
