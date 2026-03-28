#!/.venv/Scirpts/python.exe
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from hashlib import md5
from pathlib import Path
from typing import Any, Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from openai import AsyncOpenAI
import uvicorn
import numpy as np
print("Booting...")
from lightrag import LightRAG, QueryParam
from lightrag.utils import setup_logger, wrap_embedding_func_with_attrs
setup_logger("lightrag", level="INFO")
import sys
import argparse
argparser = argparse.ArgumentParser(description="LightRAG API Service\n命令行参数可以覆盖配置文件中的设置，优先级高于配置文件。\nThis agent has super cow powers")
argparser.add_argument("--rag-openai-api-key", type=str, help="RAG OpenAI API Key")
argparser.add_argument("--rag-openai-base-url", type=str, default=os.getenv("LIGHTRAG_OPENAI_BASE_URL", os.getenv("OPENAI_API_BASE", "https://www.dmxapi.cn/v1")), help="RAG OpenAI Base URL")
argparser.add_argument("--rag-chat-model", type=str, default=os.getenv("LIGHTRAG_CHAT_MODEL", "qwen3.5-27b"), help="RAG Chat Model Name")
argparser.add_argument("--rag-embed-model", type=str, default=os.getenv("LIGHTRAG_EMBED_MODEL", "text-embedding-3-small"), help="RAG Embed Model Name")
args = argparser.parse_args()

PATH=str(Path(__file__).resolve().parent.parent.parent/"faust_backend")
#insert to PATH
sys.path.insert(0, PATH)
print(PATH)
print(os.listdir(PATH))
import faust_backend.config_loader as conf
print("Config loaded:", conf)

BASE_DIR = Path(__file__).resolve().parent
WORKING_ROOT_DIR = BASE_DIR / "rag_storage"
HOST = os.getenv("LIGHTRAG_HOST", "127.0.0.1")
PORT = int(os.getenv("LIGHTRAG_PORT", "18080"))


def _load_runtime_defaults():
    if conf is None:
        return {
            "api_key": args.rag_openai_api_key,
            "base_url": args.rag_openai_base_url,
            "chat_model": args.rag_chat_model,
            "embed_model": args.rag_embed_model,
            "embed_dim": int(os.getenv("LIGHTRAG_EMBED_DIM", "1536")),
            "embed_max_token_size": int(os.getenv("LIGHTRAG_EMBED_MAX_TOKEN_SIZE", "8192")),
            "agent_name": os.getenv("LIGHTRAG_AGENT_NAME", "default"),
        }

    conf.reload_configs()
    return {
        "api_key": args.rag_openai_api_key or getattr(conf, "RAG_OPENAI_API_KEY", ""),
        "base_url": args.rag_openai_base_url or getattr(conf, "RAG_LLM_BASE_URL", "https://www.dmxapi.cn/v1"),
        "chat_model": args.rag_chat_model or getattr(conf, "RAG_CHAT_MODEL", "qwen3.5-27b"),
        "embed_model": args.rag_embed_model or getattr(conf, "RAG_EMBED_MODEL", "text-embedding-3-small"),
        "embed_dim": int(getattr(conf, "RAG_EMBED_DIM", 1536)),
        "embed_max_token_size": int(getattr(conf, "RAG_EMBED_MAX_TOKEN_SIZE", 8192)),
        "agent_name": getattr(conf, "AGENT_NAME", os.getenv("LIGHTRAG_AGENT_NAME", "default")),
    }

WORKING_ROOT_DIR.mkdir(parents=True, exist_ok=True)


class InsertRequest(BaseModel):
    text: str = Field(..., min_length=1, description="要写入 LightRAG 的文本")
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


class DocumentDetailResponse(BaseModel):
    status: str = "ok"
    document: DocumentRecord
    content: str = ""


class UpdateRequest(BaseModel):
    text: str = Field(..., min_length=1, description="更新后的文档正文")
    file_path: Optional[str] = Field(default=None, description="可选，文档来源路径")


