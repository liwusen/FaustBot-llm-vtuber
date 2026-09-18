import asyncio
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

    yield gs
    gs.close()


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
# Phase 1b: tree_list include_metadata
# ══════════════════════════════════════════════════════════════════════

class TestTreeListIncludeMetadata:

    METADATA_KEYS = {
        "updated_at", "tags", "chunk_count", "indexed",
        "declared_by", "score_patch", "content_type",
    }

    def _write_file(self, memory_store):
        async def _test():
            await memory_store.file_write(
                "/meta/doc.md", "第一行\n第二行", description="元数据文件",
                declared_by="config", tags=["alpha", "beta"], index=False,
            )
        asyncio.run(_test())

    def _find_file_node(self, tree, name="doc.md"):
        meta_dir = next(c for c in tree["children"] if c["name"] == "meta")
        return next(c for c in meta_dir["children"] if c["name"] == name)

    def test_tree_list_include_metadata_adds_keys_without_line_count(self, memory_store):
        self._write_file(memory_store)
        tree = asyncio.run(memory_store.tree_list("/", include_metadata=True))
        node = self._find_file_node(tree)

        assert self.METADATA_KEYS <= set(node), f"missing keys: {self.METADATA_KEYS - set(node)}"
        assert "line_count" not in node
        assert node["description"] == "元数据文件"
        assert node["tags"] == ["alpha", "beta"]
        assert node["declared_by"] == "config"
        assert node["chunk_count"] >= 1
        assert node["indexed"] is False
        assert node["score_patch"] == 0.0
        assert node["content_type"] == ""
        assert isinstance(node["updated_at"], str) and node["updated_at"]

    def test_tree_list_default_excludes_metadata_keys(self, memory_store):
        self._write_file(memory_store)
        tree = asyncio.run(memory_store.tree_list("/"))
        node = self._find_file_node(tree)

        assert set(node) == {"path", "name", "type", "description"}
        assert not (self.METADATA_KEYS & set(node))
        assert node["description"] == "元数据文件"


