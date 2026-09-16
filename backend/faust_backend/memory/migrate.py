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
        "orphan_meta": 0,
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
            # 旧库存在「meta 文件比图节点多」的孤儿元数据（节点已删、meta 残留）；
            # graph.json 是树形状的真源，孤儿 meta 不入库，否则 tags 外键失败。
            if conn.execute("SELECT 1 FROM nodes WHERE id=?", (nid,)).fetchone() is None:
                counts["orphan_meta"] += 1
                log.warning("migrate skip meta without node: %s -> %s", path, nid)
                continue
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