class StatusResponse(BaseModel):
    status: str
    working_dir: str
    initialized: bool
    agent_name: str
    base_url: str
    chat_model: str
    embed_model: str

runtime_config = _load_runtime_defaults()


rag_instance: Optional[LightRAG] = None
openai_client: Optional[AsyncOpenAI] = None
rag_lock = asyncio.Lock()


async def close_openai_client() -> None:
    global openai_client
    if openai_client is None:
        return
    http_client = getattr(openai_client, "_client", None)
    if http_client is not None:
        aclose = getattr(http_client, "aclose", None)
        if callable(aclose):
            await aclose()
    openai_client = None


def build_openai_client() -> AsyncOpenAI:
    if not runtime_config["api_key"]:
        raise RuntimeError("LIGHTRAG_OPENAI_API_KEY 未配置，无法调用 OpenAI 兼容模型")
    return AsyncOpenAI(api_key=runtime_config["api_key"], base_url=runtime_config["base_url"])


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


def get_doc_content_store_path() -> Path:
    return get_working_dir() / "_doc_content_store.json"


def load_doc_content_store() -> dict[str, dict[str, Any]]:
    store_path = get_doc_content_store_path()
    if not store_path.exists():
        return {}
    try:
        data = json.loads(store_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_doc_content_store(store: dict[str, dict[str, Any]]) -> None:
    store_path = get_doc_content_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_doc_content(doc_id: str, content: str, file_path: str | None = None) -> None:
    store = load_doc_content_store()
    now = datetime.now().isoformat()
    prev = store.get(doc_id, {}) if isinstance(store.get(doc_id), dict) else {}
    store[doc_id] = {
        "content": content,
        "file_path": file_path,
        "created_at": prev.get("created_at") or now,
        "updated_at": now,
    }
    save_doc_content_store(store)


def delete_doc_content(doc_id: str) -> None:
    store = load_doc_content_store()
    if doc_id in store:
        del store[doc_id]
        save_doc_content_store(store)


def get_doc_content(doc_id: str) -> str:
    store = load_doc_content_store()
    payload = store.get(doc_id)
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("content") or "")


def parse_datetime_like(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    raw = str(value).strip()
    if not raw:
        return None
    candidates = [raw, raw.replace("Z", "+00:00")]
    for item in candidates:
        try:
            return datetime.fromisoformat(item)
        except Exception:
            continue
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except Exception:
            continue
    return None


def get_doc_status_map(rag: LightRAG) -> dict[str, Any]:
    storage = getattr(rag.doc_status, "_data", None)
    return storage if isinstance(storage, dict) else {}


def document_matches_search(record: DocumentRecord, keyword: str, content: str = "") -> bool:
    if not keyword:
        return True
    needle = keyword.lower()
    haystack = "\n".join([
        record.doc_id,
        record.file_path or "",
        record.content_summary or "",
        content,
    ]).lower()
    return needle in haystack


def _doc_record_from_status(doc_id: str, doc_status: object) -> DocumentRecord:
    if isinstance(doc_status, dict):
        data = doc_status
    else:
        data = {
            "status": getattr(doc_status, "status", "unknown"),
            "content_summary": getattr(doc_status, "content_summary", "") or "",
            "content_length": getattr(doc_status, "content_length", 0) or 0,
            "created_at": getattr(doc_status, "created_at", None),
            "updated_at": getattr(doc_status, "updated_at", None),
            "file_path": getattr(doc_status, "file_path", None),
            "track_id": getattr(doc_status, "track_id", "") or "",
            "chunks_count": getattr(doc_status, "chunks_count", 0) or 0,
        }
    return DocumentRecord(
        doc_id=doc_id,
        status=str(data.get("status", "unknown")),
        content_summary=str(data.get("content_summary", "") or ""),
        content_length=int(data.get("content_length", 0) or 0),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        file_path=data.get("file_path"),
        track_id=str(data.get("track_id", "") or ""),
        chunks_count=int(data.get("chunks_count", 0) or 0),
    )


async def list_documents_from_rag(rag: LightRAG) -> list[DocumentRecord]:
    storage = getattr(rag.doc_status, "_data", None)
    if not isinstance(storage, dict):
        raise RuntimeError("当前 doc_status 存储不支持直接列出文档，请改用按 track_id 查询")
    return [_doc_record_from_status(doc_id, status) for doc_id, status in storage.items()]


def _normalize_embedding_response(response) -> np.ndarray:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenAI 兼容嵌入模型返回了无法解析的字符串响应: {response!r}") from exc

    if isinstance(response, dict):
        data = response.get("data")
        if not isinstance(data, list):
            raise RuntimeError(f"OpenAI 兼容嵌入模型返回了无效响应: {response!r}")
        vectors = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list):
                raise RuntimeError(f"OpenAI 兼容嵌入模型返回了无效向量数据: {item!r}")
            vectors.append(embedding)
        return np.array(vectors, dtype=np.float32)

    if not getattr(response, "data", None):
        raise RuntimeError(f"OpenAI 兼容嵌入模型返回了无效响应: {response!r}")
    return np.array([item.embedding for item in response.data], dtype=np.float32)


