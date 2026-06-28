import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.argv = [sys.argv[0]]

import faust_backend.memory.store as store
import faust_backend.config_loader as conf


@pytest.fixture
def memory_store(tmp_path, monkeypatch):
    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "AGENT_NAME", "test_agent")
    monkeypatch.setattr(conf, "KB_OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(conf, "CHAT_API_KEY", "test-key")
    monkeypatch.setattr(conf, "CHAT_API_BASE", "http://test.example/v1")
    monkeypatch.setattr(conf, "CHAT_MODEL", "gpt-4o")
    gs = store.GraphStore("test_agent")

    async def _noop_embed_index(chunk_items):
        pass
    monkeypatch.setattr(gs, "_embed_and_index", _noop_embed_index)

    yield gs
    gs.flush()


def _mock_embed(_texts):
    np.random.seed(42)
    return np.random.rand(len(_texts), 1536).astype(np.float32)


# ══════════════════════════════════════════════════════════════════════
# Phase 1: Tree structure
# ══════════════════════════════════════════════════════════════════════

class TestPhase1TreeStructure:

    def test_root_node_exists_on_init(self, memory_store):
        assert memory_store._has_node("path:/")
        ndata = memory_store._graph.nodes["path:/"]
        assert ndata["type"] == "dir"
        assert ndata["name"] == "/"

    def test_ensure_ancestors_creates_intermediate_dirs(self, memory_store):
        norm = store._normalize_path("/a/b/c/file.md")
        parent_nid = memory_store._ensure_ancestors(norm)

        assert memory_store._has_node("path:/a")
        assert memory_store._has_node("path:/a/b")
        assert memory_store._has_node("path:/a/b/c")
        assert parent_nid == "path:/a/b/c"

        a_nid = store._path_id("/a")
        a_b_nid = store._path_id("/a/b")
        a_b_c_nid = store._path_id("/a/b/c")
        assert memory_store._graph.has_edge("path:/", a_nid)
        assert memory_store._graph.has_edge(a_nid, a_b_nid)
        assert memory_store._graph.has_edge(a_b_nid, a_b_c_nid)

    def test_ensure_ancestors_idempotent(self, memory_store):
        norm = store._normalize_path("/x/y/z/deep.md")
        memory_store._ensure_ancestors(norm)
        memory_store._ensure_ancestors(norm)
        x_nid = store._path_id("/x")
        assert memory_store._has_node(x_nid)
        out_edges = list(memory_store._graph.out_edges("path:/", data=True, keys=True))
        x_count = sum(1 for src, tgt, ek, d in out_edges if tgt == x_nid and d and d.get("type") == store.TREE_EDGE)
        assert x_count == 1

    def test_file_write_creates_full_ancestor_chain(self, memory_store):
        async def _test():
            await memory_store.file_write("/deep/nested/path/doc.md", "hello", index=False)
        asyncio.run(_test())

        assert memory_store._has_node("path:/deep")
        assert memory_store._has_node("path:/deep/nested")
        assert memory_store._has_node("path:/deep/nested/path")
        assert memory_store._has_node("path:/deep/nested/path/doc.md")

        for parent, child in [
            ("path:/", "path:/deep"),
            ("path:/deep", "path:/deep/nested"),
            ("path:/deep/nested", "path:/deep/nested/path"),
            ("path:/deep/nested/path", "path:/deep/nested/path/doc.md"),
        ]:
            assert memory_store._graph.has_edge(parent, child), f"Missing edge {parent} -> {child}"

    def test_mkdir_creates_full_ancestor_chain(self, memory_store):
        async def _test():
            await memory_store.mkdir("/a/b/c/newdir")
        import asyncio
        asyncio.run(_test())

        for nid in ["path:/a", "path:/a/b", "path:/a/b/c", "path:/a/b/c/newdir"]:
            assert memory_store._has_node(nid), f"Missing node {nid}"

    def test_repair_tree_fixes_missing_parents(self, memory_store, tmp_path, monkeypatch):
        nid_a = store._path_id("/orphan_dir")
        memory_store._add_node(nid_a, type="dir", name="orphan_dir")
        nid_b = store._path_id("/orphan_dir/sub")
        memory_store._add_node(nid_b, type="dir", name="sub")
        memory_store.save()
        data = json.loads(memory_store.graph_file.read_text(encoding="utf-8"))
        assert "path:/orphan_dir" in data["nodes"]
        assert "path:/orphan_dir/sub" in data["nodes"]

        gs2 = store.GraphStore("test_agent")
        assert gs2._has_node("path:/orphan_dir")
        assert gs2._has_node(store._path_id("/"))
        assert gs2._graph.has_edge("path:/", store._path_id("/orphan_dir"))
        assert gs2._graph.has_edge(store._path_id("/orphan_dir"), store._path_id("/orphan_dir/sub"))

    def test_tree_list_returns_full_tree_after_write(self, memory_store):
        async def _test():
            await memory_store.file_write("/ishmael/chemistry/basics.md", "内容", index=False)
        asyncio.run(_test())

        tree = asyncio.run(memory_store.tree_list("/"))
        assert tree["type"] == "dir"
        children = {c["name"]: c for c in tree["children"]}
        assert "ishmael" in children
        chem_children = {c["name"]: c for c in children["ishmael"]["children"]}
        assert "chemistry" in chem_children
        file_children = {c["name"]: c for c in chem_children["chemistry"]["children"]}
        assert "basics.md" in file_children
        assert file_children["basics.md"]["type"] == "file"

    def test_tree_list_empty_scope_returns_root(self, memory_store):
        tree = asyncio.run(memory_store.tree_list(None))
        assert tree["path"] == "/"
        assert tree["type"] == "dir"
        assert "children" in tree

    def test_tree_list_nonexistent_scope_returns_empty(self, memory_store):
        tree = asyncio.run(memory_store.tree_list("/nonexistent"))
        assert tree["path"] == "/nonexistent"
        assert tree["children"] == []


# ══════════════════════════════════════════════════════════════════════
# Phase 2: Description field
# ══════════════════════════════════════════════════════════════════════

class TestPhase2Description:

    def test_entity_add_stores_description(self, memory_store):
        eid = memory_store.entity_add("化学", entity_type="concept",
                                       description="化学是研究物质组成、结构、性质及变化规律的科学")
        ndata = memory_store._graph.nodes[eid]
        assert ndata["description"] == "化学是研究物质组成、结构、性质及变化规律的科学"
        assert ndata["name"] == "化学"
        assert ndata["entity_type"] == "concept"

    def test_entity_add_creates_md_file(self, memory_store):
        eid = memory_store.entity_add("测试实体", entity_type="object", description="test description")
        entity_path = f"/entities/{eid}.md"
        cp = memory_store._content_path(entity_path)
        assert cp.exists()
        content = cp.read_text(encoding="utf-8")
        assert "测试实体" in content
        assert "test description" in content

    def test_file_write_stores_description(self, memory_store):
        async def _test():
            return await memory_store.file_write("/test/desc_file.md", "词正文内容",
                                                  description="这是一个测试摘要", index=False)
        result = asyncio.run(_test())
        assert result["meta"]["description"] == "这是一个测试摘要"
        ndata = memory_store._graph.nodes[store._path_id("/test/desc_file.md")]
        assert ndata["description"] == "这是一个测试摘要"

    def test_mkdir_stores_description(self, memory_store):
        async def _test():
            return await memory_store.mkdir("/test_dir", description="目录描述")
        result = asyncio.run(_test())
        ndata = memory_store._graph.nodes[store._path_id("/test_dir")]
        assert ndata["description"] == "目录描述"

    def test_file_read_returns_description(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/read_file.md", "内容文本",
                                           description="文件描述文字", index=False)
            return await memory_store.file_read("/test/read_file.md")
        result = asyncio.run(_test())
        assert result["content"] == "内容文本"
        assert result["description"] == "文件描述文字"

    def test_entity_iter_returns_description(self, memory_store):
        memory_store.entity_add("林浅", entity_type="person", description="化学系学生")
        memory_store.entity_add("化学", entity_type="concept", description="自然科学的一门基础学科")
        entities = memory_store.entity_iter()
        names_descs = {e["name"]: e["description"] for e in entities}
        assert names_descs["林浅"] == "化学系学生"
        assert names_descs["化学"] == "自然科学的一门基础学科"

    def test_entity_search_returns_description(self, memory_store):
        memory_store.entity_add("林浅", entity_type="person", description="化学系学生")
        results = memory_store.entity_search("林浅")
        assert len(results) >= 1
        assert results[0]["description"] == "化学系学生"

    def test_tree_list_returns_description_on_file(self, memory_store):
        async def _test():
            await memory_store.file_write("/desc/file.md", "content",
                                           description="file desc", index=False)
            return await memory_store.tree_list("/")
        tree = asyncio.run(_test())
        children = tree["children"]
        desc_dir = next(c for c in children if c["name"] == "desc")
        file_node = next(c for c in desc_dir["children"] if c["name"] == "file.md")
        assert file_node["description"] == "file desc"

    def test_description_indexed_with_content(self, memory_store, monkeypatch):
        chunks_captured = []

        async def fake_embed_and_index(items):
            chunks_captured.extend(items)

        monkeypatch.setattr(memory_store, "_embed_and_index", fake_embed_and_index)

        async def _test():
            return await memory_store.file_write("/idx/test.md", "ABCDEFG",
                                                  description="摘要内容", index=True)
        asyncio.run(_test())
        all_text = " ".join(c["text"] for c in chunks_captured)
        assert "摘要内容" in all_text
        assert "ABCDEFG" in all_text


# ══════════════════════════════════════════════════════════════════════
# Phase 3: Entity on tree / has_child / semantic dedup
# ══════════════════════════════════════════════════════════════════════

class TestPhase3EntityOnTree:

    def test_record_entity_uses_has_child_not_references(self, memory_store):
        async def _test():
            return await memory_store.add_chat_record("你好", "你好，有什么可以帮你的？")
        result = asyncio.run(_test())
        nid = store._path_id(result["path"])

        has_child_edges = []
        ref_edges = []
        for _, tgt, _k, edata in memory_store._graph.out_edges(nid, data=True, keys=True):
            etype = edata.get("type", "") if edata else ""
            if etype == store.TREE_EDGE:
                has_child_edges.append(tgt)
            elif etype == "references":
                ref_edges.append(tgt)
        assert len(has_child_edges) >= 1, "Record entity should have has_child edge from file"
        assert len(ref_edges) == 0

    def test_entity_children_appear_in_tree_list(self, memory_store):
        async def _test():
            return await memory_store.add_chat_record("测试", "回复")
        result = asyncio.run(_test())
        path = result["path"]

        ents = memory_store.get_entity_children(path)
        assert len(ents) >= 1, f"File {path} should have entity children via get_entity_children"
        assert any(e.get("edge_type") in ("from", store.TREE_EDGE) for e in ents)

        tree = asyncio.run(memory_store.tree_list("/"))
        records_dir = next(c for c in tree["children"] if c["name"] == "records")
        date_dir = next(c for c in records_dir["children"])
        file_node = next(c for c in date_dir["children"] if c["type"] == "file")
        assert "children" not in file_node, "Entity children should NOT appear in tree_list"

    def test_entity_find_similar_detects_duplicates(self, memory_store, monkeypatch):
        embeds = [
            np.array([0.1, 0.2, 0.3], dtype=np.float32),
            np.array([0.1, 0.2, 0.31], dtype=np.float32),
            np.array([0.9, 0.9, 0.9], dtype=np.float32),
        ]
        monkeypatch.setattr(memory_store, "_embed_texts", lambda texts: embeds[:len(texts)])

        eid1 = memory_store.entity_add("化学", entity_type="concept",
                                        name_embedding=embeds[0].tolist())
        eid2 = memory_store.entity_add("物理", entity_type="concept",
                                        name_embedding=embeds[2].tolist())

        async def _test():
            return await memory_store.entity_find_similar(
                [embeds[1], embeds[2]], threshold=0.8
            )
        import asyncio
        results = asyncio.run(_test())

        assert results[0] == eid1, "化学≈化学 → should find duplicate"
        assert results[1] != eid1
        assert results[1] is not None, "Same embedding should match itself"

    def test_entity_name_embedding_persisted(self, memory_store, tmp_path, monkeypatch):
        vec = np.array([0.5, 0.5, 0.5], dtype=np.float32).tolist()
        memory_store.entity_add("test_ent", name_embedding=vec)
        memory_store.flush()

        gs2 = store.GraphStore("test_agent")
        entities = gs2.entity_iter()
        test_ent = next(e for e in entities if e["name"] == "test_ent")
        assert test_ent["id"] in gs2._graph.nodes
        cached_vec = gs2._graph.nodes[test_ent["id"]].get("_name_vec")
        assert cached_vec is not None
        np.testing.assert_allclose(cached_vec, vec, atol=1e-6)

    def test_tree_list_includes_dir_description(self, memory_store):
        async def _test():
            await memory_store.mkdir("/described_dir", description="This is a described directory")
            return await memory_store.tree_list("/")
        import asyncio
        tree = asyncio.run(_test())
        desc_dir = next(c for c in tree["children"] if c["name"] == "described_dir")
        assert desc_dir["description"] == "This is a described directory"


# ══════════════════════════════════════════════════════════════════════
# Core helpers
# ══════════════════════════════════════════════════════════════════════

class TestCoreHelpers:

    def test_path_id(self):
        assert store._path_id("/a/b/c.md") == "path:/a/b/c.md"
        assert store._path_id("/") == "path:/"
        assert store._path_id("") == "path:/"

    def test_id_to_path(self):
        assert store._id_to_path("path:/a/b/c.md") == "/a/b/c.md"
        assert store._id_to_path("path:/") == "/"
        assert store._id_to_path("ent_abc") == "ent_abc"

    def test_normalize_path(self):
        assert store._normalize_path("/a//b/c.md") == "/a/b/c.md"
        assert store._normalize_path("\\a\\b") == "/a/b"
        assert store._normalize_path("") == "/"

    def test_cosine_sim(self):
        a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        assert abs(store._cosine_sim(a, b) - 1.0) < 1e-6

        c = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        assert abs(store._cosine_sim(a, c) - 0.0) < 1e-6

        d = np.array([-1.0, 0.0, 0.0], dtype=np.float32)
        assert abs(store._cosine_sim(a, d) + 1.0) < 1e-6

        zero = np.zeros(3, dtype=np.float32)
        assert store._cosine_sim(a, zero) == 0.0

    def test_chunk_text(self):
        long_text = "A" * 5000
        chunks = store._chunk_text(long_text)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 3000


# ══════════════════════════════════════════════════════════════════════
# entity_add verification
# ══════════════════════════════════════════════════════════════════════

class TestEntityAdd:

    def test_defaults(self, memory_store):
        eid = memory_store.entity_add("test")
        ndata = memory_store._graph.nodes[eid]
        assert ndata["type"] == "entity"
        assert ndata["entity_type"] == "custom"
        assert ndata["description"] == ""
        assert ndata["properties"] == {}
        assert ndata["kb_refs"] == [f"/entities/{eid}.md"]

    def test_error_handling(self, memory_store):
        memory_store.entity_delete("nonexistent")  # should not crash
        assert memory_store.entity_delete("nonexistent") is False

    def test_entity_delete_removes_node(self, memory_store):
        eid = memory_store.entity_add("to_delete")
        assert memory_store._has_node(eid)
        assert memory_store.entity_delete(eid) is True
        assert not memory_store._has_node(eid)


# ══════════════════════════════════════════════════════════════════════
# Phase 4: Rename / Copy / Move
# ══════════════════════════════════════════════════════════════════════

class TestPhase4RenameCopyMove:

    def test_file_rename_basic(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/old_name.md", "hello world", index=False)
            result = await memory_store.file_rename("/test/old_name.md", "new_name.md")
            assert result["new_path"] == "/test/new_name.md"
            assert result["type"] == "file"
            # old node should not exist
            assert not memory_store._has_node(store._path_id("/test/old_name.md"))
            # new node should exist
            assert memory_store._has_node(store._path_id("/test/new_name.md"))
            # content is preserved
            read_result = await memory_store.file_read("/test/new_name.md")
            assert read_result["content"] == "hello world"
        asyncio.run(_test())

    def test_file_rename_same_name_noop(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/same.md", "content", index=False)
            result = await memory_store.file_rename("/test/same.md", "same.md")
            assert result["new_path"] == "/test/same.md"
            assert memory_store._has_node(store._path_id("/test/same.md"))
        asyncio.run(_test())

    def test_file_rename_nonexistent_raises(self, memory_store):
        async def _test():
            try:
                await memory_store.file_rename("/test/no_exist.md", "new.md")
                assert False, "Should raise FileNotFoundError"
            except FileNotFoundError:
                pass
        asyncio.run(_test())

    def test_file_rename_to_existing_raises(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/existing.md", "content", index=False)
            await memory_store.file_write("/test/target.md", "other", index=False)
            try:
                await memory_store.file_rename("/test/existing.md", "target.md")
                assert False, "Should raise FileExistsError"
            except FileExistsError:
                pass
        asyncio.run(_test())

    def test_dir_rename_recursive(self, memory_store):
        async def _test():
            await memory_store.file_write("/old_dir/sub/file1.md", "content1", index=False)
            await memory_store.file_write("/old_dir/file2.md", "content2", index=False)
            result = await memory_store.file_rename("/old_dir", "new_dir")
            assert result["new_path"] == "/new_dir"
            # child paths should exist under new dir
            assert memory_store._has_node(store._path_id("/new_dir/sub/file1.md"))
            assert memory_store._has_node(store._path_id("/new_dir/file2.md"))
            # old paths should not exist
            assert not memory_store._has_node(store._path_id("/old_dir/sub/file1.md"))
            assert not memory_store._has_node(store._path_id("/old_dir/file2.md"))
            # content preserved
            r1 = await memory_store.file_read("/new_dir/sub/file1.md")
            assert r1["content"] == "content1"
            r2 = await memory_store.file_read("/new_dir/file2.md")
            assert r2["content"] == "content2"
        asyncio.run(_test())

    def test_file_copy_basic(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/src.md", "source content", index=False)
            result = await memory_store.file_copy("/test/src.md", "/test/dest.md")
            assert result["dest"] == "/test/dest.md"
            # both exist
            r1 = await memory_store.file_read("/test/src.md")
            r2 = await memory_store.file_read("/test/dest.md")
            assert r1["content"] == "source content"
            assert r2["content"] == "source content"
        asyncio.run(_test())

    def test_file_copy_to_existing_raises(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/source.md", "a", index=False)
            await memory_store.file_write("/test/existing_target.md", "b", index=False)
            try:
                await memory_store.file_copy("/test/source.md", "/test/existing_target.md")
                assert False, "Should raise FileExistsError"
            except FileExistsError:
                pass
        asyncio.run(_test())

    def test_file_copy_nonexistent_raises(self, memory_store):
        async def _test():
            try:
                await memory_store.file_copy("/nonexistent/doc.md", "/dest/doc.md")
                assert False, "Should raise FileNotFoundError"
            except FileNotFoundError:
                pass
        asyncio.run(_test())

    def test_dir_copy_recursive(self, memory_store):
        async def _test():
            await memory_store.file_write("/src_dir/sub/a.md", "a content", index=False)
            await memory_store.file_write("/src_dir/b.md", "b content", index=False)
            result = await memory_store.file_copy("/src_dir", "/dest_dir")
            assert result["dest"] == "/dest_dir"
            # source still exists
            assert memory_store._has_node(store._path_id("/src_dir/sub/a.md"))
            # dest has copies
            assert memory_store._has_node(store._path_id("/dest_dir/sub/a.md"))
            assert memory_store._has_node(store._path_id("/dest_dir/b.md"))
            r1 = await memory_store.file_read("/dest_dir/sub/a.md")
            assert r1["content"] == "a content"
        asyncio.run(_test())

    def test_file_move_to_dir(self, memory_store):
        async def _test():
            await memory_store.mkdir("/target_dir")
            await memory_store.file_write("/source_doc.md", "move me", index=False)
            result = await memory_store.file_move("/source_doc.md", "/target_dir")
            assert result["new_path"] == "/target_dir/source_doc.md"
            # old gone
            assert not memory_store._has_node(store._path_id("/source_doc.md"))
            # new exists
            assert memory_store._has_node(store._path_id("/target_dir/source_doc.md"))
            r = await memory_store.file_read("/target_dir/source_doc.md")
            assert r["content"] == "move me"
        asyncio.run(_test())

    def test_rename_copies_tags_and_meta(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/tagged.md", "content", index=False, tags=["tag1", "tag2"])
            await memory_store.set_score_patch("/test/tagged.md", 0.1)
            await memory_store.file_rename("/test/tagged.md", "renamed_tagged.md")
            meta = memory_store._read_meta("/test/renamed_tagged.md")
            assert "tag1" in meta.get("tags", [])
            assert "tag2" in meta.get("tags", [])
            assert meta.get("score_patch") == 0.1
        asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════════
# Phase 5: Advanced Search
# ══════════════════════════════════════════════════════════════════════

class TestPhase5AdvancedSearch:

    def test_advanced_search_by_tags(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/doc1.md", "content 1", index=False, tags=["python", "web"])
            await memory_store.file_write("/test/doc2.md", "content 2", index=False, tags=["python"])
            await memory_store.file_write("/test/doc3.md", "content 3", index=False, tags=["rust"])
            results = await memory_store.advanced_search(tags=["python"])
            paths = [r["path"] for r in results]
            assert "/test/doc1.md" in paths
            assert "/test/doc2.md" in paths
            assert "/test/doc3.md" not in paths
        asyncio.run(_test())

    def test_advanced_search_by_scope(self, memory_store):
        async def _test():
            await memory_store.file_write("/scope_a/doc.md", "in scope a", index=False)
            await memory_store.file_write("/scope_b/doc.md", "in scope b", index=False)
            results = await memory_store.advanced_search(scope="/scope_a")
            paths = [r["path"] for r in results]
            assert "/scope_a/doc.md" in paths
            assert "/scope_b/doc.md" not in paths
        asyncio.run(_test())

    def test_advanced_search_by_text_query(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/alpha.md", "alpha bravo charlie", index=False)
            await memory_store.file_write("/test/other.md", "delta echo", index=False)
            results = await memory_store.advanced_search(query="bravo")
            paths = [r["path"] for r in results]
            assert "/test/alpha.md" in paths
            assert "/test/other.md" not in paths
        asyncio.run(_test())

    def test_advanced_search_and_tag_logic(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/a.md", "content", index=False, tags=["tag1", "tag2"])
            await memory_store.file_write("/test/b.md", "content", index=False, tags=["tag1"])
            await memory_store.file_write("/test/c.md", "content", index=False, tags=["tag2"])
            # AND: both tags required
            results_and = await memory_store.advanced_search(tags=["tag1", "tag2"], tag_logic="AND")
            paths_and = [r["path"] for r in results_and]
            assert "/test/a.md" in paths_and
            assert "/test/b.md" not in paths_and
            assert "/test/c.md" not in paths_and
        asyncio.run(_test())

    def test_advanced_search_or_tag_logic(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/a.md", "content", index=False, tags=["tag1", "tag2"])
            await memory_store.file_write("/test/b.md", "content", index=False, tags=["tag1"])
            # OR: any tag matches
            results_or = await memory_store.advanced_search(tags=["tag2"], tag_logic="OR")
            paths_or = [r["path"] for r in results_or]
            assert "/test/a.md" in paths_or
            assert "/test/b.md" not in paths_or
        asyncio.run(_test())

    def test_advanced_search_empty_query_returns_all_filtered(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/doc.md", "content", index=False, tags=["mytag"])
            await memory_store.file_write("/other/doc.md", "content", index=False)
            # No query, just scope = /test
            results = await memory_store.advanced_search(scope="/test")
            paths = [r["path"] for r in results]
            assert "/test/doc.md" in paths
            assert "/other/doc.md" not in paths
        asyncio.run(_test())

    def test_advanced_search_sort_by_updated_at(self, memory_store):
        async def _test():
            await memory_store.file_write("/test/old.md", "old", index=False)
            import time
            time.sleep(0.01)
            await memory_store.file_write("/test/new.md", "new", index=False)
            results = await memory_store.advanced_search(sort_by="updated_at", sort_order="desc")
            assert len(results) >= 2
            # newer file should come first
            assert results[0]["path"] == "/test/new.md"
        asyncio.run(_test())


# ══════════════════════════════════════════════════════════════════════
# Phase 6: Extraction Status
# ══════════════════════════════════════════════════════════════════════

class TestPhase6ExtractionStatus:

    def test_initial_status(self, memory_store):
        status = memory_store.get_extraction_status()
        assert status["pending"] == 0
        assert status["running"] == 0
        assert status["last_running"] is None
        assert status["last_success"] is None
        assert status["last_error"] is None

    def test_register_and_complete(self, memory_store):
        memory_store.register_extraction("/test/doc.md")
        status = memory_store.get_extraction_status()
        assert status["pending"] == 1
        assert status["running"] == 1
        assert status["last_running"] == "/test/doc.md"

        memory_store.complete_extraction("/test/doc.md", success=True)
        status = memory_store.get_extraction_status()
        assert status["pending"] == 0
        assert status["running"] == 0
        assert status["last_success"] == "/test/doc.md"

    def test_register_multiple_then_error(self, memory_store):
        memory_store.register_extraction("/test/a.md")
        memory_store.register_extraction("/test/b.md")
        status = memory_store.get_extraction_status()
        assert status["pending"] == 2
        assert status["running"] == 2

        memory_store.complete_extraction("/test/a.md", success=False, error="API error")
        status = memory_store.get_extraction_status()
        assert status["pending"] == 1
        assert status["running"] == 1
        assert "API error" in (status["last_error"] or "")


# ══════════════════════════════════════════════════════════════════════
# Phase 7: Entity Detail
# ══════════════════════════════════════════════════════════════════════

class TestPhase7EntityDetail:

    def test_entity_detail_exists(self, memory_store):
        eid = memory_store.entity_add("测试实体", entity_type="concept",
                                       description="这是一个测试")
        detail = memory_store.get_entity_detail(eid)
        assert detail is not None
        assert detail["name"] == "测试实体"
        assert detail["entity_type"] == "concept"
        assert detail["description"] == "这是一个测试"
        assert detail["relations_count"] == 0
        assert detail["id"] == eid

    def test_entity_detail_not_found(self, memory_store):
        detail = memory_store.get_entity_detail("nonexistent_id")
        assert detail is None

    def test_entity_detail_not_entity(self, memory_store):
        nid = store._path_id("/not_an_entity")
        memory_store._add_node(nid, type="file", name="test.txt")
        detail = memory_store.get_entity_detail(nid)
        assert detail is None

    def test_entity_detail_shows_kb_refs(self, memory_store):
        eid = memory_store.entity_add("test", kb_refs=["/notes/test.md", "/notes/ref.md"])
        detail = memory_store.get_entity_detail(eid)
        assert len(detail["kb_refs"]) >= 2

    def test_entity_detail_shows_relations_count(self, memory_store):
        eid1 = memory_store.entity_add("entity1")
        eid2 = memory_store.entity_add("entity2")
        memory_store.relation_add(eid1, eid2, rel_type="relates_to")
        detail = memory_store.get_entity_detail(eid1)
        assert detail["relations_count"] >= 1
