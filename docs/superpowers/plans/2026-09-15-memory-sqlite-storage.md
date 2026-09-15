# 记忆存储 SQLite 化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `GraphStore` 的持久化从 6 类全量重写 JSON 文件换成每 agent 一个 `memory/memory.sqlite`（图/元数据/分块/任务）+ 第二个 nano-vectordb 实例存实体名向量，并在首次启动时自动从旧 JSON 迁移。

**Architecture:** SQLite 是唯一持久真源；`nx.MultiDiGraph` 保留为内存拓扑视图（启动时从 SQL 构建，写入时同步维护）；nano-vectordb 继续负责向量检索（`index/chunks.vdb` 不动，新增 `index/entity.vdb` 存实体名向量）。每个领域写操作 = 一个 SQL 事务 + nx 同步，删除 `save()/flush()/_flush_async()/_dirty` 全套全量落盘机制。

**Tech Stack:** Python 3.11（`.runtime`）、stdlib `sqlite3`（SQLite 3.45.1，WAL）、`networkx`、`nano-vectordb 0.0.4.3`、`rank_bm25`、`jieba`、pytest。

**Spec:** `docs/superpowers/specs/2026-09-15-memory-sqlite-storage-design.md`

## Global Constraints

- Python 解释器只用 `.runtime/python.exe`（仓库规则 1）；命令在仓库根目录 `D:/dev/faustbot/faust` 执行。
- **不新增任何第三方依赖**：只用 stdlib `sqlite3` / `json` / `shutil` / `threading` / `calendar`；`networkx`、`nano-vectordb`、`rank_bm25`、`jieba` 已存在。`requirements.txt` 不变。
- 提交只在 `dev` 分支（仓库规则 8），每个 Task 末尾提交一次；`main` 不动。
- 每个 Task 结束时 `backend/tests` 必须全绿（仓库规则 6）。
- 不做静默降级（规则 2）：迁移校验失败、schema 版本不符、库打不开 → 直接抛错。
- 全部异步化（规则 2）：新增 I/O 不得阻塞事件循环；已有 `async` 方法保持 `async`。
- 文档/注释中不使用 ASCII Art 画图，用 Mermaid（规则 5）。
- 前端配色规则不涉及本计划（无前端改动）。
- 时间列统一 UTC epoch（`REAL`），对外输出仍为 `%Y-%m-%dT%H:%M:%SZ` 字符串。
- `nano-vectordb` 的 `chunks.vdb` 角色不变；不得把分块向量搬进 SQLite。
- 迁移后旧文件必须归档到 `memory/_legacy_json/`（可回滚），不得删除。

---

## File Structure

| 文件 | 责任 |
| --- | --- |
| `backend/faust_backend/memory/storage.py`（新增） | SQLite schema、`MemoryDB`（连接 + 可重入事务 + 点查封装）、时间转换工具、`ensure_root_node` |
| `backend/faust_backend/memory/migrate.py`（新增） | 旧 JSON → SQLite 导入、校验、`entity.vdb` 生成、旧文件归档、`*.tmp` 清理 |
| `backend/faust_backend/memory/store.py`（改造） | `GraphStore`：SQL 读写 + nx 视图同步；删除文件层 helper 与 `save/flush` 机制 |
| `backend/faust_backend/memory/tools.py`（微改 168-188 行） | 抽取批处理改用 `transaction()` |
| `backend/tests/conftest.py`（改） | fixture 去掉 `gs.flush()` |
| `backend/tests/test_memory_store.py`（改） | 删除断言文件格式的测试，其余保持 |
| `backend/tests/test_memory_storage.py`（新增） | `storage.py` 单测 |
| `backend/tests/test_memory_migrate.py`（新增） | 迁移单测（完整性 / 幂等 / 失败回滚） |
| `backend/tests/test_memory_concurrency.py`（新增） | 并发写与事务原子性 |

---

## Task 1: SQLite 存储层 `storage.py`

**Files:**
- Create: `backend/faust_backend/memory/storage.py`
- Test: `backend/tests/test_memory_storage.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `SCHEMA_VERSION: int = 1`
  - `class MigrationError(RuntimeError)`
  - `iso_to_epoch(value: str | None) -> float | None`
  - `epoch_to_iso(ts: float | None) -> str`
  - `class MemoryDB(db_path: Path)`：`.path`、`.transaction() -> ContextManager[sqlite3.Connection]`（可重入）、`.execute(sql, params=()) -> sqlite3.Cursor`、`.executemany(sql, rows) -> None`、`.one(sql, params=()) -> sqlite3.Row | None`、`.all(sql, params=()) -> list[sqlite3.Row]`、`.close() -> None`
  - `ensure_root_node(db: MemoryDB) -> None`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_memory_storage.py`:

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_memory_storage.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'faust_backend.memory.storage'`

- [ ] **Step 3: 实现 `storage.py`**

`backend/faust_backend/memory/storage.py`:

```python
"""记忆库 SQLite 存储层：schema、连接与事务封装。

约定：
- `nodes.parent_id` 是两个 path 节点之间 has_child 关系的唯一真源；
- 时间列一律 UTC epoch（REAL），对外输出用 `epoch_to_iso` 转回 ISO 字符串；
- 所有读写经 `MemoryDB`（单连接 + threading.RLock 串行化），事务可重入。
"""

from __future__ import annotations

import calendar
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from faust_backend.logger import get_logger

log = get_logger("faust.memory.storage")

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS nodes(
  id            TEXT PRIMARY KEY,
  type          TEXT NOT NULL,
  name          TEXT NOT NULL DEFAULT '',
  description   TEXT NOT NULL DEFAULT '',
  entity_type   TEXT,
  content_type  TEXT,
  parent_id     TEXT REFERENCES nodes(id) ON UPDATE CASCADE ON DELETE SET NULL,
  path          TEXT,
  declared_by   TEXT,
  updated_at    REAL,
  created_at    REAL,
  score_patch   REAL NOT NULL DEFAULT 0.0,
  score_patch_updated_at REAL,
  managed_by    TEXT,
  chunk_count   INTEGER NOT NULL DEFAULT 0,
  indexed       INTEGER NOT NULL DEFAULT 0,
  data          TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS nodes_parent ON nodes(parent_id, type);
CREATE UNIQUE INDEX IF NOT EXISTS nodes_path ON nodes(path) WHERE path IS NOT NULL;
CREATE INDEX IF NOT EXISTS nodes_type_updated ON nodes(type, updated_at DESC);

CREATE TABLE IF NOT EXISTS edges(
  src  TEXT NOT NULL REFERENCES nodes(id) ON UPDATE CASCADE ON DELETE CASCADE,
  dst  TEXT NOT NULL REFERENCES nodes(id) ON UPDATE CASCADE ON DELETE CASCADE,
  type TEXT NOT NULL,
  key  TEXT NOT NULL,
  PRIMARY KEY(src, dst, type, key)
);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src, type);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, type);

CREATE TABLE IF NOT EXISTS tags(
  node_id TEXT NOT NULL REFERENCES nodes(id) ON UPDATE CASCADE ON DELETE CASCADE,
  tag     TEXT NOT NULL,
  PRIMARY KEY(node_id, tag)
);
CREATE INDEX IF NOT EXISTS tags_tag ON tags(tag);

CREATE TABLE IF NOT EXISTS chunks(
  chunk_id     TEXT PRIMARY KEY,
  node_id      TEXT NOT NULL REFERENCES nodes(id) ON UPDATE CASCADE ON DELETE CASCADE,
  chunk_index  INTEGER NOT NULL,
  text         TEXT NOT NULL,
  text_preview TEXT NOT NULL DEFAULT '',
  scope_prefix TEXT NOT NULL DEFAULT '/',
  updated_at   REAL
);
CREATE INDEX IF NOT EXISTS chunks_node ON chunks(node_id);
CREATE INDEX IF NOT EXISTS chunks_scope ON chunks(scope_prefix);

CREATE TABLE IF NOT EXISTS tasks(
  task_id    TEXT PRIMARY KEY,
  type       TEXT NOT NULL,
  status     TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',
  created_at REAL,
  updated_at REAL,
  error      TEXT NOT NULL DEFAULT ''
);
"""


class MigrationError(RuntimeError):
    """迁移失败：数据损坏或校验不一致（不允许静默降级）。"""


def iso_to_epoch(value: str | None) -> float | None:
    """ISO8601 UTC 串 -> epoch；空值/非法值返回 None。"""
    if not value:
        return None
    try:
        return float(calendar.timegm(time.strptime(str(value), "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, TypeError):
        return None


def epoch_to_iso(ts: float | None) -> str:
    """epoch -> ISO8601 UTC 串；空值返回空串。"""
    if not ts:
        return ""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(ts)))


class MemoryDB:
    """单连接 SQLite 封装：RLock 串行化 + 可重入事务。"""

    def __init__(self, db_path: Path | str):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None  # 手动管理事务
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    # ── schema ──

    def _ensure_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            row = self._conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
            if row is None:
                self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
            elif int(row["version"]) != SCHEMA_VERSION:
                raise RuntimeError(
                    f"memory schema 版本不支持: 库内={row['version']} 代码={SCHEMA_VERSION}"
                )

    # ── 事务 ──

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """可重入事务：外层开启 BEGIN IMMEDIATE，内层加入同一事务。"""
        with self._lock:
            self._depth += 1
            try:
                if self._depth == 1:
                    self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                if self._depth == 1:
                    self._conn.execute("COMMIT")
            except BaseException:
                if self._depth == 1:
                    self._conn.execute("ROLLBACK")
                raise
            finally:
                self._depth -= 1

    # ── 点查 ──

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]]) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def all(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def ensure_root_node(db: MemoryDB) -> None:
    """保证 `path:/` 根节点存在（空库首次启动时使用）。"""
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO nodes(id, type, name, path) VALUES ('path:/', 'dir', '/', '/') "
            "ON CONFLICT(id) DO NOTHING"
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_memory_storage.py -q`
Expected: PASS（6 passed）

- [ ] **Step 5: 提交**

```bash
git add backend/faust_backend/memory/storage.py backend/tests/test_memory_storage.py
git commit -m "feat(memory): 新增 SQLite 存储层 storage.py（schema/事务/时间转换）"
```

---

## Task 2: 迁移模块 `migrate.py`

**Files:**
- Create: `backend/faust_backend/memory/migrate.py`
- Test: `backend/tests/test_memory_migrate.py`

**Interfaces:**
- Consumes: Task 1 的 `MemoryDB`、`MigrationError`、`iso_to_epoch`、`ensure_root_node`
- Produces:
  - `LEGACY_ARCHIVE_DIRNAME = "_legacy_json"`
  - `@dataclass MigrationReport(migrated: bool, counts: dict[str, int], archived_to: str | None)`
  - `needs_migration(store_dir: Path, db: MemoryDB) -> bool`
  - `migrate_if_needed(store_dir: Path, db: MemoryDB, *, embed_dim: int) -> MigrationReport`
  - `migrate_from_json(store_dir: Path, db: MemoryDB, *, embed_dim: int) -> MigrationReport`（强制迁移，测试用）

旧文件布局（迁移输入）：

| 文件 | 结构 |
| --- | --- |
| `memory/graph.json` | `{"nodes": {nid: {attrs...}}, "edges": [{"source","target","key","type"}]}`；旧数据可能内嵌 `_name_vec` |
| `memory/index/entity_vecs.jsonl` | 每行 `{"id": eid, "v": [float...]}` |
| `memory/meta/**/*.meta.json` | `{"path","declared_by","description","updated_at"(ISO),"chunk_count","indexed","tags":[],"score_patch","content_type","managed_by","score_patch_updated_at"}` |
| `memory/meta/**/*.chunks.json` | `[{chunk_id,node_path,chunk_index,text,text_preview,scope_prefix,updated_at,indexed}]` |
| `memory/meta/chunks_index.json` | `{chunk_id: <同上的 item>}`（并集，作为主来源） |
| `memory/meta/tasks.json` | `[{task_id,type,status,payload,created_at(ISO),updated_at(ISO),error}]` |

- [ ] **Step 1: 写失败测试**

`backend/tests/test_memory_migrate.py`:

```python
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
```

（`1789002123.0` = `2026-09-10T01:02:03Z`，`1788220800.0` = `2026-09-01T00:00:00Z`，由 `calendar.timegm` 换算得出。）

- [ ] **Step 2: 运行测试确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_memory_migrate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'faust_backend.memory.migrate'`

- [ ] **Step 3: 实现 `migrate.py`**

`backend/faust_backend/memory/migrate.py`:

```python
"""旧 JSON 文件布局 -> memory.sqlite 的一次性迁移。

