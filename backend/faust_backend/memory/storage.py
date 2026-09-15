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
