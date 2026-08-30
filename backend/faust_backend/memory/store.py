from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import aiofiles
import aiofiles.os
import networkx as nx
import numpy as np
from nano_vectordb import NanoVectorDB
from openai import AsyncOpenAI
from rank_bm25 import BM25Okapi

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger
from faust_backend.memory.config import (
    EMBED_MODEL, EMBED_DIM, MAX_CHUNK_CHARS, CHUNK_OVERLAP_CHARS,
    MIN_SCORE_PATCH, MAX_SCORE_PATCH,
)

log = get_logger("faust.memory")

_NODE_PATH = "memory"
TREE_EDGE = "has_child"


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now() -> float:
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


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", str(text).lower())


def _bm25_score_worker(args: tuple) -> list[dict]:
    corpus, doc_metas, query_tokens, top_k = args
    bm25 = BM25Okapi(corpus)
    scores = bm25.get_scores(query_tokens)
    ps: dict[str, list[float]] = {}
    for dm, sc in zip(doc_metas, scores):
        ps.setdefault(dm["path"], []).append(sc)
    ranked = sorted(
        [{"path": p, "score": sum(sl) / len(sl), "_source": "bm25"} for p, sl in ps.items()],
        key=lambda x: x["score"], reverse=True,
    )
    return ranked[:top_k]


def _normalize_path(path: str) -> str:
    raw = str(path or "").replace("\\", "/").strip()
    raw = "/" + raw.strip("/")
    if raw == "/":
        return "/"
    parts = [p for p in raw.split("/") if p and p not in (".", "..")]
    return "/" + "/".join(parts)


