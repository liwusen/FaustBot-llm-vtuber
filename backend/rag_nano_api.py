#!/usr/bin/env python
import argparse
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from hashlib import md5
from pathlib import Path
from typing import Any, Literal, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from nano_vectordb import NanoVectorDB



print("Booting nano RAG API...")

argparser = argparse.ArgumentParser(
    description="Nano VectorDB RAG API Service\n与 LightRAG api.py 接口兼容，优先保证响应速度。"
)
argparser.add_argument("--rag-openai-api-key", type=str, help="RAG OpenAI API Key")
argparser.add_argument(
    "--rag-openai-base-url",
    type=str,
    default=os.getenv("LIGHTRAG_OPENAI_BASE_URL", os.getenv("OPENAI_API_BASE", "https://www.dmxapi.cn/v1")),
    help="RAG OpenAI Base URL",
)
argparser.add_argument(
    "--rag-chat-model",
    type=str,
    default=os.getenv("LIGHTRAG_CHAT_MODEL", "qwen3.5-27b"),
    help="RAG Chat Model Name",
)
argparser.add_argument(
    "--rag-embed-model",
    type=str,
    default=os.getenv("LIGHTRAG_EMBED_MODEL", "text-embedding-3-small"),
    help="RAG Embed Model Name",
)
argparser.add_argument(
    "--host",
    type=str,
    default=os.getenv("LIGHTRAG_HOST", "127.0.0.1"),
    help="Bind host",
)
argparser.add_argument(
    "--port",
    type=int,
    default=int(os.getenv("LIGHTRAG_PORT", "18080")),
    help="Bind port",
)
args = argparser.parse_args()


from faust_backend import config_loader as conf

BASE_DIR = Path(__file__).resolve().parent
WORKING_ROOT_DIR = BASE_DIR / "rag_storage_nano"
WORKING_ROOT_DIR.mkdir(parents=True, exist_ok=True)

HOST = args.host
PORT = args.port

DEFAULT_TOP_K = 8
DEFAULT_CHUNK_SIZE = 600
DEFAULT_CHUNK_OVERLAP = 120
MAX_CONTEXT_CHUNKS = 12
QUERY_CONCURRENCY = 8


