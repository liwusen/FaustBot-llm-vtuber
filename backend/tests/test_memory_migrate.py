"""migrate.py：旧 JSON -> SQLite 的完整性、幂等与失败回滚。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

DIM = 8


def _write_legacy(store_dir: Path, *, break_graph: bool = False) -> None:
    store_dir.mkdir(parents=True, exist_ok=True)
    (store_dir / "index").mkdir(exist_ok=True)
    (store_dir / "meta" / "notes").mkdir(parents=True, exist_ok=True)
    (store_dir / "content" / "notes").mkdir(parents=True, exist_ok=True)
    (store_dir / "content" / "notes" / "a.md").write_text("hello", encoding="utf-8")

    graph = {
        "nodes": {
            "path:/": {"type": "dir", "name": "/"},
            "path:/notes": {"type": "dir", "name": "notes"},
            "path:/notes/a.md": {"type": "file", "name": "a.md", "description": "笔记"},
            "ent_1": {
                "type": "entity", "name": "Faust", "entity_type": "person",
                "description": "角色", "properties": {"k": "v"},
                "kb_refs": ["/notes/a.md"], "created_at": "2026-09-01T00:00:00Z",
            },
        },
        "edges": [
            {"source": "path:/", "target": "path:/notes", "key": "e1", "type": "has_child"},
            {"source": "path:/notes", "target": "path:/notes/a.md", "key": "e2", "type": "has_child"},
            {"source": "path:/notes/a.md", "target": "ent_1", "key": "e3", "type": "from"},
        ],
    }
    raw = json.dumps(graph, ensure_ascii=False)
    if break_graph:
        raw = raw[:-20]  # 截断 => JSON 解析失败
    (store_dir / "graph.json").write_text(raw, encoding="utf-8")

    meta = {
        "path": "/notes/a.md", "declared_by": "agent", "description": "笔记",
        "updated_at": "2026-09-10T01:02:03Z", "chunk_count": 1, "indexed": True,
        "tags": ["fav", "note"], "score_patch": 0.1,
    }
    (store_dir / "meta" / "notes" / "a.md.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    chunk = {
        "chunk_id": "/notes/a.md::chunk::1::abc", "node_path": "/notes/a.md",
        "chunk_index": 1, "text": "hello", "text_preview": "hello",
        "scope_prefix": "/notes", "updated_at": "2026-09-10T01:02:03Z", "indexed": True,
    }
    (store_dir / "meta" / "notes" / "a.md.chunks.json").write_text(
        json.dumps([chunk], ensure_ascii=False), encoding="utf-8")
    (store_dir / "meta" / "chunks_index.json").write_text(
        json.dumps({chunk["chunk_id"]: chunk}, ensure_ascii=False), encoding="utf-8")
    (store_dir / "meta" / "tasks.json").write_text(json.dumps([{
        "task_id": "tsk_1", "type": "extract", "status": "pending", "payload": {"a": 1},
        "created_at": "2026-09-10T01:00:00Z", "updated_at": "2026-09-10T01:00:00Z", "error": "",
    }], ensure_ascii=False), encoding="utf-8")

    # 真实旧库存在「meta 文件比图节点多」的孤儿元数据（节点已删、meta 残留）
    orphan = dict(meta, path="/gone/deleted.md", tags=["ghost"], chunk_count=1)
    (store_dir / "meta" / "gone").mkdir(parents=True, exist_ok=True)
    (store_dir / "meta" / "gone" / "deleted.md.meta.json").write_text(
        json.dumps(orphan, ensure_ascii=False), encoding="utf-8")
    orphan_chunk = dict(chunk, chunk_id="/gone/deleted.md::chunk::1::ghost",
                        node_path="/gone/deleted.md")
    (store_dir / "meta" / "chunks_index.json").write_text(
        json.dumps({chunk["chunk_id"]: chunk, orphan_chunk["chunk_id"]: orphan_chunk},
                   ensure_ascii=False), encoding="utf-8")

    vecs = [{"id": "ent_1", "v": [0.1] * DIM}]
    (store_dir / "index" / "entity_vecs.jsonl").write_text(
        "\n".join(json.dumps(v) for v in vecs) + "\n", encoding="utf-8")
    (store_dir / "index" / "entity_vecs.jsonl.deadbeef.tmp").write_text("junk", encoding="utf-8")


def test_migration_imports_everything_and_archives(tmp_path):
    from faust_backend.memory.migrate import LEGACY_ARCHIVE_DIRNAME, migrate_from_json
    from faust_backend.memory.storage import MemoryDB, epoch_to_iso

    store_dir = tmp_path / "memory"
    _write_legacy(store_dir)
    db = MemoryDB(store_dir / "memory.sqlite")
    report = migrate_from_json(store_dir, db, embed_dim=DIM)

    assert report.migrated is True
    assert db.one("SELECT count(*) AS c FROM nodes")["c"] == 4
    assert db.one("SELECT count(*) AS c FROM edges")["c"] == 1
    assert db.one("SELECT parent_id FROM nodes WHERE id='path:/notes/a.md'")["parent_id"] == "path:/notes"
    assert db.one("SELECT path FROM nodes WHERE id='path:/notes/a.md'")["path"] == "/notes/a.md"
    assert [r["tag"] for r in db.all("SELECT tag FROM tags ORDER BY tag")] == ["fav", "note"]
    assert db.one("SELECT updated_at FROM nodes WHERE id='path:/notes/a.md'")["updated_at"] == 1789002123.0
    assert db.one("SELECT declared_by, chunk_count, indexed FROM nodes WHERE path='/notes/a.md'")["chunk_count"] == 1
    assert db.one("SELECT count(*) AS c FROM chunks")["c"] == 1
    assert db.one("SELECT count(*) AS c FROM tasks")["c"] == 1
    assert db.one("SELECT created_at FROM nodes WHERE id='ent_1'")["created_at"] == 1788220800.0
    entity = json.loads(db.one("SELECT data FROM nodes WHERE id='ent_1'")["data"])
    assert entity["properties"] == {"k": "v"}
    assert entity["kb_refs"] == ["/notes/a.md"]

    from nano_vectordb import NanoVectorDB
    vdb = NanoVectorDB(DIM, storage_file=str(store_dir / "index" / "entity.vdb"))
    assert len(vdb) == 1
    assert vdb.get(["ent_1"])[0]["__id__"] == "ent_1"

    archive = store_dir / LEGACY_ARCHIVE_DIRNAME
    assert (archive / "graph.json").exists()
    assert (archive / "index" / "entity_vecs.jsonl").exists()
    assert (archive / "meta" / "notes" / "a.md.meta.json").exists()
    assert not (store_dir / "graph.json").exists()
    assert not (store_dir / "meta" / "chunks_index.json").exists()
    assert not list((store_dir / "index").glob("*.tmp"))
    assert (store_dir / "content" / "notes" / "a.md").read_text(encoding="utf-8") == "hello"
    assert epoch_to_iso(None) == ""
    db.close()


def test_migration_is_idempotent(tmp_path):
    from faust_backend.memory.migrate import migrate_if_needed
    from faust_backend.memory.storage import MemoryDB

    store_dir = tmp_path / "memory"
    _write_legacy(store_dir)
    db = MemoryDB(store_dir / "memory.sqlite")
    first = migrate_if_needed(store_dir, db, embed_dim=DIM)
    second = migrate_if_needed(store_dir, db, embed_dim=DIM)
    assert first.migrated is True
    assert second.migrated is False
    assert db.one("SELECT count(*) AS c FROM nodes")["c"] == 4
    db.close()


def test_migration_failure_rolls_back_and_keeps_legacy(tmp_path):
    from faust_backend.memory.migrate import migrate_from_json
    from faust_backend.memory.storage import MemoryDB, MigrationError

    store_dir = tmp_path / "memory"
    _write_legacy(store_dir, break_graph=True)
    db = MemoryDB(store_dir / "memory.sqlite")
    with pytest.raises(MigrationError):
        migrate_from_json(store_dir, db, embed_dim=DIM)

    assert (store_dir / "graph.json").exists()
    assert not (store_dir / "_legacy_json").exists()
    assert db.one("SELECT count(*) AS c FROM nodes")["c"] == 0
    db.close()


def test_migration_skips_orphan_meta_without_node(tmp_path):
    """meta 比图节点多（真实旧库有 162 例）不得让迁移整体失败：孤儿 meta/分块跳过并计数。"""
    from faust_backend.memory.migrate import migrate_from_json
    from faust_backend.memory.storage import MemoryDB

    store_dir = tmp_path / "memory"
    _write_legacy(store_dir)
    db = MemoryDB(store_dir / "memory.sqlite")
    report = migrate_from_json(store_dir, db, embed_dim=DIM)

    assert report.counts["orphan_meta"] == 1
    assert db.one("SELECT count(*) AS c FROM nodes WHERE path LIKE '/gone/%'")["c"] == 0
    assert db.one("SELECT count(*) AS c FROM tags WHERE tag='ghost'")["c"] == 0
    assert db.one("SELECT count(*) AS c FROM chunks WHERE chunk_id LIKE '/gone/%'")["c"] == 0
    assert db.one("SELECT count(*) AS c FROM nodes")["c"] == 4
    db.close()