def _normalize_chat_response(response) -> str:
    if isinstance(response, str):
        return response

    if isinstance(response, dict):
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(f"OpenAI 兼容模型返回了无效响应: {response!r}")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            return content
        raise RuntimeError(f"OpenAI 兼容模型返回了无效消息结构: {response!r}")

    if not getattr(response, "choices", None):
        raise RuntimeError(f"OpenAI 兼容模型返回了无效响应: {response!r}")

    message = response.choices[0].message
    return message.content or ""


async def llm_model_func(
    prompt, system_prompt=None, history_messages=[], keyword_extraction=False, **kwargs
) -> str:
    client = get_openai_client()
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    if history_messages:
        messages.extend(history_messages)
    messages.append({"role": "user", "content": prompt})

    response_format = kwargs.pop("response_format", None)
    for extra_key in (
        "hashing_kv",
        "keyword_extraction",
        "cache_type",
        "cache_key",
        "conversation_id",
        "stream",
        "enable_cot",
        "enable_thinking",
        "response_model",
        "token_tracker",
        "timeout",
    ):
        kwargs.pop(extra_key, None)
    create_kwargs = {
        "model": runtime_config["chat_model"],
        "messages": messages,
        **kwargs,
    }
    if response_format is not None:
        create_kwargs["response_format"] = response_format

    response = await client.chat.completions.create(**create_kwargs)
    return _normalize_chat_response(response)


@wrap_embedding_func_with_attrs(
    embedding_dim=runtime_config["embed_dim"],
    max_token_size=runtime_config["embed_max_token_size"],
    model_name=runtime_config["embed_model"],
)
async def embedding_func(texts: list[str]) -> np.ndarray:
    client = get_openai_client()
    response = await client.embeddings.create(
        model=runtime_config["embed_model"],
        input=texts,
    )
    return _normalize_embedding_response(response)


def refresh_embedding_metadata() -> None:
    embedding_func.embedding_dim = runtime_config["embed_dim"]
    embedding_func.max_token_size = runtime_config["embed_max_token_size"]
    embedding_func.model_name = runtime_config["embed_model"]


async def initialize_rag() -> LightRAG:
    refresh_embedding_metadata()
    working_dir = get_working_dir()
    rag = LightRAG(
        working_dir=str(working_dir),
        embedding_func=embedding_func,
        llm_model_func=llm_model_func,
    )
    await rag.initialize_storages()
    return rag


async def rebuild_rag_instance() -> LightRAG:
    global rag_instance
    async with rag_lock:
        await reset_openai_client()
        if rag_instance is not None:
            await rag_instance.finalize_storages()
            rag_instance = None
        rag_instance = await initialize_rag()
        return rag_instance


