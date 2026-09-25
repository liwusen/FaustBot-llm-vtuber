from __future__ import annotations

import asyncio
import base64
import inspect
import json
import math
import shutil
import threading
import time
import uuid
from collections import deque
from functools import wraps
from itertools import chain
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
from nano_vectordb import NanoVectorDB
from openai import AsyncOpenAI
from rank_bm25 import BM25Okapi

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger
from faust_backend.memory.migrate import migrate_if_needed
from faust_backend.memory.storage import (
    MemoryDB, ensure_root_node, epoch_to_iso, iso_to_epoch,
)
from faust_backend.memory.tokenize_pool import jieba_tokenize, jieba_tokenize_batch
from faust_backend.memory.config import (
    EMBED_MODEL, EMBED_DIM, MAX_CHUNK_CHARS, CHUNK_OVERLAP_CHARS,
    MIN_SCORE_PATCH, MAX_SCORE_PATCH,
)

log = get_logger("faust.memory")

_NODE_PATH = "memory"
TREE_EDGE = "has_child"


class _Waiter:
    """排队中的等待者：`fut` 用于唤醒，`granted` 记录持有权是否已交给它。

    `granted` 不能省：取消发生在「已被 `_handoff_locked` 弹出并排程唤起、协程尚未
    恢复」的窗口时，future 既不在 `_waiters` 里也没有结果，只能靠它判断该不该交还锁。
    """

    __slots__ = ("fut", "granted")

    def __init__(self, fut: asyncio.Future) -> None:
        self.fut = fut
        self.granted = False


def _wake_waiter(waiter: _Waiter) -> None:
    """在等待者自己的循环里唤醒它（`call_soon_threadsafe` 回调）。"""
    if not waiter.fut.done():
        waiter.fut.set_result(None)


class _CrossLoopLock:
    """跨事件循环、跨线程可用的异步互斥锁。

    `asyncio.Lock` 绑定创建它的循环，而 `GraphStore` 的协程会在多个循环里跑
    （araya `asyncio.run`、插件 hook 的 `_run_sync`、`_run_async_in_thread`），
    同一把 `asyncio.Lock` 跨循环争用会抛 "is bound to a different event loop"。
    这里用线程锁保护状态、用 future 把持有权交给下一个等待者：等待不占用线程池，
    也不会阻塞任何事件循环。
    """

    def __init__(self) -> None:
        self._state = threading.Lock()
        self._held = False
        self._waiters: deque[_Waiter] = deque()

    async def __aenter__(self) -> None:
        loop = asyncio.get_running_loop()
        with self._state:
            if not self._held:
                self._held = True
                return None
            waiter = _Waiter(loop.create_future())
            self._waiters.append(waiter)
        # 不变量：`granted` 为真 <=> 持有权已经是「我的」，哪怕这次等待再也醒不过来。
        # 取消发生在「已被 `_handoff_locked` 弹出并唤起、协程尚未恢复」的窗口里时，
        # waiter 既不在 `_waiters` 里也没有结果集，只能靠这个标记判断该不该交还锁。
        try:
            await waiter.fut
        except BaseException:
            with self._state:
                if waiter.granted:
                    self._handoff_locked()  # 授予后被取消：持有权在我手上，必须交还
                elif waiter in self._waiters:
                    self._waiters.remove(waiter)  # 还在排队：从未被授予，离队即可
                # 其余：已弹出但 `call_soon_threadsafe` 失败（等待者的循环已关），
                # `_handoff_locked` 已把持有权转给下一个等待者或置为未持有——不归我管。
            raise
        return None

    async def __aexit__(self, *exc: object) -> None:
        with self._state:
            if not self._held:
                raise RuntimeError("_CrossLoopLock is not acquired")
            self._handoff_locked()

    def _handoff_locked(self) -> None:
        """把持有权直接交给下一个等待者；没有等待者就置为未持有。"""
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.fut.cancelled():
                continue
            try:
                waiter.fut.get_loop().call_soon_threadsafe(_wake_waiter, waiter)
            except RuntimeError:
                continue  # 等待者的循环已关闭
            waiter.granted = True  # 持有权已交给它（`__aenter__` 的取消分支据此交还）
            return
        self._held = False


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _attr_epoch(value: Any) -> float | None:
    """节点属性里的时间 -> epoch：数值直通，ISO 串转换，空值 None。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return iso_to_epoch(str(value))


def _now_epoch() -> float:
    return time.time()


def _path_id(path: str) -> str:
    p = path.replace("\\", "/").strip().strip("/")
    return f"path:/{p}" if p else "path:/"


def _id_to_path(nid: str) -> str:
    return "/" + nid[len("path:"):].strip("/") if nid.startswith("path:") else nid


def _is_path_id(nid: str) -> bool:
    return nid.startswith("path:")


def _ent_id() -> str:
    return f"ent_{uuid.uuid4().hex}"



def _normalize_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    raw = "/" + raw.strip("/")
    if raw == "/":
        return "/"
    parts = [p for p in raw.split("/") if p and p not in (".", "..")]
    return "/" + "/".join(parts)


def _escape_like(value: str) -> str:
    """LIKE 字面量转义：先转义 ESCAPE 字符本身，再转义 `%` / `_`。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _chunk_text(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if len(normalized) <= MAX_CHUNK_CHARS:
        return [normalized]
    chunks: list[str] = []
    step = max(1, MAX_CHUNK_CHARS - CHUNK_OVERLAP_CHARS)
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + MAX_CHUNK_CHARS)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start += step
    return chunks


def _mutating(method):
    """串行化所有会改 `_graph` / `nodes` / `edges` 的写入（可重入叶子锁）。

    nx 图不是线程安全的，而同一轮里模型会并发发出多个写工具调用
    （曾出现两个 `entity_merge` 并发 → FOREIGN KEY constraint failed：
    一方算好的改指边引用了另一方已删除的节点）。`_mutation_lock` 只在同步
    区段内持有、从不跨越 await，故锁序恒为 `_write_lock`（异步写锁）→
    `_mutation_lock`，不成环。被装饰的协程方法体内不得出现 await。
    """
    if inspect.iscoroutinefunction(method):
        @wraps(method)
        async def _async_locked(self, *args, **kwargs):
            with self._mutation_lock:
                return await method(self, *args, **kwargs)

        return _async_locked

    @wraps(method)
    def _sync_locked(self, *args, **kwargs):
        with self._mutation_lock:
            return method(self, *args, **kwargs)

    return _sync_locked


