from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any

from faust_backend.logger import get_logger
from faust_backend.memory import get_memory

log = get_logger("faust.memory.tools")


def _run(coro) -> Any:
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
                return future.result(timeout=120)
        return asyncio.run(coro)
    except RuntimeError:
        return asyncio.run(coro)


def _run_bg(name: str, coro) -> str:
    task_id = f"bg_{uuid.uuid4().hex[:12]}"
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            async def _wrapped():
                try:
                    await coro
                except Exception as e:
                    import faust_backend.logger as log
                    log.get_logger("faust.memory.bg").error("background task %s failed: %s", name, e)
            asyncio.run_coroutine_threadsafe(_wrapped(), loop)
        else:
            import threading
            def _thread():
                import asyncio as _a
                _a.run(coro)
            threading.Thread(target=_thread, daemon=True, name=f"bg_{name}").start()
    except RuntimeError:
        import threading
        def _thread():
            import asyncio as _a
            _a.run(coro)
        threading.Thread(target=_thread, daemon=True, name=f"bg_{name}").start()
    return task_id


def _m():
    return get_memory()


def memoryListTool(scope: str = "") -> str:
    try:
        return json.dumps(_run(_m().tree_list(scope)), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def memoryReadTool(path: str) -> str:
    try:
        return json.dumps(_run(_m().file_read(path)), ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def memoryWriteTool(path: str, content: str, declared_by: str = "agent",
                    index: bool = True, tags_json: str = "[]") -> str:
    try:
        tags = json.loads(tags_json) if str(tags_json or "").strip() else []
        result = _run(_m().file_write(path, content, declared_by=declared_by,
                                       index=index, tags=tags))
        _run_bg("auto_extract", _bg_extract_and_save(content, path))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def memorySearchTool(query: str, scope: str = "", top_k: int = 5,
                     return_mode: str = "compact", tags_json: str = "[]",
                     use_graph: bool = True) -> str:
    try:
        tags = json.loads(tags_json) if str(tags_json or "").strip() else []
        if return_mode == "compact":
            items = _run(_m().search_compact(query, top_k=int(top_k)))
        else:
            items = _run(_m().search(query=query, scope=scope, top_k=int(top_k),
                                      return_mode=return_mode, tags=tags,
                                      use_graph=use_graph))
        if return_mode == "paths":
            return json.dumps([it.get("path") for it in items], ensure_ascii=False)
        return json.dumps(items, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def attachmentWriteTool(file_path: str, path: str = "", *,
                        description: str = "",
                        content_type: str = "") -> str:
    try:
        from pathlib import Path as _Path
        fp = _Path(file_path)
        if not fp.exists():
            return json.dumps({"status": "error", "error": f"文件不存在: {file_path}"}, ensure_ascii=False)
        raw = fp.read_bytes()
        image_base64 = base64.b64encode(raw).decode("ascii")
        kb_path = str(path or "").strip() or f"/images/{fp.name}"
        ct = str(content_type or "").strip() or {
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
        }.get(fp.suffix.lower(), "image/png")
        result = _run(_m().attachment_write(kb_path, image_base64,
                                            description=description,
                                            content_type=ct))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


def attachmentReadTool(path: str) -> str:
    try:
        result = _run(_m().attachment_read(path))
        payload = {
            "kind": "multimodal_tool_result",
            "text": result.get("description", ""),
            "images": [{
                "url": f"data:{result['content_type']};base64,{result['content_base64']}"
            }],
        }
        return json.dumps(payload, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


async def _bg_extract_and_save(text: str, doc_path: str = "") -> None:
    log.info("_bg_extract_and_save doc_path=%s text_len=%d", doc_path, len(text))
    try:
        import faust_backend.config_loader as conf
        from pathlib import Path
        from faust_backend.memory.store import _path_id
        from faust_backend.memory.config import ENTITY_DEDUP_THRESHOLD
        from openai import AsyncOpenAI
        prompt_path = Path(__file__).parent / "extraction_prompt.md"
        system_prompt = prompt_path.read_text(encoding="utf-8") if prompt_path.exists() else "Extract entities and relations as JSON."
        client = AsyncOpenAI(
            api_key=conf.KB_OPENAI_API_KEY or conf.CHAT_API_KEY,
            base_url=conf.CHAT_API_BASE or "https://api.openai.com/v1",
        )
        response = await client.chat.completions.create(
            model=conf.CHAT_MODEL or "gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )
        raw = str(response.choices[0].message.content or "{}")
        result = json.loads(raw)
        entities = result.get("entities", [])
        relations = result.get("relations", [])
        m = get_memory()

        if entities and doc_path:
            names = [str(e.get("name", "")) for e in entities]
            name_vecs = await m._embed_texts(names)
            existing_ids = await m.entity_find_similar(name_vecs, threshold=ENTITY_DEDUP_THRESHOLD)
            doc_nid = _path_id(doc_path)

            for item, name_vec, existing_id in zip(entities, name_vecs, existing_ids):
                name = str(item.get("name", ""))
                etype = str(item.get("type", "custom"))
                desc = str(item.get("description", ""))
                props = item.get("properties", {}) or {}
                refs = item.get("kb_refs", []) or []

                if existing_id:
                    if m._has_node(doc_nid) and m._has_node(existing_id):
                        m._add_edge(doc_nid, existing_id, "from")
                else:
                    eid = m.entity_add(name, etype, description=desc,
                                       properties=props, kb_refs=refs,
                                       name_embedding=name_vec.tolist())
                    if m._has_node(doc_nid):
                        m._add_edge(doc_nid, eid, "from")

        for item in relations:
            m.relation_add(
                source_id=str(item.get("source", "")),
                target_id=str(item.get("target", "")),
                rel_type=str(item.get("type", "relates_to")),
            )
        m.flush()
        log.info("_bg_extract_and_save done entities=%d relations=%d",
                 len(entities) if entities else 0, len(relations) if relations else 0)
    except Exception as e:
        log.error("bg_extract_and_save failed: %s", e)