不变量：校验不通过则完全不改变磁盘状态（不归档、不删文件），并抛 MigrationError。
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from nano_vectordb import NanoVectorDB

from faust_backend.logger import get_logger
from faust_backend.memory.storage import (
    MemoryDB,
    MigrationError,
    ensure_root_node,
    epoch_to_iso,
    iso_to_epoch,
)

log = get_logger("faust.memory.migrate")

LEGACY_ARCHIVE_DIRNAME = "_legacy_json"
TREE_EDGE = "has_child"
ENTITY_VDB_NAME = "entity.vdb"

# 迁移后需要归档的旧文件/目录（相对 store_dir）
_ARCHIVE_FILES = ("graph.json",)
_ARCHIVE_DIRS = ("meta",)


@dataclass
class MigrationReport:
    migrated: bool
    counts: dict[str, int] = field(default_factory=dict)
    archived_to: str | None = None


# ── legacy 读取 ──


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _path_id(path: str) -> str:
    p = str(path or "").replace("\\", "/").strip().strip("/")
    return f"path:/{p}" if p else "path:/"


def _id_to_path(nid: str) -> str:
    return "/" + nid[len("path:"):].strip("/") if nid.startswith("path:") else nid


def _node_columns(attrs: dict, nid: str) -> dict:
    """把 graph.json 的节点属性拆成 SQL 列 + data JSON。"""
    known = {
        "type", "name", "description", "entity_type", "content_type", "declared_by",
        "updated_at", "created_at", "score_patch", "score_patch_updated_at",
        "managed_by", "chunk_count", "indexed", "path",
    }
    extra = {k: v for k, v in attrs.items() if k not in known and not k.startswith("_")}
    return {
        "id": nid,
        "type": str(attrs.get("type") or "file"),
        "name": str(attrs.get("name") or ""),
        "description": str(attrs.get("description") or ""),
        "entity_type": attrs.get("entity_type"),
        "content_type": attrs.get("content_type"),
        "parent_id": None,
        "path": _id_to_path(nid) if nid.startswith("path:") else None,
        "declared_by": attrs.get("declared_by"),
        "updated_at": iso_to_epoch(attrs.get("updated_at")),
        "created_at": iso_to_epoch(attrs.get("created_at")),
        "score_patch": float(attrs.get("score_patch") or 0.0),
        "score_patch_updated_at": iso_to_epoch(attrs.get("score_patch_updated_at")),
        "managed_by": attrs.get("managed_by"),
        "chunk_count": int(attrs.get("chunk_count") or 0),
        "indexed": 1 if attrs.get("indexed") else 0,
        "data": json.dumps(extra, ensure_ascii=False),
    }


_NODE_INSERT = (
    "INSERT OR REPLACE INTO nodes(id, type, name, description, entity_type, content_type,"
    " parent_id, path, declared_by, updated_at, created_at, score_patch, score_patch_updated_at,"
    " managed_by, chunk_count, indexed, data)"
    " VALUES (:id, :type, :name, :description, :entity_type, :content_type, :parent_id, :path,"
    " :declared_by, :updated_at, :created_at, :score_patch, :score_patch_updated_at,"
    " :managed_by, :chunk_count, :indexed, :data)"
)


def _iter_meta(meta_dir: Path) -> Iterable[tuple[str, dict]]:
    if not meta_dir.exists():
        return []
    out = []
    for mp in sorted(meta_dir.rglob("*.meta.json")):
        meta = _read_json(mp, {})
        path = str(meta.get("path") or "").strip()
        if path:
            out.append((path, meta))
    return out


def _load_legacy_chunks(meta_dir: Path) -> dict[str, dict]:
    """以 chunks_index.json 为主，per-doc *.chunks.json 补齐缺失项。"""
    items: dict[str, dict] = {}
    for _, item in (_read_json(meta_dir / "chunks_index.json", {}) or {}).items():
        cid = str((item or {}).get("chunk_id") or "")
        if cid:
            items[cid] = item
    for cf in sorted(meta_dir.rglob("*.chunks.json")):
        for item in _read_json(cf, []) or []:
            cid = str((item or {}).get("chunk_id") or "")
            if cid and cid not in items:
                items[cid] = item
    return items


def _load_legacy_entity_vecs(store_dir: Path, nodes: dict[str, dict]) -> dict[str, list[float]]:
    """侧车 jsonl 优先；旧格式内嵌 graph.json 的 `_name_vec` 作为补充。"""
    vecs: dict[str, list[float]] = {}
    sidecar = store_dir / "index" / "entity_vecs.jsonl"
    if sidecar.exists():
        for line in sidecar.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            eid = str(rec.get("id") or "")
            vec = rec.get("v") or rec.get("vector")
            if eid and vec:
                vecs[eid] = [float(x) for x in vec]
    for nid, nd in nodes.items():
        if nd.get("_name_vec") and nid not in vecs:
            vecs[nid] = [float(x) for x in nd["_name_vec"]]
    return vecs


# ── 迁移入口 ──


def needs_migration(store_dir: Path, db: MemoryDB) -> bool:
    """有旧 graph.json 且库内还没有节点 => 需要迁移。"""
    if not (store_dir / "graph.json").exists():
        return False
    return int(db.one("SELECT count(*) AS c FROM nodes")["c"]) == 0


def migrate_if_needed(store_dir: Path, db: MemoryDB, *, embed_dim: int) -> MigrationReport:
    if needs_migration(store_dir, db):
        return migrate_from_json(store_dir, db, embed_dim=embed_dim)
    ensure_root_node(db)
    return MigrationReport(migrated=False, counts={}, archived_to=None)


def migrate_from_json(store_dir: Path, db: MemoryDB, *, embed_dim: int) -> MigrationReport:
    store_dir = Path(store_dir)
    try:
        graph = _read_json(store_dir / "graph.json", None)
    except json.JSONDecodeError as exc:
        raise MigrationError(f"graph.json 解析失败，迁移中止: {exc}") from exc
    if not graph:
        raise MigrationError(f"graph.json 缺失或为空: {store_dir / 'graph.json'}")

    nodes: dict[str, dict] = graph.get("nodes", {})
    edges: list[dict] = graph.get("edges", [])
    meta_items = _iter_meta(store_dir / "meta")
    chunk_items = _load_legacy_chunks(store_dir / "meta")
    tasks = _read_json(store_dir / "meta" / "tasks.json", []) or []
    entity_vecs = _load_legacy_entity_vecs(store_dir, nodes)

    counts = {
        "nodes": len(nodes),
        "edges": 0,
        "parent_edges": 0,
        "tags": 0,
        "chunks": 0,
        "tasks": len(tasks),
        "entity_vecs": len(entity_vecs),
    }

    # 实体向量先落盘：只依赖旧文件，失败则迁移整体中止且 SQL 未动
    _write_entity_vdb(store_dir, entity_vecs, embed_dim=embed_dim)

    with db.transaction() as conn:
        for nid, attrs in nodes.items():
            conn.execute(_NODE_INSERT, _node_columns(dict(attrs or {}), nid))

        for e in edges:
            src, dst = str(e.get("source") or ""), str(e.get("target") or "")
            etype = str(e.get("type") or "relates_to")
            if not src or not dst:
                continue
            if (
                etype == TREE_EDGE
                and src.startswith("path:")
                and dst.startswith("path:")
            ):
                conn.execute("UPDATE nodes SET parent_id=? WHERE id=?", (src, dst))
                counts["parent_edges"] += 1
                continue
            conn.execute(
                "INSERT OR REPLACE INTO edges(src, dst, type, key) VALUES (?, ?, ?, ?)",
                (src, dst, etype, str(e.get("key") or "")),
            )
            counts["edges"] += 1

        for path, meta in meta_items:
            nid = _path_id(path)
            tags = [str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()]
            conn.execute(
                "UPDATE nodes SET declared_by=COALESCE(?, declared_by), description=?,"
                " updated_at=COALESCE(?, updated_at), chunk_count=?, indexed=?,"
                " score_patch=?, score_patch_updated_at=COALESCE(?, score_patch_updated_at),"
                " managed_by=COALESCE(?, managed_by), content_type=COALESCE(?, content_type)"
                " WHERE id=?",
                (
                    meta.get("declared_by"),
                    str(meta.get("description") or ""),
                    iso_to_epoch(meta.get("updated_at")),
                    int(meta.get("chunk_count") or 0),
                    1 if meta.get("indexed") else 0,
                    float(meta.get("score_patch") or 0.0),
                    iso_to_epoch(meta.get("score_patch_updated_at")),
                    meta.get("managed_by"),
                    meta.get("content_type"),
                    nid,
                ),
            )
            for tag in tags:
                conn.execute("INSERT OR IGNORE INTO tags(node_id, tag) VALUES (?, ?)", (nid, tag))
            counts["tags"] += len(tags)

        for cid, item in chunk_items.items():
            node_id = _path_id(str(item.get("node_path") or ""))
            if conn.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone() is None:
                log.warning("migrate skip chunk without node: %s -> %s", cid, node_id)
                continue
            conn.execute(
                "INSERT OR REPLACE INTO chunks(chunk_id, node_id, chunk_index, text, text_preview,"
                " scope_prefix, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    cid,
                    node_id,
                    int(item.get("chunk_index") or 0),
                    str(item.get("text") or ""),
                    str(item.get("text_preview") or ""),
                    str(item.get("scope_prefix") or "/"),
                    iso_to_epoch(item.get("updated_at")),
                ),
            )
            counts["chunks"] += 1

        for t in tasks:
            conn.execute(
                "INSERT OR REPLACE INTO tasks(task_id, type, status, payload, created_at,"
                " updated_at, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(t.get("task_id") or ""),
                    str(t.get("type") or ""),
                    str(t.get("status") or "pending"),
                    json.dumps(t.get("payload") or {}, ensure_ascii=False),
                    iso_to_epoch(t.get("created_at")),
                    iso_to_epoch(t.get("updated_at")),
                    str(t.get("error") or ""),
                ),
            )

        # 校验在同一事务内完成：不一致则整体回滚，不留半成品
        _verify(db, counts)

    archived = _archive_legacy(store_dir)
    _cleanup_tmp(store_dir)
    ensure_root_node(db)

    log.info("migration done counts=%s archived=%s", counts, archived)
    return MigrationReport(migrated=True, counts=counts, archived_to=str(archived))


