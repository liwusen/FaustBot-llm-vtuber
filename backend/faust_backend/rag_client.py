import asyncio
import json
import hashlib
from typing import Any, Dict
from urllib import request
from urllib.parse import urlencode
from pathlib import Path
try:
    import config_loader as conf
except ImportError:
    import faust_backend.config_loader as conf
import aiohttp
import pathlib
import time
from aiohttp import ClientResponseError
DEFAULT_RAG_BASE_URL = "http://127.0.0.1:18080"
import fnmatch
import os
import datetime
import httpx


def _ensure_text_payload(text: str) -> str:
    if not isinstance(text, str):
        raise TypeError(f"RAG 插入内容必须是字符串，当前类型: {type(text).__name__}")
    normalized = text.strip()
    if not normalized:
        raise ValueError("RAG 插入内容不能为空字符串")
    return normalized


class docTracker():
    """
        Document Tracker for RAG
        自动在模型创建文件时记录文件路径和关联的文档ID，方便后续查询和管理。
        记录模型生成的文件md5值、当前文件路径、关联的文档ID、创建时间和更新时间等信息。
        如果md5变化，自动更新关联的文档ID和文件路径，确保RAG中的文档与实际文件保持一致。
    """
    class DocInfo:
        doc_id:str
        file_path:str
        md5:str
        create_time:float
        update_time:float
        def __init__(self, doc_id: str, file_path: str, md5: str, create_time: float, update_time: float):
            self.doc_id = doc_id
            self.file_path = file_path
            self.md5 = md5
            self.create_time = create_time
            self.update_time = update_time
    verbosity:bool=False
    def __init__(self, agent_root: str | Path | None = None, base_url: str | None = None):
        self.refresh_runtime(agent_root=agent_root, base_url=base_url)

    def refresh_runtime(self, agent_root: str | Path | None = None, base_url: str | None = None):
        self.agent_root = Path(agent_root or conf.AGENT_ROOT)
        self.base_url = base_url or getattr(conf, "RAG_API_URL", DEFAULT_RAG_BASE_URL)
        self.datafile = self.agent_root / "rag_doc_tracker.json"
        self.mem_record_file = self.agent_root / "rag_chat_history_records.json"
        self.record_dir = self.agent_root / "record"
        self.record_dir.mkdir(parents=True, exist_ok=True)
        self.doc_info_map: Dict[str, docTracker.DocInfo] = {}
        self._load_from_file()
    
    def _to_dict(self) -> Dict[str, Dict[str, Any]]:
        return {
            file_path: {
                "doc_id": info.doc_id,
                "file_path": info.file_path,
                "md5": info.md5,
                "create_time": info.create_time,
                "update_time": info.update_time,
            }
            for file_path, info in self.doc_info_map.items()
        }

    def _load_from_file(self):
        self.datafile.parent.mkdir(parents=True, exist_ok=True)
        if Path(self.datafile).exists():
            with open(self.datafile, "r", encoding="utf-8") as f:
                data = json.load(f)
                
                for file_path, info in data.items():
                    self.doc_info_map[file_path] = docTracker.DocInfo(
                        doc_id=info["doc_id"],
                        file_path=info["file_path"],
                        md5=info["md5"],
                        create_time=info["create_time"],
                        update_time=info["update_time"],
                    )
        else:
            self._save_to_file()
    async def clear_not_exist_docs(self):
        to_delete = []
        for file_path, doc_info in self.doc_info_map.items():
            if not Path(file_path).exists():
                to_delete.append((file_path, doc_info.doc_id))
        for file_path, doc_id in to_delete:
            await rag_delete_document(doc_id, base_url=self.base_url)
            del self.doc_info_map[file_path]
            if self.verbosity:
                print(f"[RAG Tracker] Deleted document for non-existent file {file_path} with doc_id {doc_id}")
        if to_delete:
            self._save_to_file()
    async def declareUpdateDoc(self,file_path:str,dry_run:bool=False):
        # make sure file_path is absolute
        file_path = str(Path(file_path).resolve())
        now_time = time.time()
        file_text = Path(file_path).read_text(encoding="utf-8")
        md5 = hashlib.md5(Path(file_path).read_bytes()).hexdigest()
        if file_path in self.doc_info_map:
            doc_info = self.doc_info_map[file_path]
            current_doc_id = doc_info.doc_id
            if doc_info.md5 != md5:
                new_doc_id = pathlib.Path(file_path).name+"_"+str(int(now_time))
                doc_info.file_path = file_path
                doc_info.md5 = md5
                doc_info.update_time = now_time
                if not dry_run:
                    await rag_delete_document(current_doc_id, base_url=self.base_url)
                    await rag_insert_document(file_text, doc_id=new_doc_id, file_path=file_path, base_url=self.base_url)
                doc_info.doc_id = new_doc_id
                if self.verbosity:
                    print(f"[RAG Tracker] Updated document for {file_path} with doc_id {doc_info.doc_id}")
            elif self.verbosity:
                print(f"[RAG Tracker] Skipped unchanged document for {file_path} with doc_id {doc_info.doc_id}")
        else:
            new_doc_id = pathlib.Path(file_path).name+"_"+str(int(now_time))
            self.doc_info_map[file_path] = docTracker.DocInfo(new_doc_id, file_path, md5, now_time, now_time)
            if not dry_run:
                await rag_insert_document(file_text, doc_id=self.doc_info_map[file_path].doc_id, file_path=file_path, base_url=self.base_url)
            if self.verbosity:
                print(f"[RAG Tracker] Inserted new document for {file_path} with doc_id {self.doc_info_map[file_path].doc_id}")
        self._save_to_file()
    def is_tracked(self,file_path:str)->bool:
        file_path = str(Path(file_path).resolve())
        return file_path in self.doc_info_map
    def untrack_doc(self,file_path:str):
        file_path = str(Path(file_path).resolve())
        if file_path in self.doc_info_map:
            doc_id = self.doc_info_map[file_path].doc_id
            asyncio.run(rag_delete_document(doc_id, base_url=self.base_url))
            del self.doc_info_map[file_path]
            self._save_to_file()
            if self.verbosity:
                print(f"[RAG Tracker] Untracked document for {file_path} with doc_id {doc_id}")
        else:
            if self.verbosity:
                print(f"[RAG Tracker] No document to untrack for {file_path}")
    async def recursive_track_dir(self,dir_path:str,allowed_suffixes:list[str]=[".md",".txt"],blacklist_fnmatch_pattern:str=None):
        dir_path = str(Path(dir_path).resolve())
        for path in Path(dir_path).rglob("*"):
            if path.is_file():
                if allowed_suffixes is None or \
                    path.suffix in allowed_suffixes and \
                    (blacklist_fnmatch_pattern is None or not fnmatch.fnmatch(str(path), blacklist_fnmatch_pattern)):
                    await self.declareUpdateDoc(str(path))
    async def new_chat_history_part(self,user_input:str,text:str):
        now_time = time.time()
        SAVE=f"""
        用户输入: {user_input}
        助手回复: {text}
        """
        print(f"[RAG Tracker] Adding new chat history part to RAG with doc_id __CHAT_HISTORY__{str(int(now_time))}")
        print(f"[RAG Tracker] Chat history content:\n{SAVE}")
        await rag_insert_document(SAVE, doc_id="__CHAT_HISTORY__"+(doc_id:=str(int(now_time))), file_path="__MEM__", base_url=self.base_url)
        self.append_chat_record(user_input, text)
        record = {
            "text": text,
            "timestamp": now_time,
            "doc_id": doc_id,
        }
        if Path(self.mem_record_file).exists():
            with open(self.mem_record_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = []
        data.append(record)
        with open(self.mem_record_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    async def clear_chat_history(self):
        if Path(self.mem_record_file).exists():
            with open(self.mem_record_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for record in data:
                doc_id = record.get("doc_id")
                if doc_id:
                    await rag_delete_document(doc_id, base_url=self.base_url)
            Path(self.mem_record_file).unlink()
        else:
            if self.verbosity:
                print("[RAG Tracker] No chat history records to clear.")

    def append_chat_record(self, user_text: str, assistant_text: str, when: datetime.datetime | None = None) -> Path:
        now = when or datetime.datetime.now()
        record_path = self.record_dir / f"{now.strftime('%Y%m%d')}.md"
        record_path.parent.mkdir(parents=True, exist_ok=True)
        with open(record_path, "a", encoding="utf-8") as f:
            f.write(f"## {now.strftime('%H:%M:%S')}\n\n")
            f.write(f"**用户**\n\n{str(user_text).strip()}\n\n")
            f.write(f"**Faust**\n\n{str(assistant_text).strip()}\n\n")
        return record_path

    async def append_and_index_chat_record(self, user_text: str, assistant_text: str) -> str:
        record_path = self.append_chat_record(user_text, assistant_text)
        if getattr(conf, "RAG_ENABLED", True) and getattr(conf, "RAG_AUTO_INDEX_RECORD", True):
            await self.declareUpdateDoc(str(record_path))
        return str(record_path)

    def _save_to_file(self):
        Path(self.datafile).parent.mkdir(parents=True, exist_ok=True)
        with open(self.datafile, "w", encoding="utf-8") as f:
            json.dump(self._to_dict(), f, ensure_ascii=False, indent=4)


def create_tracker(agent_root: str | Path | None = None, base_url: str | None = None) -> docTracker:
    return docTracker(agent_root=agent_root, base_url=base_url)

async def _json_request(
    method: str,
    url: str,
    payload: Dict[str, Any] | None = None,
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if params:
        query = urlencode({k: v for k, v in params.items() if v is not None and v != ""})
        if query:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{query}"
    if aiohttp is not None:
        async with aiohttp.ClientSession() as session:
            async with session.request(method, url, json=payload) as response:
                response.raise_for_status()
                return await response.json()

    def _sync_request() -> Dict[str, Any]:
        data = None
        headers = {"Content-Type": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(url, data=data, headers=headers, method=method)
        with request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))

    return await asyncio.to_thread(_sync_request)


async def rag_health(base_url: str = DEFAULT_RAG_BASE_URL) -> Dict[str, Any]:
    return await _json_request("GET", f"{base_url}/health")


async def rag_insert(text: str, base_url: str = DEFAULT_RAG_BASE_URL) -> Dict[str, Any]:
    return await _json_request("POST", f"{base_url}/insert", {"text": _ensure_text_payload(text)})

async def rag_get_document_content(doc_id: str, base_url: str = DEFAULT_RAG_BASE_URL) -> Dict[str, Any]:
    return await _json_request("GET", f"{base_url}/documents/{doc_id}/content")

async def rag_insert_document(
    text: str,
    *,
    base_url: str = DEFAULT_RAG_BASE_URL,
    doc_id: str | None = None,
    file_path: str | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"text": _ensure_text_payload(text)}
    if doc_id is not None:
        payload["doc_id"] = doc_id
    if file_path is not None:
        payload["file_path"] = file_path
    return await _json_request("POST", f"{base_url}/insert", payload)


async def rag_list_documents(base_url: str = DEFAULT_RAG_BASE_URL) -> Dict[str, Any]:
    return await _json_request("GET", f"{base_url}/documents")


async def rag_list_documents_paginated(
    base_url: str,
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    time_from: str | None = None,
    time_to: str | None = None,
) -> dict:
    """从 RAG 服务分页列出文档"""
    async with httpx.AsyncClient(timeout=20) as client:
        params = {"page": page, "page_size": page_size}
        if search:
            params["search"] = search
        if time_from:
            params["time_from"] = time_from
        if time_to:
            params["time_to"] = time_to

        resp = await client.get(f"{base_url}/documents", params=params)
        resp.raise_for_status()
        return resp.json()


async def rag_get_document_detail(doc_id: str, base_url: str) -> dict:
    return await _json_request("GET", f"{base_url}/documents/{doc_id}")


async def rag_update_document(
    doc_id: str,
    *,
    text: str,
    file_path: str | None = None,
    base_url: str = DEFAULT_RAG_BASE_URL,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"text": _ensure_text_payload(text)}
    if file_path is not None:
        payload["file_path"] = file_path
    return await _json_request("PUT", f"{base_url}/documents/{doc_id}", payload)


async def rag_get_documents_by_track_id(track_id: str, base_url: str = DEFAULT_RAG_BASE_URL) -> Dict[str, Any]:
    return await _json_request("GET", f"{base_url}/documents/track/{track_id}")


async def rag_delete_document(doc_id: str, base_url: str = DEFAULT_RAG_BASE_URL) -> Dict[str, Any]:
    try:
        return await _json_request("DELETE", f"{base_url}/documents/{doc_id}")
    except ClientResponseError as exc:
        if exc.status == 404:
            return {
                "status": "not_found",
                "doc_id": doc_id,
                "message": "文档不存在，已视为删除完成",
                "file_path": None,
            }
        raise


async def rag_config(
    *,
    base_url: str = DEFAULT_RAG_BASE_URL,
    api_key: str | None = None,
    model_base_url: str | None = None,
    chat_model: str | None = None,
    embed_model: str | None = None,
    embed_dim: int | None = None,
    embed_max_token_size: int | None = None,
) -> Dict[str, Any]:
    payload = {
        "api_key": api_key,
        "base_url": model_base_url,
        "chat_model": chat_model,
        "embed_model": embed_model,
        "embed_dim": embed_dim,
        "embed_max_token_size": embed_max_token_size,
    }
    clean_payload = {k: v for k, v in payload.items() if v is not None}
    return await _json_request("POST", f"{base_url}/config", clean_payload)

async def rag_set_agent_id(agent_name:str, base_url: str = DEFAULT_RAG_BASE_URL)->Dict[str,Any]:
    payload = {
        "agent_name": agent_name
    }
    return await _json_request("POST", f"{base_url}/agent", payload)


async def rag_get_agent(base_url: str = DEFAULT_RAG_BASE_URL) -> Dict[str, Any]:
    return await _json_request("GET", f"{base_url}/agent")
async def rag_query(
    query: str,
    mode: str = "naive",
    *,
    base_url: str = DEFAULT_RAG_BASE_URL,
    only_need_context: bool = False,
    response_type: str = "Multiple Paragraphs",
    enable_rerank: bool = False,
) -> str:
    print("[RAG Client] Querying RAG with query:", query)
    payload = await _json_request(
        "POST",
        f"{base_url}/query",
        {
            "query": query,
            "mode": mode,
            "only_need_context": only_need_context,
            "response_type": response_type,
            "enable_rerank": enable_rerank,
        },
    )
    return str(payload.get("answer", ""))
async def demo(basic_test:bool=False):
    if basic_test:
        print("Checking RAG health...")
        health = await rag_health()
        print("RAG Health:", health)

        print("\nInserting text into RAG...")
        insert_result = await rag_insert_document("这是一些测试文本，用于验证 RAG 的插入功能。")
        print("Insert Result:", insert_result)

        print("\nListing RAG documents...")
        documents = await rag_list_documents()
        print("Documents:", documents)

        print("\nQuerying RAG context directly...")
        answer = await rag_query("测试文本是什么？", only_need_context=True)
        print("RAG Answer:", answer)
    await rag_set_agent_id("__DOC_TRACKER_TEST__")

    test_doc_tracker = docTracker()
    os.remove(test_doc_tracker.datafile) if Path(test_doc_tracker.datafile).exists() else None
    os.remove(test_doc_tracker.mem_record_file) if Path(test_doc_tracker.mem_record_file).exists() else None
    docTracker._load_from_file(test_doc_tracker)
    test_doc_tracker.verbosity = True
    print("\nTesting docTracker with test.md...")
    with open("test.md", "w", encoding="utf-8") as f:
        f.write("# 测试文档\n这是一个测试文档，用于验证 docTracker 的功能。\n FaustBot-llm-vtuber 是一个桌宠,它可以陪伴在你的电脑桌面，与你进行自然语言的交流互动。它可以回答你的问题，提供信息，讲笑话，甚至可以根据你的喜好定制个性化的对话内容。无论你是需要一个虚拟的朋友，还是想要一个智能助手，FaustBot-llm-vtuber 都能满足你的需求。")
    print("RAG result before tracking:", await rag_query("陪伴在桌面", only_need_context=True))#这里不应该搜索到结果
    await test_doc_tracker.declareUpdateDoc("./test.md")
    print("\n没有修改文件内容，重复 declareUpdateDoc不应该更新文档...")
    await test_doc_tracker.declareUpdateDoc("./test.md")
    print("\nTesting docTracker with test.md...")
    print("RAG result:", await rag_query("陪伴在桌面", only_need_context=True))
    with open("test.md", "w", encoding="utf-8") as f:
        f.write("# 测试文档\n这是一个测试文档，用于验证 docTracker 的功能。")
    await test_doc_tracker.declareUpdateDoc("./test.md")
    print("\nTesting docTracker with test.md...")
    print("RAG result:", await rag_query("陪伴在桌面", only_need_context=True))
    os.remove("test.md")
    await test_doc_tracker.clear_not_exist_docs()


    await test_doc_tracker.new_chat_history_part("你好","这是第一条聊天记录。")
    await test_doc_tracker.new_chat_history_part("你好","这是第二条聊天记录。")
    await test_doc_tracker.new_chat_history_part("你好","这是第三条聊天记录。")


    print("\nTesting chat history tracking...")
    print("RAG result for '聊天记录':\n", await rag_query("聊天记录", only_need_context=True),end="\n===聊天记录结束===\n")
    if os.path.exists(test_doc_tracker.mem_record_file):
        with open(test_doc_tracker.mem_record_file, "r", encoding="utf-8") as f:
            chat_records = json.load(f)
            print("Chat History Records:", chat_records)
if __name__ == "__main__":
    asyncio.run(demo())