def utc_now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def parse_time_like(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    # 支持前端常见格式："YYYY-MM-DD HH:mm:ss"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            pass

    # 支持 ISO 格式（含 Z）
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        # 转成 naive UTC 语义便于和存储值比较
        return dt.replace(tzinfo=None)
    except Exception:
        return None


def _load_runtime_defaults():
    if conf is not None:
        try:
            conf.reload_configs()
        except Exception:
            pass

    return {
        "api_key": args.rag_openai_api_key or getattr(conf, "RAG_OPENAI_API_KEY", ""),
        "base_url": args.rag_openai_base_url or getattr(conf, "RAG_LLM_BASE_URL", "https://www.dmxapi.cn/v1"),
        "chat_model": args.rag_chat_model or getattr(conf, "RAG_CHAT_MODEL", "qwen3.5-27b"),
        "embed_model": args.rag_embed_model or getattr(conf, "RAG_EMBED_MODEL", "text-embedding-3-small"),
        "embed_dim": int(getattr(conf, "RAG_EMBED_DIM", 1536)),
        "embed_max_token_size": int(getattr(conf, "RAG_EMBED_MAX_TOKEN_SIZE", 8192)),
        "agent_name": getattr(conf, "AGENT_NAME", os.getenv("LIGHTRAG_AGENT_NAME", "default")),
        "chunk_size": int(getattr(conf, "RAG_CHUNK_SIZE", DEFAULT_CHUNK_SIZE)),
        "chunk_overlap": int(getattr(conf, "RAG_CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP)),
        "top_k": int(getattr(conf, "RAG_TOP_K", DEFAULT_TOP_K)),
    }


runtime_config = _load_runtime_defaults()


class InsertRequest(BaseModel):
    text: str = Field(..., min_length=1, description="要写入 RAG 的文本")
    doc_id: Optional[str] = Field(default=None, description="可选，自定义文档 ID")
    file_path: Optional[str] = Field(default=None, description="可选，文档来源路径")


class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="查询问题")
    mode: Literal["naive", "local", "global", "hybrid", "mix"] = Field(default="hybrid")
    only_need_context: bool = Field(default=True, description="是否只返回检索到的上下文，不使用模型总结")
    response_type: str = Field(default="Multiple Paragraphs", description="模型回答格式，仅在非 only_need_context 模式下生效")
    enable_rerank: bool = Field(default=False, description="是否启用 rerank")


class ConfigRequest(BaseModel):
    api_key: Optional[str] = Field(default=None, description="OpenAI 兼容接口 API Key")
    base_url: Optional[str] = Field(default=None, description="OpenAI 兼容接口 Base URL")
    chat_model: Optional[str] = Field(default=None, description="聊天模型名")
    embed_model: Optional[str] = Field(default=None, description="向量模型名")
    embed_dim: Optional[int] = Field(default=None, ge=1, description="向量维度")
    embed_max_token_size: Optional[int] = Field(default=None, ge=1, description="向量模型最大 token 数")
    agent_name: Optional[str] = Field(default=None, description="当前要切换到的 RAG Agent 名称，对应独立记忆库")


class AgentSwitchRequest(BaseModel):
    agent_name: str = Field(..., min_length=1, description="要切换到的 RAG Agent 名称")


class QueryResponse(BaseModel):
    status: str = "ok"
    query: str
    mode: str
    answer: str


class ErrorResponse(BaseModel):
    status: str = "error"
    message: str


class InsertResponse(BaseModel):
    status: str = "ok"
    doc_id: str
    track_id: str
    inserted_length: int


class DeleteResponse(BaseModel):
    status: str
    doc_id: str
    message: str
    file_path: Optional[str] = None


class DocumentRecord(BaseModel):
    doc_id: str
    status: str
    content_summary: str = ""
    content_length: int = 0
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    file_path: Optional[str] = None
    track_id: str = ""
    chunks_count: int = 0


class DocumentsResponse(BaseModel):
    status: str = "ok"
    documents: list[DocumentRecord]


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class DocumentsPageResponse(BaseModel):
    status: str = "ok"
    documents: list[DocumentRecord]
    pagination: Pagination


class DocumentDetailResponse(BaseModel):
    status: str = "ok"
    document: dict[str, Any]


class StatusResponse(BaseModel):
    status: str
    working_dir: str
    initialized: bool
    agent_name: str
    base_url: str
    chat_model: str
    embed_model: str


def sanitize_agent_name(agent_name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in agent_name.strip())
    return safe or "default"


def get_working_dir(agent_name: str | None = None) -> Path:
    selected_name = sanitize_agent_name(agent_name or runtime_config["agent_name"])
    working_dir = WORKING_ROOT_DIR / selected_name
    working_dir.mkdir(parents=True, exist_ok=True)
    return working_dir


def make_doc_id(text: str, doc_id: str | None = None) -> str:
    if doc_id:
        return doc_id
    return f"doc-{md5(text.encode('utf-8')).hexdigest()}"


def normalize_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = normalize_text(text)
    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    chunks = []
    start = 0
    step = max(1, chunk_size - overlap)
    while start < len(text):
        end = min(len(text), start + chunk_size)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start += step
    return chunks


def short_summary(text: str, limit: int = 120) -> str:
    text = normalize_text(text)
    return text[:limit]


class AgentStorage:
    def __init__(self, working_dir: Path, embed_dim: int):
        self.working_dir = working_dir
        self.embed_dim = embed_dim
        self.docs_path = self.working_dir / "docs.json"
        self.chunks_meta_path = self.working_dir / "chunks_meta.json"
        self.db_path = str(self.working_dir / "chunks.vdb")
        self.docs: dict[str, dict[str, Any]] = {}
        self.chunks_meta: dict[str, dict[str, Any]] = {}
        self.vdb = NanoVectorDB(self.embed_dim, storage_file=self.db_path)
        self._load_meta()

    def _load_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _save_json(self, path: Path, data):
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_meta(self):
        self.docs = self._load_json(self.docs_path, {})
        self.chunks_meta = self._load_json(self.chunks_meta_path, {})

    def flush_meta(self):
        self._save_json(self.docs_path, self.docs)
        self._save_json(self.chunks_meta_path, self.chunks_meta)

    def upsert_doc(self, doc: dict[str, Any]):
        self.docs[doc["doc_id"]] = doc
        self.flush_meta()

    def delete_doc_meta(self, doc_id: str):
        if doc_id in self.docs:
            del self.docs[doc_id]

        remove_ids = [chunk_id for chunk_id, item in self.chunks_meta.items() if item.get("doc_id") == doc_id]
        for chunk_id in remove_ids:
            self.chunks_meta.pop(chunk_id, None)

        self.flush_meta()

    def add_chunks_meta(self, chunk_items: list[dict[str, Any]]):
        for item in chunk_items:
            self.chunks_meta[item["__id__"]] = item
        self.flush_meta()
    def _limit_summary_length(self,summary):
        if len(summary)>200:
            return summary[0:190]+"......"
        else:
            return summary
    def all_documents(self) -> list[DocumentRecord]:
        return [
            DocumentRecord(
                doc_id=doc_id,
                status=str(item.get("status", "processed")),
                content_summary=self._limit_summary_length((item.get("content_summary", "") or "")),
                content_length=int(item.get("content_length", 0) or 0),
                created_at=item.get("created_at"),
                updated_at=item.get("updated_at"),
                file_path=item.get("file_path"),
                track_id=str(item.get("track_id", "") or ""),
                chunks_count=int(item.get("chunks_count", 0) or 0),
            )
            for doc_id, item in self.docs.items()
        ]

    def get_document_text(self, doc_id: str) -> str:
        parts: list[tuple[int, str]] = []
        for chunk_id, item in self.chunks_meta.items():
            if item.get("doc_id") != doc_id:
                continue
            idx = item.get("chunk_index", 0)
            try:
                idx = int(idx)
            except Exception:
                idx = 0
            text = str(item.get("text", "") or "")
            parts.append((idx, text))

        if not parts:
            return ""
        parts.sort(key=lambda x: x[0])
        return "".join(chunk for _, chunk in parts)

    def documents_by_track(self, track_id: str) -> list[DocumentRecord]:
        result = []
        for doc_id, item in self.docs.items():
            if str(item.get("track_id", "")) == str(track_id):
                result.append(
                    DocumentRecord(
                        doc_id=doc_id,
                        status=str(item.get("status", "processed")),
                        content_summary=str(item.get("content_summary", "") or ""),
                        content_length=int(item.get("content_length", 0) or 0),
                        created_at=item.get("created_at"),
                        updated_at=item.get("updated_at"),
                        file_path=item.get("file_path"),
                        track_id=str(item.get("track_id", "") or ""),
                        chunks_count=int(item.get("chunks_count", 0) or 0),
                    )
                )
        return result


openai_client: Optional[AsyncOpenAI] = None
rag_lock = asyncio.Lock()
agent_storages: dict[str, AgentStorage] = {}


async def close_openai_client() -> None:
    global openai_client
    if openai_client is None:
        return
    http_client = getattr(openai_client, "_client", None)
    if http_client is not None:
        try:
            await http_client.aclose()
        except Exception:
            pass
    openai_client = None


def build_openai_client() -> AsyncOpenAI:
    if not runtime_config["api_key"]:
        raise HTTPException(status_code=500, detail="RAG_OPENAI_API_KEY 未配置")
    return AsyncOpenAI(
        api_key=runtime_config["api_key"],
        base_url=runtime_config["base_url"],
    )


def get_openai_client() -> AsyncOpenAI:
    global openai_client
    if openai_client is None:
        openai_client = build_openai_client()
    return openai_client


async def reset_openai_client() -> AsyncOpenAI:
    await close_openai_client()
    client = build_openai_client()
    globals()["openai_client"] = client
    return client


def get_storage(agent_name: str | None = None) -> AgentStorage:
    selected = sanitize_agent_name(agent_name or runtime_config["agent_name"])
    storage = agent_storages.get(selected)
    if storage is not None:
        return storage

    working_dir = get_working_dir(selected)
    storage = AgentStorage(working_dir=working_dir, embed_dim=runtime_config["embed_dim"])
    agent_storages[selected] = storage
    return storage


def rebuild_all_storages_if_needed() -> None:
    stale_agents = [
        agent_name
        for agent_name, storage in agent_storages.items()
        if storage.embed_dim != runtime_config["embed_dim"]
    ]
    for agent_name in stale_agents:
        agent_storages.pop(agent_name, None)


async def embed_texts(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, runtime_config["embed_dim"]), dtype=np.float32)

    client = get_openai_client()
    response = await client.embeddings.create(
        model=runtime_config["embed_model"],
        input=texts,
    )
    if not getattr(response, "data", None):
        raise HTTPException(status_code=500, detail="Embedding 接口返回为空")
    return np.array([item.embedding for item in response.data], dtype=np.float32)


async def llm_answer(query: str, context: str, response_type: str) -> str:
    client = get_openai_client()
    messages = [
        {
            "role": "system",
            "content": (
                "你是一个RAG问答助手。请严格依据提供的上下文回答；"
                "若上下文不足，请明确说明上下文不足。回答尽量简洁直接。"
            ),
        },
        {
            "role": "user",
            "content": f"问题：{query}\n\n上下文：\n{context}\n\n请以 {response_type} 风格作答。",
        },
    ]
    response = await client.chat.completions.create(
        model=runtime_config["chat_model"],
        messages=messages,
        temperature=0.2,
    )
    if not getattr(response, "choices", None):
        raise HTTPException(status_code=500, detail="Chat 接口返回为空")
    return response.choices[0].message.content or ""


def build_context_from_hits(hits: list[dict[str, Any]]) -> str:
    parts = []
    for index, hit in enumerate(hits, start=1):
        if not isinstance(hit, dict):
            continue
        chunk_text = str(hit.get("text", "") or hit.get("text_preview", "") or "")
        score = hit.get("__score__", hit.get("score", ""))
        if isinstance(score, dict):
            score = score.get("cosine_similarity", score.get("score", ""))
        doc_id = hit.get("doc_id", "")
        file_path = hit.get("file_path", "")
        parts.append(
            f"[片段{index}] doc_id={doc_id} file_path={file_path} score={score}\n{chunk_text}"
        )
    return "\n\n".join(parts).strip()


def normalize_query_hits(raw_hits: Any, storage: AgentStorage) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    if raw_hits is None:
        return normalized

    if isinstance(raw_hits, np.ndarray):
        raw_hits = raw_hits.tolist()

    if isinstance(raw_hits, dict):
        raw_hits = [raw_hits]

    if not isinstance(raw_hits, list):
        try:
            raw_hits = list(raw_hits)
        except TypeError:
            return normalized

    for item in raw_hits:
        if isinstance(item, dict):
            normalized.append(item)
            continue

        chunk_meta = None
        score = ""

        if isinstance(item, (list, tuple)):
            if len(item) >= 1 and isinstance(item[0], dict):
                chunk_meta = dict(item[0])
                if len(item) >= 2:
                    score = item[1]
            elif len(item) >= 1 and isinstance(item[0], str):
                chunk_meta = dict(storage.chunks_meta.get(item[0], {}))
                if len(item) >= 2:
                    score = item[1]
        elif isinstance(item, str):
            chunk_meta = dict(storage.chunks_meta.get(item, {}))

        if chunk_meta:
            if score != "":
                chunk_meta["__score__"] = score
            normalized.append(chunk_meta)

    return normalized


def build_track_id(doc_id: str, file_path: Optional[str]) -> str:
    base = f"{doc_id}|{file_path or ''}"
    return md5(base.encode("utf-8")).hexdigest()


async def insert_document(storage: AgentStorage, text: str, doc_id: str | None, file_path: str | None) -> InsertResponse:
    text = normalize_text(text)
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    final_doc_id = make_doc_id(text, doc_id)
    track_id = build_track_id(final_doc_id, file_path)

    old_chunk_ids = [
        chunk_id for chunk_id, item in storage.chunks_meta.items()
        if item.get("doc_id") == final_doc_id
    ]
    if old_chunk_ids:
        try:
            storage.vdb.delete(old_chunk_ids)
        except Exception:
            pass
        for chunk_id in old_chunk_ids:
            storage.chunks_meta.pop(chunk_id, None)

    chunks = split_text(
        text=text,
        chunk_size=runtime_config["chunk_size"],
        overlap=runtime_config["chunk_overlap"],
    )

    embeddings = await embed_texts(chunks)
    rows = []
    chunk_metas = []

    for index, (chunk_text, vector) in enumerate(zip(chunks, embeddings), start=1):
        chunk_id = f"{final_doc_id}::chunk::{index}"
        item = {
            "__id__": chunk_id,
            "__vector__": np.asarray(vector, dtype=np.float32),
            "doc_id": final_doc_id,
            "track_id": track_id,
            "file_path": file_path,
            "chunk_index": index,
            "text": chunk_text,
            "text_preview": short_summary(chunk_text, 80),
        }
        rows.append(item)
        chunk_metas.append(item)

    if rows:
        storage.vdb.upsert(rows)

    now = utc_now_iso()
    existing = storage.docs.get(final_doc_id, {})
    created_at = existing.get("created_at") or now

    storage.add_chunks_meta(chunk_metas)
    storage.upsert_doc(
        {
            "doc_id": final_doc_id,
            "status": "processed",
            "content_summary": short_summary(text),
            "content_length": len(text),
            "created_at": created_at,
            "updated_at": now,
            "file_path": file_path,
            "track_id": track_id,
            "chunks_count": len(chunks),
        }
    )

    return InsertResponse(
        status="ok",
        doc_id=final_doc_id,
        track_id=track_id,
        inserted_length=len(text),
    )


async def query_document(storage: AgentStorage, payload: QueryRequest) -> QueryResponse:
    query = normalize_text(payload.query)
    if not query:
        raise HTTPException(status_code=400, detail="query 不能为空")

    emb = await embed_texts([query])
    top_k = runtime_config["top_k"]

    try:
        hits = storage.vdb.query(
            query=emb[0].tolist(),
            top_k=top_k,
            better_than_threshold=None,
        )
    except TypeError:
        hits = storage.vdb.query(
            emb[0].tolist(),
            top_k=top_k,
        )

    hits = normalize_query_hits(hits, storage)[:MAX_CONTEXT_CHUNKS]
    context = build_context_from_hits(hits)

    if payload.only_need_context:
        answer = context or ""
    else:
        if not context:
            answer = "未检索到相关上下文。"
        else:
            answer = await llm_answer(
                query=query,
                context=context,
                response_type=payload.response_type,
            )

    return QueryResponse(
        status="ok",
        query=query,
        mode=payload.mode,
        answer=answer,
    )


async def rebuild_runtime_for_agent(agent_name: str | None = None):
    selected = sanitize_agent_name(agent_name or runtime_config["agent_name"])
    get_storage(selected)
    return True


def _safe_match_text(value: Any, needle: str) -> bool:
    if not needle:
        return True
    return needle in str(value or "").lower()


def _filter_documents(
    docs: list[DocumentRecord],
    search: str | None,
    time_from: str | None,
    time_to: str | None,
) -> list[DocumentRecord]:
    q = (search or "").strip().lower()
    dt_from = parse_time_like(time_from)
    dt_to = parse_time_like(time_to)

    out: list[DocumentRecord] = []
    for doc in docs:
        if q:
            hit = (
                _safe_match_text(doc.doc_id, q)
                or _safe_match_text(doc.file_path, q)
                or _safe_match_text(doc.content_summary, q)
            )
            if not hit:
                continue

        created = parse_time_like(doc.created_at)
        if dt_from and created and created < dt_from:
            continue
        if dt_to and created and created > dt_to:
            continue

        out.append(doc)

    # 新到旧排序（created_at优先，其次updated_at）
    out.sort(
        key=lambda x: (
            parse_time_like(x.created_at) or datetime.min,
            parse_time_like(x.updated_at) or datetime.min,
        ),
        reverse=True,
    )
    return out


@asynccontextmanager
async def lifespan(_: FastAPI):
    await rebuild_runtime_for_agent()
    try:
        yield
    finally:
        await close_openai_client()


app = FastAPI(
    title="Faust Nano VectorDB RAG API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=StatusResponse)
async def health_check():
    working_dir = get_working_dir()
    return StatusResponse(
        status="ok",
        working_dir=str(working_dir),
        initialized=True,
        agent_name=sanitize_agent_name(runtime_config["agent_name"]),
        base_url=runtime_config["base_url"],
        chat_model=runtime_config["chat_model"],
        embed_model=runtime_config["embed_model"],
    )


@app.post("/config")
async def update_config(payload: ConfigRequest):
    changed_agent = False

    if payload.api_key is not None:
        runtime_config["api_key"] = payload.api_key
    if payload.base_url is not None:
        runtime_config["base_url"] = payload.base_url
    if payload.chat_model is not None:
        runtime_config["chat_model"] = payload.chat_model
    if payload.embed_model is not None:
        runtime_config["embed_model"] = payload.embed_model
    if payload.embed_dim is not None:
        runtime_config["embed_dim"] = int(payload.embed_dim)
    if payload.embed_max_token_size is not None:
        runtime_config["embed_max_token_size"] = int(payload.embed_max_token_size)
    if payload.agent_name is not None:
        runtime_config["agent_name"] = sanitize_agent_name(payload.agent_name)
        changed_agent = True

    rebuild_all_storages_if_needed()

    await reset_openai_client()

    if changed_agent:
        await rebuild_runtime_for_agent(runtime_config["agent_name"])

    return {
        "status": "ok",
        "agent_name": runtime_config["agent_name"],
        "base_url": runtime_config["base_url"],
        "chat_model": runtime_config["chat_model"],
        "embed_model": runtime_config["embed_model"],
        "embed_dim": runtime_config["embed_dim"],
        "embed_max_token_size": runtime_config["embed_max_token_size"],
    }


@app.get("/agent")
async def get_current_agent():
    return {
        "status": "ok",
        "agent_name": sanitize_agent_name(runtime_config["agent_name"]),
        "working_dir": str(get_working_dir()),
    }


@app.post("/agent")
async def switch_agent(payload: AgentSwitchRequest):
    agent_name = sanitize_agent_name(payload.agent_name)
    runtime_config["agent_name"] = agent_name
    await rebuild_runtime_for_agent(agent_name)
    return {
        "status": "ok",
        "agent_name": agent_name,
        "working_dir": str(get_working_dir(agent_name)),
    }


@app.post("/insert", response_model=InsertResponse)
async def insert_text(payload: InsertRequest):
    async with rag_lock:
        storage = get_storage()
        return await insert_document(
            storage=storage,
            text=payload.text,
            doc_id=payload.doc_id,
            file_path=payload.file_path,
        )


@app.get("/documents", response_model=DocumentsPageResponse)
async def list_documents(
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
):
    if page < 1:
        page = 1
    if page_size < 1:
        page_size = 10
    if page_size > 100:
        page_size = 100

    storage = get_storage()
    docs = storage.all_documents()
    filtered = _filter_documents(docs, search=search, time_from=time_from, time_to=time_to)

    total = len(filtered)
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages

    start = (page - 1) * page_size
    end = start + page_size
    sliced = filtered[start:end]

    return DocumentsPageResponse(
        status="ok",
        documents=sliced,
        pagination=Pagination(
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
        ),
    )


@app.get("/documents/track/{track_id}", response_model=DocumentsResponse)
async def list_documents_by_track(track_id: str):
    storage = get_storage()
    return DocumentsResponse(
        status="ok",
        documents=storage.documents_by_track(track_id),
    )

@app.get("/documents/{doc_id}", response_model=DocumentDetailResponse)
async def get_document_detail(doc_id: str):
    storage = get_storage()
    existing = storage.docs.get(doc_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

    document = {
        "doc_id": doc_id,
        "status": str(existing.get("status", "processed")),
        "content_summary": str(existing.get("content_summary", "") or ""),
        "content_length": int(existing.get("content_length", 0) or 0),
        "created_at": existing.get("created_at"),
        "updated_at": existing.get("updated_at"),
        "file_path": existing.get("file_path"),
        "track_id": str(existing.get("track_id", "") or ""),
        "chunks_count": int(existing.get("chunks_count", 0) or 0),
        "text": storage.get_document_text(doc_id),
    }
    return DocumentDetailResponse(status="ok", document=document)


@app.get("/documents/{doc_id}/content")
async def get_document_content(doc_id: str):
    storage = get_storage()
    existing = storage.docs.get(doc_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
    return {
        "status": "ok",
        "doc_id": doc_id,
        "text": storage.get_document_text(doc_id),
        "content_length": int(existing.get("content_length", 0) or 0),
    }


@app.put("/documents/{doc_id}")
async def update_document(doc_id: str, payload: InsertRequest):
    async with rag_lock:
        storage = get_storage()
        existing = storage.docs.get(doc_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

        text = normalize_text(payload.text)
        if not text:
            raise HTTPException(status_code=400, detail="text 不能为空")

        file_path = payload.file_path if payload.file_path is not None else existing.get("file_path")
        result = await insert_document(
            storage=storage,
            text=text,
            doc_id=doc_id,
            file_path=file_path,
        )

        return {
            "status": "ok",
            "doc_id": result.doc_id,
            "track_id": result.track_id,
            "inserted_length": result.inserted_length,
            "message": "updated",
        }
@app.delete("/documents/{doc_id}", response_model=DeleteResponse)
async def delete_document(doc_id: str):
    async with rag_lock:
        storage = get_storage()
        existing = storage.docs.get(doc_id)
        if not existing:
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

        to_delete = [
            chunk_id for chunk_id, item in storage.chunks_meta.items()
            if item.get("doc_id") == doc_id
        ]
        if to_delete:
            try:
                storage.vdb.delete(to_delete)
            except Exception:
                pass

        file_path = existing.get("file_path")
        storage.delete_doc_meta(doc_id)

        return DeleteResponse(
            status="ok",
            doc_id=doc_id,
            message="deleted",
            file_path=file_path,
        )


@app.post("/query", response_model=QueryResponse)
async def query_text(payload: QueryRequest):
    storage = get_storage()
    try:
        return await query_document(storage, payload)
    except Exception as e:
        print(f"Error during query: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def main():
    import uvicorn

    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()