# ── 实体向量 ──


def _write_entity_vdb(store_dir: Path, vecs: dict[str, list[float]], *, embed_dim: int) -> None:
    if not vecs:
        return
    vdb_path = store_dir / "index" / ENTITY_VDB_NAME
    if vdb_path.exists():
        vdb_path.unlink()
    vdb = NanoVectorDB(embed_dim, storage_file=str(vdb_path))
    rows = []
    for eid, vec in vecs.items():
        arr = np.asarray(vec, dtype=np.float32)
        if arr.shape[0] != embed_dim:
            raise MigrationError(f"实体向量维度不符: {eid} 期望 {embed_dim} 实得 {arr.shape[0]}")
        rows.append({"__id__": eid, "__vector__": arr})
    vdb.upsert(rows)
    vdb.save()


# ── 校验 / 归档 / 清理 ──


def _verify(db: MemoryDB, counts: dict[str, int]) -> None:
    """逐项校验（在导入事务内调用：不一致即抛错，由外层事务整体回滚）。"""
    expect_nodes = int(db.one("SELECT count(*) AS c FROM nodes")["c"])
    if expect_nodes != counts["nodes"]:
        raise MigrationError(f"节点数不一致: 源 {counts['nodes']} 库 {expect_nodes}")
    expect_edges = int(db.one("SELECT count(*) AS c FROM edges")["c"])
    if expect_edges != counts["edges"]:
        raise MigrationError(f"边数不一致: 源 {counts['edges']} 库 {expect_edges}")
    expect_chunks = int(db.one("SELECT count(*) AS c FROM chunks")["c"])
    if expect_chunks != counts["chunks"]:
        raise MigrationError(f"分块数不一致: 源 {counts['chunks']} 库 {expect_chunks}")
    expect_tasks = int(db.one("SELECT count(*) AS c FROM tasks")["c"])
    if expect_tasks != counts["tasks"]:
        raise MigrationError(f"任务数不一致: 源 {counts['tasks']} 库 {expect_tasks}")

    for row in db.all("SELECT id, path, parent_id FROM nodes WHERE path IS NOT NULL AND id <> 'path:/'"):
        parent_path = str(Path(row["path"]).parent).replace("\\", "/")
        expected = _path_id(parent_path)
        if row["parent_id"] != expected:
            raise MigrationError(
                f"父节点推导不一致: {row['id']} parent_id={row['parent_id']} 期望={expected}"
            )

    for row in db.all("SELECT chunk_id, node_id, text FROM chunks ORDER BY chunk_id LIMIT 5"):
        if not row["text"]:
            raise MigrationError(f"分块正文为空: {row['chunk_id']}")


def _archive_legacy(store_dir: Path) -> Path:
    archive = store_dir / LEGACY_ARCHIVE_DIRNAME
    archive.mkdir(parents=True, exist_ok=True)
    for name in _ARCHIVE_FILES:
        src = store_dir / name
        if src.exists():
            shutil.move(str(src), str(archive / name))
    sidecar = store_dir / "index" / "entity_vecs.jsonl"
    if sidecar.exists():
        (archive / "index").mkdir(parents=True, exist_ok=True)
        shutil.move(str(sidecar), str(archive / "index" / sidecar.name))
    for name in _ARCHIVE_DIRS:
        src = store_dir / name
        if src.exists():
            dst = archive / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.move(str(src), str(dst))
    return archive


