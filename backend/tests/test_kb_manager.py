import asyncio
import sys
import time
from pathlib import Path

import numpy as np


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.argv = [sys.argv[0]]

import faust_backend.kb_manager as kb_manager


def test_kb_manager_crud_cycle():
    manager = kb_manager.get_kb_manager(refresh=True)

    async def main():
        await manager.write_node("tmp_pytest/sample.md", "hello kb", declared_by="pytest", index=False)
        node = manager.read_node("tmp_pytest/sample.md")
        assert node["content"] == "hello kb"
        tree = manager.list_tree("tmp_pytest")
        assert tree["type"] == "dir"
        await manager.delete_node("tmp_pytest/sample.md")

    asyncio.run(main())


def test_kb_manager_incremental_chunk_update_and_delete():
    manager = kb_manager.get_kb_manager(refresh=True)

    async def fake_embed(texts):
        return np.zeros((len(texts), kb_manager.EMBED_DIM), dtype=np.float32)

    manager._embed_texts = fake_embed

    async def main():
        await manager.write_node("tmp_pytest/incremental.md", "alpha beta gamma", declared_by="pytest", index=True)
        await manager._reindex_single_node("tmp_pytest/incremental.md")
        chunks_index = manager._load_chunks_index()
        first_ids = [
            chunk_id for chunk_id, item in chunks_index.items()
            if str(item.get("node_path") or "") == "tmp_pytest/incremental.md"
        ]
        assert len(first_ids) >= 1

        await manager.write_node("tmp_pytest/incremental.md", "delta epsilon zeta" * 500, declared_by="pytest", index=True)
        await manager._reindex_single_node("tmp_pytest/incremental.md")
        chunks_index = manager._load_chunks_index()
        second_ids = [
            chunk_id for chunk_id, item in chunks_index.items()
            if str(item.get("node_path") or "") == "tmp_pytest/incremental.md"
        ]
        assert len(second_ids) >= 1
        assert set(first_ids).isdisjoint(set(second_ids))

        await manager.delete_node("tmp_pytest/incremental.md")
        await manager._delete_node_chunks("tmp_pytest/incremental.md")
        chunks_index = manager._load_chunks_index()
        remain_ids = [
            chunk_id for chunk_id, item in chunks_index.items()
            if str(item.get("node_path") or "") == "tmp_pytest/incremental.md"
        ]
        assert remain_ids == []

    asyncio.run(main())