class GraphStore:
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
        self._vdb_dirty: bool = False
        self._entity_vdb: NanoVectorDB | None = None
        self._openai_client: AsyncOpenAI | None = None
        self._embed_lock = asyncio.Lock()
        self._write_lock = _CrossLoopLock()
        # 保护所有 nx/SQL 写入的叶子锁，见 `_mutating`
        self._mutation_lock = threading.RLock()
        self._bm25_dirty: bool = True
        self._bm25_index: BM25Okapi | None = None
        self._bm25_corpus: list[list[str]] | None = None
        self._bm25_docs: list[dict] = []
        self._extraction_status: dict = {
            "pending": 0,
            "running": 0,
            "last_running": None,
            "last_success": None,
            "last_error": None,
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

    # ── initialization ──

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

    @_mutating
    def _add_node(self, nid: str, **attrs) -> None:
        """新节点：**先写 SQL 行、再改 nx**（设计不变量 2）。

        顺序不能反：nx 是 SQL 的投影，反序会让其他线程/协程从 nx 看到尚未落库的
        节点而跳过插入，随后的 `_link_parent` 就会撞外键。
        """
        if self._graph.has_node(nid):
            return
        with self.db.transaction() as conn:
            conn.execute(self._NODE_UPSERT, self._node_row(nid, dict(attrs)))
        self._graph.add_node(nid, **attrs)

    @_mutating
    def _db_delete_node(self, nid: str) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM nodes WHERE id=?", (nid,))
        if self._graph.has_node(nid):
            self._graph.remove_node(nid)

    @_mutating
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
                conn.execute("UPDATE nodes SET data=? WHERE id=?",
                             (json.dumps(data, ensure_ascii=False), nid))

    @_mutating
    def _add_edge(self, src: str, tgt: str, etype: str = "relates_to") -> str:
        key = str(uuid.uuid4().hex)
        self._graph.add_edge(src, tgt, key=key, type=etype)
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO edges(src, dst, type, key) VALUES (?, ?, ?, ?)",
                (src, tgt, etype, key),
            )
        return key

    @_mutating
    def _remove_edge(self, src: str, tgt: str) -> None:
        if self._graph.has_node(src) and self._graph.has_node(tgt) and self._graph.has_edge(src, tgt):
            for key in list(self._graph[src][tgt].keys()):
                self._graph.remove_edge(src, tgt, key)
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM edges WHERE src=? AND dst=?", (src, tgt))
            conn.execute("UPDATE nodes SET parent_id=NULL WHERE id=? AND parent_id=?", (tgt, src))

    def _has_node(self, nid: str) -> bool:
        return self._graph.has_node(nid)

    def _get_node_attr(self, nid: str, key: str, default: Any = None) -> Any:
        return self._graph.nodes[nid].get(key, default) if self._graph.has_node(nid) else default

    def _children(self, parent_id: str) -> list[tuple[str, str]]:
        out = []
        for _, tgt, k, edata in self._graph.out_edges(parent_id, data=True, keys=True):
            if edata and edata.get("type") == TREE_EDGE:
                out.append((tgt, k))
        out.sort(key=lambda x: self._get_node_attr(x[0], "name", x[0]))
        return out

    # ── tree path helpers ──

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

    @_mutating
    def _link_parent(self, child_id: str, parent_id: str) -> None:
        """建立路径树父子关系（唯一真源 = nodes.parent_id），并修正 nx 中的父边。"""
        with self.db.transaction() as conn:
            conn.execute("UPDATE nodes SET parent_id=? WHERE id=?", (parent_id, child_id))
        self._reset_nx_parent_edge(child_id, parent_id)

    @_mutating
    def _reset_nx_parent_edge(self, child_id: str, parent_id: str) -> None:
        """nx 视图里只保留一条指向 parent_id 的 has_child 边。"""
        if not self._graph.has_node(child_id):
            return
        for src, _, key, edata in list(self._graph.in_edges(child_id, data=True, keys=True)):
            if edata and edata.get("type") == TREE_EDGE and src != parent_id:
                self._graph.remove_edge(src, child_id, key)
        if self._graph.has_node(parent_id) and not self._graph.has_edge(parent_id, child_id):
            self._graph.add_edge(parent_id, child_id, key="parent", type=TREE_EDGE)

    def _parent_of(self, nid: str) -> str | None:
        row = self.db.one("SELECT parent_id FROM nodes WHERE id=?", (nid,))
        return row["parent_id"] if row else None

    def _verify_tree(self) -> None:
        """启动时修树：path 派生父缺失则补齐（旧 JSON 图的全量修复语义）。"""
        for r in self.db.all(
            "SELECT id, path, parent_id FROM nodes WHERE path IS NOT NULL AND id <> 'path:/'"
        ):
            expected = _path_id(str(Path(r["path"]).parent).replace("\\", "/"))
            if r["parent_id"] == expected and self._graph.has_node(expected):
                continue
            if not self._graph.has_node(expected):
                self._add_node(expected, type="dir", name=Path(_id_to_path(expected)).name or "/")
            self._link_parent(r["id"], expected)

    def _subtree_rows(self, norm_path: str) -> list[tuple[str, str]]:
        """该路径自身及所有后代的 `(id, path)`，浅 -> 深。

        用 Python 前缀判断而不是 SQL `LIKE '<prefix>%'`：`_` / `%` 是 LIKE 通配符，
        路径里的 `_`（如 `/records/2026-09-15/193539_6b65ab`）会误匹配到同层兄弟，
        让子树改名/复制波及无关节点。
        """
        prefix = norm_path.rstrip("/") + "/"
        rows = self.db.all("SELECT id, path FROM nodes WHERE path IS NOT NULL")
        out = [(r["id"], r["path"]) for r in rows
               if r["path"] == norm_path or r["path"].startswith(prefix)]
        out.sort(key=lambda ip: (len(ip[1]), ip[1]))
        return out

    def _subtree_paths(self, norm_path: str) -> list[str]:
        """返回该路径自身及所有后代路径（浅 -> 深）。"""
        return [path for _, path in self._subtree_rows(norm_path)]

    def _rewrite_subtree_paths(self, conn, old_path: str, new_path: str, now: float) -> None:
        """按前缀改写整棵子树的主键与 `path` 列（单条 UPDATE）。

        不能用 `replace(path, old, new)`：子串替换会把 `/diary/diary_2026.md` 改写成
        `/journal/journal_2026.md`。这里用「常量新前缀 + `substr(旧 path, len(old)+1)`」拼后缀，
        旧名在后代里再次出现也不会被二次替换；`LIKE` 必须转义 `_` / `%` 并声明 `ESCAPE`，
        否则 `/a_b` 会误配 `/axb/...`。主键改名经外键 `ON UPDATE CASCADE`
        自动重指 edges / tags / chunks。
        """
        suffix_start = len(old_path) + 1  # SQLite substr 从 1 开始计数
        conn.execute(
            "UPDATE nodes SET id = 'path:' || ? || substr(path, ?),"
            " path = ? || substr(path, ?), updated_at = ?"
            " WHERE path = ? OR path LIKE ? ESCAPE '\\'",
            (
                new_path, suffix_start,
                new_path, suffix_start,
                now,
                old_path, _escape_like(old_path) + "%",
            ),
        )

    @_mutating
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
        if cp.is_file():
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

    # ── content helpers ──

    def _content_path(self, norm_path: str) -> Path:
        relative = norm_path.strip("/")
        return self.content_dir / relative

    def _count_content_lines(self, norm_path: str) -> int | None:
        """列举元数据用：内容文件 ≤2MB 且 UTF-8 可解码时返回行数，否则 None。"""
        cp = self._content_path(norm_path)
        try:
            if cp.stat().st_size > 2 * 1024 * 1024:
                return None
            text = cp.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        return len(text.splitlines())

    # ── tree operations ──

    async def tree_list(self, scope: str | None = None, *,
                        include_metadata: bool = False,
                        include_line_count: bool = False) -> dict:
        scope_path = _normalize_path(scope or "/")
        log.info("tree_list scope=%s include_metadata=%s include_line_count=%s",
                 scope_path, include_metadata, include_line_count)
        scope_id = _path_id(scope_path)
        if not self._has_node(scope_id):
            return {"path": scope_path, "type": "dir", "children": []}

        def _build_entity(nid: str) -> dict:
            ndata = self._graph.nodes.get(nid, {})
            return {
                "id": nid,
                "name": str(ndata.get("name", nid)),
                "type": "entity",
                "entity_type": str(ndata.get("entity_type", "custom")),
                "description": str(ndata.get("description", "")),
            }

        async def build(nid: str, _depth: int = 0) -> dict:
            if _depth > 200:
                raise RecursionError(f"Tree cycle or depth exceeded at node {nid}")
            ntype = self._get_node_attr(nid, "type", "dir")
            if ntype == "entity":
                return _build_entity(nid)
            rel = _id_to_path(nid)
            if ntype == "file":
                node = {
                    "path": rel,
                    "name": self._get_node_attr(nid, "name", Path(rel).name),
                    "type": "file",
                    "description": self._get_node_attr(nid, "description", ""),
                }
                if include_metadata:
                    meta = self._get_meta(rel)
                    node["updated_at"] = str(meta.get("updated_at") or "")
                    node["tags"] = [str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()]
                    node["chunk_count"] = int(meta.get("chunk_count") or 0)
                    node["indexed"] = bool(meta.get("indexed"))
                    node["declared_by"] = str(meta.get("declared_by") or "")
                    node["score_patch"] = float(meta.get("score_patch") or 0.0)
                    node["content_type"] = str(
                        meta.get("content_type")
                        or self._get_node_attr(nid, "content_type", "")
                        or ""
                    )
                    if include_line_count:
                        node["line_count"] = self._count_content_lines(rel)
                return node
            children = []
            for cid, _ in self._children(nid):
                ctype = self._get_node_attr(cid, "type", "")
                if ctype == "entity":
                    continue
                children.append(await build(cid, _depth + 1))
            children.sort(key=lambda x: (
                {"dir": 0, "file": 1}.get(x.get("type"), 2),
                x.get("name", "").lower(),
            ))
            return {
                "path": rel,
                "name": self._get_node_attr(nid, "name", Path(rel).name or "/"),
                "type": "dir",
                "description": self._get_node_attr(nid, "description", ""),
                "children": children,
            }

        return await build(scope_id)

    def get_entity_children(self, path: str) -> list[dict]:
        norm = _normalize_path(path)
        nid = _path_id(norm)
        if not self._has_node(nid):
            return []
        results = []
        for _, tgt, _k, edata in self._graph.out_edges(nid, data=True, keys=True):
            if not edata:
                continue
            etype = edata.get("type", "")
            if etype not in ("from", TREE_EDGE):
                continue
            ndata = self._graph.nodes.get(tgt)
            if not ndata or ndata.get("type") != "entity":
                continue
            results.append({
                "id": tgt,
                "name": str(ndata.get("name", tgt)),
                "entity_type": str(ndata.get("entity_type", "custom")),
                "description": str(ndata.get("description", "")),
                "edge_type": etype,
            })
        return results

    async def file_read(self, path: str) -> dict:
        # ── memory_read_pre hook ──
        try:
            from faust_backend.runtime import state
            pm = getattr(state, 'plugin_manager', None)
            if pm:
                results = await pm._call_pluggy_hook('memory_read_pre', query=path, filters=None, ctx=None)
                if results:
                    for r in results:
                        if r is not None and isinstance(r, str):
                            path = r
                            break
        except Exception:
            pass

        norm = _normalize_path(path)
        nid = _path_id(norm)
        if not self._has_node(nid):
            log.warning("file_read not_found path=%s", norm)
            raise FileNotFoundError(f"节点不存在: {norm}")
        ntype = self._get_node_attr(nid, "type", "file")
        if ntype == "dir":
            raise FileNotFoundError(f"是目录不是文件: {norm}")
        content = ""
        cp = self._content_path(norm)
        if cp.exists():
            # 检测是否为图片文件后缀，图片文件不读取文本内容
            suffix = norm.rsplit(".", 1)[-1].lower() if "." in norm else ""
            if suffix in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg"):
                content = ""
            else:
                try:
                    content = cp.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    content = ""
        description = self._get_node_attr(nid, "description", "")
        meta = self._get_meta(norm)

        result = {"path": norm, "content": content, "description": description, "meta": meta}

        # ── memory_read_post hook ──
        try:
            from faust_backend.runtime import state as _state
            pm = getattr(_state, 'plugin_manager', None)
            if pm:
                post_results = await pm._call_pluggy_hook('memory_read_post', query=path, results=[result], ctx=None)
                if post_results:
                    for r in post_results:
                        if r is not None:
                            result = r
                            break
        except Exception:
            pass

        log.info("file_read path=%s content_len=%d", norm, len(content))
        return result

    async def file_write(self, path: str, content: str, *,
                         description: str = "",
                         declared_by: str = "agent", index: bool = True,
                         tags: list[str] | None = None) -> dict:
        # ── memory_write_pre hook ──
        try:
            from faust_backend.runtime import state
            pm = getattr(state, 'plugin_manager', None)
            if pm:
                results = await pm._call_pluggy_hook('memory_write_pre', content=content, metadata={"path": path, "description": description, "declared_by": declared_by, "tags": tags}, ctx=None)
                if results:
                    for r in results:
                        if r is not None and isinstance(r, str):
                            content = r
                            break
        except Exception:
            pass

        norm = _normalize_path(path)
        nid = _path_id(norm)
        name = Path(norm).name
        log.info("file_write path=%s declared_by=%s index=%s tags=%s desc_len=%d",
                 norm, declared_by, index, tags or [], len(description))

        async with self._write_lock:
            # 祖先创建必须在写锁内：`_ensure_ancestors` 的「查 nx / 插行 / 连父」三步
            # 不加锁就会与并发写入交错，`_link_parent` 会指向尚未落库的父节点。
            parent_nid = self._ensure_ancestors(norm)
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
                    "SELECT tag FROM tags WHERE node_id=? ORDER BY rowid", (nid,)).fetchall()]
                tags_final = [t.strip() for t in (tags or existing_tags) if t and t.strip()]
                conn.execute("DELETE FROM tags WHERE node_id=?", (nid,))
                conn.executemany("INSERT OR IGNORE INTO tags(node_id, tag) VALUES (?, ?)",
                                 [(nid, t) for t in tags_final])
                conn.execute(
                    "UPDATE nodes SET description=?, declared_by=?, updated_at=?, chunk_count=?,"
                    " indexed=? WHERE id=?",
                    (str(description or ""), declared_by, _now_epoch(),
                     len(chunks), 1 if index else 0, nid),
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
            if chunk_items:
                await self._embed_and_index(chunk_items)
            if self._vdb_dirty:
                self._vdb_dirty = False
                await asyncio.to_thread(self._ensure_vdb().save)

        log.info("file_write done path=%s chunks=%d", norm, len(chunks) if index else 0)
        result = {"path": norm, "meta": meta}

        # ── memory_write_post hook ──
        try:
            from faust_backend.runtime import state as _state
            pm = getattr(_state, 'plugin_manager', None)
            if pm:
                await pm._call_pluggy_hook('memory_write_post', content=content, metadata={"path": path, "description": description, "declared_by": declared_by, "tags": tags}, id=nid, ctx=None)
        except Exception:
            pass

        return result

    async def attachment_write(self, path: str, image_base64: str, *,
                                description: str = "",
                                content_type: str = "image/png",
                                declared_by: str = "agent") -> dict:
        norm = _normalize_path(path)
        nid = _path_id(norm)
        name = Path(norm).name
        log.info("attachment_write path=%s content_type=%s desc_len=%d",
                 norm, content_type, len(description))
        image_bytes = base64.b64decode(image_base64)

        async with self._write_lock:
            parent_nid = self._ensure_ancestors(norm)
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

        if description:
            await self._index_attachment_description(norm, content_type, description)
        self._mark_bm25_dirty()
        if self._vdb_dirty:
            self._vdb_dirty = False
            await asyncio.to_thread(self._ensure_vdb().save)

        log.info("attachment_write done path=%s size=%d desc_len=%d",
                 norm, len(image_bytes), len(description))
        return {"path": norm, "description": description, "content_type": content_type}

    async def _index_attachment_description(self, norm: str, content_type: str, description: str) -> None:
        try:
            chunk_text = f"[image:{content_type}] {description}"
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
            if self._vdb_dirty:
                self._vdb_dirty = False
                await asyncio.to_thread(self._ensure_vdb().save)
        except Exception as e:
            log.error("_index_attachment_description failed path=%s: %s", norm, e)

    async def attachment_read(self, path: str) -> dict:
        norm = _normalize_path(path)
        nid = _path_id(norm)
        if not self._has_node(nid):
            log.warning("attachment_read not_found path=%s", norm)
            raise FileNotFoundError(f"节点不存在: {norm}")
        content_type = self._get_node_attr(nid, "content_type", "image/png")
        description = self._get_node_attr(nid, "description", "")
        cp = self._content_path(norm)
        if not cp.exists():
            raise FileNotFoundError(f"文件不存在: {norm}")
        image_bytes = cp.read_bytes()
        content_base64 = base64.b64encode(image_bytes).decode("ascii")
        log.info("attachment_read path=%s content_type=%s size=%d",
                 norm, content_type, len(image_bytes))
        return {
            "path": norm,
            "content_base64": content_base64,
            "content_type": content_type,
            "description": description,
        }

    async def file_delete(self, path: str) -> dict:
        norm = _normalize_path(path)
        nid = _path_id(norm)
        log.info("file_delete path=%s", norm)
        if not self._has_node(nid):
            raise FileNotFoundError(f"节点不存在: {norm}")

        async with self._write_lock:
            chunk_ids: list[str] = []
            with self.db.transaction() as conn:
                cp = self._content_path(norm)
                if cp.exists():
                    if cp.is_dir():
                        # 目录节点：递归删除其内容目录（含残留子目录/文件）
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
            if self._vdb_dirty:
                self._vdb_dirty = False
                await asyncio.to_thread(self._ensure_vdb().save)
        return {"path": norm}

    async def file_delete_tree(self, path: str) -> dict:
        """递归删除目录（含所有子文件/子目录），或删除单个文件。

        先深层后浅层逐个调用 file_delete，最后删除目录本身。
        """
        norm = _normalize_path(path)
        nid = _path_id(norm)
        log.info("file_delete_tree path=%s", norm)
        if not self._has_node(nid):
            raise FileNotFoundError(f"节点不存在: {norm}")
        ntype = self._get_node_attr(nid, "type", "file")
        if ntype != "dir":
            return await self.file_delete(norm)

        tree = await self.tree_list(norm)
        descendants: list[str] = []

        def collect(node: dict) -> None:
            p = str(node.get("path") or "").strip("/")
            if p and f"/{p}" != norm:
                descendants.append(f"/{p}")
            for child in node.get("children", []):
                collect(child)

        collect(tree)
        # 先删深层，再删浅层（子先于父）
        for cp in sorted(set(descendants), key=lambda s: s.count("/"), reverse=True):
            await self.file_delete(cp)
        await self.file_delete(norm)
        return {"path": norm}

    async def file_rename(self, path: str, new_name: str) -> dict:
        """重命名文件或目录。目录会递归重命名所有子节点。"""
        norm = _normalize_path(path)
        parent = str(Path(norm).parent)
        new_path = _normalize_path(str(Path(parent) / new_name)) if parent != "/" else f"/{new_name}"
        new_path = _normalize_path(new_path)
        if norm == new_path:
            return {"path": norm, "new_path": new_path}  # no-op
        nid = _path_id(norm)
        new_nid = _path_id(new_path)
        if not self._has_node(nid):
            raise FileNotFoundError(f"节点不存在: {norm}")
        if self._has_node(new_nid):
            raise FileExistsError(f"目标路径已存在: {new_path}")
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
                    self._rewrite_subtree_paths(conn, norm, new_path, _now_epoch())
                    conn.execute("UPDATE nodes SET name=? WHERE id=?", (new_name, new_nid))
                self._mark_bm25_dirty()
            self._relabel_nx(norm, new_path)
            if self._graph.has_node(new_nid):
                self._graph.nodes[new_nid]["name"] = new_name
                self._graph.nodes[new_nid]["updated_at"] = _utc_iso()
            self._reset_nx_parent_edge(new_nid, _path_id(str(Path(new_path).parent)))
        log.info("file_rename path=%s -> new_path=%s type=%s", norm, new_path, ntype)
        return {"path": norm, "new_path": new_path, "type": ntype}

    async def file_copy(self, path: str, dest_path: str) -> dict:
        """复制文件或目录到目标路径。"""
        norm = _normalize_path(path)
        dest = _normalize_path(dest_path)
        nid = _path_id(norm)
        dest_nid = _path_id(dest)
        if not self._has_node(nid):
            raise FileNotFoundError(f"源节点不存在: {norm}")
        if self._has_node(dest_nid):
            raise FileExistsError(f"目标路径已存在: {dest}")
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
            if self._vdb_dirty:
                self._vdb_dirty = False
                await asyncio.to_thread(self._ensure_vdb().save)
        log.info("file_copy path=%s -> dest=%s type=%s", norm, dest, ntype)
        return {"path": norm, "dest": dest, "type": ntype}

    async def file_move(self, path: str, dest_dir: str) -> dict:
        """移动文件或目录到目标目录。"""
        norm = _normalize_path(path)
        dest = _normalize_path(str(Path(dest_dir) / Path(norm).name))
        if norm == dest:
            return {"path": norm, "new_path": dest}
        # implement as read + write + delete with dest parent
        nid = _path_id(norm)
        if not self._has_node(nid):
            raise FileNotFoundError(f"节点不存在: {norm}")
        dest_nid = _path_id(dest)
        if self._has_node(dest_nid):
            raise FileExistsError(f"目标路径已存在: {dest}")
        ntype = self._get_node_attr(nid, "type", "file")
        async with self._write_lock:
            old_cp = self._content_path(norm)
            new_cp = self._content_path(dest)
            if old_cp.exists():
                new_cp.parent.mkdir(parents=True, exist_ok=True)
                old_cp.rename(new_cp)
            with self.db.transaction() as conn:
                self._ensure_ancestors(dest)
                if ntype == "file":
                    conn.execute(
                        "UPDATE nodes SET id=?, path=?, updated_at=? WHERE id=?",
                        (dest_nid, dest, _now_epoch(), nid),
                    )
                else:
                    self._rewrite_subtree_paths(conn, norm, dest, _now_epoch())
                new_parent = _path_id(str(Path(dest).parent))
                conn.execute("UPDATE nodes SET parent_id=? WHERE id=?", (new_parent, dest_nid))
                self._mark_bm25_dirty()
            self._relabel_nx(norm, dest)
            self._reset_nx_parent_edge(dest_nid, new_parent)
        log.info("file_move path=%s -> dest=%s type=%s", norm, dest, ntype)
        return {"path": norm, "new_path": dest, "type": ntype}

    async def mkdir(self, path: str, description: str = "") -> dict:
        norm = _normalize_path(path)
        nid = _path_id(norm)
        log.info("mkdir path=%s", norm)
        async with self._write_lock:
            parent_nid = self._ensure_ancestors(norm)
            if not self._has_node(nid):
                self._add_node(nid, type="dir", name=Path(norm).name,
                               description=str(description or ""))
            self._link_parent(nid, parent_nid)
        return {"path": norm, "type": "dir"}

    # ── meta（SQL） ──

    def _get_meta(self, norm_path: str) -> dict:
        """返回与旧 meta.json 同键的字典（对外时间仍是 ISO 串）。

        tags 按写入顺序（rowid）返回，与旧 meta.json 的数组顺序一致。
        """
        nid = _path_id(norm_path)
        row = self.db.one(
            "SELECT declared_by, description, updated_at, chunk_count, indexed, score_patch,"
            " score_patch_updated_at, managed_by, content_type FROM nodes WHERE id=?", (nid,)
        )
        tags = [r["tag"] for r in self.db.all(
            "SELECT tag FROM tags WHERE node_id=? ORDER BY rowid", (nid,))]
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
        ids = [r["chunk_id"] for r in self.db.all(
            "SELECT chunk_id FROM chunks WHERE node_id=?", (nid,))]
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

    # ── extraction status ──

    def register_extraction(self, doc_path: str) -> None:
        """注册一个正在进行的实体提取任务。"""
        self._extraction_status["pending"] = max(0, self._extraction_status.get("pending", 0)) + 1
        self._extraction_status["running"] = max(0, self._extraction_status.get("running", 0)) + 1
        self._extraction_status["last_running"] = doc_path
        log.info("register_extraction doc_path=%s", doc_path)

    def complete_extraction(self, doc_path: str, success: bool = True, error: str | None = None) -> None:
        """完成一个实体提取任务。"""
        self._extraction_status["pending"] = max(0, self._extraction_status.get("pending", 1) - 1)
        self._extraction_status["running"] = max(0, self._extraction_status.get("running", 1) - 1)
        if success:
            self._extraction_status["last_success"] = doc_path
            self._extraction_status["last_error"] = None
        else:
            self._extraction_status["last_error"] = f"{doc_path}: {error}" if error else doc_path
        if self._extraction_status["running"] <= 0:
            self._extraction_status["last_running"] = None
        log.info("complete_extraction doc_path=%s success=%s error=%s", doc_path, success, error)

    def get_extraction_status(self) -> dict:
        """获取当前实体提取状态。"""
        return dict(self._extraction_status)


    # ── tags / score_patch ──

    @_mutating
    async def set_tags(self, path: str, tags: list[str], managed_by: str | None = None) -> dict:
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
            # 纯元数据写入不刷 `updated_at`：否则维护 Agent 自己每轮打的标签都会把
            # 这些文件重新算进下一次 changed-nodes，真增量被自己的写操作淹没。
            if managed_by is not None:
                conn.execute("UPDATE nodes SET managed_by=? WHERE id=?", (str(managed_by), nid))
        self._set_node_attr(nid, tags=clean)
        return {"path": norm, "meta": self._get_meta(norm)}

    @_mutating
    async def set_score_patch(self, path: str, score_patch: float) -> dict:
        norm = _normalize_path(path)
        log.info("set_score_patch path=%s score_patch=%s", norm, score_patch)
        patch = float(score_patch)
        if not math.isfinite(patch):
            raise ValueError("score_patch 必须是有限数值")
        if patch < MIN_SCORE_PATCH or patch > MAX_SCORE_PATCH:
            raise ValueError(f"score_patch 超出范围 [{MIN_SCORE_PATCH}, {MAX_SCORE_PATCH}]")
        nid = _path_id(norm)
        with self.db.transaction() as conn:
            # 同 set_tags：权重是元数据，不刷 `updated_at`，避免污染 changed-nodes。
            conn.execute(
                "UPDATE nodes SET score_patch=?, score_patch_updated_at=? WHERE id=?",
                (patch, _now_epoch(), nid),
            )
        self._set_node_attr(nid, score_patch=patch)
        return {"path": norm, "meta": self._get_meta(norm)}


    # ── advanced search ──

    async def advanced_search(self, query: str | None = None,
                               tags: list[str] | None = None,
                               scope: str | None = None,
                               date_from: str | None = None,
                               date_to: str | None = None,
                               declared_by: str | None = None,
                               content_type: str | None = None,
                               top_k: int = 20,
                               sort_by: str = "relevance",
                               sort_order: str = "desc",
                               tag_logic: str = "AND") -> list[dict]:
        """多条件组合搜索。查询可以为空（仅按条件筛选）。"""
        scope_prefix = _normalize_path(scope or "").strip("/")
        scope_prefix = f"/{scope_prefix}/" if scope_prefix else ""
        required_tags = {t.casefold() for t in (tags or [])} if tags else set()
        need_text_query = str(query or "").strip() if query else ""

        # Collect candidate rows from SQL（唯一真源）
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
            # scope filter
            if scope_prefix and not (p.startswith(scope_prefix) or p == scope_prefix.rstrip("/")):
                continue
            # tags filter
            meta_tags = [t for t in str(row["tag_csv"] or "").split(",") if t]
            if required_tags:
                tag_set = {t.casefold() for t in meta_tags}
                if tag_logic == "AND":
                    if not required_tags.issubset(tag_set):
                        continue
                else:  # OR
                    if not required_tags.intersection(tag_set):
                        continue
            # date filter
            updated = epoch_to_iso(row["updated_at"])
            updated_date = updated[:10] if updated else ""
            if date_from and updated_date and updated_date < date_from:
                continue
            if date_to and updated_date and updated_date > date_to:
                continue
            # declared_by filter
            if declared_by and str(row["declared_by"] or "") != declared_by:
                continue
            # content_type filter
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
            # text match score
            if need_text_query:
                text_lower = need_text_query.lower()
                score = 0.0
                if text_lower in Path(p).name.lower():
                    score = 1.0
                if text_lower in candidate["description"].lower():
                    score = max(score, 0.8)
                # also check content file
                cp = self._content_path(p)
                if cp.exists() and score < 0.5:
                    try:
                        content = cp.read_text(encoding="utf-8", errors="ignore")[:5000]
                        if text_lower in content.lower():
                            score = max(score, 0.6)
                    except Exception:
                        pass
                if score == 0:
                    continue  # text query present but no match
                candidate["score"] = score
            else:
                candidate["score"] = 0.0
                # no text query - path, tag match is enough
            candidates.append(candidate)

        if sort_by == "relevance" and need_text_query:
            candidates.sort(key=lambda x: x.get("score", 0), reverse=True)
        elif sort_by == "updated_at":
            candidates.sort(key=lambda x: x.get("updated_at", ""), reverse=(sort_order != "asc"))
        elif sort_by == "created_at":
            # fallback to updated_at (created_at not tracked in meta)
            candidates.sort(key=lambda x: x.get("updated_at", ""), reverse=(sort_order != "asc"))

        # Add line_count
        for c in candidates[:top_k]:
            c["line_count"] = self._count_lines(c["path"])

        return candidates[:top_k]

    # ── entity detail ──

    def get_entity_detail(self, entity_id: str) -> dict | None:
        """获取单个实体的完整信息。"""
        if not self._has_node(entity_id):
            return None
        ndata = self._graph.nodes[entity_id]
        node_type = str(ndata.get("type", ""))
        if node_type != "entity":
            if _is_path_id(entity_id):
                path = _id_to_path(entity_id)
                rel_count = 0
                for _ in self._graph.edges(entity_id):
                    rel_count += 1
                for _ in self._graph.in_edges(entity_id):
                    rel_count += 1
                return {
                    "id": entity_id,
                    "name": str(ndata.get("name", path or "/")),
                    "entity_type": node_type or "path",
                    "description": str(ndata.get("description", "")),
                    "properties": dict(ndata.get("properties", {})),
                    "kb_refs": [path],
                    "linked_files": [path],
                    "relations_count": rel_count,
                    "created_at": str(ndata.get("created_at", ndata.get("updated_at", ""))),
                }
            return None
        # count relations
        rel_count = 0
        for _ in self._graph.edges(entity_id):
            rel_count += 1
        kb_refs = list(ndata.get("kb_refs", []))
        # get linked file paths
        linked_files = []
        for ref in kb_refs:
            nref = _normalize_path(ref)
            if nref != ref:
                linked_files.append(nref)
            else:
                linked_files.append(ref)
        return {
            "id": entity_id,
            "name": str(ndata.get("name", "")),
            "entity_type": str(ndata.get("entity_type", "custom")),
            "description": str(ndata.get("description", "")),
            "properties": dict(ndata.get("properties", {})),
            "kb_refs": kb_refs,
            "linked_files": linked_files,
            "relations_count": rel_count,
            "created_at": str(ndata.get("created_at", "")),
        }

    # ── BM25 index ──

    async def _ensure_bm25_index(self) -> None:
        if self._bm25_index is not None and not self._bm25_dirty:
            return
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

        if not docs:
            self._bm25_index = None
            self._bm25_docs = []
            self._bm25_corpus = None
            self._mark_bm25_clean()
            return

        tokenized = await jieba_tokenize_batch([d["text"] for d in docs])
        self._bm25_corpus = tokenized
        self._bm25_index = BM25Okapi(tokenized)
        self._bm25_docs = docs
        self._mark_bm25_clean()

    async def _bm25_search(self, query: str, top_k: int) -> list[dict]:
        await self._ensure_bm25_index()
        if self._bm25_index is None or not self._bm25_docs:
            return []
        query_tokens = await jieba_tokenize(query)
        scores = self._bm25_index.get_scores(query_tokens)
        path_scores: dict[str, list[float]] = {}
        for doc, score in zip(self._bm25_docs, scores):
            path_scores.setdefault(doc["path"], []).append(score)
        results = []
        for path, sc_list in path_scores.items():
            results.append({"path": path, "score": sum(sc_list) / len(sc_list), "_source": "bm25"})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    # ── vector index ──

    def _ensure_vdb(self) -> NanoVectorDB:
        if self._vdb is None:
            try:
                self._vdb = NanoVectorDB(EMBED_DIM, storage_file=str(self.index_file))
            except Exception:
                if self.index_file.exists():
                    self.index_file.unlink()
                self._vdb = NanoVectorDB(EMBED_DIM, storage_file=str(self.index_file))
        return self._vdb

    def _ensure_entity_vdb(self) -> NanoVectorDB:
        if self._entity_vdb is None:
            self.index_dir.mkdir(parents=True, exist_ok=True)
            self._entity_vdb = NanoVectorDB(EMBED_DIM, storage_file=str(self.entity_index_file))
        return self._entity_vdb

    def _get_openai(self) -> AsyncOpenAI:
        if self._openai_client is None:
            from faust_backend.runtime import state as runtime_state
            from faust_backend.provider import get_main_credentials
            _, _fallback_key, _ = get_main_credentials(runtime_state.get_model_providers())
            api_key = conf.EMBED_API_KEY or _fallback_key
            base_url = conf.EMBED_API_BASE or "https://api.openai.com/v1"
            self._openai_client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        return self._openai_client

    async def _embed_texts(self, texts: list[str], max_batch_size: int = 8) -> np.ndarray:
        if not texts:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)
        client = self._get_openai()
        all_embeddings: list[list[float]] = []
        for batch in self.chunk_list(texts, chunk_size=max_batch_size):
            response = await client.embeddings.create(model=EMBED_MODEL, input=batch, dimensions=EMBED_DIM)
            if response is None or response.data is None:
                log.warning("Embedding API returned None for batch, skipping")
                continue
            all_embeddings.extend([item.embedding for item in response.data])
        if not all_embeddings:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)
        return np.array(all_embeddings, dtype=np.float32)
    
    def chunk_list(self, lst, chunk_size=10):
        """返回一个列表，其中每个元素是大小为 chunk_size 的子列表"""
        return [lst[i:i+chunk_size] for i in range(0, len(lst), chunk_size)]
    
    async def _embed_and_index(self, chunk_items: list[dict]) -> None:
        if not chunk_items:
            return
        if getattr(conf, 'BM25_ONLY', False):
            return
        texts = [c["text"] for c in chunk_items]
        embeddings = await self._embed_texts(texts)
        if len(embeddings) != len(chunk_items):
            log.critical("embedding count mismatch: expected %d, got %d", len(chunk_items), len(embeddings))
            raise ValueError(f"embedding count mismatch: expected {len(chunk_items)}, got {len(embeddings)}")
        if embeddings.ndim != 2 or embeddings.shape[1] != EMBED_DIM:
            log.critical("embedding dimension mismatch: expected %d, got %s", EMBED_DIM, getattr(embeddings, 'shape', None))
            raise ValueError(f"embedding dimension mismatch: expected {EMBED_DIM}, got {getattr(embeddings, 'shape', None)}")
        vdb = self._ensure_vdb()
        rows = []
        for item, vec in zip(chunk_items, embeddings):
            rows.append({
                "__id__": item["chunk_id"],
                "__vector__": np.asarray(vec, dtype=np.float32),
                "node_path": item["node_path"],
                "scope_prefix": item["scope_prefix"],
                "chunk_index": item["chunk_index"],
                "text": item["text"],
                "text_preview": item["text_preview"],
            })
        vdb.upsert(rows)
        self._vdb_dirty = True

    async def _delete_chunk_ids(self, chunk_ids: list[str]) -> int:
        if not chunk_ids or not self.index_file.exists():
            return 0
        try:
            vdb = self._ensure_vdb()
            vdb.delete(chunk_ids)
            self._vdb_dirty = True
            return len(chunk_ids)
        except Exception:
            return 0

    # ── entity / relation operations ──

    @_mutating
    def entity_add(self, name: str, entity_type: str = "custom",
                   description: str = "",
                   properties: dict | None = None, kb_refs: list[str] | None = None,
                   name_embedding: list[float] | None = None) -> str:
        eid = _ent_id()
        desc = str(description or "")
        entity_path = f"/entities/{eid}.md"
        entity_content = f"# {name}\n\n{desc}\n" if desc else f"# {name}\n"
        cp = self._content_path(entity_path)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(entity_content, encoding="utf-8")
        all_refs = list(kb_refs or [])
        if entity_path not in all_refs:
            all_refs.append(entity_path)
        self._add_node(eid, type="entity", entity_type=entity_type,
                       name=name, description=desc,
                       properties=dict(properties or {}),
                       kb_refs=all_refs, created_at=_utc_iso(),
                       updated_at=_utc_iso())
        if name_embedding:
            vdb = self._ensure_entity_vdb()
            vdb.upsert([{
                "__id__": eid,
                "__vector__": np.asarray(name_embedding, dtype=np.float32),
            }])
            vdb.save()
        log.info("entity_add name=%s type=%s eid=%s desc_len=%d refs=%d",
                 name, entity_type, eid[:16], len(desc), len(all_refs))
        return eid

    @_mutating
    def entity_delete(self, entity_id: str) -> bool:
        if not self._has_node(entity_id):
            return False
        if self._get_node_attr(entity_id, "type") != "entity":
            return False
        if self._entity_vdb is not None or self.entity_index_file.exists():
            vdb = self._ensure_entity_vdb()
            vdb.delete([entity_id])
            vdb.save()
        self._db_delete_node(entity_id)
        log.info("entity_delete eid=%s", entity_id[:16])
        return True

    @_mutating
    def entity_merge(self, keep_id: str, absorb_id: str) -> dict:
        """把 absorb 实体并入 keep 实体，随后删除 absorb。

        合并规则：
        - description/entity_type 空则取被吸收者；properties 以 keep 为准、缺失键从 absorb 补；
          kb_refs 取并集（顺序：keep 原有 + absorb 新增）。
        - absorb 的入射/出射边全部改指到 keep：改指后成为自环的边丢弃，
          (src, dst, type) 与既有边重复的边丢弃。
        - absorb 的节点行、内容文件 `/entities/<id>.md`、实体名向量一并删除。
        """
        keep_id = str(keep_id or "").strip()
        absorb_id = str(absorb_id or "").strip()
        if not keep_id or not absorb_id or keep_id == absorb_id:
            return {"ok": False, "error": "keep_id 与 absorb_id 必须非空且互不相同"}
        for eid in (keep_id, absorb_id):
            if not self._has_node(eid):
                return {"ok": False, "error": f"节点不存在: {eid}"}
            if self._get_node_attr(eid, "type") != "entity":
                return {"ok": False, "error": f"节点不是实体: {eid}"}
        log.info("entity_merge keep=%s absorb=%s", keep_id[:16], absorb_id[:16])

        keep_attrs = dict(self._graph.nodes[keep_id])
        absorb_attrs = dict(self._graph.nodes[absorb_id])

        merged_props = dict(absorb_attrs.get("properties") or {})
        merged_props.update(keep_attrs.get("properties") or {})
        merged_refs = list(keep_attrs.get("kb_refs") or [])
        absorb_self_ref = f"/entities/{absorb_id}.md"
        for ref in absorb_attrs.get("kb_refs") or []:
            if ref == absorb_self_ref or ref in merged_refs:
                continue
            merged_refs.append(ref)
        entity_type = str(keep_attrs.get("entity_type") or "custom")
        if entity_type == "custom":
            entity_type = str(absorb_attrs.get("entity_type") or entity_type)
        description = str(keep_attrs.get("description") or "") or str(absorb_attrs.get("description") or "")

        incident: set[tuple[str, str, str]] = set()
        for u, v, _k, edata in self._graph.in_edges(absorb_id, data=True, keys=True):
            incident.add((u, v, str((edata or {}).get("type") or "relates_to")))
        for u, v, _k, edata in self._graph.out_edges(absorb_id, data=True, keys=True):
            incident.add((u, v, str((edata or {}).get("type") or "relates_to")))

        existing: set[tuple[str, str, str]] = set()
        for u, v, _k, edata in self._graph.edges(data=True, keys=True):
            if absorb_id in (u, v):
                continue
            existing.add((u, v, str((edata or {}).get("type") or "relates_to")))

        rewired: list[tuple[str, str, str, str]] = []
        dropped_duplicates = 0
        dropped_self_loops = 0
        for u, v, etype in incident:
            src = keep_id if u == absorb_id else u
            dst = keep_id if v == absorb_id else v
            if src == dst:
                dropped_self_loops += 1
                continue
            if (src, dst, etype) in existing:
                dropped_duplicates += 1
                continue
            existing.add((src, dst, etype))
            rewired.append((src, dst, etype, uuid.uuid4().hex))

        with self.db.transaction() as conn:
            self._set_node_attr(keep_id, entity_type=entity_type, description=description,
                                properties=merged_props, kb_refs=merged_refs,
                                updated_at=_utc_iso())
            conn.execute("DELETE FROM edges WHERE src=? OR dst=?", (absorb_id, absorb_id))
            for src, dst, etype, key in rewired:
                self._graph.add_edge(src, dst, key=key, type=etype)
                conn.execute(
                    "INSERT OR REPLACE INTO edges(src, dst, type, key) VALUES (?, ?, ?, ?)",
                    (src, dst, etype, key),
                )
            self._db_delete_node(absorb_id)

        self._content_path(f"/entities/{absorb_id}.md").unlink(missing_ok=True)
        if self._entity_vdb is not None or self.entity_index_file.exists():
            vdb = self._ensure_entity_vdb()
            vdb.delete([absorb_id])
            vdb.save()
        log.info("entity_merge done keep=%s absorb=%s rewired=%d dup=%d loops=%d",
                 keep_id[:16], absorb_id[:16], len(rewired), dropped_duplicates, dropped_self_loops)
        return {
            "ok": True,
            "keep_id": keep_id,
            "absorb_id": absorb_id,
            "keep_name": str(keep_attrs.get("name") or ""),
            "absorb_name": str(absorb_attrs.get("name") or ""),
            "rewired_edges": len(rewired),
            "dropped_duplicate_edges": dropped_duplicates,
            "dropped_self_loops": dropped_self_loops,
        }

    def entity_search(self, query: str, type_filter: str | None = None, top_k: int = 20) -> list[dict]:
        q = str(query or "").strip().lower()
        results = []
        for nid, ndata in self._graph.nodes(data=True):
            if ndata.get("type") != "entity":
                continue
            if type_filter and ndata.get("entity_type") != type_filter:
                continue
            name = str(ndata.get("name", "")).lower()
            if q and q not in name:
                continue
            results.append({
                "id": nid,
                "name": ndata.get("name", ""),
                "entity_type": ndata.get("entity_type", "custom"),
                "description": ndata.get("description", ""),
                "properties": dict(ndata.get("properties", {})),
                "kb_refs": list(ndata.get("kb_refs", [])),
                "created_at": ndata.get("created_at", ""),
            })
        results.sort(key=lambda x: (0 if x["name"].lower().startswith(q) else 1, x["name"]))
        out = results[:top_k]
        log.info("entity_search query=%s filter=%s hits=%d", query, type_filter, len(out))
        return out

    def entity_iter(self) -> list[dict]:
        results = []
        for nid, ndata in self._graph.nodes(data=True):
            if ndata.get("type") != "entity":
                continue
            results.append({
                "id": nid,
                "name": ndata.get("name", ""),
                "entity_type": ndata.get("entity_type", "custom"),
                "description": ndata.get("description", ""),
                "properties": dict(ndata.get("properties", {})),
                "kb_refs": list(ndata.get("kb_refs", [])),
                "created_at": ndata.get("created_at", ""),
            })
        log.info("entity_iter count=%d", len(results))
        return results

    @_mutating
    def relation_add(self, source_id: str, target_id: str,
                     rel_type: str = "relates_to") -> str:
        # 直接插边会撞外键，只抛 "FOREIGN KEY constraint failed"，调用方看不出该修什么
        for nid in (str(source_id or ""), str(target_id or "")):
            if not self._has_node(nid):
                raise FileNotFoundError(f"节点不存在: {nid}")
        key = self._add_edge(source_id, target_id, rel_type)
        log.info("relation_add src=%s tgt=%s type=%s key=%s",
                 source_id[:16], target_id[:16], rel_type, key[:8])
        return key

    @_mutating
    def relation_remove(self, source_id: str, target_id: str) -> None:
        self._remove_edge(source_id, target_id)
        log.info("relation_remove src=%s tgt=%s", source_id[:16], target_id[:16])

    def relation_iter(self) -> list[dict]:
        results = []
        for src, tgt, k, edata in self._graph.edges(data=True, keys=True):
            etype = str(edata.get("type", "relates_to")) if edata else "relates_to"
            results.append({
                "source": src, "target": tgt,
                "type": etype, "key": str(k),
            })
        return results

    def get_neighbors(self, entity_id: str, depth: int = 1) -> list[dict]:
        """返回 entity_id 的 depth 跳邻居（附每条边的类型与方向）。

        回归：旧实现只在「展开」节点时把节点记入 `seen`，最后一层发散出的 frontier
        从不并入，于是 depth=1（工具默认值）恒返回 []，depth=d 实际只覆盖 1..d-1 层。
        """
        if not self._has_node(entity_id):
            log.warning("get_neighbors not_found eid=%s", entity_id[:16])
            return []
        seen: set[str] = set()
        relations: dict[str, set[tuple[str, str]]] = {}
        current: set[str] = {entity_id}
        for _ in range(max(0, int(depth))):
            nxt: set[str] = set()
            for nid in current:
                incident = chain(
                    ((tgt, "out", edata) for _s, tgt, _k, edata in
                     self._graph.out_edges(nid, data=True, keys=True)),
                    ((src, "in", edata) for src, _t, _k, edata in
                     self._graph.in_edges(nid, data=True, keys=True)),
                )
                for other, direction, edata in incident:
                    if other == entity_id or other in seen:
                        continue
                    etype = str((edata or {}).get("type") or "relates_to")
                    relations.setdefault(other, set()).add((direction, etype))
                    nxt.add(other)
            seen |= nxt
            current = nxt
            if not current:
                break
        results = []
        for nid in sorted(seen, key=lambda x: (_id_to_path(x) if _is_path_id(x) else x)):
            ndata = self._graph.nodes[nid]
            ntype = ndata.get("type", "unknown")
            item: dict[str, Any] = {
                "id": nid, "name": ndata.get("name", ""),
                "entity_type": ndata.get("entity_type", ntype) if ntype == "entity" else ntype,
                "relations": [
                    {"type": etype, "direction": direction}
                    for direction, etype in sorted(relations.get(nid, ()))
                ],
            }
            if ntype == "entity":
                item["description"] = ndata.get("description", "")
                item["path_ref"] = None
            elif ntype in ("file", "dir"):
                item["path_ref"] = _id_to_path(nid)
            else:
                continue
            results.append(item)
        log.info("get_neighbors eid=%s depth=%d hits=%d", entity_id[:16], depth, len(results))
        return results

    # ── semantic entity dedup ──

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
        for (eid, _), vec in zip(missing, vecs):
            rows.append({"__id__": eid, "__vector__": np.asarray(vec, dtype=np.float32)})
        if rows:
            vdb.upsert(rows)
            vdb.save()

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

    # ── top-level search (hybrid + graph + 2-hop) ──

    async def search(self, query: str, scope: str | None = None,
                     top_k: int = 8, return_mode: str = "snippets",
                     tags: list[str] | None = None, use_graph: bool = True) -> list[dict]:
        q = str(query or "").strip()
        if not q:
            return []

        scope_prefix = _normalize_path(scope or "").strip("/")
        scope_prefix = f"/{scope_prefix}/" if scope_prefix else ""

        hybrid_results = await self._hybrid_search(q, scope_prefix, tags, top_k)
        graph_results = self._graph_search(q, top_k) if use_graph else []

        seen: dict[str, dict] = {}
        for item in hybrid_results:
            seen[item["path"]] = item
        for item in graph_results:
            p = item["path"]
            if p in seen:
                seen[p]["_source"] = "hybrid+graph"
                seen[p]["score"] = max(seen[p]["score"], item["score"])
            else:
                seen[p] = item

        # 2-hop expansion for matched paths（图谱通道：经实体/来源边把相关文件补进来）
        required_tags = {t.casefold() for t in (tags or [])}
        extra: dict[str, dict] = {}
        for p in list(seen.keys()):
            nid = _path_id(p)
            if self._has_node(nid):
                nb = self.get_neighbors(nid, depth=2)
                for n in nb:
                    pref = n.get("path_ref")
                    # 只补文件：邻居里必然含父/祖父目录（has_child 链），目录没有内容
                    if not pref or n.get("entity_type") != "file":
                        continue
                    # 邻居可能落在 scope 之外（父目录链、跨目录的来源文件），
                    # 必须与主检索同一套过滤，否则带 scope 的搜索会漏出别的目录
                    if scope_prefix and not (pref.startswith(scope_prefix)
                                             or pref == scope_prefix.rstrip("/")):
                        continue
                    if pref in seen or pref in extra:
                        continue
                    meta = self._get_meta(pref)
                    if required_tags and not required_tags.issubset(
                            {str(t).casefold() for t in meta.get("tags", [])}):
                        continue
                    patch = float(meta.get("score_patch", 0.0))
                    extra[pref] = {
                        "path": pref,
                        "raw_score": 0,
                        "score_patch": patch,
                        "score": patch,
                        "tags": meta.get("tags", []),
                        "snippet": "",
                        "_source": "2hop",
                    }

        seen.update(extra)
        merged_list = list(seen.values())
        reranked = await self._rerank(q, merged_list, top_k)
        sorted_items = sorted(reranked, key=lambda x: x.get("score", 0), reverse=True)
        results = sorted_items[:top_k * 2]

        for item in results:
            if return_mode == "full" and not item.get("content"):
                try:
                    r = await self.file_read(item["path"])
                    item["content"] = r.get("content", "")
                except Exception:
                    item["content"] = ""

        return results

    # ── hybrid search (vector + BM25) ──

    async def _vector_search(self, query: str, scope_prefix: str,
                             tags: list[str] | None, top_k: int) -> list[dict]:
        if not self.index_file.exists():
            return []
        emb = await self._embed_texts([query])
        vdb = self._ensure_vdb()
        qv = emb[0].tolist()
        hits = vdb.query(query=qv, top_k=max(top_k * 3, 10))

        if isinstance(hits, dict):
            raw = [hits]
        else:
            raw = list(hits or [])

        required_tags = {t.casefold() for t in (tags or [])}
        best: dict[str, dict] = {}
        for item in raw:
            hit = dict(item) if isinstance(item, dict) else {}
            node_path = str(hit.get("node_path") or "")
            if not node_path:
                continue
            node_path = _normalize_path(node_path)
            if scope_prefix and not (node_path.startswith(scope_prefix) or node_path == scope_prefix.rstrip("/")):
                continue
            meta = self._get_meta(node_path)
            normalized_tags = [t for t in meta.get("tags", []) if t]
            if required_tags:
                current_set = {t.casefold() for t in normalized_tags}
                if not required_tags.issubset(current_set):
                    continue
            metrics = hit.get("__metrics__")
            if isinstance(metrics, dict):
                score = float(metrics.get("cosine_similarity", metrics.get("score", 0)))
            elif metrics is not None:
                score = float(metrics)
            else:
                raw_score: Any = hit.get("__score__", hit.get("score", 0))
                score = float(raw_score)
            if not math.isfinite(score):
                score = 0.0
            patch = float(meta.get("score_patch", 0.0))
            final = score + patch
            if not math.isfinite(final):
                final = 0.0

            current = best.get(node_path)
            if current is None or current["score"] < final:
                best[node_path] = {
                    "path": node_path,
                    "raw_score": score,
                    "score_patch": patch,
                    "score": final,
                    "tags": normalized_tags,
                    "snippet": str(hit.get("text_preview", "")),
                    "_source": "vector",
                }

        return sorted(best.values(), key=lambda x: x["score"], reverse=True)

    async def _hybrid_search(self, query: str, scope_prefix: str,
                              tags: list[str] | None, top_k: int) -> list[dict]:
        if getattr(conf, 'BM25_ONLY', False):
            bm25_results = await self._bm25_search(query, top_k * 2)
            filtered: list[dict] = []
            required_tags = {t.casefold() for t in (tags or [])}
            for item in bm25_results:
                p = item["path"]
                if scope_prefix and not (p.startswith(scope_prefix) or p == scope_prefix.rstrip("/")):
                    continue
                if required_tags:
                    meta = self._get_meta(p)
                    if not required_tags.issubset({t.casefold() for t in meta.get("tags", [])}):
                        continue
                if p.startswith("ent_"):
                    continue
                meta = self._get_meta(p)
                patch = float(meta.get("score_patch", 0.0))
                normalized_tags = meta.get("tags", [])
                filtered.append({
                    "path": p,
                    "raw_score": item["score"],
                    "score_patch": patch,
                    "score": item["score"] + patch,
                    "tags": normalized_tags,
                    "snippet": "",
                    "_source": "bm25_only",
                })
            return filtered[:top_k]

        vector_results = await self._vector_search(query, scope_prefix, tags, top_k)
        bm25_results = await self._bm25_search(query, top_k * 2)

        required_tags = {t.casefold() for t in (tags or [])}
        bm25_filtered = []
        for item in bm25_results:
            p = item["path"]
            if scope_prefix and not (p.startswith(scope_prefix) or p == scope_prefix.rstrip("/")):
                continue
            if required_tags:
                meta = self._get_meta(p)
                current_set = {t.casefold() for t in meta.get("tags", [])}
                if not required_tags.issubset(current_set):
                    continue
            if p.startswith("ent_"):
                continue
            bm25_filtered.append(item)

        vec_norms: dict[str, float] = {}
        if vector_results:
            mx = max(item["raw_score"] for item in vector_results)
            if mx > 0:
                for item in vector_results:
                    vec_norms[item["path"]] = item["raw_score"] / mx
            else:
                for item in vector_results:
                    vec_norms[item["path"]] = 0.0

        bm25_norms: dict[str, float] = {}
        if bm25_filtered:
            mx = max(item["score"] for item in bm25_filtered)
            if mx > 0:
                for item in bm25_filtered:
                    bm25_norms[item["path"]] = item["score"] / mx
            else:
                for item in bm25_filtered:
                    bm25_norms[item["path"]] = 0.0

        alpha = 0.5
        merged: dict[str, dict] = {}

        for item in vector_results:
            p = item["path"]
            vec_n = vec_norms.get(p, 0)
            bm25_n = bm25_norms.get(p, 0)
            combined = alpha * vec_n + (1 - alpha) * bm25_n
            patch = item.get("score_patch", 0)
            merged[p] = {**item, "score": combined + patch, "_source": "hybrid"}

        for item in bm25_filtered:
            p = item["path"]
            if p in merged:
                continue
            meta = self._get_meta(p)
            patch = float(meta.get("score_patch", 0.0))
            bm25_n = bm25_norms.get(p, 0)
            merged[p] = {
                "path": p,
                "raw_score": 0,
                "score_patch": patch,
                "score": bm25_n + patch,
                "tags": meta.get("tags", []),
                "snippet": "",
                "_source": "bm25",
            }

        results = sorted(merged.values(), key=lambda x: x.get("score", 0), reverse=True)
        return results[:top_k]

    async def search_compact(self, query: str, scope: str | None = None,
                              top_k: int = 5) -> list[dict]:
        q = str(query or "").strip()
        if not q:
            return []
        scope_prefix = _normalize_path(scope or "").strip("/")
        scope_prefix = f"{scope_prefix}/" if scope_prefix else ""
        hybrid_results = await self._hybrid_search(q, scope_prefix, None, top_k)
        seen: dict[str, dict] = {}
        for item in hybrid_results:
            p = item["path"]
            meta = self._get_meta(p)
            desc = meta.get("description", "") or self._get_node_attr(_path_id(p), "description", "")
            # 图片文件不计数行数，直接置 0
            lc = self._count_lines(p)
            seen[p] = {
                "path": p,
                "line_count": lc,
                "description": desc,
                "score": item.get("score", 0),
            }
            nid = _path_id(p)
            if self._has_node(nid):
                nb = self.get_neighbors(nid, depth=2)
                for n in nb:
                    pref = n.get("path_ref")
                    if pref and pref not in seen:
                        meta2 = self._get_meta(pref)
                        desc2 = meta2.get("description", "") or self._get_node_attr(_path_id(pref), "description", "")
                        seen[pref] = {
                            "path": pref,
                            "line_count": self._count_lines(pref),
                            "description": desc2,
                            "score": 0,
                        }
        merged = list(seen.values())
        reranked = await self._rerank(q, merged, top_k)
        reranked.sort(key=lambda x: x.get("score", 0), reverse=True)
        return reranked[:top_k * 2]

    async def search_bm25(self, query_tokens: list[str], top_k: int = 3) -> list[dict]:
        """Lite 模式入口：接受已分词的 query token（jieba），纯 BM25 检索。"""
        await self._ensure_bm25_index()
        if self._bm25_index is None or not self._bm25_docs or not query_tokens:
            return []
        scores = self._bm25_index.get_scores(list(query_tokens))
        path_scores: dict[str, list[float]] = {}
        for doc, score in zip(self._bm25_docs, scores):
            path_scores.setdefault(doc["path"], []).append(score)
        results = [
            {"path": path, "score": sum(sc_list) / len(sc_list), "_source": "bm25"}
            for path, sc_list in path_scores.items()
        ]
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _is_binary_path(self, path: str) -> bool:
        """检测文件后缀是否为图片/二进制文件"""
        suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
        return suffix in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "ico")

    def _count_lines(self, path: str) -> int:
        """统计文件行数，二进制文件返回 0"""
        if self._is_binary_path(path):
            return 0
        cp = self._content_path(path)
        if not cp.exists():
            return 0
        try:
            return len(cp.read_text(encoding="utf-8").splitlines())
        except (UnicodeDecodeError, OSError):
            return 0

    def _graph_search(self, query: str, top_k: int) -> list[dict]:
        q = str(query or "").strip().lower()
        if not q:
            return []
        # match both entity names AND descriptions
        matched = self.entity_search(q, top_k=10)
        for nid, ndata in self._graph.nodes(data=True):
            if ndata.get("type") != "entity":
                continue
            nid_ = str(nid)
            if any(m["id"] == nid_ for m in matched):
                continue
            desc = str(ndata.get("description", "") or "").lower()
            if q in desc:
                matched.append({
                    "id": nid_,
                    "name": ndata.get("name", ""),
                    "entity_type": ndata.get("entity_type", "custom"),
                    "description": ndata.get("description", ""),
                    "properties": dict(ndata.get("properties", {})),
                    "kb_refs": list(ndata.get("kb_refs", [])),
                    "created_at": ndata.get("created_at", ""),
                })
        if not matched:
            return []
        path_scores: dict[str, float] = {}
        for e in matched:
            for ref in e.get("kb_refs", []):
                norm = _normalize_path(ref)
                path_scores[norm] = max(path_scores.get(norm, 0), 0.5)
            neighbors = self.get_neighbors(e["id"], depth=1)
            for n in neighbors:
                pref = n.get("path_ref")
                if pref:
                    path_scores[pref] = max(path_scores.get(pref, 0), 0.3)
        results = []
        for path_str, gscore in path_scores.items():
            meta = self._get_meta(path_str)
            patch = float(meta.get("score_patch", 0.0))
            results.append({
                "path": path_str,
                "raw_score": gscore,
                "score_patch": patch,
                "score": gscore + patch,
                "tags": meta.get("tags", []),
                "snippet": "",
                "_source": "graph",
            })
        return results

    # ── reranker ──

    async def _rerank(self, query: str, items: list[dict], top_k: int) -> list[dict]:
        if not conf.RERANK_ENABLED or getattr(conf, 'BM25_ONLY', False) or not items:
            return items
        texts: list[str] = []
        for item in items:
            t = item.get("snippet") or item.get("description", "")
            if not t:
                meta = self._get_meta(item["path"])
                t = str(meta.get("description", "") or "")
            texts.append(t[:512])
        if not any(texts):
            return items
        try:
            import httpx
            from faust_backend.runtime import state as runtime_state
            from faust_backend.provider import get_main_credentials
            _rmodel, _rkey, _rbase = get_main_credentials(runtime_state.get_model_providers())
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{_rbase}/rerank",
                    headers={"Authorization": f"Bearer {_rkey}"},
                    json={
                        "model": _rmodel,
                        "documents": texts,
                        "top_n": min(top_k * 2, len(items)),
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            results = data.get("results", [])
            if not results:
                return items
            score_map: dict[str, float] = {}
            for r in results:
                idx = r.get("index")
                if isinstance(idx, int) and 0 <= idx < len(items):
                    score_map[items[idx]["path"]] = r.get("relevance_score") or r.get("score") or 0.0
            for item in items:
                rerank_score = score_map.get(item["path"])
                if rerank_score is not None:
                    item["_rerank_score"] = rerank_score
                    item["score"] = rerank_score
                    item["_source"] = str(item.get("_source", "")) + "+rerank"
            items.sort(key=lambda x: x.get("score", 0), reverse=True)
        except Exception as e:
            log.warning("rerank failed: %s", e)
        return items

    # ── changed nodes ──

    async def get_changed_nodes(self, since_ts: float, scope: str | None = None,
                                tags: list[str] | None = None,
                                include_entities: bool = False,
                                limit: int | None = None) -> list[dict]:
        """自 since_ts 起被写入过的节点（文件/目录，可选实体）。

        注意 `updated_at` 会被内容写入与元数据写入（标签/权重）共同刷新，所以结果里
        可能包含只改过标签的节点。`limit` 按 `updated_at` 倒序保留最新的若干条。
        """
        scope_prefix = _normalize_path(scope or "").strip("/")
        scope_prefix = f"/{scope_prefix}/" if scope_prefix else ""
        required_tags = {t.casefold() for t in (tags or [])}
        node_types = ("file", "dir", "entity") if include_entities else ("file", "dir")
        placeholders = ", ".join("?" for _ in node_types)
        rows = self.db.all(
            "SELECT n.id AS id, n.type AS type, n.path AS path, n.name AS name,"
            " n.updated_at AS updated_at, n.score_patch AS score_patch,"
            " (SELECT group_concat(t.tag, ',') FROM tags t WHERE t.node_id = n.id) AS tag_csv"
            f" FROM nodes n WHERE n.type IN ({placeholders}) AND n.updated_at IS NOT NULL"
            " AND n.updated_at >= ? ORDER BY n.updated_at DESC",
            (*node_types, float(since_ts)),
        )
        results = []
        for row in rows:
            ntype = str(row["type"] or "")
            node_path = str(row["path"] or "")
            if ntype in ("file", "dir"):
                if not node_path:
                    continue
                if scope_prefix and not node_path.startswith(scope_prefix):
                    continue
            ntags = [t for t in str(row["tag_csv"] or "").split(",") if t]
            if required_tags and not required_tags.issubset({t.casefold() for t in ntags}):
                continue
            item = {
                "updated_at": epoch_to_iso(row["updated_at"]),
                "tags": ntags,
                "score_patch": float(row["score_patch"] or 0.0),
            }
            if ntype == "entity":
                item["id"] = str(row["id"])
                item["name"] = str(row["name"] or "")
                item["type"] = "entity"
            else:
                item["path"] = node_path
            results.append(item)
        if limit is not None and int(limit) > 0:
            results = results[:int(limit)]
        log.info("get_changed_nodes since=%s scope=%s entities=%s hits=%d",
                 since_ts, scope or "/", include_entities, len(results))
        return results

    # ── tasks ──

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
            "task_id": f"tsk_{uuid.uuid4().hex}",
            "type": task_type,
            "status": "pending",
            "payload": payload or {},
            "created_at": epoch_to_iso(now),
            "updated_at": epoch_to_iso(now),
            "error": "",
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

    # ── diary / chat record ──

    async def add_chat_record(self, user_text: str, assistant_text: str,
                              attachments: list[dict] | None = None) -> dict:
        stamp = time.strftime("%Y-%m-%d/%H%M%S", time.localtime())
        suffix = uuid.uuid4().hex[:6]
        path = f"/records/{stamp}_{suffix}.md"
        content = f"## 用户\n\n{user_text}\n\n## 助手\n\n{assistant_text}\n"
        desc = f"Chat record: {user_text[:120]}" if user_text else ""
        log.info("add_chat_record path=%s user_len=%d assistant_len=%d", path, len(user_text), len(assistant_text))
        result = await self.file_write(path, content, description=desc,
                                       declared_by="chat_record", index=True)
        await self._add_record_entity("chat_record", path, {
            "user_preview": user_text[:200],
            "assistant_preview": assistant_text[:200],
        })
        return result

    async def write_diary(self, content: str) -> dict:
        stamp = time.strftime("%Y-%m-%d/%H%M%S", time.localtime())
        suffix = uuid.uuid4().hex[:6]
        path = f"/diary/{stamp}_{suffix}.md"
        desc = content[:200] if content else ""
        log.info("write_diary path=%s content_len=%d", path, len(content))
        result = await self.file_write(path, content, description=desc,
                                       declared_by="diary", index=True)
        await self._add_record_entity("diary", path, {"preview": content[:200]})
        return result

    async def _add_record_entity(self, record_type: str, path: str, extra: dict) -> None:
        name = f"{record_type}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        props = {
            "timestamp": _utc_iso(),
            "path": path,
            **extra,
        }
        with self.db.transaction():
            eid = self.entity_add(name, entity_type=record_type, properties=props, kb_refs=[path])
            nid = _path_id(path)
            if self._has_node(nid):
                # 不变量 3：只有「两个 path 节点之间」的 has_child 进 nodes.parent_id；
                # 记录节点 -> 实体的 has_child 走 edges 表（实体没有 path）。
                self._add_edge(nid, eid, TREE_EDGE)
            prev = self._find_latest_entity(record_type, exclude=eid)
            if prev:
                self._add_edge(prev, eid, "next")

    def _find_latest_entity(self, entity_type: str, exclude: str | None = None) -> str | None:
        best: str | None = None
        best_ts = ""
        for nid, ndata in self._graph.nodes(data=True):
            if ndata.get("type") != "entity":
                continue
            if ndata.get("entity_type") != entity_type:
                continue
            if exclude and nid == exclude:
                continue
            ts = str(ndata.get("properties", {}).get("timestamp", "") or "")
            if ts > best_ts:
                best_ts = ts
                best = nid
        return best

    # ── declare file update ──

    async def declare_file_update(self, file_path: str, kb_path: str | None = None) -> dict:
        source = Path(file_path).resolve()
        log.info("declare_file_update source=%s kb_path=%s", source, kb_path)
        if not source.exists():
            raise FileNotFoundError(f"源文件不存在: {source}")
        target = kb_path or f"/imports/{source.name}"
        try:
            content = source.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"源文件不是 UTF-8 文本，不能直接导入 memory: {source}") from exc
        result = await self.file_write(target, content, declared_by=str(source), index=True)
        return result