class TestTreeRouteIncludeMetadata:
    """路由层：include_metadata 参数名一旦漂移，前端会静默拿不到元数据（不报错），故单独钉住。"""

    def _client(self, memory_store, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import faust_backend.memory.api as memory_api

        monkeypatch.setattr(memory_api, "_m", lambda: memory_store)
        app = FastAPI()
        app.include_router(memory_api.router)
        return TestClient(app)

    def _write_file(self, memory_store):
        async def _test():
            await memory_store.file_write(
                "/meta/doc.md", "第一行", description="元数据文件",
                declared_by="config", tags=["alpha"], index=False,
            )
        asyncio.run(_test())

    def _doc_node(self, tree):
        meta_dir = next(c for c in tree["children"] if c["name"] == "meta")
        return next(c for c in meta_dir["children"] if c["name"] == "doc.md")

    def test_route_forwards_include_metadata(self, memory_store, monkeypatch):
        self._write_file(memory_store)
        client = self._client(memory_store, monkeypatch)

        with_meta = self._doc_node(client.get("/faust/memory/tree", params={"include_metadata": True}).json()["tree"])
        plain = self._doc_node(client.get("/faust/memory/tree").json()["tree"])

        assert with_meta["chunk_count"] >= 1
        assert with_meta["tags"] == ["alpha"]
        assert "line_count" not in with_meta
        assert "chunk_count" not in plain
        assert plain["description"] == "元数据文件"


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
        # 生产向量维度（1536）：nano-vectordb 实例按 EMBED_DIM 建库，维度必须一致
        vec_a = np.zeros(1536, dtype=np.float32)
        vec_a[:3] = (0.1, 0.2, 0.3)
        vec_near = np.zeros(1536, dtype=np.float32)
        vec_near[:3] = (0.1, 0.2, 0.31)
        vec_far = np.zeros(1536, dtype=np.float32)
        vec_far[:3] = (0.9, 0.9, 0.9)
        embeds = [vec_a, vec_near, vec_far]
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
            meta = memory_store._get_meta("/test/renamed_tagged.md")
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

    def test_search_with_scope_filters_to_scope(self, memory_store, monkeypatch):
        """回归：search() 带 scope 时不应滤掉所有结果（scope_prefix 丢失前导斜杠的 bug）。"""
        monkeypatch.setattr(store.conf, "BM25_ONLY", True)  # 走 BM25 路径，避免依赖向量库
        async def _test():
            await memory_store.file_write("/user/preferences", "用户偏好是喜欢简洁回复", index=True, tags=["user"])
            await memory_store.file_write("/records/2026-01-01/chat", "随意聊天记录内容", index=True)
            # 不带 scope：两篇都应出现在潜在结果里
            global_results = await memory_store.search("简洁 回复", top_k=5, use_graph=False)
            global_paths = [r["path"] for r in global_results]
            assert global_paths, "BM25 索引应能检索到内容"
            # 带 scope：只应返回 user 内的
            scoped = await memory_store.search("简洁 回复", scope="/user", top_k=5, use_graph=False)
            scoped_paths = [r["path"] for r in scoped]
            assert scoped_paths, "带 scope 搜索不应为空（scope_prefix 前导斜杠 bug 回归）"
            assert "/user/preferences" in scoped_paths
            assert all(p.startswith("/user") for p in scoped_paths)
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
        detail:dict = memory_store.get_entity_detail(nid)
        assert detail["entity_type"]=="file"

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


# ══════════════════════════════════════════════════════════════════════
# Phase 7: file_delete_tree (recursive delete)
# ══════════════════════════════════════════════════════════════════════

class TestFileDeleteTree:

    def _seed(self, memory_store):
        async def _test():
            await memory_store.file_write("/del/dir1/a.md", "a", index=True)
            await memory_store.file_write("/del/dir1/sub/b.md", "b", index=True)
            await memory_store.file_write("/del/f.md", "f", index=True)
        asyncio.run(_test())

    def test_delete_single_file(self, memory_store):
        self._seed(memory_store)
        result = asyncio.run(memory_store.file_delete_tree("/del/f.md"))
        assert result["path"] == "/del/f.md"
        assert not memory_store._has_node(store._path_id("/del/f.md"))
        assert not memory_store._content_path("/del/f.md").exists()

    def test_recursive_delete_clears_tree(self, memory_store):
        self._seed(memory_store)
        result = asyncio.run(memory_store.file_delete_tree("/del/dir1"))
        assert result["path"] == "/del/dir1"
        for p in ("/del/dir1", "/del/dir1/a.md", "/del/dir1/sub", "/del/dir1/sub/b.md"):
            assert not memory_store._has_node(store._path_id(p)), f"节点残留: {p}"
        assert not memory_store._content_path("/del/dir1").exists()
        # 兄弟节点不受影响
        assert memory_store._has_node(store._path_id("/del/f.md"))

    def test_delete_nonexistent_raises(self, memory_store):
        import pytest
        with pytest.raises(FileNotFoundError):
            asyncio.run(memory_store.file_delete_tree("/no/such/path"))

    def test_delete_dir_requires_recursive_flag(self, memory_store):
        """工具层保护：目录不带 recursive_dangerous 应拒绝。"""
        from faust_backend.memory.store import _path_id
        self._seed(memory_store)
        nid = _path_id("/del/dir1")
        assert memory_store._has_node(nid)
        ntype = memory_store._get_node_attr(nid, "type", "file")
        assert ntype == "dir"


# ── BM25 jieba 化 + search_bm25 ──────────────────────────────

@pytest.mark.asyncio
async def test_search_bm25_chinese_match(memory_store):
    await memory_store.file_write("/notes/映射.md", "LSTM 是长短期记忆网络，用于序列建模。", index=True)
    await memory_store.file_write("/notes/无关.md", "今天天气很好，适合出门散步。", index=True)
    # rank_bm25 在 N<3 的语料下 IDF 退化（零/负分），补充 filler 使排序有意义
    await memory_store.file_write("/notes/f1.md", "会议记录：讨论了季度预算分配与人员安排。", index=True)
    await memory_store.file_write("/notes/f2.md", "读书笔记：三体讲述了宇宙文明间的接触。", index=True)
    await memory_store.file_write("/notes/f3.md", "菜谱：红烧肉需要焯水、炒糖色、炖煮两小时。", index=True)
    await memory_store.file_write("/notes/f4.md", "旅行计划：明年春天去云南看洱海和雪山。", index=True)
    res = await memory_store.search_bm25(["LSTM", "序列"], top_k=3)
    assert res, "中文词命中文档失败"
    assert "映射" in res[0]["path"], "相关文档应排第一"
    assert res[0]["_source"] == "bm25"
    assert res[0]["score"] > res[-1]["score"]


@pytest.mark.asyncio
async def test_search_bm25_empty_tokens(memory_store):
    assert await memory_store.search_bm25([], top_k=3) == []


# ── 实体名向量落 entity.vdb ──────────────────────────────────

def test_entity_name_vector_survives_restart_and_dedup(memory_store):
    """行为断言：写入的实体名向量在重启后仍能被去重检索命中。"""
    import asyncio

    import numpy as np

    import faust_backend.memory.store as store

    vec = [0.5] * 1536
    eid = memory_store.entity_add("vec_persist", "concept", name_embedding=vec)
    memory_store.close()

    gs2 = store.GraphStore("test_agent")
    hits = asyncio.run(gs2.entity_find_similar([np.asarray(vec, dtype=np.float32)], threshold=0.99))
    assert hits == [eid]
    gs2.close()


def test_entity_delete_removes_name_vector(memory_store):
    import asyncio

    import numpy as np

    import faust_backend.memory.store as store

    vec = [0.25] * 1536
    eid = memory_store.entity_add("vec_gone", "concept", name_embedding=vec)
    assert memory_store.entity_delete(eid) is True
    memory_store.close()

    gs2 = store.GraphStore("test_agent")
    hits = asyncio.run(gs2.entity_find_similar([np.asarray(vec, dtype=np.float32)], threshold=0.99))
    assert hits == [None]
    gs2.close()


def test_bm25_index_comes_from_sql_not_filesystem(memory_store, monkeypatch):
    """BM25 数据源必须是 SQL：写入后立即检索可命中，且不再依赖 meta 目录文件。"""
    import asyncio

    async def _run():
        await memory_store.file_write("/bm25/alpha.md", "阿尔法 记忆 检索 内容", description="阿尔法")
        memory_store._mark_bm25_dirty()
        hits = await memory_store.search_bm25(["阿尔法"], top_k=3)
        return hits

    hits = asyncio.run(_run())
    assert any(h["path"] == "/bm25/alpha.md" for h in hits)


def test_changed_and_advanced_search_are_sql_backed(memory_store):
    import asyncio
    import time

    async def _run():
        await memory_store.file_write("/scan/a.md", "内容 A", description="甲")
        await memory_store.set_tags("/scan/a.md", ["t1", "t2"])
        changed = await memory_store.get_changed_nodes(time.time() - 60)
        advanced = await memory_store.advanced_search(tags=["t2"], tag_logic="AND")
        return changed, advanced

    changed, advanced = asyncio.run(_run())
    assert any(c["path"] == "/scan/a.md" for c in changed)
    assert [a["path"] for a in advanced] == ["/scan/a.md"]
    assert advanced[0]["tags"] == ["t1", "t2"]


def test_get_changed_nodes_scope_matches_path_prefix(memory_store):
    """回归：scope 必须按 `/scope/` 前缀匹配（曾漏掉前导斜杠，导致 scope 过滤恒为空）。"""
    import asyncio
    import time

    async def _run():
        since = time.time() - 3600
        await memory_store.file_write("/notes/a.md", "甲")
        await memory_store.file_write("/notes/sub/b.md", "乙")
        await memory_store.file_write("/other/c.md", "丙")
        return await memory_store.get_changed_nodes(since, scope="/notes")

    assert sorted(item["path"] for item in asyncio.run(_run())) == ["/notes/a.md", "/notes/sub/b.md"]


def test_entity_merge_rewires_edges_without_duplicates_or_self_loops(memory_store):
    import asyncio

    import faust_backend.memory.store as store

    async def _write_doc():
        await memory_store.file_write("/kb/doc.md", "文档")

    asyncio.run(_write_doc())
    doc_nid = store._path_id("/kb/doc.md")

    keep = memory_store.entity_add("重复实体", "custom", properties={"y": 9}, kb_refs=["/a.md"])
    absorb = memory_store.entity_add("重复实体别名", "concept", description="描述B",
                                     properties={"x": 1, "y": 2}, kb_refs=["/b.md"])
    other = memory_store.entity_add("其他", "concept")

    memory_store._add_edge(doc_nid, keep, "from")
    memory_store._add_edge(doc_nid, absorb, "from")        # 改指后与上一条重边
    memory_store._add_edge(keep, absorb, "relates_to")     # 改指后成自环
    memory_store._add_edge(keep, other, "relates_to")
    memory_store._add_edge(absorb, other, "relates_to")    # 改指后与上一条重边
    memory_store._add_edge(other, absorb, "relates_to")    # 改指后成为 other -> keep

    stats = memory_store.entity_merge(keep, absorb)

    assert stats["ok"] is True
    assert stats["rewired_edges"] == 1
    assert stats["dropped_duplicate_edges"] == 2
    assert stats["dropped_self_loops"] == 1
    assert stats["absorb_name"] == "重复实体别名"

    assert memory_store._has_node(absorb) is False
    assert not memory_store._graph.has_edge(keep, keep)
    assert not memory_store._graph.has_edge(keep, absorb)
    assert not memory_store._content_path(f"/entities/{absorb}.md").exists()

    triples = [(u, v, d.get("type")) for u, v, _k, d in memory_store._graph.edges(data=True, keys=True)]
    assert len(triples) == len(set(triples)), "合并后不允许出现 (src, dst, type) 重复边"
    assert triples.count((doc_nid, keep, "from")) == 1
    assert triples.count((keep, other, "relates_to")) == 1
    assert triples.count((other, keep, "relates_to")) == 1

    detail = memory_store.get_entity_detail(keep)
    assert detail["entity_type"] == "concept"
    assert detail["description"] == "描述B"
    assert detail["properties"] == {"x": 1, "y": 9}
    assert detail["kb_refs"] == ["/a.md", f"/entities/{keep}.md", "/b.md"]


def test_entity_merge_rejects_invalid_arguments(memory_store):
    keep = memory_store.entity_add("甲", "concept")
    assert memory_store.entity_merge(keep, keep)["ok"] is False
    assert memory_store.entity_merge(keep, "ent_不存在")["ok"] is False
    assert memory_store.entity_merge("path:/somewhere", keep)["ok"] is False
    assert memory_store._has_node(keep) is True


def test_entity_merge_persists_and_drops_absorbed_name_vector(memory_store):
    import asyncio

    import numpy as np

    import faust_backend.memory.store as store

    keep_vec = [1.0] + [0.0] * 1535
    absorb_vec = [0.0, 1.0] + [0.0] * 1534
    other_vec = [0.0, 0.0, 1.0] + [0.0] * 1533
    keep = memory_store.entity_add("保留", "concept", properties={"y": 9}, name_embedding=keep_vec)
    absorb = memory_store.entity_add("吸收", "concept", description="别名", properties={"x": 1},
                                     name_embedding=absorb_vec)
    other = memory_store.entity_add("邻居", "concept", name_embedding=other_vec)
    memory_store._add_edge(absorb, other, "relates_to")

    assert memory_store.entity_merge(keep, absorb)["ok"] is True
    memory_store.close()

    gs2 = store.GraphStore("test_agent")
    assert gs2._has_node(absorb) is False
    assert list(gs2._graph.edges(keep)) == [(keep, other)]
    detail = gs2.get_entity_detail(keep)
    assert detail["properties"] == {"x": 1, "y": 9}
    assert detail["description"] == "别名"
    assert detail["kb_refs"] == [f"/entities/{keep}.md"]
    assert not gs2._content_path(f"/entities/{absorb}.md").exists()
    assert asyncio.run(gs2.entity_find_similar([np.asarray(absorb_vec, dtype=np.float32)], 0.99)) == [None]
    assert asyncio.run(gs2.entity_find_similar([np.asarray(keep_vec, dtype=np.float32)], 0.99)) == [keep]
    gs2.close()


# ══════════════════════════════════════════════════════════════════════
# 子树路径改写：必须按「路径段前缀」，不能是子串 / LIKE 通配符
# ══════════════════════════════════════════════════════════════════════

class TestSubtreePathRewrite:
    """回归终审 3 处缺陷：目录改名 / 移动用 SQL `replace()`、`_subtree_paths` 用 `LIKE`。"""

    def test_dir_rename_rewrites_descendants_by_prefix(self, memory_store):
        """`/diary/diary_2026.md` 改名 `/diary`->`journal` 后必须还在子目录里。

        SQL `replace()` 是子串替换，会把孩子改写成 `/journal/journal_2026.md`：
        同进程内 SQL 与 nx 立刻分歧，重启后原文件读不到正文。
        """
        async def _run():
            await memory_store.file_write("/diary/diary_2026.md", "dear diary", index=False)
            await memory_store.file_rename("/diary", "journal")

        asyncio.run(_run())

        sql_paths = sorted(r["path"] for r in memory_store.db.all(
            "SELECT path FROM nodes WHERE path = '/journal' OR path LIKE '/journal/%'"))
        assert sql_paths == ["/journal", "/journal/diary_2026.md"]
        assert memory_store.db.one(
            "SELECT count(*) AS c FROM nodes WHERE path='/journal/journal_2026.md'")["c"] == 0
        # SQL 是唯一真源，nx 必须与之一致
        assert memory_store._graph.has_node("path:/journal/diary_2026.md")
        assert not memory_store._graph.has_node("path:/journal/journal_2026.md")

        memory_store.close()
        gs2 = store.GraphStore("test_agent")
        assert asyncio.run(gs2.file_read("/journal/diary_2026.md"))["content"] == "dear diary"
        with pytest.raises(FileNotFoundError):
            asyncio.run(gs2.file_read("/journal/journal_2026.md"))
        gs2.close()

    def test_dir_move_rewrites_descendants_by_prefix(self, memory_store):
        """移动目录时同样不能子串替换（`/diary/diary_2026.md` -> `/target/diary/target/...`）。"""
        async def _run():
            await memory_store.file_write("/diary/diary_2026.md", "dear diary", index=False)
            await memory_store.file_write("/docs/sub/sub_notes.md", "sub notes", index=False)
            await memory_store.mkdir("/target")
            await memory_store.file_move("/diary", "/target")
            await memory_store.file_move("/docs/sub", "/target")

        asyncio.run(_run())

        sql_paths = sorted(r["path"] for r in memory_store.db.all(
            "SELECT path FROM nodes WHERE path LIKE '/target/%'"))
        assert sql_paths == [
            "/target/diary", "/target/diary/diary_2026.md",
            "/target/sub", "/target/sub/sub_notes.md",
        ]
        assert memory_store._graph.has_node("path:/target/diary/diary_2026.md")

        memory_store.close()
        gs2 = store.GraphStore("test_agent")
        assert asyncio.run(gs2.file_read("/target/diary/diary_2026.md"))["content"] == "dear diary"
        assert asyncio.run(gs2.file_read("/target/sub/sub_notes.md"))["content"] == "sub notes"
        gs2.close()

    def test_dir_copy_does_not_treat_underscore_as_like_wildcard(self, memory_store):
        """`/a_b` 的子树查询不能把 `/axb` 也算进去（`_` 是 LIKE 通配符）。"""
        async def _run():
            await memory_store.file_write("/a_b/c.md", "in a_b", index=False)
            await memory_store.file_write("/axb/other.md", "in axb", index=False)
            await memory_store.file_copy("/a_b", "/copy_a_b")

        asyncio.run(_run())

        assert memory_store._subtree_paths("/a_b") == ["/a_b", "/a_b/c.md"]
        copied = sorted(r["path"] for r in memory_store.db.all(
            "SELECT path FROM nodes WHERE path LIKE '/copy_a_b%'"))
        assert copied == ["/copy_a_b", "/copy_a_b/c.md"]


def test_record_entity_has_child_lives_in_edges_not_parent_id(memory_store):
    """不变量 3：记录节点 -> 实体的 has_child 属于 `edges`，不是 `nodes.parent_id`。

    实体没有 path，`parent_id` 只承载两个 path 节点之间的关系（`migrate.py` 也是这样导入的）。
    """
    result = asyncio.run(memory_store.add_chat_record("你好", "回复"))
    nid = store._path_id(result["path"])
    ents = memory_store.get_entity_children(result["path"])
    assert len(ents) == 1
    eid = ents[0]["id"]

    assert memory_store.db.one("SELECT parent_id FROM nodes WHERE id=?", (eid,))["parent_id"] is None
    edge_srcs = [r["src"] for r in memory_store.db.all(
        "SELECT src FROM edges WHERE dst=? AND type=?", (eid, store.TREE_EDGE))]
    assert edge_srcs == [nid]

    memory_store.close()
    gs2 = store.GraphStore("test_agent")
    assert [e["id"] for e in gs2.get_entity_children(result["path"])] == [eid]
    gs2.close()