async def get_rag() -> LightRAG:
    global rag_instance
    if rag_instance is not None:
        return rag_instance

    async with rag_lock:
        if rag_instance is None:
            await reset_openai_client()
            rag_instance = await initialize_rag()
        return rag_instance


@asynccontextmanager
async def lifespan(_: FastAPI):
    global rag_instance
    await reset_openai_client()
    rag_instance = await initialize_rag()
    try:
        yield
    finally:
        if rag_instance is not None:
            await rag_instance.finalize_storages()
            rag_instance = None
        await close_openai_client()


app = FastAPI(title="Faust LightRAG API", version="1.0.0", lifespan=lifespan)


@app.get("/health", response_model=StatusResponse)
async def health_check():
    return StatusResponse(
        status="ok",
        working_dir=str(get_working_dir()),
        initialized=rag_instance is not None,
        agent_name=runtime_config["agent_name"],
        base_url=runtime_config["base_url"],
        chat_model=runtime_config["chat_model"],
        embed_model=runtime_config["embed_model"],
    )


@app.post("/config")
async def update_config(payload: ConfigRequest):
    global rag_instance
    changed = False
    for key in ("api_key", "base_url", "chat_model", "embed_model", "embed_dim", "embed_max_token_size", "agent_name"):
        value = getattr(payload, key)
        if value:
            runtime_config[key] = sanitize_agent_name(value) if key == "agent_name" else value
            changed = True

    if changed:
        await rebuild_rag_instance()

    return {
        "status": "ok",
        "changed": changed,
        "agent_name": runtime_config["agent_name"],
        "working_dir": str(get_working_dir()),
        "base_url": runtime_config["base_url"],
        "chat_model": runtime_config["chat_model"],
        "embed_model": runtime_config["embed_model"],
    }


@app.get("/agent")
async def get_current_agent():
    return {
        "status": "ok",
        "agent_name": runtime_config["agent_name"],
        "working_dir": str(get_working_dir()),
    }


@app.post("/agent")
async def switch_agent(payload: AgentSwitchRequest):
    runtime_config["agent_name"] = sanitize_agent_name(payload.agent_name)
    await rebuild_rag_instance()
    return {
        "status": "ok",
        "agent_name": runtime_config["agent_name"],
        "working_dir": str(get_working_dir()),
    }


@app.post("/insert", response_model=InsertResponse)
async def insert_text(payload: InsertRequest):
    rag = await get_rag()
    doc_id = make_doc_id(payload.text, payload.doc_id)
    track_id = await rag.ainsert(
        payload.text,
        ids=doc_id,
        file_paths=payload.file_path,
    )
    upsert_doc_content(doc_id, payload.text, payload.file_path)
    return InsertResponse(
        doc_id=doc_id,
        track_id=track_id,
        inserted_length=len(payload.text),
    )


@app.get("/documents")
async def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
    search: Optional[str] = Query(default=None),
    time_from: Optional[str] = Query(default=None),
    time_to: Optional[str] = Query(default=None),
):
    rag = await get_rag()
    all_documents = await list_documents_from_rag(rag)
    from_dt = parse_datetime_like(time_from)
    to_dt = parse_datetime_like(time_to)
    search_text = (search or "").strip()

    filtered: list[tuple[DocumentRecord, Optional[datetime]]] = []
    for item in all_documents:
        created_dt = parse_datetime_like(item.created_at) or parse_datetime_like(item.updated_at)
        if from_dt and (not created_dt or created_dt < from_dt):
            continue
        if to_dt and (not created_dt or created_dt > to_dt):
            continue
        if search_text:
            content = get_doc_content(item.doc_id)
            if not document_matches_search(item, search_text, content=content):
                continue
        filtered.append((item, created_dt))

    filtered.sort(key=lambda pair: pair[1] or datetime.min, reverse=True)
    total = len(filtered)
    total_pages = max((total + page_size - 1) // page_size, 1)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    end = start + page_size
    page_items = [pair[0] for pair in filtered[start:end]]

    return {
        "status": "ok",
        "documents": [item.model_dump() for item in page_items],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        },
        "filters": {
            "search": search_text,
            "time_from": time_from,
            "time_to": time_to,
        },
    }


