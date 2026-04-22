from __future__ import annotations

import asyncio
import calendar
import json
import math
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from nano_vectordb import NanoVectorDB
from openai import AsyncOpenAI

import faust_backend.config_loader as conf


EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
MAX_CHUNK_CHARS = 3000
CHUNK_OVERLAP_CHARS = 300
MAX_TASK_HISTORY = 200
MIN_SCORE_PATCH = -0.15
MAX_SCORE_PATCH = 0.15


def _utc_ts() -> float:
    return time.time()


def _utc_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _normalize_scope(scope: str | None) -> str:
    text = str(scope or "").replace("\\", "/").strip()
    text = text.strip("/")
    return f"{text}/" if text else ""


def _normalize_tags(tags: list[str] | tuple[str, ...] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        value = str(tag or "").strip()
        if not value:
            continue
        lowered = value.casefold()
        if lowered in seen:
            continue
        seen.add(lowered)
        normalized.append(value)
    return normalized


def _normalize_score_patch(value: float | int | str | None) -> float:
    try:
        patch = float(value or 0.0)
    except Exception as exc:
        raise ValueError(f"score_patch 非法: {value}") from exc
    if not math.isfinite(patch):
        raise ValueError("score_patch 必须是有限数值")
    if patch < MIN_SCORE_PATCH or patch > MAX_SCORE_PATCH:
        raise ValueError(f"score_patch 超出范围，必须位于 [{MIN_SCORE_PATCH}, {MAX_SCORE_PATCH}]")
    return patch


def _normalize_kb_path(path: str) -> str:
    text = str(path or "").replace("\\", "/").strip()
    text = text.lstrip("/")
    normalized = Path(text).as_posix().strip()
    if not normalized or normalized == ".":
        raise ValueError("path 不能为空")
    if normalized.startswith("../") or "/../" in f"/{normalized}" or normalized == "..":
        raise ValueError("path 非法，不能越过 KB 根目录")
    return normalized


def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap_chars: int = CHUNK_OVERLAP_CHARS) -> List[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if len(normalized) <= max_chars:
        return [normalized]
    chunks: List[str] = []
    step = max(1, max_chars - overlap_chars)
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + max_chars)
        chunk = normalized[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(normalized):
            break
        start += step
    return chunks


@dataclass
class KBPaths:
    agent_root: Path
    kb_root: Path
    meta_root: Path
    index_root: Path
    index_file: Path
    chunks_index_file: Path
    tasks_file: Path


class KBManager:
    def __init__(self, agent_name: str | None = None):
        self.agent_name = str(agent_name or conf.AGENT_NAME)
        self.agent_root = Path(conf.CONFIG_ROOT) / "agents" / self.agent_name
        self.paths = KBPaths(
            agent_root=self.agent_root,
            kb_root=self.agent_root / "kb",
            meta_root=self.agent_root / "kb_meta",
            index_root=self.agent_root / "kb_index",
            index_file=self.agent_root / "kb_index" / "chunks.vdb",
            chunks_index_file=self.agent_root / "kb_meta" / "chunks_index.json",
            tasks_file=self.agent_root / "kb_meta" / "tasks.json",
        )
        self._write_lock = asyncio.Lock()
        self._task_lock = asyncio.Lock()
        self._worker_started = False
        self._queue: asyncio.Queue[dict] = asyncio.Queue()
        self._openai_client: AsyncOpenAI | None = None
        self._vdb: NanoVectorDB | None = None
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        self.paths.kb_root.mkdir(parents=True, exist_ok=True)
        self.paths.meta_root.mkdir(parents=True, exist_ok=True)
        self.paths.index_root.mkdir(parents=True, exist_ok=True)
        if not self.paths.chunks_index_file.exists():
            _atomic_write_json(self.paths.chunks_index_file, {})
        if not self.paths.tasks_file.exists():
            _atomic_write_json(self.paths.tasks_file, [])

    def refresh_agent(self, agent_name: str | None = None) -> None:
        target = str(agent_name or conf.AGENT_NAME)
        if target == self.agent_name:
            return
        self.__init__(target)

    def _node_file(self, path: str) -> Path:
        return self.paths.kb_root / _normalize_kb_path(path)

    def _meta_file(self, path: str) -> Path:
        return self.paths.meta_root / f"{_normalize_kb_path(path)}.meta.json"

    def _chunks_file(self, path: str) -> Path:
        return self.paths.meta_root / f"{_normalize_kb_path(path)}.chunks.json"

    def _iter_meta_files(self):
        if not self.paths.meta_root.exists():
            return []
        return sorted(self.paths.meta_root.rglob("*.meta.json"))

    def _read_meta(self, path: str) -> dict:
        norm = _normalize_kb_path(path)
        meta = _read_json(self._meta_file(norm), {})
        meta.setdefault("path", norm)
        meta["tags"] = _normalize_tags(meta.get("tags") or [])
        try:
            meta["score_patch"] = _normalize_score_patch(meta.get("score_patch", 0.0))
        except ValueError:
            meta["score_patch"] = 0.0
        return meta

    def _write_meta(self, path: str, meta: dict) -> dict:
        norm = _normalize_kb_path(path)
        merged = dict(meta or {})
        merged["path"] = norm
        merged["tags"] = _normalize_tags(merged.get("tags") or [])
        merged["score_patch"] = _normalize_score_patch(merged.get("score_patch", 0.0))
        _atomic_write_json(self._meta_file(norm), merged)
        return merged

    def _load_chunks_index(self) -> dict:
        return _read_json(self.paths.chunks_index_file, {})

    def _save_chunks_index(self, data: dict) -> None:
        _atomic_write_json(self.paths.chunks_index_file, data)

    def _load_tasks(self) -> list:
        return _read_json(self.paths.tasks_file, [])

    def _save_tasks(self, tasks: list) -> None:
        _atomic_write_json(self.paths.tasks_file, tasks[-MAX_TASK_HISTORY:])

    def _new_task(self, task_type: str, payload: dict | None = None) -> dict:
        return {
            "task_id": f"kbtsk_{uuid.uuid4().hex}",
            "type": task_type,
            "status": "pending",
            "payload": payload or {},
            "created_at": _utc_iso(),
            "updated_at": _utc_iso(),
            "error": "",
        }

    async def ensure_worker_started(self) -> None:
        if self._worker_started:
            return
        self._worker_started = True
        asyncio.create_task(self._worker_loop())

    async def _update_task_status(self, task_id: str, status: str, error: str = "") -> None:
        async with self._task_lock:
            tasks = self._load_tasks()
            for item in tasks:
                if str(item.get("task_id")) == str(task_id):
                    item["status"] = status
                    item["updated_at"] = _utc_iso()
                    item["error"] = error or ""
                    break
            self._save_tasks(tasks)

    async def enqueue_task(self, task_type: str, payload: dict | None = None) -> dict:
        await self.ensure_worker_started()
        task = self._new_task(task_type, payload)
        async with self._task_lock:
            tasks = self._load_tasks()
            tasks.append(task)
            self._save_tasks(tasks)
        await self._queue.put(task)
        return task

    async def _worker_loop(self) -> None:
        while True:
            task = await self._queue.get()
            task_id = str(task.get("task_id") or "")
            try:
                await self._update_task_status(task_id, "running")
                task_type = str(task.get("type") or "")
                payload = task.get("payload") or {}
                if task_type == "reindex_all":
                    await self._rebuild_index_snapshot()
                elif task_type in {"write_node", "declare_file_update"}:
                    await self._reindex_single_node(str(payload.get("path") or ""))
                elif task_type == "delete_node":
                    await self._delete_node_chunks(str(payload.get("path") or ""))
                await self._update_task_status(task_id, "done")
            except Exception as exc:
                await self._update_task_status(task_id, "failed", str(exc))
            finally:
                self._queue.task_done()

    def _build_openai_client(self) -> AsyncOpenAI:
        api_key = getattr(conf, "KB_OPENAI_API_KEY", "") or getattr(conf, "CHAT_API_KEY", "")
        base_url = getattr(conf, "CHAT_API_BASE", "https://api.openai.com/v1")
        if not api_key:
            raise RuntimeError("缺少可用的 OpenAI API Key，无法构建 KB embedding")
        return AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _get_openai_client(self) -> AsyncOpenAI:
        if self._openai_client is None:
            self._openai_client = self._build_openai_client()
        return self._openai_client

    async def _embed_texts(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)
        client = self._get_openai_client()
        response = await client.embeddings.create(model=EMBED_MODEL, input=texts)
        return np.array([item.embedding for item in response.data], dtype=np.float32)

    def _create_vdb(self) -> NanoVectorDB:
        try:
            return NanoVectorDB(EMBED_DIM, storage_file=str(self.paths.index_file))
        except Exception:
            if self.paths.index_file.exists():
                self.paths.index_file.unlink()
            return NanoVectorDB(EMBED_DIM, storage_file=str(self.paths.index_file))

    def _get_vdb(self, force_reload: bool = False) -> NanoVectorDB:
        if force_reload or self._vdb is None:
            self._vdb = self._create_vdb()
        return self._vdb

    def _replace_vdb(self) -> NanoVectorDB:
        self._vdb = self._create_vdb()
        return self._vdb

    def ensure_vdb_initialized(self, force_reload: bool = False) -> NanoVectorDB:
        vdb = self._get_vdb(force_reload=force_reload)
        if force_reload or not self.paths.index_file.exists():
            vdb.save()
        return vdb

    def _delete_chunk_ids_from_vdb(self, chunk_ids: list[str]) -> int:
        if not chunk_ids:
            return 0
        if not self.paths.index_file.exists():
            return 0
        vdb = self._get_vdb()
        try:
            vdb.delete(chunk_ids)
            vdb.save()
            return len(chunk_ids)
        except Exception:
            return 0

    async def _reindex_single_node(self, path: str) -> dict:
        norm = _normalize_kb_path(path)
        chunks_file = self._chunks_file(norm)
        chunk_items = _read_json(chunks_file, [])
        chunks_index = self._load_chunks_index()

        old_chunk_ids = [
            chunk_id for chunk_id, item in list(chunks_index.items())
            if str(item.get("node_path") or "") == norm
        ]
        if old_chunk_ids:
            self._delete_chunk_ids_from_vdb(old_chunk_ids)

        valid_items = []
        texts = []
        for item in chunk_items:
            if not isinstance(item, dict):
                continue
            if not item.get("indexed", True):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            valid_items.append(item)
            texts.append(text)

        for chunk_id in old_chunk_ids:
            chunks_index.pop(chunk_id, None)

        if not valid_items:
            self._save_chunks_index(chunks_index)
            return {"path": norm, "indexed_chunks": 0}

        embeddings = await self._embed_texts(texts)
        rows = []
        for item, vector in zip(valid_items, embeddings):
            chunk_id = str(item.get("chunk_id") or "")
            row = {
                "__id__": chunk_id,
                "__vector__": np.asarray(vector, dtype=np.float32),
                "node_path": str(item.get("node_path") or norm),
                "scope_prefix": str(item.get("scope_prefix") or ""),
                "chunk_index": int(item.get("chunk_index") or 0),
                "text": str(item.get("text") or ""),
                "text_preview": str(item.get("text_preview") or str(item.get("text") or "")[:120]),
            }
            rows.append(row)
            chunks_index[chunk_id] = dict(item)

        vdb = self._get_vdb()
        vdb.upsert(rows)
        vdb.save()
        self._save_chunks_index(chunks_index)
        return {"path": norm, "indexed_chunks": len(rows)}

    async def _delete_node_chunks(self, path: str) -> dict:
        norm = _normalize_kb_path(path)
        chunks_index = self._load_chunks_index()
        delete_prefix = f"{norm}/"
        chunk_ids = []
        for chunk_id, item in list(chunks_index.items()):
            node_path = str(item.get("node_path") or "")
            if node_path == norm or node_path.startswith(delete_prefix):
                chunk_ids.append(chunk_id)
                chunks_index.pop(chunk_id, None)
        deleted = self._delete_chunk_ids_from_vdb(chunk_ids)
        self._save_chunks_index(chunks_index)
        return {"path": norm, "deleted_chunks": deleted}

    async def _rebuild_index_snapshot(self) -> dict:
        chunks_index = self._load_chunks_index()
        rows: list[dict] = []
        texts: list[str] = []
        ordered_items = []
        for chunk_id, item in chunks_index.items():
            if not isinstance(item, dict):
                continue
            if not item.get("indexed", True):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            ordered_items.append((chunk_id, item, text))
            texts.append(text)

        if self.paths.index_file.exists():
            self.paths.index_file.unlink()

        vdb = self._replace_vdb()
        if not ordered_items:
            vdb.save()
            return {"indexed_chunks": 0}

        embeddings = await self._embed_texts(texts)
        for (chunk_id, item, text), vector in zip(ordered_items, embeddings):
            rows.append({
                "__id__": chunk_id,
                "__vector__": np.asarray(vector, dtype=np.float32),
                "node_path": str(item.get("node_path") or ""),
                "scope_prefix": str(item.get("scope_prefix") or ""),
                "chunk_index": int(item.get("chunk_index") or 0),
                "text": text,
                "text_preview": str(item.get("text_preview") or text[:120]),
            })
        vdb.upsert(rows)
        vdb.save()
        return {"indexed_chunks": len(rows)}

    def list_tree(self, scope: str | None = None) -> dict:
        scope_prefix = _normalize_scope(scope)
        base = self.paths.kb_root / scope_prefix if scope_prefix else self.paths.kb_root
        if not base.exists():
            return {"path": scope_prefix or "/", "type": "dir", "children": []}

        def build(node: Path) -> dict:
            rel = node.relative_to(self.paths.kb_root).as_posix() if node != self.paths.kb_root else ""
            if node.is_dir():
                children = [build(child) for child in sorted(node.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))]
                return {"path": rel, "name": node.name or "/", "type": "dir", "children": children}
            return {"path": rel, "name": node.name, "type": "file"}

        return build(base)

    def read_node(self, path: str) -> dict:
        norm = _normalize_kb_path(path)
        file_path = self._node_file(norm)
        if not file_path.exists() or not file_path.is_file():
            raise FileNotFoundError(f"KB 节点不存在: {norm}")
        meta = self._read_meta(norm)
        return {
            "path": norm,
            "content": file_path.read_text(encoding="utf-8"),
            "meta": meta,
        }

    async def write_node(self, path: str, content: str, declared_by: str | None = None, index: bool = True, tags: list[str] | None = None) -> dict:
        norm = _normalize_kb_path(path)
        async with self._write_lock:
            file_path = self._node_file(norm)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = file_path.with_suffix(file_path.suffix + ".tmp")
            tmp.write_text(str(content or ""), encoding="utf-8")
            os.replace(tmp, file_path)

            chunks = _chunk_text(content)
            existing_meta = self._read_meta(norm) if self._meta_file(norm).exists() else {"path": norm, "tags": [], "score_patch": 0.0}
            meta = {
                "path": norm,
                "declared_by": declared_by or "agent",
                "updated_at": _utc_iso(),
                "source": "kb_write",
                "chunk_count": len(chunks),
                "indexed": bool(index),
                "tags": _normalize_tags(tags if tags is not None else existing_meta.get("tags") or []),
                "score_patch": _normalize_score_patch(existing_meta.get("score_patch", 0.0)),
                "score_patch_updated_at": existing_meta.get("score_patch_updated_at", ""),
                "managed_by": existing_meta.get("managed_by", ""),
            }
            meta = self._write_meta(norm, meta)

            chunk_items = []
            chunks_index = self._load_chunks_index()
            for chunk_id, item in list(chunks_index.items()):
                if str(item.get("node_path") or "") == norm:
                    chunks_index.pop(chunk_id, None)

            for idx, chunk_text in enumerate(chunks, start=1):
                chunk_id = f"{norm}::chunk::{idx}::{uuid.uuid4().hex[:8]}"
                item = {
                    "chunk_id": chunk_id,
                    "node_path": norm,
                    "chunk_index": idx,
                    "text": chunk_text,
                    "text_preview": chunk_text[:120],
                    "scope_prefix": str(Path(norm).parent.as_posix()).strip("."),
                    "updated_at": _utc_iso(),
                    "indexed": bool(index),
                }
                chunk_items.append(item)
                chunks_index[chunk_id] = item

            _atomic_write_json(self._chunks_file(norm), chunk_items)
            self._save_chunks_index(chunks_index)

        task = None
        if index:
            task = await self.enqueue_task("write_node", {"path": norm})
        return {"path": norm, "meta": meta, "task": task}

    async def set_tags(self, path: str, tags: list[str], managed_by: str | None = None) -> dict:
        norm = _normalize_kb_path(path)
        node = self.read_node(norm)
        meta = dict(node.get("meta") or {})
        meta["tags"] = _normalize_tags(tags)
        meta["updated_at"] = _utc_iso()
        if managed_by is not None:
            meta["managed_by"] = str(managed_by or "")
        meta = self._write_meta(norm, meta)
        return {"path": norm, "meta": meta}

    async def set_score_patch(self, path: str, score_patch: float, managed_by: str | None = None) -> dict:
        norm = _normalize_kb_path(path)
        node = self.read_node(norm)
        meta = dict(node.get("meta") or {})
        meta["score_patch"] = _normalize_score_patch(score_patch)
        meta["score_patch_updated_at"] = _utc_iso()
        meta["updated_at"] = _utc_iso()
        if managed_by is not None:
            meta["managed_by"] = str(managed_by or "")
        meta = self._write_meta(norm, meta)
        return {"path": norm, "meta": meta}

    def get_changed_nodes(self, since_ts: float | int | str, scope: str | None = None, tags: list[str] | None = None) -> list[dict]:
        try:
            since_value = float(since_ts)
        except Exception as exc:
            raise ValueError("since_ts 非法") from exc
        scope_prefix = _normalize_scope(scope)
        required_tags = {item.casefold() for item in _normalize_tags(tags)}
        items: list[dict] = []
        for meta_file in self._iter_meta_files():
            meta = _read_json(meta_file, {})
            node_path = str(meta.get("path") or "")
            if not node_path:
                continue
            if scope_prefix and not (node_path == scope_prefix.rstrip("/") or node_path.startswith(scope_prefix)):
                continue
            normalized_tags = _normalize_tags(meta.get("tags") or [])
            if required_tags:
                current_tags = {item.casefold() for item in normalized_tags}
                if not required_tags.issubset(current_tags):
                    continue
            updated_at = str(meta.get("updated_at") or "")
            try:
                updated_ts = float(calendar.timegm(time.strptime(updated_at, "%Y-%m-%dT%H:%M:%SZ")))
            except Exception:
                updated_ts = 0.0
            if updated_ts < since_value:
                continue
            items.append({
                "path": node_path,
                "updated_at": updated_at,
                "tags": normalized_tags,
                "score_patch": float(meta.get("score_patch") or 0.0),
                "managed_by": str(meta.get("managed_by") or ""),
            })
        return sorted(items, key=lambda item: str(item.get("updated_at") or ""), reverse=True)

    async def mkdir(self, path: str) -> dict:
        norm = _normalize_kb_path(path)
        target = self._node_file(norm)
        target.mkdir(parents=True, exist_ok=True)
        return {"path": norm, "type": "dir"}

    async def delete_node(self, path: str) -> dict:
        norm = _normalize_kb_path(path)
        async with self._write_lock:
            target = self._node_file(norm)
            if target.is_dir():
                for child in sorted(target.rglob("*"), reverse=True):
                    if child.is_file():
                        child.unlink()
                    elif child.is_dir():
                        child.rmdir()
                if target.exists():
                    target.rmdir()
                chunks_index = self._load_chunks_index()
                delete_prefix = f"{norm}/"
                for chunk_id, item in list(chunks_index.items()):
                    node_path = str(item.get("node_path") or "")
                    if node_path == norm or node_path.startswith(delete_prefix):
                        chunks_index.pop(chunk_id, None)
                self._save_chunks_index(chunks_index)
                for meta_file in sorted(self.paths.meta_root.glob(f"{norm}/**/*.meta.json"), reverse=True):
                    if meta_file.exists():
                        meta_file.unlink()
                for chunks_file in sorted(self.paths.meta_root.glob(f"{norm}/**/*.chunks.json"), reverse=True):
                    if chunks_file.exists():
                        chunks_file.unlink()
            else:
                if not target.exists():
                    raise FileNotFoundError(f"KB 节点不存在: {norm}")
                target.unlink()
                meta_file = self._meta_file(norm)
                chunks_file = self._chunks_file(norm)
                if meta_file.exists():
                    meta_file.unlink()
                if chunks_file.exists():
                    chunks_file.unlink()
                chunks_index = self._load_chunks_index()
                for chunk_id, item in list(chunks_index.items()):
                    if str(item.get("node_path") or "") == norm:
                        chunks_index.pop(chunk_id, None)
                self._save_chunks_index(chunks_index)
        task = await self.enqueue_task("delete_node", {"path": norm})
        return {"path": norm, "task": task}

    async def add_chat_record(self, user_text: str, assistant_text: str, meta: dict | None = None) -> dict:
        stamp = time.strftime("%Y-%m-%d/%H%M%S", time.localtime())
        suffix = uuid.uuid4().hex[:6]
        path = f"records/{stamp}_{suffix}.md"
        content = f"## 用户\n\n{str(user_text).strip()}\n\n## 助手\n\n{str(assistant_text).strip()}\n"
        result = await self.write_node(path, content, declared_by="chat_record", index=True)
        if meta:
            merged = dict(result.get("meta") or {})
            merged["chat_meta"] = meta
            _atomic_write_json(self._meta_file(path), merged)
            result["meta"] = merged
        return result

    async def write_diary(self, content: str) -> dict:
        stamp = time.strftime("%Y-%m-%d/%H%M%S", time.localtime())
        suffix = uuid.uuid4().hex[:6]
        path = f"diary/{stamp}_{suffix}.md"
        return await self.write_node(path, str(content or ""), declared_by="diary", index=True)

    async def declare_file_update(self, file_path: str, kb_path: str | None = None) -> dict:
        source = Path(file_path).resolve()
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"源文件不存在: {source}")
        target = kb_path or f"imports/{source.name}"
        result = await self.write_node(target, source.read_text(encoding="utf-8"), declared_by=str(source), index=True)
        meta = dict(result.get("meta") or {})
        meta["bound_file"] = str(source)
        _atomic_write_json(self._meta_file(target), meta)
        result["meta"] = meta
        return result

    async def search(
        self,
        query: str,
        scope: str | None = None,
        top_k: int = 8,
        return_mode: str = "snippets",
        tags: list[str] | None = None,
        ignore_score_patch: bool = False,
    ) -> list[dict]:
        scope_prefix = _normalize_scope(scope)
        query_text = str(query or "").strip()
        required_tags = {item.casefold() for item in _normalize_tags(tags)}
        if not query_text and not required_tags:
            return []
        if not query_text:
            items = self.get_changed_nodes(0, scope=scope, tags=list(required_tags))
            results = []
            for item in items[:top_k]:
                patch = 0.0 if ignore_score_patch else float(item.get("score_patch") or 0.0)
                results.append({
                    "path": item.get("path"),
                    "raw_score": 0.0,
                    "score_patch": patch,
                    "score": patch,
                    "tags": list(item.get("tags") or []),
                    "snippet": "",
                })
            return results
        if not self.paths.index_file.exists():
            return []
        emb = await self._embed_texts([query_text])
        vdb = self._get_vdb()
        try:
            hits = vdb.query(query=emb[0].tolist(), top_k=max(top_k * 3, top_k), better_than_threshold=None)
        except TypeError:
            hits = vdb.query(emb[0].tolist(), top_k=max(top_k * 3, top_k))

        if isinstance(hits, dict):
            raw_hits = [hits]
        else:
            raw_hits = list(hits or [])

        best_by_path: dict[str, dict] = {}
        for item in raw_hits:
            hit = dict(item) if isinstance(item, dict) else {}
            node_path = str(hit.get("node_path") or "")
            if not node_path:
                continue
            if scope_prefix and not (node_path == scope_prefix.rstrip("/") or node_path.startswith(scope_prefix)):
                continue
            meta = self._read_meta(node_path)
            normalized_tags = list(meta.get("tags") or [])
            if required_tags:
                current_tags = {tag.casefold() for tag in normalized_tags}
                if not required_tags.issubset(current_tags):
                    continue
            metrics = hit.get("__metrics__")
            if isinstance(metrics, dict):
                score = metrics.get("cosine_similarity", metrics.get("score", 0))
            elif metrics is not None:
                score = metrics
            else:
                score = hit.get("__score__", hit.get("score", 0))
            raw_score = float(score or 0)
            if not math.isfinite(raw_score):
                raw_score = 0.0
            score_patch = 0.0 if ignore_score_patch else float(meta.get("score_patch") or 0.0)
            score = raw_score + score_patch
            if not math.isfinite(score):
                score = 0.0
            current = best_by_path.get(node_path)
            if current is None or float(current.get("score") or 0) < score:
                row = {
                    "path": node_path,
                    "raw_score": raw_score,
                    "score_patch": score_patch,
                    "score": score,
                    "tags": normalized_tags,
                    "snippet": str(hit.get("text_preview") or hit.get("text") or ""),
                }
                if return_mode == "full":
                    try:
                        row["content"] = self.read_node(node_path)["content"]
                    except Exception:
                        row["content"] = ""
                best_by_path[node_path] = row

        results = sorted(best_by_path.values(), key=lambda x: float(x.get("score") or 0), reverse=True)
        return results[:top_k]

    def get_tasks(self) -> list[dict]:
        return list(reversed(self._load_tasks()))


_KB_MANAGER: KBManager | None = None


def get_kb_manager(refresh: bool = False) -> KBManager:
    global _KB_MANAGER
    if _KB_MANAGER is None:
        _KB_MANAGER = KBManager(conf.AGENT_NAME)
    elif refresh:
        _KB_MANAGER.refresh_agent(conf.AGENT_NAME)
    return _KB_MANAGER