def _atomic_write_json(path: Path, data: Any, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 唯一临时文件名，避免多线程写同一目标时在 tmp 文件上相互冲突（WinError 32）
    tmp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex[:8]}.tmp")
    if compact:
        payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    else:
        payload = json.dumps(data, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


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


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    dot = float(np.dot(a, b))
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class GraphStore:
    def __init__(self, agent_name: str | None = None):
        self.agent_name = str(agent_name or conf.AGENT_NAME)
        self.agent_root = Path(conf.CONFIG_ROOT) / "agents" / self.agent_name
        self.store_dir = self.agent_root / _NODE_PATH
        self.content_dir = self.store_dir / "content"
        self.meta_dir = self.store_dir / "meta"
        self.index_dir = self.store_dir / "index"
        self.attachments_dir = self.store_dir / "attachments"
        self.graph_file = self.store_dir / "graph.json"
        self.index_file = self.index_dir / "chunks.vdb"
        self.chunks_index_file = self.meta_dir / "chunks_index.json"
        self.tasks_file = self.meta_dir / "tasks.json"
        # 实体名称向量独立存储（1536 维浮点 JSON 体积大，拖慢 graph.json 全量保存）
        self.vecs_file = self.index_dir / "entity_vecs.jsonl"
        self._graph: nx.MultiDiGraph = nx.MultiDiGraph()
        self._vdb: NanoVectorDB | None = None
        self._openai_client: AsyncOpenAI | None = None
        self._embed_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._save_lock = threading.Lock()
        self._dirty: bool = False
        self._bm25_dirty: bool = True
        self._bm25_index: BM25Okapi | None = None
        self._bm25_corpus: list[list[str]] | None = None
        self._extraction_status: dict = {
            "pending": 0,
            "running": 0,
            "last_running": None,
            "last_success": None,
            "last_error": None,
        }
        self._ensure_dirs()
        self._load()

    # ── initialization ──

    def refresh(self, agent_name: str | None = None) -> None:
        target = str(agent_name or conf.AGENT_NAME)
        if target == self.agent_name:
            return
        self.__init__(target)

    def _ensure_dirs(self) -> None:
        self.content_dir.mkdir(parents=True, exist_ok=True)
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.attachments_dir.mkdir(parents=True, exist_ok=True)

    # ── persistence ──

    def _load(self) -> None:
        data = _read_json(self.graph_file, {"nodes": {}, "edges": []})
        nodes = data.get("nodes", {})
        edges = data.get("edges", [])

        self._graph.clear()
        for nid, attrs in nodes.items():
            self._graph.add_node(nid, **attrs)
        self._load_entity_vecs()
        for e in edges:
            src = str(e.get("source", ""))
            tgt = str(e.get("target", ""))
            key = str(e.get("key", "")) or str(uuid.uuid4().hex)
            etype = str(e.get("type", "relates_to"))
            self._graph.add_edge(src, tgt, key=key, type=etype)

        if not self._graph.has_node("path:/"):
            self._graph.add_node("path:/", type="dir", name="/")
            self._dirty = True

        self._repair_tree()
        self._ensure_vdb()

    def _load_entity_vecs(self) -> None:
        """读取实体名称向量侧车文件；兼容旧数据（向量内嵌在 graph.json 中）。"""
        if self.vecs_file.exists():
            try:
                with open(self.vecs_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        nid = rec.get("id")
                        vec = rec.get("v")
                        if nid and vec is not None and self._graph.has_node(nid):
                            self._graph.nodes[nid]["_name_vec"] = [float(x) for x in vec]
            except Exception:  # noqa: BLE001 损坏的侧车文件不阻塞加载
                pass
        # 旧格式迁移：graph.json 内嵌 _name_vec → 下次 save() 移入侧车
        for nid, ndata in self._graph.nodes(data=True):
            if ndata.get("_name_vec") is not None:
                self._dirty = True
                break

    def _write_entity_vecs(self, vecs: dict[str, list[float]]) -> None:
        """原子写实体名称向量侧车文件（先写侧车再写图，旧图可读时数据不丢）。"""
        self.index_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.vecs_file.with_suffix(self.vecs_file.suffix + f".{uuid.uuid4().hex[:8]}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for nid, vec in vecs.items():
                f.write(json.dumps({"id": nid, "v": vec}, ensure_ascii=False,
                                   separators=(",", ":")) + "\n")
        os.replace(tmp, self.vecs_file)

    def save(self) -> None:
        # 同步工具在线程池中执行，save 可能被多线程并发调用，须串行化
        with self._save_lock:
            if not self._dirty:
                return
            nodes = {}
            vecs: dict[str, list[float]] = {}
            for nid, ndata in self._graph.nodes(data=True):
                d = dict(ndata) if ndata else {}
                v = d.pop("_name_vec", None)
                if v is not None:
                    vecs[nid] = [float(x) for x in v]
                nodes[nid] = d
            edges = []
            for src, tgt, k, edata in self._graph.edges(data=True, keys=True):
                etype = str(edata.get("type", "relates_to")) if edata else "relates_to"
                edges.append({"source": src, "target": tgt, "key": str(k), "type": etype})
            if vecs:
                self._write_entity_vecs(vecs)
            _atomic_write_json(self.graph_file, {"nodes": nodes, "edges": edges},
                               compact=True)
            self._dirty = False
            log.info("save wrote %d nodes, %d edges", len(nodes), len(edges))

    def _mark_bm25_dirty(self) -> None:
        self._bm25_dirty = True

    def _mark_bm25_clean(self) -> None:
        self._bm25_dirty = False

    def flush(self) -> None:
        self.save()
        log.info("flush")

    async def _flush_async(self) -> None:
        """异步调用 flush（save 为全量磁盘写，移出事件循环避免卡顿）。"""
        await asyncio.to_thread(self.flush)

    # ── node helpers ──

    def _add_node(self, nid: str, **attrs) -> None:
        if not self._graph.has_node(nid):
            self._graph.add_node(nid, **attrs)
            self._dirty = True

    def _add_edge(self, src: str, tgt: str, etype: str = "relates_to") -> str:
        key = str(uuid.uuid4().hex)
        self._graph.add_edge(src, tgt, key=key, type=etype)
        self._dirty = True
        return key

    def _remove_edge(self, src: str, tgt: str) -> None:
        if self._graph.has_edge(src, tgt):
            self._graph.remove_edge(src, tgt)
            self._dirty = True

    def _has_node(self, nid: str) -> bool:
        return self._graph.has_node(nid)

    def _get_node_attr(self, nid: str, key: str, default: Any = None) -> Any:
        return self._graph.nodes[nid].get(key, default) if self._graph.has_node(nid) else default

    def _set_node_attr(self, nid: str, **kwargs) -> None:
        if not self._graph.has_node(nid):
            return
        for k, v in kwargs.items():
            self._graph.nodes[nid][k] = v
        self._dirty = True

    def _children(self, parent_id: str) -> list[tuple[str, str]]:
        out = []
        for _, tgt, k, edata in self._graph.out_edges(parent_id, data=True, keys=True):
            if edata and edata.get("type") == TREE_EDGE:
                out.append((tgt, k))
        out.sort(key=lambda x: self._get_node_attr(x[0], "name", x[0]))
        return out

    # ── tree path helpers ──

    def _ensure_ancestors(self, normalized_path: str) -> str:
        parts = Path(normalized_path).parts
        current = ""
        for i, part in enumerate(parts):
            if part in ("/", "\\"):
                current = "/"
                continue
            if i == len(parts) - 1:
                break
            parent = current
            current = str(Path(current) / part) if current != "/" else f"/{part}"
            parent_nid = _path_id(parent)
            child_nid = _path_id(current)
            if not self._has_node(child_nid):
                self._add_node(child_nid, type="dir", name=part)
            if parent_nid == child_nid:
                continue
            has_edge = False
            for _, tgt, _k, edata in self._graph.out_edges(parent_nid, data=True, keys=True):
                if tgt == child_nid and edata and edata.get("type") == TREE_EDGE:
                    has_edge = True
                    break
            if not has_edge:
                self._add_edge(parent_nid, child_nid, TREE_EDGE)
        return _path_id(str(Path(normalized_path).parent))

    def _repair_tree(self) -> None:
        changed = True
        for _ in range(20):
            if not changed:
                break
            changed = False
            for nid in list(self._graph.nodes):
                if not _is_path_id(nid) or nid == "path:/":
                    continue
                parent_nid = _path_id(str(Path(_id_to_path(nid)).parent))
                if not self._has_node(parent_nid):
                    self._add_node(parent_nid, type="dir", name=Path(_id_to_path(parent_nid)).name or "/")
                    changed = True
                has_edge = False
                if self._has_node(parent_nid) and self._has_node(nid):
                    for _, tgt, _k, edata in self._graph.out_edges(parent_nid, data=True, keys=True):
                        if tgt == nid and edata and edata.get("type") == TREE_EDGE:
                            has_edge = True
                            break
                if not has_edge:
                    self._add_edge(parent_nid, nid, TREE_EDGE)
                    changed = True

    # ── content helpers ──

    def _content_path(self, norm_path: str) -> Path:
        relative = norm_path.strip("/")
        return self.content_dir / relative

    def _meta_path(self, norm_path: str) -> Path:
        relative = norm_path.strip("/")
        return self.meta_dir / f"{relative}.meta.json"

    # ── tree operations ──

    async def tree_list(self, scope: str | None = None) -> dict:
        scope_path = _normalize_path(scope or "/")
        log.info("tree_list scope=%s", scope_path)
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
        meta = self._read_meta(norm)

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
        parent_nid = self._ensure_ancestors(norm)

        async with self._write_lock:
            cp = self._content_path(norm)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(str(content or ""), encoding="utf-8")

            if not self._has_node(nid):
                self._add_node(nid, type="file", name=name, description=str(description or ""),
                               tags=[], score_patch=0.0)
            self._set_node_attr(nid, updated_at=_utc_iso(), declared_by=declared_by)

            has_child = False
            if self._has_node(parent_nid):
                for _, tgt, _k, edata in self._graph.out_edges(parent_nid, data=True, keys=True):
                    if tgt == nid and edata and edata.get("type") == TREE_EDGE:
                        has_child = True
                        break
            if not has_child:
                self._add_edge(parent_nid, nid, TREE_EDGE)

            index_text = str(content or "")
            if description:
                index_text = f"{description}\n\n{content}"
            chunks = _chunk_text(index_text)
            existing_meta = self._read_meta(norm) or {}
            meta = {
                "path": norm,
                "declared_by": declared_by,
                "description": str(description or ""),
                "updated_at": _utc_iso(),
                "chunk_count": len(chunks),
                "indexed": bool(index),
                "tags": [t.strip() for t in (tags or existing_meta.get("tags", [])) if t and t.strip()],
                "score_patch": float(existing_meta.get("score_patch", 0.0)),
            }
            self._write_meta(norm, meta)
            self._set_node_attr(nid, tags=meta["tags"], score_patch=meta["score_patch"])

            if not index:
                return {"path": norm, "meta": meta}

            chunk_items = []
            chunks_index = self._load_chunks_index()
            old_ids = [cid for cid, item in chunks_index.items() if str(item.get("node_path")) == norm]
            for cid in old_ids:
                chunks_index.pop(cid, None)
            if old_ids:
                await self._delete_chunk_ids(old_ids)

            for idx, chunk_text in enumerate(chunks, 1):
                cid = f"{norm}::chunk::{idx}::{uuid.uuid4().hex[:8]}"
                item = {
                    "chunk_id": cid, "node_path": norm, "chunk_index": idx,
                    "text": chunk_text, "text_preview": chunk_text[:120],
                    "scope_prefix": str(Path(norm).parent.as_posix()).strip(".") or "/",
                    "updated_at": _utc_iso(), "indexed": True,
                }
                chunk_items.append(item)
                chunks_index[cid] = item
            _atomic_write_json(self._chunks_file(norm), chunk_items)
            self._save_chunks_index(chunks_index)

            if chunk_items:
                await self._embed_and_index(chunk_items)
            self._mark_bm25_dirty()

        await self._flush_async()
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
        parent_nid = self._ensure_ancestors(norm)

        async with self._write_lock:
            cp = self._content_path(norm)
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_bytes(image_bytes)

            if not self._has_node(nid):
                self._add_node(nid, type="file", name=name,
                               description=str(description or ""),
                               content_type=content_type, tags=[],
                               score_patch=0.0)
            self._set_node_attr(nid, updated_at=_utc_iso(),
                                declared_by=declared_by)

            has_child = False
            if self._has_node(parent_nid):
                for _, tgt, _k, edata in self._graph.out_edges(parent_nid, data=True, keys=True):
                    if tgt == nid and edata and edata.get("type") == TREE_EDGE:
                        has_child = True
                        break
            if not has_child:
                self._add_edge(parent_nid, nid, TREE_EDGE)

            meta = {
                "path": norm,
                "declared_by": declared_by,
                "description": str(description or ""),
                "content_type": content_type,
                "updated_at": _utc_iso(),
                "tags": [],
                "score_patch": 0.0,
            }
            self._write_meta(norm, meta)

        if description:
            await self._index_attachment_description(norm, content_type, description)
        self._mark_bm25_dirty()

        await self._flush_async()

        log.info("attachment_write done path=%s size=%d desc_len=%d",
                 norm, len(image_bytes), len(description))
        return {"path": norm, "description": description, "content_type": content_type}

    async def _index_attachment_description(self, norm: str, content_type: str, description: str) -> None:
        try:
            chunk_text = f"[image:{content_type}] {description}"
            chunks = _chunk_text(chunk_text)
            chunk_items = []
            chunks_index = self._load_chunks_index()
            old_ids = [cid for cid, item in chunks_index.items()
                       if str(item.get("node_path")) == norm]
            for cid in old_ids:
                chunks_index.pop(cid, None)
            if old_ids:
                await self._delete_chunk_ids(old_ids)
            for idx, chunk_text in enumerate(chunks, 1):
                cid = f"{norm}::chunk::{idx}::{uuid.uuid4().hex[:8]}"
                item = {
                    "chunk_id": cid, "node_path": norm, "chunk_index": idx,
                    "text": chunk_text, "text_preview": chunk_text[:120],
                    "scope_prefix": str(Path(norm).parent.as_posix()).strip(".") or "/",
                    "updated_at": _utc_iso(), "indexed": True,
                }
                chunk_items.append(item)
                chunks_index[cid] = item
            _atomic_write_json(self._chunks_file(norm), chunk_items)
            self._save_chunks_index(chunks_index)
            if chunk_items:
                await self._embed_and_index(chunk_items)
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
            cp = self._content_path(norm)
            if cp.exists():
                if cp.is_dir():
                    # 目录节点：递归删除其内容目录（含残留子目录/文件）
                    shutil.rmtree(cp, ignore_errors=True)
                else:
                    cp.unlink(missing_ok=True)
            mp = self._meta_path(norm)
            if mp.exists():
                mp.unlink(missing_ok=True)
            cf = self._chunks_file(norm)
            if cf.exists():
                cf.unlink(missing_ok=True)

            parent_nid = _path_id(str(Path(norm).parent))
            self._remove_edge(parent_nid, nid)

            chunks_index = self._load_chunks_index()
            old_ids = [cid for cid, item in chunks_index.items() if str(item.get("node_path")) == norm]
            for cid in old_ids:
                chunks_index.pop(cid, None)
            self._save_chunks_index(chunks_index)
            await self._delete_chunk_ids(old_ids)
            self._mark_bm25_dirty()

            if self._has_node(nid):
                self._graph.remove_node(nid)
                self._dirty = True

        await self._flush_async()
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
            if ntype == "file":
                # 文件：移动内容+元数据+chunks
                cp = self._content_path(norm)
                if cp.exists():
                    new_cp = self._content_path(new_path)
                    new_cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.rename(new_cp)
                mp = self._meta_path(norm)
                if mp.exists():
                    meta = self._read_meta(norm)
                    meta["path"] = new_path
                    meta["updated_at"] = _utc_iso()
                    self._write_meta(new_path, meta)
                    mp.unlink()
                cf = self._chunks_file(norm)
                if cf.exists():
                    new_cf = self._chunks_file(new_path)
                    new_cf.parent.mkdir(parents=True, exist_ok=True)
                    cf.rename(new_cf)
                    # update chunks_index references
                    chunks_index = self._load_chunks_index()
                    for cid, item in list(chunks_index.items()):
                        if str(item.get("node_path")) == norm:
                            item["node_path"] = new_path
                    self._save_chunks_index(chunks_index)
                parent_nid = _path_id(str(Path(norm).parent))
                self._remove_edge(parent_nid, nid)
                # 重新添加节点
                ndata = dict(self._graph.nodes[nid])
                ndata["name"] = new_name
                self._graph.remove_node(nid)
                self._add_node(new_nid, **ndata)
                self._add_edge(_path_id(parent), new_nid, TREE_EDGE)
            else:
                # 目录：遍历所有子节点递归重命名
                children_to_rename = []
                for child_nid in list(self._graph.nodes):
                    if not _is_path_id(child_nid):
                        continue
                    child_path = _id_to_path(child_nid)
                    if child_path.startswith(norm + "/") or child_path == norm:
                        children_to_rename.append((child_nid, child_path))
                # 按路径深度排序（深->浅），避免父路径先变导致子路径错误
                children_to_rename.sort(key=lambda x: x[1], reverse=True)
                seen_new = set()
                for child_nid, child_path in children_to_rename:
                    suffix = child_path[len(norm):]  # e.g. "/sub/file.md"
                    new_child_path = _normalize_path(new_path + suffix)
                    new_child_nid = _path_id(new_child_path)
                    if new_child_nid in seen_new:
                        continue
                    seen_new.add(new_child_nid)
                    if child_nid == nid:
                        ndata = dict(self._graph.nodes[child_nid])
                        ndata["name"] = new_name
                        self._graph.remove_node(child_nid)
                        self._add_node(new_child_nid, **ndata)
                        self._add_edge(_path_id(parent), new_child_nid, TREE_EDGE)
                    else:
                        # move path:/old/dir/sub -> path:/new/dir/sub
                        try:
                            sub_cp = self._content_path(child_path)
                            if sub_cp.exists():
                                new_sub_cp = self._content_path(new_child_path)
                                new_sub_cp.parent.mkdir(parents=True, exist_ok=True)
                                sub_cp.rename(new_sub_cp)
                        except Exception:
                            pass
                        try:
                            sub_mp = self._meta_path(child_path)
                            if sub_mp.exists():
                                sub_meta = _read_json(sub_mp, {})
                                sub_meta["path"] = new_child_path
                                sub_meta["updated_at"] = _utc_iso()
                                self._write_meta(new_child_path, sub_meta)
                                sub_mp.unlink()
                        except Exception:
                            pass
                        try:
                            sub_cf = self._chunks_file(child_path)
                            if sub_cf.exists():
                                new_cf = self._chunks_file(new_child_path)
                                new_cf.parent.mkdir(parents=True, exist_ok=True)
                                sub_cf.rename(new_cf)
                        except Exception:
                            pass
                        ndata = dict(self._graph.nodes[child_nid])
                        self._graph.remove_node(child_nid)
                        self._add_node(new_child_nid, **ndata)
                # 更新 chunks index 中的 node_path 引用
                chunks_index = self._load_chunks_index()
                for cid, item in list(chunks_index.items()):
                    np = str(item.get("node_path", ""))
                    if np.startswith(norm + "/") or np == norm:
                        item["node_path"] = np.replace(norm, new_path, 1)
                self._save_chunks_index(chunks_index)
            # 修复父目录边缘
            self._repair_tree()
        await self._flush_async()
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
            if ntype == "file":
                # 复制文件
                cp = self._content_path(norm)
                if cp.exists():
                    new_cp = self._content_path(dest)
                    new_cp.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(cp, new_cp)
                meta = self._read_meta(norm)
                new_meta = dict(meta)
                new_meta["path"] = dest
                new_meta["updated_at"] = _utc_iso()
                self._write_meta(dest, new_meta)
                cf = self._chunks_file(norm)
                if cf.exists():
                    new_cf = self._chunks_file(dest)
                    new_cf.parent.mkdir(parents=True, exist_ok=True)
                    import shutil
                    shutil.copy2(cf, new_cf)
                    # copy chunks index refs
                    chunks_items = _read_json(cf, [])
                    new_items = []
                    for item in chunks_items:
                        new_item = dict(item)
                        new_item["node_path"] = dest
                        new_cid = f"{dest}::chunk::{item.get('chunk_index', 0)}::{uuid.uuid4().hex[:8]}"
                        new_item["chunk_id"] = new_cid
                        new_items.append(new_item)
                    _atomic_write_json(new_cf, new_items)
                    # 更新全局 chunks_index
                    chunks_index = self._load_chunks_index()
                    for ni in new_items:
                        chunks_index[ni["chunk_id"]] = ni
                    self._save_chunks_index(chunks_index)
                    copied_chunk_items.extend(new_items)
                ndata = dict(self._graph.nodes[nid])
                self._add_node(dest_nid, **ndata)
                parent_dest_nid = _path_id(str(Path(dest).parent))
                self._ensure_ancestors(dest)
                self._add_edge(parent_dest_nid, dest_nid, TREE_EDGE)
            else:
                # 目录递归复制
                self._ensure_ancestors(dest)
                children_to_copy = []
                for child_nid in list(self._graph.nodes):
                    if not _is_path_id(child_nid):
                        continue
                    child_path = _id_to_path(child_nid)
                    if child_path.startswith(norm + "/") or child_path == norm:
                        children_to_copy.append((child_nid, child_path))
                children_to_copy.sort(key=lambda x: len(x[1]))
                seen_new = set()
                for child_nid, child_path in children_to_copy:
                    suffix = child_path[len(norm):]
                    new_child_path = _normalize_path(dest + suffix)
                    new_child_nid = _path_id(new_child_path)
                    if new_child_nid in seen_new:
                        continue
                    seen_new.add(new_child_nid)
                    try:
                        cp = self._content_path(child_path)
                        if cp.exists():
                            new_cp = self._content_path(new_child_path)
                            new_cp.parent.mkdir(parents=True, exist_ok=True)
                            import shutil
                            shutil.copy2(cp, new_cp)
                    except Exception:
                        pass
                    try:
                        meta = self._read_meta(child_path)
                        new_meta = dict(meta)
                        new_meta["path"] = new_child_path
                        new_meta["updated_at"] = _utc_iso()
                        self._write_meta(new_child_path, new_meta)
                    except Exception:
                        pass
                    try:
                        cf = self._chunks_file(child_path)
                        if cf.exists():
                            new_cf = self._chunks_file(new_child_path)
                            new_cf.parent.mkdir(parents=True, exist_ok=True)
                            chunks_items = _read_json(cf, [])
                            new_items = []
                            for item in chunks_items:
                                new_item = dict(item)
                                new_item["node_path"] = new_child_path
                                new_item["chunk_id"] = f"{new_child_path}::chunk::{item.get('chunk_index', 0)}::{uuid.uuid4().hex[:8]}"
                                new_items.append(new_item)
                            _atomic_write_json(new_cf, new_items)
                            # update global chunks_index
                            chunks_index = self._load_chunks_index()
                            for ni in new_items:
                                chunks_index[ni["chunk_id"]] = ni
                            self._save_chunks_index(chunks_index)
                            copied_chunk_items.extend(new_items)
                    except Exception:
                        pass
                    ndata = dict(self._graph.nodes[child_nid])
                    self._add_node(new_child_nid, **ndata)
                    # wire parent
                    if child_nid != nid:  # not the root
                        p_dest = _path_id(str(Path(new_child_path).parent))
                        self._add_edge(p_dest, new_child_nid, TREE_EDGE)
        if copied_chunk_items:
            await self._embed_and_index(copied_chunk_items)
        self._mark_bm25_dirty()
        self._repair_tree()
        await self._flush_async()
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
            if ntype == "file":
                cp = self._content_path(norm)
                if cp.exists():
                    new_cp = self._content_path(dest)
                    new_cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.rename(new_cp)
                mp = self._meta_path(norm)
                if mp.exists():
                    meta = self._read_meta(norm)
                    meta["path"] = dest
                    meta["updated_at"] = _utc_iso()
                    self._write_meta(dest, meta)
                    mp.unlink()
                cf = self._chunks_file(norm)
                if cf.exists():
                    new_cf = self._chunks_file(dest)
                    new_cf.parent.mkdir(parents=True, exist_ok=True)
                    cf.rename(new_cf)
                    chunks_index = self._load_chunks_index()
                    for cid, item in list(chunks_index.items()):
                        if str(item.get("node_path")) == norm:
                            item["node_path"] = dest
                    self._save_chunks_index(chunks_index)
                parent_nid = _path_id(str(Path(norm).parent))
                self._remove_edge(parent_nid, nid)
                ndata = dict(self._graph.nodes[nid])
                self._graph.remove_node(nid)
                self._add_node(dest_nid, **ndata)
                self._ensure_ancestors(dest)
                p_dest = _path_id(str(Path(dest).parent))
                self._add_edge(p_dest, dest_nid, TREE_EDGE)
            else:
                # 目录移动：迭代子节点，转移到新目录
                self._ensure_ancestors(dest)
                children_to_move = []
                for child_nid in list(self._graph.nodes):
                    if not _is_path_id(child_nid):
                        continue
                    child_path = _id_to_path(child_nid)
                    if child_path.startswith(norm + "/") or child_path == norm:
                        children_to_move.append((child_nid, child_path))
                children_to_move.sort(key=lambda x: len(x[1]), reverse=True)
                seen_new = set()
                for child_nid, child_path in children_to_move:
                    suffix = child_path[len(norm):]
                    new_child_path = _normalize_path(dest + suffix)
                    new_child_nid = _path_id(new_child_path)
                    if new_child_nid in seen_new:
                        continue
                    seen_new.add(new_child_nid)
                    try:
                        cp = self._content_path(child_path)
                        if cp.exists():
                            new_cp = self._content_path(new_child_path)
                            new_cp.parent.mkdir(parents=True, exist_ok=True)
                            cp.rename(new_cp)
                    except Exception:
                        pass
                    try:
                        mp = self._meta_path(child_path)
                        if mp.exists():
                            meta = _read_json(mp, {})
                            meta["path"] = new_child_path
                            meta["updated_at"] = _utc_iso()
                            self._write_meta(new_child_path, meta)
                            mp.unlink()
                    except Exception:
                        pass
                    try:
                        cf = self._chunks_file(child_path)
                        if cf.exists():
                            new_cf = self._chunks_file(new_child_path)
                            new_cf.parent.mkdir(parents=True, exist_ok=True)
                            cf.rename(new_cf)
                    except Exception:
                        pass
                    ndata = dict(self._graph.nodes[child_nid])
                    self._graph.remove_node(child_nid)
                    self._add_node(new_child_nid, **ndata)
                    if child_nid == nid:
                        old_parent_nid = _path_id(str(Path(norm).parent))
                        self._remove_edge(old_parent_nid, child_nid)
                        p_dest = _path_id(str(Path(dest).parent))
                        self._add_edge(p_dest, new_child_nid, TREE_EDGE)
                    else:
                        p_dest = _path_id(str(Path(new_child_path).parent))
                        self._add_edge(p_dest, new_child_nid, TREE_EDGE)
                # 更新 chunks index
                chunks_index = self._load_chunks_index()
                for cid, item in list(chunks_index.items()):
                    np_str = str(item.get("node_path", ""))
                    if np_str.startswith(norm + "/") or np_str == norm:
                        item["node_path"] = np_str.replace(norm, dest, 1)
                self._save_chunks_index(chunks_index)
            self._mark_bm25_dirty()
            self._repair_tree()
        await self._flush_async()
        log.info("file_move path=%s -> dest=%s type=%s", norm, dest, ntype)
        return {"path": norm, "new_path": dest, "type": ntype}

    async def mkdir(self, path: str, description: str = "") -> dict:
        norm = _normalize_path(path)
        nid = _path_id(norm)
        log.info("mkdir path=%s", norm)
        parent_nid = self._ensure_ancestors(norm)

        if not self._has_node(nid):
            self._add_node(nid, type="dir", name=Path(norm).name, description=str(description or ""))
        self._add_edge(parent_nid, nid, TREE_EDGE)
        await self._flush_async()
        return {"path": norm, "type": "dir"}

    def _chunks_file(self, norm_path: str) -> Path:
        relative = norm_path.strip("/")
        return self.meta_dir / f"{relative}.chunks.json"

    # ── meta helpers ──

    def _read_meta(self, norm_path: str) -> dict:
        mp = self._meta_path(norm_path)
        meta = _read_json(mp, {})
        meta.setdefault("path", norm_path)
        meta.setdefault("tags", [])
        meta.setdefault("score_patch", 0.0)
        return meta

    def _write_meta(self, norm_path: str, meta: dict) -> None:
        mp = self._meta_path(norm_path)
        mp.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(mp, meta)

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

    async def set_tags(self, path: str, tags: list[str], managed_by: str | None = None) -> dict:
        norm = _normalize_path(path)
        log.info("set_tags path=%s tags=%s", norm, tags)
        meta = self._read_meta(norm)
        meta["tags"] = [t.strip() for t in (tags or []) if t and t.strip()]
        meta["updated_at"] = _utc_iso()
        if managed_by is not None:
            meta["managed_by"] = str(managed_by)
        self._write_meta(norm, meta)
        nid = _path_id(norm)
        self._set_node_attr(nid, tags=meta["tags"])
        await self._flush_async()
        return {"path": norm, "meta": meta}

    async def set_score_patch(self, path: str, score_patch: float) -> dict:
        norm = _normalize_path(path)
        log.info("set_score_patch path=%s score_patch=%s", norm, score_patch)
        patch = float(score_patch)
        if not math.isfinite(patch):
            raise ValueError("score_patch 必须是有限数值")
        if patch < MIN_SCORE_PATCH or patch > MAX_SCORE_PATCH:
            raise ValueError(f"score_patch 超出范围 [{MIN_SCORE_PATCH}, {MAX_SCORE_PATCH}]")
        meta = self._read_meta(norm)
        meta["score_patch"] = patch
        meta["score_patch_updated_at"] = _utc_iso()
        meta["updated_at"] = _utc_iso()
        self._write_meta(norm, meta)
        nid = _path_id(norm)
        self._set_node_attr(nid, score_patch=patch)
        await self._flush_async()
        return {"path": norm, "meta": meta}


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

        # Collect all path nodes with meta
        candidates: list[dict] = []
        meta_dir = self.meta_dir
        if meta_dir.exists():
            for mp in sorted(meta_dir.rglob("*.meta.json")):
                try:
                    meta = _read_json(mp, {})
                    if not meta or not meta.get("path"):
                        continue
                    p = _normalize_path(meta["path"])
                    # scope filter
                    if scope_prefix and not (p.startswith(scope_prefix) or p == scope_prefix.rstrip("/")):
                        continue
                    # tags filter
                    meta_tags = [t for t in (meta.get("tags") or []) if t]
                    if required_tags:
                        tag_set = {t.casefold() for t in meta_tags}
                        if tag_logic == "AND":
                            if not required_tags.issubset(tag_set):
                                continue
                        else:  # OR
                            if not required_tags.intersection(tag_set):
                                continue
                    # date filter
                    updated = (meta.get("updated_at") or "")
                    updated_date = updated[:10] if updated else ""
                    if date_from and updated_date and updated_date < date_from:
                        continue
                    if date_to and updated_date and updated_date > date_to:
                        continue
                    # declared_by filter
                    if declared_by and meta.get("declared_by", "") != declared_by:
                        continue
                    # content_type filter
                    if content_type:
                        ctype = str(self._get_node_attr(_path_id(p), "content_type", "") or "")
                        if content_type == "text" and ctype and not ctype.startswith("text/"):
                            continue
                        if content_type == "image" and ctype and not ctype.startswith("image/"):
                            continue
                    nid = _path_id(p)
                    description = str(meta.get("description", "") or self._get_node_attr(nid, "description", ""))
                    candidate = {
                        "path": p,
                        "description": description,
                        "tags": meta_tags,
                        "updated_at": updated,
                        "declared_by": str(meta.get("declared_by", "")),
                        "score_patch": float(meta.get("score_patch", 0.0)),
                    }
                    # text match score
                    if need_text_query:
                        text_lower = need_text_query.lower()
                        name_lower = Path(p).name.lower()
                        score = 0.0
                        if text_lower in name_lower:
                            score = 1.0
                        if text_lower in description.lower():
                            score = max(score, 0.8)
                        # also check content file
                        cp = self._content_path(p)
                        if cp.exists() and score < 0.5:
                            try:
                                content = cp.read_text(encoding="utf-8", errors="ignore")[:5000]
                                if need_text_query.lower() in content.lower():
                                    score = max(score, 0.6)
                            except Exception:
                                pass
                        candidate["score"] = score
                        if score == 0:
                            continue  # text query present but no match
                    else:
                        candidate["score"] = 0.0
                        # no text query - path, tag match is enough
                    candidates.append(candidate)
                except Exception:
                    continue

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

    def _ensure_bm25_index(self) -> None:
        if self._bm25_index is not None and not self._bm25_dirty:
            return
        from concurrent.futures import ThreadPoolExecutor, as_completed
        docs: list[dict] = []
        seen: set[str] = set()

        # try VDB file first (populated in hybrid mode)
        vdb_had_data = False
        if self.index_file.exists():
            log.info("Loading BM25 index from VDB file: %s", self.index_file)
            import json as _json
            raw = _json.loads(self.index_file.read_text(encoding="utf-8"))
            items = raw.get("data", [])
            if items:
                vdb_had_data = True
                path_chunks: dict[str, list[str]] = {}
                for item in items:
                    p = _normalize_path(str(item.get("node_path", "")))
                    if p:
                        path_chunks.setdefault(p, []).append(str(item.get("text", "")))
                for p, texts in path_chunks.items():
                    docs.append({"path": p, "text": " ".join(texts), "type": "content"})
                    seen.add(p)

        # BM25-only fallback: read chunk files in parallel
        if not vdb_had_data and self.meta_dir.exists():
            log.info("Loading BM25 index from chunk files in: %s", self.meta_dir)
            chunk_files = list(self.meta_dir.rglob("*.chunks.json"))
            if chunk_files:
                def _load_chunks(cf):
                    items = _read_json(cf, [])
                    if not items:
                        return None
                    path_set: dict[str, list[str]] = {}
                    for item in items:
                        p = _normalize_path(str(item.get("node_path", "")))
                        if p:
                            path_set.setdefault(p, []).append(str(item.get("text", "")))
                    return path_set
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futures = {ex.submit(_load_chunks, cf): cf for cf in chunk_files}
                    for f in as_completed(futures):
                        try:
                            path_set = f.result()
                            if path_set:
                                for p, texts in path_set.items():
                                    if p not in seen:
                                        docs.append({"path": p, "text": " ".join(texts), "type": "content"})
                                        seen.add(p)
                        except Exception:
                            pass

        # read meta descriptions in parallel
        if self.meta_dir.exists():
            log.info("Loading BM25 index from meta files in: %s", self.meta_dir)
            meta_files = list(self.meta_dir.rglob("*.meta.json"))
            if meta_files:
                def _load_meta(mf):
                    meta = _read_json(mf, {})
                    p = meta.get("path", "")
                    desc = str(meta.get("description", "") or "").strip()
                    return (p, desc) if p and desc else None
                with ThreadPoolExecutor(max_workers=8) as ex:
                    futures = {ex.submit(_load_meta, mf): mf for mf in meta_files}
                    for f in as_completed(futures):
                        try:
                            result = f.result()
                            if result:
                                p, desc = result
                                if p not in seen:
                                    docs.append({"path": p, "text": desc, "type": "description"})
                                    seen.add(p)
                        except Exception:
                            pass

        for nid, ndata in self._graph.nodes(data=True):
            if ndata.get("type") == "entity":
                name = str(ndata.get("name", "") or "").strip()
                desc = str(ndata.get("description", "") or "").strip()
                text = f"{name} {desc}".strip()
                if text:
                    docs.append({"path": nid, "text": text, "type": "entity"})

        if not docs:
            self._bm25_index = None
            self._bm25_docs = []
            self._bm25_corpus = None
            self._mark_bm25_clean()
            return

        tokenized = [_tokenize(d["text"]) for d in docs]
        self._bm25_corpus = tokenized
        self._bm25_index = BM25Okapi(tokenized)
        self._bm25_docs = docs
        self._mark_bm25_clean()

    def _bm25_search(self, query: str, top_k: int) -> list[dict]:
        self._ensure_bm25_index()
        if self._bm25_index is None or not self._bm25_docs:
            return []
        query_tokens = _tokenize(query)
        scores = self._bm25_index.get_scores(query_tokens)
        path_scores: dict[str, list[float]] = {}
        for doc, score in zip(self._bm25_docs, scores):
            path_scores.setdefault(doc["path"], []).append(score)
        results = []
        for path, sc_list in path_scores.items():
            results.append({"path": path, "score": sum(sc_list) / len(sc_list), "_source": "bm25"})
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:top_k]

    def _bm25_search_batch(self, queries: list[str], top_k: int) -> list[list[dict]]:
        self._ensure_bm25_index()
        if self._bm25_index is None or not self._bm25_corpus:
            return [[] for _ in queries]
        import multiprocessing as _mp
        from concurrent.futures import ProcessPoolExecutor
        q_tokens = [_tokenize(q) for q in queries]
        n_workers = min(len(queries), _mp.cpu_count() or 4)
        if n_workers <= 1:
            return [self._bm25_search(q, top_k) for q in queries]
        args_list = [(self._bm25_corpus, self._bm25_docs, toks, top_k) for toks in q_tokens]
        with ProcessPoolExecutor(max_workers=n_workers) as ex:
            return list(ex.map(_bm25_score_worker, args_list))

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
        await asyncio.to_thread(vdb.save)

    async def _delete_chunk_ids(self, chunk_ids: list[str]) -> int:
        if not chunk_ids or not self.index_file.exists():
            return 0
        try:
            vdb = self._ensure_vdb()
            vdb.delete(chunk_ids)
            await asyncio.to_thread(vdb.save)
            return len(chunk_ids)
        except Exception:
            return 0

    def _load_chunks_index(self) -> dict:
        return _read_json(self.chunks_index_file, {})

    def _save_chunks_index(self, data: dict) -> None:
        _atomic_write_json(self.chunks_index_file, data)

    def _load_tasks(self) -> list:
        return _read_json(self.tasks_file, [])

    def _save_tasks(self, tasks: list) -> None:
        _atomic_write_json(self.tasks_file, tasks[-200:])

    # ── entity / relation operations ──

    def entity_add(self, name: str, entity_type: str = "custom",
                   description: str = "",
                   properties: dict | None = None, kb_refs: list[str] | None = None,
                   name_embedding: list[float] | None = None, flush: bool = True) -> str:
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
                       kb_refs=all_refs, created_at=_utc_iso())
        if name_embedding:
            self._graph.nodes[eid]["_name_vec"] = [float(v) for v in name_embedding]
        self._dirty = True
        if flush:
            self.flush()
        log.info("entity_add name=%s type=%s eid=%s desc_len=%d refs=%d",
                 name, entity_type, eid[:16], len(desc), len(all_refs))
        return eid

    def entity_delete(self, entity_id: str, flush: bool = True) -> bool:
        if not self._has_node(entity_id):
            return False
        if self._get_node_attr(entity_id, "type") != "entity":
            return False
        self._graph.remove_node(entity_id)
        self._dirty = True
        if flush:
            self.flush()
        log.info("entity_delete eid=%s", entity_id[:16])
        return True

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

    def relation_add(self, source_id: str, target_id: str,
                     rel_type: str = "relates_to", flush: bool = True) -> str:
        key = self._add_edge(source_id, target_id, rel_type)
        if flush:
            self.flush()
        log.info("relation_add src=%s tgt=%s type=%s key=%s",
                 source_id[:16], target_id[:16], rel_type, key[:8])
        return key

    def relation_remove(self, source_id: str, target_id: str, flush: bool = True) -> None:
        self._remove_edge(source_id, target_id)
        if flush:
            self.flush()
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
        if not self._has_node(entity_id):
            log.warning("get_neighbors not_found eid=%s", entity_id[:16])
            return []
        seen: set[str] = set()
        current: set[str] = {entity_id}
        for _ in range(depth):
            nxt: set[str] = set()
            for nid in current:
                if nid in seen:
                    continue
                seen.add(nid)
                for neighbor in self._graph.neighbors(nid):
                    if neighbor not in seen:
                        nxt.add(neighbor)
                for predecessor in self._graph.predecessors(nid):
                    if predecessor not in seen:
                        nxt.add(predecessor)
            current = nxt
            if not current:
                break
        seen.discard(entity_id)
        results = []
        for nid in seen:
            ndata = self._graph.nodes[nid]
            ntype = ndata.get("type", "unknown")
            if ntype == "entity":
                results.append({
                    "id": nid, "name": ndata.get("name", ""),
                    "entity_type": ndata.get("entity_type", "custom"),
                    "description": ndata.get("description", ""),
                    "path_ref": None,
                })
            elif ntype in ("file", "dir"):
                results.append({
                    "id": nid, "name": ndata.get("name", ""),
                    "entity_type": ntype, "path_ref": _id_to_path(nid),
                })
        return results

    # ── semantic entity dedup ──

    async def _ensure_entity_name_vecs(self) -> None:
        missing: list[tuple[str, str]] = []
        for nid, ndata in self._graph.nodes(data=True):
            if ndata.get("type") != "entity":
                continue
            if "_name_vec" not in ndata:
                missing.append((nid, str(ndata.get("name", ""))))
                log.warning("entity missing name_vec eid=%s name=%s", nid[:16], ndata.get("name", ""))
        if not missing:
            return
        names = [name for _, name in missing]
        vecs = await self._embed_texts(names)
        for (nid, _), vec in zip(missing, vecs):
            self._graph.nodes[nid]["_name_vec"] = np.asarray(vec, dtype=np.float32).tolist()
            self._dirty = True
        await self._flush_async()

    async def entity_find_similar(self, name_vecs: list[np.ndarray],
                                   threshold: float = 0.85) -> list[str | None]:
        await self._ensure_entity_name_vecs()
        results: list[str | None] = []
        for qv in name_vecs:
            qa = np.asarray(qv, dtype=np.float32)
            best_eid: str | None = None
            best_sim = -1.0
            for nid, ndata in self._graph.nodes(data=True):
                if ndata.get("type") != "entity":
                    continue
                cached = ndata.get("_name_vec")
                if not cached:
                    continue
                sim = _cosine_sim(qa, np.asarray(cached, dtype=np.float32))
                if sim > best_sim:
                    best_sim = sim
                    best_eid = nid
            if best_sim >= threshold and best_eid:
                results.append(best_eid)
            else:
                results.append(None)
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

        # 2-hop expansion for matched paths
        extra: dict[str, dict] = {}
        for p in list(seen.keys()):
            nid = _path_id(p)
            if self._has_node(nid):
                nb = self.get_neighbors(nid, depth=2)
                for n in nb:
                    pref = n.get("path_ref")
                    if pref and pref not in seen and pref not in extra:
                        meta = self._read_meta(pref)
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
        try:
            hits = vdb.query(query=qv, top_k=max(top_k * 3, 10), better_than_threshold=None)
        except TypeError:
            hits = vdb.query(qv, top_k=max(top_k * 3, 10))

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
            meta = self._read_meta(node_path)
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
                score = float(hit.get("__score__", hit.get("score", 0)))
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
            bm25_results = self._bm25_search(query, top_k * 2)
            filtered: list[dict] = []
            required_tags = {t.casefold() for t in (tags or [])}
            for item in bm25_results:
                p = item["path"]
                if scope_prefix and not (p.startswith(scope_prefix) or p == scope_prefix.rstrip("/")):
                    continue
                if required_tags:
                    meta = self._read_meta(p)
                    if not required_tags.issubset({t.casefold() for t in meta.get("tags", [])}):
                        continue
                if p.startswith("ent_"):
                    continue
                meta = self._read_meta(p)
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
        bm25_results = self._bm25_search(query, top_k * 2)

        required_tags = {t.casefold() for t in (tags or [])}
        bm25_filtered = []
        for item in bm25_results:
            p = item["path"]
            if scope_prefix and not (p.startswith(scope_prefix) or p == scope_prefix.rstrip("/")):
                continue
            if required_tags:
                meta = self._read_meta(p)
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
            meta = self._read_meta(p)
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
            meta = self._read_meta(p)
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
                        meta2 = self._read_meta(pref)
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
            meta = self._read_meta(path_str)
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
                meta = self._read_meta(item["path"])
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
                                tags: list[str] | None = None) -> list[dict]:
        scope_prefix = _normalize_path(scope or "").strip("/")
        scope_prefix = f"{scope_prefix}/" if scope_prefix else ""
        required_tags = {t.casefold() for t in (tags or [])}
        results = []
        for cp in sorted(self.meta_dir.rglob("*.meta.json")):
            meta = _read_json(cp, {})
            node_path = str(meta.get("path", ""))
            if not node_path:
                continue
            updated_at = str(meta.get("updated_at", ""))
            try:
                updated_ts = float(time.mktime(time.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")))
            except Exception:
                updated_ts = 0.0
            if updated_ts < since_ts:
                continue
            if scope_prefix and not node_path.startswith(scope_prefix):
                continue
            ntags = meta.get("tags", [])
            if required_tags:
                if not required_tags.issubset({t.casefold() for t in ntags}):
                    continue
            results.append({
                "path": node_path,
                "updated_at": updated_at,
                "tags": ntags,
                "score_patch": float(meta.get("score_patch", 0.0)),
            })
        results.sort(key=lambda x: x["updated_at"], reverse=True)
        log.info("get_changed_nodes since=%s scope=%s hits=%d", since_ts, scope or "/", len(results))
        return results

    # ── tasks ──

    def get_tasks(self) -> list[dict]:
        tasks = list(reversed(self._load_tasks()))
        log.info("get_tasks count=%d", len(tasks))
        return tasks

    def add_task(self, task_type: str, payload: dict | None = None) -> dict:
        tasks = self._load_tasks()
        task = {
            "task_id": f"tsk_{uuid.uuid4().hex}",
            "type": task_type,
            "status": "pending",
            "payload": payload or {},
            "created_at": _utc_iso(),
            "updated_at": _utc_iso(),
            "error": "",
        }
        tasks.append(task)
        self._save_tasks(tasks)
        log.info("add_task type=%s task_id=%s", task_type, task["task_id"][:12])
        return task

    def update_task(self, task_id: str, status: str, error: str = "") -> None:
        tasks = self._load_tasks()
        for t in tasks:
            if t.get("task_id") == task_id:
                t["status"] = status
                t["updated_at"] = _utc_iso()
                t["error"] = error
                break
        self._save_tasks(tasks)
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
        eid = self.entity_add(name, entity_type=record_type, properties=props, kb_refs=[path])
        nid = _path_id(path)
        if self._has_node(nid):
            self._add_edge(nid, eid, TREE_EDGE)
        prev = self._find_latest_entity(record_type, exclude=eid)
        if prev:
            self._add_edge(prev, eid, "next")
        await self._flush_async()

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