def test_kb_search_returns_non_zero_score_from_nanovectordb_metrics():
    manager = kb_manager.get_kb_manager(refresh=True)
    original_vdb = manager._vdb
    original_embed = manager._embed_texts
    original_create_vdb = manager._create_vdb

    async def fake_embed(texts):
        vectors = np.zeros((len(texts), kb_manager.EMBED_DIM), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors

    class FakeVDB:
        def query(self, query, top_k=10, better_than_threshold=None):
            return [
                {
                    "node_path": "tmp_pytest_score/score_check.md",
                    "text_preview": "Hello World",
                    "__metrics__": np.float32(0.875),
                }
            ]

    manager._embed_texts = fake_embed
    manager._create_vdb = lambda: FakeVDB()
    manager._vdb = None

    async def main():
        manager.paths.index_file.parent.mkdir(parents=True, exist_ok=True)
        manager.paths.index_file.write_text("{}", encoding="utf-8")
        results = await manager.search("Hello", scope="tmp_pytest_score", top_k=5, return_mode="snippets")
        assert results
        assert results[0]["path"] == "tmp_pytest_score/score_check.md"
        assert float(results[0]["score"]) == 0.875

    try:
        asyncio.run(main())
    finally:
        manager._vdb = original_vdb
        manager._embed_texts = original_embed
        manager._create_vdb = original_create_vdb


def test_kb_search_sanitizes_non_finite_scores_for_json_response():
    manager = kb_manager.get_kb_manager(refresh=True)
    original_vdb = manager._vdb
    original_embed = manager._embed_texts
    original_create_vdb = manager._create_vdb

    async def fake_embed(texts):
        vectors = np.zeros((len(texts), kb_manager.EMBED_DIM), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors

    class FakeVDB:
        def query(self, query, top_k=10, better_than_threshold=None):
            return [
                {
                    "node_path": "tmp_pytest_score/score_nan.md",
                    "text_preview": "Hello World",
                    "__metrics__": np.float32("nan"),
                },
                {
                    "node_path": "tmp_pytest_score/score_inf.md",
                    "text_preview": "Hello Again",
                    "__metrics__": np.float32("inf"),
                },
            ]

    manager._embed_texts = fake_embed
    manager._create_vdb = lambda: FakeVDB()
    manager._vdb = None

    async def main():
        manager.paths.index_file.parent.mkdir(parents=True, exist_ok=True)
        manager.paths.index_file.write_text("{}", encoding="utf-8")
        results = await manager.search("Hello", scope="tmp_pytest_score", top_k=5, return_mode="snippets")
        assert results
        assert all(float(item["score"]) == 0.0 for item in results)

    try:
        asyncio.run(main())
    finally:
        manager._vdb = original_vdb
        manager._embed_texts = original_embed
        manager._create_vdb = original_create_vdb


def test_kb_search_reuses_cached_vdb_instance():
    manager = kb_manager.get_kb_manager(refresh=True)
    original_vdb = manager._vdb
    original_create_vdb = manager._create_vdb
    calls = {"create": 0, "query": 0}

    async def fake_embed(texts):
        vectors = np.zeros((len(texts), kb_manager.EMBED_DIM), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors

    class FakeVDB:
        def query(self, query, top_k=10, better_than_threshold=None):
            calls["query"] += 1
            return [
                {
                    "node_path": "tmp_pytest_score/cached.md",
                    "text_preview": "Hello Cached",
                    "__metrics__": np.float32(0.5),
                }
            ]

    def fake_create_vdb():
        calls["create"] += 1
        return FakeVDB()

    manager._vdb = None
    manager._embed_texts = fake_embed
    manager._create_vdb = fake_create_vdb

    async def main():
        index_file = manager.paths.index_file
        index_file.parent.mkdir(parents=True, exist_ok=True)
        index_file.write_text("{}", encoding="utf-8")
        await manager.search("Hello", scope="tmp_pytest_score", top_k=5)
        await manager.search("Hello", scope="tmp_pytest_score", top_k=5)
        assert calls["create"] == 1
        assert calls["query"] == 2

    try:
        asyncio.run(main())
    finally:
        manager._vdb = original_vdb
        manager._create_vdb = original_create_vdb


def test_kb_rebuild_replaces_cached_vdb_instance():
    manager = kb_manager.get_kb_manager(refresh=True)
    original_vdb = manager._vdb
    original_embed = manager._embed_texts
    original_create_vdb = manager._create_vdb
    calls = {"create": 0, "save": 0, "upsert": 0}

    class FakeVDB:
        def upsert(self, rows):
            calls["upsert"] += 1

        def save(self):
            calls["save"] += 1

    def fake_create_vdb():
        calls["create"] += 1
        return FakeVDB()

    async def fake_embed(texts):
        vectors = np.zeros((len(texts), kb_manager.EMBED_DIM), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors

    manager._create_vdb = fake_create_vdb
    manager._embed_texts = fake_embed
    manager._vdb = FakeVDB()
    _ = manager._get_vdb()

    test_chunk_id = f"rebuild::{np.random.randint(1, 100000)}"
    chunks_index = manager._load_chunks_index()
    chunks_index[test_chunk_id] = {
        "chunk_id": test_chunk_id,
        "node_path": "tmp_pytest_rebuild/sample.md",
        "chunk_index": 1,
        "text": "hello rebuild",
        "text_preview": "hello rebuild",
        "scope_prefix": "tmp_pytest_rebuild",
        "indexed": True,
    }
    manager._save_chunks_index(chunks_index)

    async def main():
        before = manager._vdb
        await manager._rebuild_index_snapshot()
        after = manager._vdb
        assert before is not after
        assert calls["create"] >= 1
        assert calls["upsert"] == 1
        assert calls["save"] >= 1

    try:
        asyncio.run(main())
    finally:
        chunks_index = manager._load_chunks_index()
        chunks_index.pop(test_chunk_id, None)
        manager._save_chunks_index(chunks_index)
        manager._vdb = original_vdb
        manager._embed_texts = original_embed
        manager._create_vdb = original_create_vdb


def test_kb_ensure_vdb_initialized_creates_empty_index_file():
    manager = kb_manager.get_kb_manager(refresh=True)
    original_vdb = manager._vdb

    try:
        manager._vdb = None
        if manager.paths.index_file.exists():
            manager.paths.index_file.unlink()
        vdb = manager.ensure_vdb_initialized()
        assert vdb is manager._vdb
        assert manager.paths.index_file.exists()
    finally:
        manager._vdb = original_vdb


def test_kb_write_preserves_tags_and_score_patch_and_search_by_tags():
    manager = kb_manager.get_kb_manager(refresh=True)
    original_vdb = manager._vdb
    original_embed = manager._embed_texts
    original_create_vdb = manager._create_vdb

    async def fake_embed(texts):
        vectors = np.zeros((len(texts), kb_manager.EMBED_DIM), dtype=np.float32)
        vectors[:, 0] = 1.0
        return vectors

    class FakeVDB:
        def query(self, query, top_k=10, better_than_threshold=None):
            return [
                {
                    "node_path": "tmp_pytest_tags/doc.md",
                    "text_preview": "Hello tagged world",
                    "__metrics__": np.float32(0.61),
                }
            ]

    manager._embed_texts = fake_embed
    manager._create_vdb = lambda: FakeVDB()
    manager._vdb = None

    async def main():
        manager.paths.index_file.parent.mkdir(parents=True, exist_ok=True)
        manager.paths.index_file.write_text("{}", encoding="utf-8")
        await manager.write_node("tmp_pytest_tags/doc.md", "Hello tagged world", declared_by="pytest", index=False, tags=["知识", "用户相关"])
        await manager.set_score_patch("tmp_pytest_tags/doc.md", 0.12, managed_by="pytest")
        node = manager.read_node("tmp_pytest_tags/doc.md")
        assert node["meta"]["tags"] == ["知识", "用户相关"]
        assert float(node["meta"]["score_patch"]) == 0.12
        results = await manager.search("Hello", scope="tmp_pytest_tags", tags=["知识"], top_k=5)
        assert results
        assert results[0]["path"] == "tmp_pytest_tags/doc.md"
        assert abs(float(results[0]["raw_score"]) - 0.61) < 1e-6
        assert float(results[0]["score_patch"]) == 0.12
        assert abs(float(results[0]["score"]) - 0.73) < 1e-6
        ignored = await manager.search("Hello", scope="tmp_pytest_tags", tags=["知识"], top_k=5, ignore_score_patch=True)
        assert ignored
        assert float(ignored[0]["score_patch"]) == 0.0
        assert abs(float(ignored[0]["score"]) - 0.61) < 1e-6
        await manager.delete_node("tmp_pytest_tags/doc.md")

    try:
        asyncio.run(main())
    finally:
        manager._vdb = original_vdb
        manager._embed_texts = original_embed
        manager._create_vdb = original_create_vdb
        if manager.paths.index_file.exists():
            manager.paths.index_file.unlink()


def test_kb_get_changed_nodes_filters_since_and_tags():
    manager = kb_manager.get_kb_manager(refresh=True)

    async def main():
        before = time.time() - 1
        await manager.write_node("tmp_pytest_changed/doc.md", "changed content", declared_by="pytest", index=False, tags=["知识", "行为准则"])
        changed = manager.get_changed_nodes(before, scope="tmp_pytest_changed", tags=["知识"])
        assert changed
        assert changed[0]["path"] == "tmp_pytest_changed/doc.md"
        assert changed[0]["tags"] == ["知识", "行为准则"]
        missing = manager.get_changed_nodes(before, scope="tmp_pytest_changed", tags=["废弃"])
        assert missing == []
        await manager.delete_node("tmp_pytest_changed/doc.md")

    asyncio.run(main())