def _cleanup_tmp(store_dir: Path) -> None:
    index_dir = store_dir / "index"
    if not index_dir.exists():
        return
    for tmp in index_dir.glob("*.tmp"):
        tmp.unlink(missing_ok=True)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_memory_migrate.py -q`
Expected: PASS（3 passed）。若 `updated_at` 断言数值不符，用 Step 1 的 `iso_to_epoch` 命令换算后修正测试中的常量。

- [ ] **Step 5: 提交**

```bash
git add backend/faust_backend/memory/migrate.py backend/tests/test_memory_migrate.py
git commit -m "feat(memory): 新增 JSON->SQLite 迁移模块（校验+归档+entity.vdb）"
```

---

## Task 3: `GraphStore` 图/元数据/分块/任务切 SQL

> 本 Task 是原子切换：迁移会把 `graph.json` / `meta/**` 归档，因此**所有**读取路径必须在同一次提交内切完，否则会读到已归档文件。

**Files:**
- Modify: `backend/faust_backend/memory/store.py`（`__init__`、持久化段、图原语、`file_write`、`attachment_write`、`file_delete`、`file_delete_tree`、`file_rename`、`file_copy`、`file_move`、`mkdir`、`set_tags`、`set_score_patch`、任务方法、`_add_record_entity`、`_get_meta`）
- Modify: `backend/faust_backend/memory/tools.py:168-188`
- Modify: `backend/tests/conftest.py:41`
- Modify: `backend/tests/test_memory_store.py`（删除文件格式断言与 `flush/save` 调用）
- Test: `backend/tests/test_memory_store.py`（保留的领域测试即回归网）

**Interfaces:**
- Consumes: Task 1 `MemoryDB` / `ensure_root_node` / `iso_to_epoch` / `epoch_to_iso`；Task 2 `migrate_if_needed`
- Produces（后续 Task 依赖）：
  - `GraphStore.db: MemoryDB`
  - `GraphStore._get_meta(norm_path: str) -> dict`（键：`path/declared_by/description/updated_at/content_type/tags/score_patch/chunk_count/indexed/managed_by/score_patch_updated_at`）
  - `GraphStore._load_from_db() -> None`、`GraphStore._verify_tree() -> None`
  - `GraphStore._link_parent(child_id: str, parent_id: str) -> None`、`GraphStore._reset_nx_parent_edge(child_id: str, parent_id: str) -> None`、`GraphStore._parent_of(nid: str) -> str | None`
  - `GraphStore._subtree_paths(path: str) -> list[str]`（浅→深，含自身）
  - `GraphStore._replace_chunks(norm_path: str, items: list[dict]) -> None`、`GraphStore._delete_chunks(norm_path: str) -> list[str]`
  - 删除：`save` / `flush` / `_flush_async` / `_dirty` / `_save_lock` / `_read_meta` / `_write_meta` / `_meta_path` / `_chunks_file` / `_load_chunks_index` / `_save_chunks_index` / `_load_tasks` / `_save_tasks` / `_repair_tree` / `_add_node` 的纯内存实现

- [ ] **Step 1: 改造模块级 helper 与 `__init__`**

删除 `_atomic_write_json`、`_read_json`（迁移模块已自带读取；`store.py` 内不再有 JSON 文件读写），新增 `_attr_epoch`：

```python
def _attr_epoch(value: Any) -> float | None:
    """节点属性里的时间 -> epoch：数值直通，ISO 串转换，空值 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return iso_to_epoch(str(value))


def _now_epoch() -> float:
    return time.time()
```

顶部 import 改为（删除 `aiofiles` / `aiofiles.os`——全文件已无引用；`shutil` 保留用于内容目录；新增 storage/migrate）：

```python
from faust_backend.memory.migrate import migrate_if_needed
from faust_backend.memory.storage import (
    MemoryDB, ensure_root_node, epoch_to_iso, iso_to_epoch,
)
```

`__init__` 替换为：

```python
    def __init__(self, agent_name: str | None = None):
        self.agent_name = str(agent_name or conf.AGENT_NAME)
        self.agent_root = Path(conf.CONFIG_ROOT) / "agents" / self.agent_name
        self.store_dir = self.agent_root / _NODE_PATH
        self.content_dir = self.store_dir / "content"
        self.index_dir = self.store_dir / "index"
        self.db_file = self.store_dir / "memory.sqlite"
        self.index_file = self.index_dir / "chunks.vdb"
        self.entity_index_file = self.index_dir / "entity.vdb"
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._vdb: NanoVectorDB | None = None
        self._entity_vdb: NanoVectorDB | None = None
        self._vdb_dirty: bool = False
        self._openai_client: AsyncOpenAI | None = None
        self._embed_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._bm25_dirty: bool = True
        self._bm25_index: BM25Okapi | None = None
        self._bm25_corpus: list[list[str]] | None = None
        self._bm25_docs: list[dict] = []
        self._extraction_status: dict = {
            "pending": 0, "running": 0, "last_running": None,
            "last_success": None, "last_error": None,
        }
        self._ensure_dirs()
        self.db = MemoryDB(self.db_file)
        report = migrate_if_needed(self.store_dir, self.db, embed_dim=EMBED_DIM)
        if report.migrated:
            log.info("memory migrated counts=%s archived=%s", report.counts, report.archived_to)
        ensure_root_node(self.db)
        self._load_from_db()
        self._ensure_vdb()
        self._ensure_entity_vdb()

    def refresh(self, agent_name: str | None = None) -> None:
        target = str(agent_name or conf.AGENT_NAME)
        if target == self.agent_name:
            return
        try:
            self.db.close()
        except Exception:
            log.warning("close previous memory db failed", exc_info=True)
        self.__init__(target)

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            log.warning("close memory db failed", exc_info=True)

    def _ensure_dirs(self) -> None:
        self.content_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)

    def _load_from_db(self) -> None:
        """SQL -> nx 内存视图（含由 parent_id 派生的 has_child 边）。"""
        self._graph.clear()
        for r in self.db.all(
            "SELECT id, type, name, description, entity_type, content_type, declared_by,"
            " updated_at, created_at, score_patch, score_patch_updated_at, managed_by,"
            " chunk_count, indexed, data FROM nodes"
        ):
            attrs = json.loads(r["data"] or "{}")
            for key in ("type", "name", "description", "entity_type", "content_type",
                        "declared_by", "managed_by"):
                if r[key] is not None:
                    attrs[key] = r[key]
            if r["updated_at"] is not None:
                attrs["updated_at"] = epoch_to_iso(r["updated_at"])
            if r["created_at"] is not None:
                attrs["created_at"] = epoch_to_iso(r["created_at"])
            attrs["score_patch"] = float(r["score_patch"] or 0.0)
            attrs["chunk_count"] = int(r["chunk_count"] or 0)
            attrs["indexed"] = bool(r["indexed"])
            self._graph.add_node(r["id"], **attrs)
        for r in self.db.all("SELECT id, parent_id FROM nodes WHERE parent_id IS NOT NULL"):
            self._graph.add_edge(r["parent_id"], r["id"], key="parent", type=TREE_EDGE)
        for r in self.db.all("SELECT src, dst, type, key FROM edges"):
            self._graph.add_edge(r["src"], r["dst"], key=r["key"], type=r["type"])
        self._verify_tree()
```

- [ ] **Step 2: 图原语与树维护**

替换 `save` / `_mark_bm25_*` / `flush` / `_flush_async` / `_add_node` / `_add_edge` / `_remove_edge` / `_set_node_attr` 段（删除 `save`/`flush`/`_flush_async`，保留 `_mark_bm25_dirty/_mark_bm25_clean`）：

```python
    def _mark_bm25_dirty(self) -> None:
        self._bm25_dirty = True

    def _mark_bm25_clean(self) -> None:
        self._bm25_dirty = False

    # ── node/edge 原语（SQL + nx 同步） ──

    _NODE_COLUMNS = (
        "type", "name", "description", "entity_type", "content_type", "declared_by",
        "updated_at", "created_at", "score_patch", "score_patch_updated_at",
        "managed_by", "chunk_count", "indexed",
    )

    def _node_row(self, nid: str, attrs: dict) -> dict:
        extra = {k: v for k, v in attrs.items()
                 if k not in self._NODE_COLUMNS
                 and k not in ("path", "parent_id", "tags")
                 and not k.startswith("_")}
        return {
            "id": nid,
            "type": str(attrs.get("type") or "file"),
            "name": str(attrs.get("name") or ""),
            "description": str(attrs.get("description") or ""),
            "entity_type": attrs.get("entity_type"),
            "content_type": attrs.get("content_type"),
            "parent_id": None,
            "path": _id_to_path(nid) if _is_path_id(nid) else None,
            "declared_by": attrs.get("declared_by"),
            "updated_at": _attr_epoch(attrs.get("updated_at")),
            "created_at": _attr_epoch(attrs.get("created_at")),
            "score_patch": float(attrs.get("score_patch") or 0.0),
            "score_patch_updated_at": _attr_epoch(attrs.get("score_patch_updated_at")),
            "managed_by": attrs.get("managed_by"),
            "chunk_count": int(attrs.get("chunk_count") or 0),
            "indexed": 1 if attrs.get("indexed") else 0,
            "data": json.dumps(extra, ensure_ascii=False),
        }

    _NODE_UPSERT = (
        "INSERT INTO nodes(id, type, name, description, entity_type, content_type, parent_id,"
        " path, declared_by, updated_at, created_at, score_patch, score_patch_updated_at,"
        " managed_by, chunk_count, indexed, data)"
        " VALUES (:id, :type, :name, :description, :entity_type, :content_type, :parent_id,"
        " :path, :declared_by, :updated_at, :created_at, :score_patch, :score_patch_updated_at,"
        " :managed_by, :chunk_count, :indexed, :data)"
        " ON CONFLICT(id) DO NOTHING"
    )

    def _add_node(self, nid: str, **attrs) -> None:
        if self._graph.has_node(nid):
            return
        self._graph.add_node(nid, **attrs)
        with self.db.transaction() as conn:
            conn.execute(self._NODE_UPSERT, self._node_row(nid, dict(attrs)))

    def _db_delete_node(self, nid: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM nodes WHERE id=?", (nid,))
        if self._graph.has_node(nid):
            self._graph.remove_node(nid)

    def _set_node_attr(self, nid: str, **kwargs) -> None:
        if not self._graph.has_node(nid):
            return
        self._graph.nodes[nid].update(kwargs)
        cols = {k: v for k, v in kwargs.items() if k in self._NODE_COLUMNS}
        # tags 属 tags 表，实体/记录自由属性进 data JSON；其余键仅存在于 nx
        extra = {k: v for k, v in kwargs.items()
                 if k not in self._NODE_COLUMNS and k != "tags" and not k.startswith("_")}
        with self.db.transaction() as conn:
            if cols:
                assignments, params = [], []
                for key, value in cols.items():
                    assignments.append(f"{key}=?")
                    params.append(_attr_epoch(value) if key in (
                        "updated_at", "created_at", "score_patch_updated_at"
                    ) else (1 if key == "indexed" else value))
                params.append(nid)
                conn.execute(f"UPDATE nodes SET {', '.join(assignments)} WHERE id=?", params)
            if extra:
                row = conn.execute("SELECT data FROM nodes WHERE id=?", (nid,)).fetchone()
                data = json.loads(row["data"] or "{}") if row else {}
                data.update(extra)
                conn.execute("UPDATE nodes SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), nid))

    def _link_parent(self, child_id: str, parent_id: str) -> None:
        """建立路径树父子关系（唯一真源 = nodes.parent_id），并修正 nx 中的父边。"""
        with self.db.transaction() as conn:
            conn.execute("UPDATE nodes SET parent_id=? WHERE id=?", (parent_id, child_id))
        self._reset_nx_parent_edge(child_id, parent_id)

    def _reset_nx_parent_edge(self, child_id: str, parent_id: str) -> None:
        """nx 视图里只保留一条指向 parent_id 的 has_child 边。"""
        if not self._graph.has_node(child_id):
            return
        for src, _, key, edata in list(self._graph.in_edges(child_id, data=True, keys=True)):
            if edata and edata.get("type") == TREE_EDGE and src != parent_id:
                self._graph.remove_edge(src, child_id, key)
        if self._graph.has_node(parent_id) and not self._graph.has_edge(parent_id, child_id):
            self._graph.add_edge(parent_id, child_id, key="parent", type=TREE_EDGE)

    def _add_edge(self, src: str, tgt: str, etype: str = "relates_to") -> str:
        key = str(uuid.uuid4().hex)
        self._graph.add_edge(src, tgt, key=key, type=etype)
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO edges(src, dst, type, key) VALUES (?, ?, ?, ?)",
                (src, tgt, etype, key),
            )
        return key

    def _remove_edge(self, src: str, tgt: str) -> None:
        if self._graph.has_node(src) and self._graph.has_node(tgt) and self._graph.has_edge(src, tgt):
            for key in list(self._graph[src][tgt].keys()):
                self._graph.remove_edge(src, tgt, key)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM edges WHERE src=? AND dst=?", (src, tgt))
            conn.execute("UPDATE nodes SET parent_id=NULL WHERE id=? AND parent_id=?", (tgt, src))

    def _ensure_ancestors(self, normalized_path: str) -> str:
        """确保所有中间目录存在并连好父子边；返回直接父节点 id（与原实现同语义）。"""
        parts = [p for p in Path(normalized_path).parts if p not in ("/", "\\")]
        parent_id = "path:/"
        for i, part in enumerate(parts[:-1]):
            nid = _path_id("/" + "/".join(parts[: i + 1]))
            if not self._has_node(nid):
                self._add_node(nid, type="dir", name=part)
            if self._parent_of(nid) != parent_id:
                self._link_parent(nid, parent_id)
            parent_id = nid
        return parent_id

    def _parent_of(self, nid: str) -> str | None:
        row = self.db.one("SELECT parent_id FROM nodes WHERE id=?", (nid,))
        return row["parent_id"] if row else None

    def _verify_tree(self) -> None:
        """启动时修树：path 派生父缺失则补齐（替代原 _repair_tree）。"""
        for r in self.db.all("SELECT id, path, parent_id FROM nodes WHERE path IS NOT NULL AND id <> 'path:/'"):
            expected = _path_id(str(Path(r["path"]).parent).replace("\\", "/"))
            if r["parent_id"] == expected and self._graph.has_node(expected):
                continue
            if not self._graph.has_node(expected):
                self._add_node(expected, type="dir", name=Path(_id_to_path(expected)).name or "/")
            self._link_parent(r["id"], expected)

    def _subtree_paths(self, norm_path: str) -> list[str]:
        """返回该路径自身及所有后代路径（浅 -> 深）。"""
        prefix = norm_path.rstrip("/") + "/"
        rows = self.db.all(
            "SELECT path FROM nodes WHERE path = ? OR path LIKE ? ORDER BY length(path), path",
            (norm_path, prefix + "%"),
        )
        return [r["path"] for r in rows]
```

- [ ] **Step 3: 运行领域测试（应仍失败在文件层）**

Run: `.runtime/python.exe -m pytest backend/tests/test_memory_store.py -q -x`
Expected: FAIL（`AttributeError: 'GraphStore' object has no attribute '_read_meta'` 等）—— 继续下一步补齐调用点。

- [ ] **Step 4: 元数据 / 分块 / 任务读写切 SQL**

用下面代码替换 `_read_meta` / `_write_meta` / `_meta_path` / `_chunks_file` / `_load_chunks_index` / `_save_chunks_index` / `_load_tasks` / `_save_tasks` 整段：

```python
    # ── meta（SQL） ──

    def _get_meta(self, norm_path: str) -> dict:
        """返回与旧 meta.json 同键的字典（对外时间仍是 ISO 串）。"""
        nid = _path_id(norm_path)
        row = self.db.one(
            "SELECT declared_by, description, updated_at, chunk_count, indexed, score_patch,"
            " score_patch_updated_at, managed_by, content_type FROM nodes WHERE id=?", (nid,)
        )
        tags = [r["tag"] for r in self.db.all("SELECT tag FROM tags WHERE node_id=? ORDER BY tag", (nid,))]
        if row is None:
            return {
                "path": norm_path, "declared_by": "", "description": "", "updated_at": "",
                "chunk_count": 0, "indexed": False, "tags": tags, "score_patch": 0.0,
            }
        return {
            "path": norm_path,
            "declared_by": str(row["declared_by"] or ""),
            "description": str(row["description"] or ""),
            "updated_at": epoch_to_iso(row["updated_at"]),
            "chunk_count": int(row["chunk_count"] or 0),
            "indexed": bool(row["indexed"]),
            "tags": tags,
            "score_patch": float(row["score_patch"] or 0.0),
            "score_patch_updated_at": epoch_to_iso(row["score_patch_updated_at"]),
            "managed_by": str(row["managed_by"] or ""),
            "content_type": str(row["content_type"] or ""),
        }

    # ── chunks（SQL） ──

    def _replace_chunks(self, norm_path: str, items: list[dict]) -> None:
        """整篇文档的分块行替换（含 scope_prefix / preview）。"""
        nid = _path_id(norm_path)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE node_id=?", (nid,))
            conn.executemany(
                "INSERT OR REPLACE INTO chunks(chunk_id, node_id, chunk_index, text, text_preview,"
                " scope_prefix, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        str(it["chunk_id"]), nid, int(it.get("chunk_index") or 0),
                        str(it.get("text") or ""), str(it.get("text_preview") or ""),
                        str(it.get("scope_prefix") or "/"), iso_to_epoch(it.get("updated_at")),
                    )
                    for it in items
                ],
            )

    def _delete_chunks(self, norm_path: str) -> list[str]:
        nid = _path_id(norm_path)
        ids = [r["chunk_id"] for r in self.db.all("SELECT chunk_id FROM chunks WHERE node_id=?", (nid,))]
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM chunks WHERE node_id=?", (nid,))
        return ids

    # ── tasks（SQL） ──

    def _task_row(self, row) -> dict:
        return {
            "task_id": row["task_id"], "type": row["type"], "status": row["status"],
            "payload": json.loads(row["payload"] or "{}"),
            "created_at": epoch_to_iso(row["created_at"]),
            "updated_at": epoch_to_iso(row["updated_at"]),
            "error": row["error"],
        }
```

同时替换任务方法与 `get_tasks`：

```python
    def get_tasks(self) -> list[dict]:
        rows = self.db.all(
            "SELECT task_id, type, status, payload, created_at, updated_at, error"
            " FROM tasks ORDER BY created_at DESC LIMIT 200"
        )
        log.info("get_tasks count=%d", len(rows))
        return [self._task_row(r) for r in rows]

    def add_task(self, task_type: str, payload: dict | None = None) -> dict:
        now = _now_epoch()
        task = {
            "task_id": f"tsk_{uuid.uuid4().hex}", "type": task_type, "status": "pending",
            "payload": payload or {}, "created_at": epoch_to_iso(now),
            "updated_at": epoch_to_iso(now), "error": "",
        }
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO tasks(task_id, type, status, payload, created_at, updated_at, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (task["task_id"], task_type, "pending",
                 json.dumps(task["payload"], ensure_ascii=False), now, now, ""),
            )
        log.info("add_task type=%s task_id=%s", task_type, task["task_id"][:12])
        return task

    def update_task(self, task_id: str, status: str, error: str = "") -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET status=?, error=?, updated_at=? WHERE task_id=?",
                (status, error, _now_epoch(), task_id),
            )
        log.info("update_task task_id=%s status=%s error=%s", task_id[:12], status, error or "none")
```

- [ ] **Step 5: 写路径改造（`file_write` / `attachment_write` / 附件索引）**

`file_write` 的 `async with self._write_lock:` 块替换为：

```python
        async with self._write_lock:
            with self.db.transaction() as conn:
                cp = self._content_path(norm)
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text(str(content or ""), encoding="utf-8")

                if not self._has_node(nid):
                    self._add_node(nid, type="file", name=name,
                                   description=str(description or ""), score_patch=0.0)
                self._set_node_attr(nid, updated_at=_utc_iso(), declared_by=declared_by)
                self._link_parent(nid, parent_nid)

                index_text = str(content or "")
                if description:
                    index_text = f"{description}\n\n{content}"
                chunks = _chunk_text(index_text)
                existing_tags = [r["tag"] for r in conn.execute(
                    "SELECT tag FROM tags WHERE node_id=? ORDER BY tag", (nid,)).fetchall()]
                tags_final = [t.strip() for t in (tags or existing_tags) if t and t.strip()]
                conn.execute("DELETE FROM tags WHERE node_id=?", (nid,))
                conn.executemany("INSERT OR IGNORE INTO tags(node_id, tag) VALUES (?, ?)",
                                 [(nid, t) for t in tags_final])
                conn.execute(
                    "UPDATE nodes SET description=?, declared_by=?, updated_at=?, chunk_count=?,"
                    " indexed=? WHERE id=?",
                    (str(description or ""), declared_by, _now_epoch(),
                     len(chunks) if index else 0, 1 if index else 0, nid),
                )
                self._set_node_attr(nid, tags=tags_final)
                meta = self._get_meta(norm)

                if not index:
                    return {"path": norm, "meta": meta}

                old_ids = self._delete_chunks(norm)
                chunk_items = []
                for idx, chunk_text in enumerate(chunks, 1):
                    cid = f"{norm}::chunk::{idx}::{uuid.uuid4().hex[:8]}"
                    chunk_items.append({
                        "chunk_id": cid, "node_path": norm, "chunk_index": idx,
                        "text": chunk_text, "text_preview": chunk_text[:120],
                        "scope_prefix": str(Path(norm).parent.as_posix()).strip(".") or "/",
                        "updated_at": _utc_iso(), "indexed": True,
                    })
                self._replace_chunks(norm, chunk_items)
                self._mark_bm25_dirty()

            if old_ids:
                await self._delete_chunk_ids(old_ids)
            if index and chunk_items:
                await self._embed_and_index(chunk_items)
```

（`file_write` 中 `parent_nid = self._ensure_ancestors(norm)` 与其上方的 `memory_write_pre` 钩子保持原样不动。）

`attachment_write` 的 `async with self._write_lock:` 块替换为：

```python
        async with self._write_lock:
            with self.db.transaction() as conn:
                cp = self._content_path(norm)
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_bytes(image_bytes)

                if not self._has_node(nid):
                    self._add_node(nid, type="file", name=name,
                                   description=str(description or ""),
                                   content_type=content_type, score_patch=0.0)
                self._set_node_attr(nid, updated_at=_utc_iso(), declared_by=declared_by)
                self._link_parent(nid, parent_nid)
                conn.execute(
                    "UPDATE nodes SET description=?, declared_by=?, updated_at=?, content_type=?"
                    " WHERE id=?",
                    (str(description or ""), declared_by, _now_epoch(), content_type, nid),
                )
```

`_index_attachment_description` 中 `self._load_chunks_index()/_save_chunks_index/_atomic_write_json` 段替换为：

```python
            chunks = _chunk_text(chunk_text)
            old_ids = self._delete_chunks(norm)
            if old_ids:
                await self._delete_chunk_ids(old_ids)
            chunk_items = []
            for idx, text in enumerate(chunks, 1):
                chunk_items.append({
                    "chunk_id": f"{norm}::chunk::{idx}::{uuid.uuid4().hex[:8]}",
                    "node_path": norm, "chunk_index": idx, "text": text,
                    "text_preview": text[:120],
                    "scope_prefix": str(Path(norm).parent.as_posix()).strip(".") or "/",
                    "updated_at": _utc_iso(), "indexed": True,
                })
            self._replace_chunks(norm, chunk_items)
            if chunk_items:
                await self._embed_and_index(chunk_items)
```

并删除所有 `await self._flush_async()` 调用点（`file_write`、`attachment_write`、`file_delete`、`file_delete_tree`(经 file_delete)、`file_rename`、`file_copy`、`file_move`、`mkdir`、`set_tags`、`set_score_patch`、`_ensure_entity_name_vecs`、`_add_record_entity`）与 `self._write_lock` 外层的 flush 行。

- [ ] **Step 6: 删除/改名路径改造（`file_delete` / `mkdir` / `file_rename` / `file_copy` / `file_move`）**

`file_delete` 的 `async with self._write_lock:` 块替换为：

```python
        async with self._write_lock:
            chunk_ids: list[str] = []
            with self.db.transaction() as conn:
                cp = self._content_path(norm)
                if cp.exists():
                    if cp.is_dir():
                        shutil.rmtree(cp, ignore_errors=True)
                    else:
                        cp.unlink(missing_ok=True)
                chunk_ids = [r["chunk_id"] for r in conn.execute(
                    "SELECT chunk_id FROM chunks WHERE node_id=?", (nid,)).fetchall()]
                conn.execute("DELETE FROM nodes WHERE id=?", (nid,))
                if self._graph.has_node(nid):
                    self._graph.remove_node(nid)
            await self._delete_chunk_ids(chunk_ids)
            self._mark_bm25_dirty()
        return {"path": norm}
```

`mkdir` 的写段替换为：

```python
        parent_nid = self._ensure_ancestors(norm)
        if not self._has_node(nid):
            self._add_node(nid, type="dir", name=Path(norm).name, description=str(description or ""))
        self._link_parent(nid, parent_nid)
        return {"path": norm, "type": "dir"}
```

`file_rename` 正文（保留入口校验与日志）替换为：

```python
        ntype = self._get_node_attr(nid, "type", "file")
        async with self._write_lock:
            old_cp = self._content_path(norm)
            new_cp = self._content_path(new_path)
            if old_cp.exists():
                new_cp.parent.mkdir(parents=True, exist_ok=True)
                old_cp.rename(new_cp)
            with self.db.transaction() as conn:
                if ntype == "file":
                    conn.execute(
                        "UPDATE nodes SET id=?, path=?, name=?, updated_at=? WHERE id=?",
                        (new_nid, new_path, new_name, _now_epoch(), nid),
                    )
                else:
                    conn.execute(
                        "UPDATE nodes SET id='path:' || replace(path, ?, ?),"
                        " path=replace(path, ?, ?), updated_at=?"
                        " WHERE path=? OR path LIKE ?",
                        (norm, new_path, norm, new_path, _now_epoch(), norm, norm + "/%"),
                    )
                    conn.execute("UPDATE nodes SET name=? WHERE id=?", (new_name, new_nid))
                self._mark_bm25_dirty()
            self._relabel_nx(norm, new_path)
        log.info("file_rename path=%s -> new_path=%s type=%s", norm, new_path, ntype)
        return {"path": norm, "new_path": new_path, "type": ntype}
```

新增两个私有方法（放在 `file_rename` 之上或 `_subtree_paths` 附近）：

```python
    def _relabel_nx(self, old_path: str, new_path: str) -> None:
        mapping = {}
        for nid in list(self._graph.nodes):
            if not _is_path_id(nid):
                continue
            p = _id_to_path(nid)
            if p == old_path or p.startswith(old_path + "/"):
                mapping[nid] = _path_id(new_path + p[len(old_path):])
        if mapping:
            nx.relabel_nodes(self._graph, mapping, copy=False)

    def _copy_one_node(self, conn, src_path: str, dst_path: str) -> list[dict]:
        """把一个 path 节点（含内容文件、元数据、分块）复制到 dst_path；返回新分块项。"""
        src_nid, dst_nid = _path_id(src_path), _path_id(dst_path)
        src_attrs = dict(self._graph.nodes[src_nid]) if self._graph.has_node(src_nid) else {}
        cp = self._content_path(src_path)
        if cp.exists():
            new_cp = self._content_path(dst_path)
            new_cp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cp, new_cp)
        attrs = {k: v for k, v in src_attrs.items() if k not in ("tags", "parent_id")}
        attrs["updated_at"] = _utc_iso()
        self._add_node(dst_nid, **attrs)
        self._link_parent(dst_nid, _path_id(str(Path(dst_path).parent)))
        for r in conn.execute("SELECT tag FROM tags WHERE node_id=?", (src_nid,)).fetchall():
            conn.execute("INSERT OR IGNORE INTO tags(node_id, tag) VALUES (?, ?)", (dst_nid, r["tag"]))
        new_items = []
        for r in conn.execute(
                "SELECT chunk_index, text, text_preview, scope_prefix FROM chunks"
                " WHERE node_id=? ORDER BY chunk_index", (src_nid,)).fetchall():
            new_items.append({
                "chunk_id": f"{dst_path}::chunk::{r['chunk_index']}::{uuid.uuid4().hex[:8]}",
                "node_path": dst_path, "chunk_index": int(r["chunk_index"]), "text": r["text"],
                "text_preview": r["text_preview"],
                "scope_prefix": str(Path(dst_path).parent.as_posix()).strip(".") or "/",
                "updated_at": _utc_iso(), "indexed": True,
            })
        if new_items:
            self._replace_chunks(dst_path, new_items)
        return new_items
```

`file_copy` 正文替换为：

```python
        ntype = self._get_node_attr(nid, "type", "file")
        copied_chunk_items: list[dict] = []
        async with self._write_lock:
            with self.db.transaction() as conn:
                self._ensure_ancestors(dest)
                if ntype == "file":
                    copied_chunk_items = self._copy_one_node(conn, norm, dest)
                else:
                    for child_path in self._subtree_paths(norm):
                        copied_chunk_items.extend(
                            self._copy_one_node(conn, child_path, dest + child_path[len(norm):])
                        )
            if copied_chunk_items:
                await self._embed_and_index(copied_chunk_items)
            self._mark_bm25_dirty()
        log.info("file_copy path=%s -> dest=%s type=%s", norm, dest, ntype)
        return {"path": norm, "dest": dest, "type": ntype}
```

`file_move` 正文替换为：

```python
        ntype = self._get_node_attr(nid, "type", "file")
        async with self._write_lock:
            old_cp = self._content_path(norm)
            new_cp = self._content_path(dest)
            if old_cp.exists():
                new_cp.parent.mkdir(parents=True, exist_ok=True)
                old_cp.rename(new_cp)
            with self.db.transaction() as conn:
                if ntype == "file":
                    conn.execute(
                        "UPDATE nodes SET id=?, path=?, updated_at=? WHERE id=?",
                        (dest_nid, dest, _now_epoch(), nid),
                    )
                else:
                    conn.execute(
                        "UPDATE nodes SET id='path:' || replace(path, ?, ?),"
                        " path=replace(path, ?, ?), updated_at=?"
                        " WHERE path=? OR path LIKE ?",
                        (norm, dest, norm, dest, _now_epoch(), norm, norm + "/%"),
                    )
                new_parent = _path_id(str(Path(dest).parent))
                conn.execute("UPDATE nodes SET parent_id=? WHERE id=?", (new_parent, dest_nid))
                self._mark_bm25_dirty()
            self._relabel_nx(norm, dest)
            if self._graph.has_node(dest_nid):
                self._graph.add_edge(new_parent, dest_nid, key="parent", type=TREE_EDGE)
        log.info("file_move path=%s -> dest=%s type=%s", norm, dest, ntype)
        return {"path": norm, "new_path": dest, "type": ntype}
```

- [ ] **Step 7: 标签/分数/记录实体改造**

`set_tags` 写段：

```python
        norm = _normalize_path(path)
        log.info("set_tags path=%s tags=%s", norm, tags)
        nid = _path_id(norm)
        if not self._has_node(nid):
            raise FileNotFoundError(f"节点不存在: {norm}")
        clean = [t.strip() for t in (tags or []) if t and t.strip()]
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM tags WHERE node_id=?", (nid,))
            conn.executemany("INSERT OR IGNORE INTO tags(node_id, tag) VALUES (?, ?)",
                             [(nid, t) for t in clean])
            if managed_by is not None:
                conn.execute("UPDATE nodes SET managed_by=?, updated_at=? WHERE id=?",
                             (str(managed_by), _now_epoch(), nid))
            else:
                conn.execute("UPDATE nodes SET updated_at=? WHERE id=?", (_now_epoch(), nid))
        self._set_node_attr(nid, tags=clean)
        return {"path": norm, "meta": self._get_meta(norm)}
```

`set_score_patch` 写段：

```python
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE nodes SET score_patch=?, score_patch_updated_at=?, updated_at=? WHERE id=?",
                (patch, _now_epoch(), _now_epoch(), nid),
            )
        self._set_node_attr(nid, score_patch=patch)
        return {"path": norm, "meta": self._get_meta(norm)}
```

`_add_record_entity` 末尾的 `await self._flush_async()` 删除，改为用事务包住三条写入：

```python
        with self.db.transaction():
            eid = self.entity_add(name, entity_type=record_type, properties=props, kb_refs=[path])
            nid = _path_id(path)
            if self._has_node(nid):
                self._link_parent(eid, nid)
            prev = self._find_latest_entity(record_type, exclude=eid)
            if prev:
                self._add_edge(prev, eid, "next")
```

（`_link_parent` 用于「记录节点 → 实体」的 has_child 语义；实体 id 非 path，`_verify_tree` 不会触碰它，`get_entity_children` 依旧能通过 has_child 边找到。）

- [ ] **Step 8: 调用方与测试改造**

`backend/faust_backend/memory/tools.py` 的实体/关系写入段（原 149-190 行）替换为：

```python
        name_to_id: dict[str, str] = {}
        if entities and doc_path:
            names = [str(e.get("name", "")) for e in entities]
            name_vecs = await m._embed_texts(names)
            existing_ids = await m.entity_find_similar(name_vecs, threshold=ENTITY_DEDUP_THRESHOLD)
            doc_nid = _path_id(doc_path)

            with m.db.transaction():
                for item, name_vec, existing_id in zip(entities, name_vecs, existing_ids):
                    name = str(item.get("name", ""))
                    etype = str(item.get("type", "custom"))
                    desc = str(item.get("description", ""))
                    props = item.get("properties", {}) or {}
                    refs = item.get("kb_refs", []) or []

                    if existing_id:
                        eid = existing_id
                        if m._has_node(doc_nid) and m._has_node(existing_id):
                            m._add_edge(doc_nid, existing_id, "from")
                    else:
                        eid = m.entity_add(name, etype, description=desc,
                                           properties=props, kb_refs=refs,
                                           name_embedding=name_vec.tolist())
                        if m._has_node(doc_nid):
                            m._add_edge(doc_nid, eid, "from")
                    name_to_id[name] = eid

        with m.db.transaction():
            for item in relations:
                src_name = str(item.get("source", ""))
                tgt_name = str(item.get("target", ""))
                src_id = name_to_id.get(src_name)
                tgt_id = name_to_id.get(tgt_name)
                if not src_id or not tgt_id:
                    log.warning("relation skipped: source=%s target=%s not found in current extraction", src_name, tgt_name)
                    continue
                m.relation_add(source_id=src_id, target_id=tgt_id,
                               rel_type=str(item.get("type", "relates_to")))

        log.info("_bg_extract_and_save done entities=%d relations=%d",
                 len(entities) if entities else 0, len(relations) if relations else 0)
        m.complete_extraction(doc_path, success=True)
```

同时删除 `entity_add` / `entity_delete` / `relation_add` / `relation_remove` 的 `flush: bool = True` 形参与内部 `if flush: self.flush()` 分支（`memory/api.py`、`tools/*.py`、插件均未使用该形参）。

`backend/tests/conftest.py`：`yield gs` 之后的 `gs.flush()` 改为 `gs.close()`。

`backend/tests/test_memory_store.py`：
- 删除第 39 行 `gs.flush()`（fixture teardown 改 `gs.close()`）；
- 删除 `112-125`（`_repair_tree` 断言 `graph.json`）、`396-408`、`863-909` 这三个断言文件格式的测试；
- 其余测试保持不动（它们只依赖公开行为）。

- [ ] **Step 8b: 确认没有文件层残留**

Run: `grep -rn "_read_meta\|_write_meta\|_flush_async\|_load_chunks_index\|_save_chunks_index\|_load_tasks\|_save_tasks\|_chunks_file\|_meta_path\|_repair_tree\|_atomic_write_json\|_read_json\|_name_vec\|self\.save()" backend/faust_backend/memory/ backend/tests/`
Expected: 无输出（`_read_json` 仅存在于 `migrate.py`，若出现则说明 store.py 未清干净）

- [ ] **Step 9: 运行全量测试**

Run: `.runtime/python.exe -m pytest backend/tests -q`
Expected: PASS（全部通过；失败项只允许是「原断言文件格式的测试」——那些已在上一步删除）

- [ ] **Step 10: 提交**

```bash
git add backend/faust_backend/memory/store.py backend/faust_backend/memory/tools.py backend/tests/conftest.py backend/tests/test_memory_store.py
git commit -m "refactor(memory): GraphStore 持久化切换为 SQLite（图/元数据/分块/任务）"
```

---

## Task 4: 实体名向量切 `entity.vdb`

**Files:**
- Modify: `backend/faust_backend/memory/store.py`（`entity_add`、`entity_delete`、`_ensure_entity_name_vecs`、`entity_find_similar`、新增 `_ensure_entity_vdb`）
- Test: `backend/tests/test_memory_store.py`（新增 2 个用例）

**Interfaces:**
- Consumes: Task 1/2/3（`self.entity_index_file`、`migrate._write_entity_vdb` 产出的文件格式：条目 `{"__id__": eid, "__vector__": float32}`）
- Produces: `GraphStore._ensure_entity_vdb() -> NanoVectorDB`、`GraphStore._entity_vec_dirty: bool`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_memory_store.py` 增加：

```python
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
```

（向量维度用生产值 1536，与文件中既有测试一致；`test_agent` 的 `CONFIG_ROOT` 由 fixture 的 monkeypatch 指向 `tmp_path`，第二个 `GraphStore` 因此落在同一临时目录，验证的是「重启后仍在」。）

- [ ] **Step 2: 运行测试确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_memory_store.py -q -k entity_name_vector or entity_delete_removes`
Expected: FAIL（`_name_vec` 已不存在 / 命中为空）

- [ ] **Step 3: 实现 `entity.vdb` 读写**

新增：

```python
    def _ensure_entity_vdb(self) -> NanoVectorDB:
        if self._entity_vdb is None:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            self._entity_vdb = NanoVectorDB(EMBED_DIM, storage_file=str(self.entity_index_file))
        return self._entity_vdb
```

`entity_add` 的向量写入（替换 `self._graph.nodes[eid]["_name_vec"] = ...`）：

```python
        if name_embedding:
            vdb = self._ensure_entity_vdb()
            vdb.upsert([{
                "__id__": eid,
                "__vector__": np.asarray(name_embedding, dtype=np.float32),
                "name": name,
            }])
            vdb.save()
```

`entity_delete` 删除向量：

```python
        if self._entity_vdb is not None or self.entity_index_file.exists():
            vdb = self._ensure_entity_vdb()
            vdb.delete([entity_id])
            vdb.save()
```

`_ensure_entity_name_vecs` 替换为：

```python
    async def _ensure_entity_name_vecs(self) -> None:
        vdb = self._ensure_entity_vdb()
        entities = {r["id"]: r["name"] for r in self.db.all(
            "SELECT id, name FROM nodes WHERE type='entity'")}
        have = {d["__id__"] for d in (vdb.get(list(entities)) or [])}
        missing = [(eid, name) for eid, name in entities.items() if eid not in have]
        if not missing:
            return
        log.warning("entities missing name_vec count=%d", len(missing))
        vecs = await self._embed_texts([name for _, name in missing])
        rows = []
        for (eid, name), vec in zip(missing, vecs):
            rows.append({"__id__": eid, "__vector__": np.asarray(vec, dtype=np.float32), "name": name})
        if rows:
            vdb.upsert(rows)
            vdb.save()
```

`entity_find_similar` 替换为：

```python
    async def entity_find_similar(self, name_vecs: list[np.ndarray],
                                   threshold: float = 0.85) -> list[str | None]:
        await self._ensure_entity_name_vecs()
        vdb = self._ensure_entity_vdb()
        results: list[str | None] = []
        for qv in name_vecs:
            hits = vdb.query(np.asarray(qv, dtype=np.float32), top_k=1,
                             better_than_threshold=threshold)
            results.append(hits[0]["__id__"] if hits else None)
        return results
```

- [ ] **Step 4: 运行测试确认通过**

Run: `.runtime/python.exe -m pytest backend/tests/test_memory_store.py -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/faust_backend/memory/store.py backend/tests/test_memory_store.py backend/tests/conftest.py
git commit -m "perf(memory): 实体名向量改用第二个 nano-vectordb 实例（删除 48MB JSONL 侧车）"
```

---

## Task 5: 检索路径下推 SQL 与向量落盘批量化

**Files:**
- Modify: `backend/faust_backend/memory/store.py`（`advanced_search`、`get_changed_nodes`、`_ensure_bm25_index`、`search_compact`、`search`、`_hybrid_search`、`_graph_search`、`_rerank`、`_embed_and_index`、`_delete_chunk_ids`）
- Test: `backend/tests/test_memory_store.py`（新增 2 个用例）

**Interfaces:**
- Consumes: Task 3 的 `_get_meta`、`chunks`/`tags` 表；Task 1 的 `MemoryDB`
- Produces: 无新公共接口（内部行为等价）

- [ ] **Step 1: 写失败测试**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.runtime/python.exe -m pytest backend/tests/test_memory_store.py -q -k bm25_index_comes or changed_and_advanced`
Expected: FAIL（`_ensure_bm25_index` 仍走文件系统 / `advanced_search` 仍 rglob）

- [ ] **Step 3: 实现**

`_ensure_bm25_index` 中**从 `docs: list[dict] = []` 到实体节点循环结束**的整段替换为（其后的 `if not docs:` 早退、`jieba_tokenize_batch` 分词、`BM25Okapi` 构建与 `_mark_bm25_clean()` 全部保持原样；被替掉的顺序里 `from concurrent.futures import ...` 与 `import json as _json` 也随之删除，因为不再遍历文件系统）：

```python
        docs: list[dict] = []
        seen: set[str] = set()
        for r in self.db.all(
            "SELECT n.path AS path, group_concat(c.text, ' ') AS text FROM chunks c"
            " JOIN nodes n ON n.id = c.node_id WHERE n.path IS NOT NULL GROUP BY n.id"
        ):
            docs.append({"path": r["path"], "text": r["text"] or "", "type": "content"})
            seen.add(r["path"])
        for r in self.db.all(
            "SELECT path, description FROM nodes WHERE type IN ('file', 'dir')"
            " AND description <> '' AND path IS NOT NULL"
        ):
            if r["path"] in seen:
                continue
            docs.append({"path": r["path"], "text": r["description"], "type": "description"})
            seen.add(r["path"])
        for r in self.db.all("SELECT id, name, description FROM nodes WHERE type='entity'"):
            text = f"{r['name']} {r['description']}".strip()
            if text:
                docs.append({"path": r["id"], "text": text, "type": "entity"})
```

`advanced_search` 的候选收集段（`for mp in sorted(meta_dir.rglob(...))` 循环）替换为：

```python
        rows = self.db.all(
            "SELECT n.id AS id, n.path AS path, n.description AS description,"
            " n.declared_by AS declared_by, n.updated_at AS updated_at,"
            " n.score_patch AS score_patch, n.content_type AS content_type,"
            " (SELECT group_concat(t.tag, ',') FROM tags t WHERE t.node_id = n.id) AS tag_csv"
            " FROM nodes n WHERE n.type IN ('file', 'dir') AND n.path IS NOT NULL"
            " ORDER BY n.updated_at DESC"
        )
        candidates: list[dict] = []
        for row in rows:
            p = _normalize_path(row["path"])
            if scope_prefix and not (p.startswith(scope_prefix) or p == scope_prefix.rstrip("/")):
                continue
            meta_tags = [t for t in str(row["tag_csv"] or "").split(",") if t]
            if required_tags:
                tag_set = {t.casefold() for t in meta_tags}
                if tag_logic == "AND":
                    if not required_tags.issubset(tag_set):
                        continue
                else:
                    if not required_tags.intersection(tag_set):
                        continue
            updated = epoch_to_iso(row["updated_at"])
            updated_date = updated[:10] if updated else ""
            if date_from and updated_date and updated_date < date_from:
                continue
            if date_to and updated_date and updated_date > date_to:
                continue
            if declared_by and str(row["declared_by"] or "") != declared_by:
                continue
            if content_type:
                ctype = str(row["content_type"] or "")
                if content_type == "text" and ctype and not ctype.startswith("text/"):
                    continue
                if content_type == "image" and ctype and not ctype.startswith("image/"):
                    continue
            candidate = {
                "path": p,
                "description": str(row["description"] or ""),
                "tags": meta_tags,
                "updated_at": updated,
                "declared_by": str(row["declared_by"] or ""),
                "score_patch": float(row["score_patch"] or 0.0),
            }
            if need_text_query:
                text_lower = need_text_query.lower()
                score = 0.0
                if text_lower in Path(p).name.lower():
                    score = 1.0
                if text_lower in candidate["description"].lower():
                    score = max(score, 0.8)
                cp = self._content_path(p)
                if cp.exists() and score < 0.5:
                    try:
                        content = cp.read_text(encoding="utf-8", errors="ignore")[:5000]
                        if text_lower in content.lower():
                            score = max(score, 0.6)
                    except Exception:
                        pass
                if score == 0:
                    continue
                candidate["score"] = score
            else:
                candidate["score"] = 0.0
            candidates.append(candidate)
```

`get_changed_nodes` 主体替换为：

```python
        scope_prefix = _normalize_path(scope or "").strip("/")
        scope_prefix = f"{scope_prefix}/" if scope_prefix else ""
        required_tags = {t.casefold() for t in (tags or [])}
        rows = self.db.all(
            "SELECT n.path AS path, n.updated_at AS updated_at, n.score_patch AS score_patch,"
            " (SELECT group_concat(t.tag, ',') FROM tags t WHERE t.node_id = n.id) AS tag_csv"
            " FROM nodes n WHERE n.type IN ('file', 'dir') AND n.updated_at IS NOT NULL"
            " AND n.updated_at >= ? ORDER BY n.updated_at DESC",
            (float(since_ts),),
        )
        results = []
        for row in rows:
            node_path = str(row["path"] or "")
            if not node_path:
                continue
            if scope_prefix and not node_path.startswith(scope_prefix):
                continue
            ntags = [t for t in str(row["tag_csv"] or "").split(",") if t]
            if required_tags and not required_tags.issubset({t.casefold() for t in ntags}):
                continue
            results.append({
                "path": node_path,
                "updated_at": epoch_to_iso(row["updated_at"]),
                "tags": ntags,
                "score_patch": float(row["score_patch"] or 0.0),
            })
        log.info("get_changed_nodes since=%s scope=%s hits=%d", since_ts, scope or "/", len(results))
        return results
```

向量落盘批量化：`_embed_and_index` / `_delete_chunk_ids` 里的 `await asyncio.to_thread(vdb.save)` 替换为 `self._vdb_dirty = True`，并在所有调用它们的写方法末尾（`file_write`、`attachment_write`、`_index_attachment_description`、`file_delete`、`file_copy`）追加：

```python
            if self._vdb_dirty:
                self._vdb_dirty = False
                await asyncio.to_thread(self._ensure_vdb().save)
```

其余检索方法（`search`、`search_compact`、`_hybrid_search`、`_vector_search`、`_graph_search`、`_rerank`）中所有 `self._read_meta(x)` 调用改名为 `self._get_meta(x)`。

- [ ] **Step 4: 运行全量测试**

Run: `.runtime/python.exe -m pytest backend/tests -q`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add backend/faust_backend/memory/store.py backend/tests/test_memory_store.py
git commit -m "perf(memory): BM25/高级检索/变更查询下推 SQL，向量落盘按事务合并"
```

---

## Task 6: 并发与事务原子性测试

**Files:**
- Create: `backend/tests/test_memory_concurrency.py`

**Interfaces:**
- Consumes: Task 3 的 `GraphStore`（`db`、`file_write`、`file_rename`）
- Produces: 无

- [ ] **Step 1: 写测试**

```python
"""并发写与事务原子性（旧实现全量写盘时会产生 *.tmp 竞态）。"""

from __future__ import annotations

import asyncio
import sys
import threading
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import pytest  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    import faust_backend.config_loader as conf
    import faust_backend.memory.store as store_mod

    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "AGENT_NAME", "test_agent")
    gs = store_mod.GraphStore("test_agent")

    async def _noop_embed_index(chunk_items):
        return None

    monkeypatch.setattr(gs, "_embed_and_index", _noop_embed_index)
    yield gs
    gs.close()


def test_concurrent_writes_keep_all_rows(store):
    def worker(prefix: str) -> None:
        for i in range(50):
            asyncio.run(store.file_write(f"/conc/{prefix}_{i}.md", f"body {i}"))

    threads = [threading.Thread(target=worker, args=(p,)) for p in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    rows = store.db.all("SELECT path FROM nodes WHERE path LIKE '/conc/%'")
    assert len(rows) == 100
    assert not list((store.store_dir / "index").glob("*.tmp"))


def test_rename_db_state_unchanged_on_failure(store, monkeypatch):
    asyncio.run(store.file_write("/atomic/a.md", "x", description="d"))
    asyncio.run(store.file_write("/atomic/sub/b.md", "y"))

    before_nodes = {(r["id"], r["parent_id"]) for r in store.db.all("SELECT id, parent_id FROM nodes")}
    before_chunks = [(r["node_id"], r["chunk_id"]) for r in store.db.all("SELECT node_id, chunk_id FROM chunks ORDER BY chunk_id")]

    class Boom(RuntimeError):
        pass

    real_transaction = store.db.transaction
    state = {"fail": False}

    def flaky_transaction():
        if state["fail"]:
            raise Boom("injected")
        return real_transaction()

    monkeypatch.setattr(store.db, "transaction", flaky_transaction)
    state["fail"] = True
    with pytest.raises(Boom):
        asyncio.run(store.file_rename("/atomic", "atomic_renamed"))

    after_nodes = {(r["id"], r["parent_id"]) for r in store.db.all("SELECT id, parent_id FROM nodes")}
    after_chunks = [(r["node_id"], r["chunk_id"]) for r in store.db.all("SELECT node_id, chunk_id FROM chunks ORDER BY chunk_id")]
    assert after_nodes == before_nodes
    assert after_chunks == before_chunks
```

（断言只覆盖 SQL 侧：正文文件与数据库本就是两套存储，跨存储原子性不在本设计承诺内——spec「错误处理」一节已说明。）

- [ ] **Step 2: 运行测试**

Run: `.runtime/python.exe -m pytest backend/tests/test_memory_concurrency.py -q`
Expected: PASS（2 passed）。`test_concurrent_writes_keep_all_rows` 若出现丢行，说明 `MemoryDB` 的 RLock/事务封装有漏（并发写是旧实现 `.tmp` 竞态的等价场景），必须定位修好而不是放宽断言。

- [ ] **Step 3: 提交**

```bash
git add backend/tests/test_memory_concurrency.py
git commit -m "test(memory): 并发写与改名事务原子性回归测试"
```

---

## Task 7: 真实数据迁移演练与验收测量

**Files:**
- Create: `.tmp_acceptance_measure.py`（临时脚本，测量后删除）
- Modify: `docs/superpowers/specs/2026-09-15-memory-sqlite-storage-design.md`（把「验收标准」表的实测列填上真实数字）
- Create: `backend/faust_backend/memory/README.md`（迁移与回滚说明）

**Interfaces:**
- Consumes: 全部前置 Task
- Produces: 验收数据 + 回滚文档

- [ ] **Step 1: 在真实数据副本上演练迁移**

```python
"""临时脚本：在 ~/.faustbot/agents/<agent>/memory 的副本上跑迁移与指标测量。"""
import os, shutil, sys, tempfile, time
from pathlib import Path

SRC = Path(os.path.expanduser("~/.faustbot/agents/faust"))
TMP = Path(tempfile.mkdtemp(prefix="migrate_rehearsal_"))
shutil.copytree(SRC / "memory", TMP / "agents" / "faust" / "memory",
                ignore=shutil.ignore_patterns("*.tmp", "memory.sqlite*"))

sys.path.insert(0, "backend")
import faust_backend.config_loader as conf
conf.CONFIG_ROOT = str(TMP)
conf.AGENT_NAME = "faust"
conf.BM25_ONLY = True

from faust_backend.memory import store

t = time.perf_counter()
gs = store.GraphStore("faust")
print(f"cold start（含首次迁移）: {time.perf_counter() - t:.3f}s")
print("nodes:", gs.db.one("SELECT count(*) AS c FROM nodes")["c"],
      "edges:", gs.db.one("SELECT count(*) AS c FROM edges")["c"],
      "chunks:", gs.db.one("SELECT count(*) AS c FROM chunks")["c"],
      "tags:", gs.db.one("SELECT count(*) AS c FROM tags")["c"])
print("archived:", (TMP / "agents" / "faust" / "memory" / "_legacy_json").exists())
gs.close()

t = time.perf_counter()
gs = store.GraphStore("faust")
print(f"cold start（迁移后重启）: {time.perf_counter() - t:.3f}s")
print("db size:", (gs.db_file).stat().st_size / 1e6, "MB",
      "entity.vdb:", gs.entity_index_file.stat().st_size / 1e6, "MB")
shutil.rmtree(TMP, ignore_errors=True)
```

Run: `.runtime/python.exe .tmp_acceptance_measure.py`
Expected: 输出显示节点/边/分块数与迁移前 `graph.json`（2739/5460/1316）一致、`_legacy_json` 存在、冷启动（迁移后重启）≈0.08s。

- [ ] **Step 2: 测量验收指标**

在同一个临时脚本中追加（沿用真实数据副本，`_embed_and_index` 换成 noop 以避免联网）：

```python
async def measure():
    async def noop(items): return None
    gs._embed_and_index = noop
    t = time.perf_counter(); await gs.file_write("/bench/probe.md", "hello world " * 200, description="bench")
    print(f"file_write: {time.perf_counter() - t:.4f}s")
    t = time.perf_counter(); await gs.advanced_search(query=""); print(f"advanced_search: {time.perf_counter() - t:.4f}s")
    t = time.perf_counter(); await gs.get_changed_nodes(time.time() - 7 * 86400); print(f"changed: {time.perf_counter() - t:.4f}s")
    t = time.perf_counter(); await gs._ensure_bm25_index(); print(f"bm25 build: {time.perf_counter() - t:.3f}s")
asyncio.run(measure())
```

对照 spec「验收标准」表逐项核对：冷启动 ≤0.15s、`file_write` ≤50ms、`advanced_search("")` ≤20ms、`get_changed_nodes` ≤20ms、BM25 冷建 ≤5s、单文件 ≤20MB、graph+meta+向量总量 ≤25MB。

- [ ] **Step 3: 把实测值写回 spec 并撰写回滚文档**

- 在 `docs/superpowers/specs/2026-09-15-memory-sqlite-storage-design.md` 的验收标准表中补一列「实测」，填入 Step 2 的数字（不修改目标值）。
- 新增 `backend/faust_backend/memory/README.md`：

```markdown
# Memory 存储布局

| 路径 | 内容 |
| --- | --- |
| `memory.sqlite` | 真源：`nodes` / `edges` / `tags` / `chunks` / `tasks`（WAL 模式） |
| `index/chunks.vdb` | nano-vectordb 分块向量（分块正文在 `chunks` 表） |
| `index/entity.vdb` | nano-vectordb 实体名向量（去重检索用） |
| `content/**` | 文档正文与附件（文件，不入库） |
| `_legacy_json/**` | 迁移前的旧 JSON 归档（可删除；删除后不可回滚） |

## 自动迁移

首次启动若存在 `graph.json` 且 `nodes` 表为空 → 单事务导入 + 校验 + 归档旧文件；
校验失败会抛错并保持旧文件不动（不静默降级）。

## 回滚

1. 停止后端；
2. 删除 `memory/memory.sqlite`、`memory/memory.sqlite-wal`、`memory/memory.sqlite-shm`；
3. 把 `memory/_legacy_json/` 下的内容移回原位（`graph.json` 回 `memory/`，`index/entity_vecs.jsonl` 回 `memory/index/`，`meta/` 回 `memory/meta/`）；
4. 删除 `memory/index/entity.vdb`；
5. 切回迁移前的代码版本，启动。
```

- [ ] **Step 4: 前端集成验证**

按仓库规则 7：启动后端（`.runtime/python.exe backend/main.py`）与前端（带 CDP 参数），连 CDP 打开配置窗口的记忆页，确认：树列表可展开、文件详情可读、检索可返回结果、标签与评分保存生效、图谱视图可渲染。验证完成后关闭前后端。若无法启动前端，明确报告该项未验证。

- [ ] **Step 5: 清理与提交**

```bash
rm -f .tmp_acceptance_measure.py
git add backend/faust_backend/memory/README.md docs/superpowers/specs/2026-09-15-memory-sqlite-storage-design.md
git commit -m "docs(memory): 迁移/回滚说明与验收实测数据"
```

---

## Self-Review

**1. Spec coverage**

| Spec 章节 | 对应 Task |
| --- | --- |
| 目标架构（分层 + 不变量 1-5） | Task 3（真源/事务/边归属/时间统一）、Task 4（向量） |
| 数据库 Schema | Task 1 Step 3 |
| 组件与改动清单（`storage.py`/`migrate.py`/`store.py`/调用方） | Task 1、2、3、4、5 |
| 写入路径与事务 | Task 3 Step 5-7、Task 5 Step 3（向量批量落盘） |
| 检索路径（hybrid/advanced/changed/BM25） | Task 5 |
| 实体向量 | Task 4 |
| 迁移（导入/校验/归档/回滚/幂等） | Task 2 + Task 7 Step 3 |
| 并发与线程模型 | Task 1（RLock+可重入事务）、Task 6 |
| 错误处理 | Task 1（schema 版本抛错）、Task 2（`MigrationError`） |
| 测试 | Task 2（迁移三用例）、Task 6（并发/原子性）、各 Task 内联用例 |
| 验收标准 | Task 7 Step 2 |
| 非目标 | Global Constraints 中逐条禁止（Kùzu/FTS5/正文入库/前端改动） |

**2. Placeholder scan**：已逐条检查，无 `TBD` / `TODO` / 「稍后实现」/「类似 Task N」/ 只描述不写代码的步骤。三处曾出现的草稿残留（`file_write` 的 `if False else`、`_ensure_entity_name_vecs` 的占位行、Task 6 依赖调用次序的注入点）已在自审中改为最终代码。

**3. Type consistency**：`MemoryDB.transaction()` / `.one()` / `.all()` / `.execute()` / `.executemany()` / `.close()`、`epoch_to_iso` / `iso_to_epoch`、`migrate_if_needed` / `migrate_from_json` / `MigrationReport` / `LEGACY_ARCHIVE_DIRNAME`、`GraphStore._get_meta` / `_link_parent` / `_reset_nx_parent_edge` / `_parent_of` / `_verify_tree` / `_subtree_paths` / `_replace_chunks` / `_delete_chunks` / `_relabel_nx` / `_copy_one_node` / `_entity_vdb` / `_vdb_dirty` / `entity_index_file` / `db` 在各 Task 间命名与签名一致。

**已知需在实现时确认的两点**（不属于占位，属于实现期验证）：

1. Task 3 Step 2 的 `_ensure_ancestors` 重写必须与原实现语义一致：返回**直接父节点 id**，并为缺失的中间目录建节点 + 父子边。实现后跑 `test_file_write_creates_full_ancestor_chain` / `test_mkdir_creates_full_ancestor_chain` / `test_ensure_ancestors_creates_intermediate_dirs` 三个既有测试验证。
2. SQLite `ON UPDATE CASCADE` 对 `nodes.parent_id` 的自引用级联在 3.45.1 上按行生效；Task 1 的 `test_foreign_key_cascade_on_rename_and_delete` 已覆盖该假设，若失败则改为显式 `UPDATE nodes SET parent_id=...` 批量更新（`file_rename`/`file_move` 各加一条语句）。
