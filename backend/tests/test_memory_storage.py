"""storage.py：schema、事务、时间转换。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


def test_schema_created_and_versioned(tmp_path):
    from faust_backend.memory.storage import SCHEMA_VERSION, MemoryDB

    db = MemoryDB(tmp_path / "memory.sqlite")
    tables = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"nodes", "edges", "tags", "chunks", "tasks", "schema_version"} <= tables
    assert db.one("SELECT version FROM schema_version")["version"] == SCHEMA_VERSION
    assert db.one("PRAGMA journal_mode")[0].lower() == "wal"
    db.close()


def test_transaction_commit_and_rollback(tmp_path):
    from faust_backend.memory.storage import MemoryDB

    db = MemoryDB(tmp_path / "memory.sqlite")
    with db.transaction() as conn:
        conn.execute("INSERT INTO nodes(id, type, name) VALUES (?, ?, ?)", ("n1", "dir", "n1"))
    assert db.one("SELECT id FROM nodes WHERE id='n1'")["id"] == "n1"

    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO nodes(id, type, name) VALUES (?, ?, ?)", ("n2", "dir", "n2"))
            raise RuntimeError("boom")
    assert db.one("SELECT id FROM nodes WHERE id='n2'") is None
    db.close()


def test_transaction_is_reentrant(tmp_path):
    from faust_backend.memory.storage import MemoryDB

    db = MemoryDB(tmp_path / "memory.sqlite")
    with db.transaction() as outer:
        outer.execute("INSERT INTO nodes(id, type, name) VALUES ('a', 'dir', 'a')")
        with db.transaction() as inner:
            inner.execute("INSERT INTO nodes(id, type, name) VALUES ('b', 'dir', 'b')")
        assert db.one("SELECT count(*) AS c FROM nodes")["c"] == 2
    db.close()


def test_foreign_key_cascade_on_rename_and_delete(tmp_path):
    from faust_backend.memory.storage import MemoryDB

    db = MemoryDB(tmp_path / "memory.sqlite")
    with db.transaction() as conn:
        conn.execute("INSERT INTO nodes(id, type, name) VALUES ('ent_1', 'entity', 'E')")
        conn.execute("INSERT INTO nodes(id, type, name, path) VALUES ('path:/a', 'dir', 'a', '/a')")
        conn.execute("INSERT INTO nodes(id, type, name, path, parent_id) VALUES ('path:/a/b', 'file', 'b', '/a/b', 'path:/a')")
        conn.execute("INSERT INTO edges(src, dst, type, key) VALUES ('path:/a', 'ent_1', 'from', 'k1')")
        conn.execute("INSERT INTO tags(node_id, tag) VALUES ('path:/a/b', 'x')")
        conn.execute("INSERT INTO chunks(chunk_id, node_id, chunk_index, text) VALUES ('c1', 'path:/a/b', 1, 't')")

    with db.transaction() as conn:
        conn.execute("UPDATE nodes SET id='path:/z', path='/z' WHERE id='path:/a'")
    assert db.one("SELECT parent_id FROM nodes WHERE id='path:/a/b'")["parent_id"] == "path:/z"

    with db.transaction() as conn:
        conn.execute("DELETE FROM nodes WHERE id='path:/a/b'")
    assert db.one("SELECT count(*) AS c FROM tags")["c"] == 0
    assert db.one("SELECT count(*) AS c FROM chunks")["c"] == 0
    db.close()


def test_time_roundtrip_utc():
    from faust_backend.memory.storage import epoch_to_iso, iso_to_epoch

    assert epoch_to_iso(iso_to_epoch("2026-09-15T00:00:00Z")) == "2026-09-15T00:00:00Z"
    assert iso_to_epoch("") is None
    assert epoch_to_iso(None) == ""


def test_ensure_root_node(tmp_path):
    from faust_backend.memory.storage import MemoryDB, ensure_root_node

    db = MemoryDB(tmp_path / "memory.sqlite")
    ensure_root_node(db)
    ensure_root_node(db)
    assert db.one("SELECT type, name FROM nodes WHERE id='path:/'")["type"] == "dir"
    assert db.one("SELECT count(*) AS c FROM nodes")["c"] == 1
    db.close()