@app.get("/documents/{doc_id}", response_model=DocumentDetailResponse)
async def get_document_detail(doc_id: str):
    rag = await get_rag()
    status_map = get_doc_status_map(rag)
    doc_status = status_map.get(doc_id)
    if doc_status is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    record = _doc_record_from_status(doc_id, doc_status)
    return DocumentDetailResponse(document=record, content=get_doc_content(doc_id))


@app.get("/documents/track/{track_id}", response_model=DocumentsResponse)
async def get_documents_by_track_id(track_id: str):
    rag = await get_rag()
    records = await rag.aget_docs_by_track_id(track_id)
    documents = [_doc_record_from_status(doc_id, status) for doc_id, status in records.items()]
    return DocumentsResponse(documents=documents)


@app.delete("/documents/{doc_id}", response_model=DeleteResponse)
async def delete_document(doc_id: str):
    rag = await get_rag()
    result = await rag.adelete_by_doc_id(doc_id)
    result_status = getattr(result, "status", "fail")
    if result_status == "not_found":
        delete_doc_content(doc_id)
        return DeleteResponse(
            status="ok",
            doc_id=doc_id,
            message=getattr(result, "message", "文档不存在，已视为删除完成"),
            file_path=getattr(result, "file_path", None),
        )
    if result_status != "success":
        raise HTTPException(status_code=getattr(result, "status_code", 500), detail=getattr(result, "message", "删除失败"))
    delete_doc_content(doc_id)
    return DeleteResponse(
        status=result.status,
        doc_id=result.doc_id,
        message=result.message,
        file_path=getattr(result, "file_path", None),
    )


@app.put("/documents/{doc_id}")
async def update_document(doc_id: str, payload: UpdateRequest):
    rag = await get_rag()
    status_map = get_doc_status_map(rag)
    old_status = status_map.get(doc_id)
    if old_status is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    old_record = _doc_record_from_status(doc_id, old_status)

    deleted = await rag.adelete_by_doc_id(doc_id)
    deleted_status = getattr(deleted, "status", "fail")
    if deleted_status not in ("success", "not_found"):
        raise HTTPException(status_code=getattr(deleted, "status_code", 500), detail=getattr(deleted, "message", "更新前删除旧文档失败"))

    track_id = await rag.ainsert(
        payload.text,
        ids=doc_id,
        file_paths=payload.file_path or old_record.file_path,
    )
    upsert_doc_content(doc_id, payload.text, payload.file_path or old_record.file_path)

    refreshed_map = get_doc_status_map(rag)
    refreshed_status = refreshed_map.get(doc_id)
    record = _doc_record_from_status(doc_id, refreshed_status or {
        "status": "processed",
        "file_path": payload.file_path or old_record.file_path,
        "content_summary": payload.text[:120],
        "content_length": len(payload.text),
        "track_id": track_id,
    })
    return {
        "status": "ok",
        "message": "文档已更新",
        "track_id": track_id,
        "document": record.model_dump(),
    }

@app.post("/query", response_model=QueryResponse)
async def query_text(payload: QueryRequest):
    rag = await get_rag()
    try:
        answer = await rag.aquery(
            payload.query,
            param=QueryParam(
                mode=payload.mode,
                only_need_context=payload.only_need_context,
                response_type=payload.response_type,
                enable_rerank=payload.enable_rerank,
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LightRAG query failed: {e}") from e
    return QueryResponse(query=payload.query, mode=payload.mode, answer=str(answer))


def main():
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()