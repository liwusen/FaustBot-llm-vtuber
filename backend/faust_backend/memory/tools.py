from __future__ import annotations

import asyncio
import base64
import json
import threading
import uuid
from typing import Any
import traceback

from faust_backend.logger import get_logger
from faust_backend.memory import get_memory
from faust_backend.memory import GraphStore
from openai import AsyncOpenAI
log = get_logger("faust.memory.tools")


def _run(coro) -> Any:
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            result_box: dict[str, Any] = {}
            error_box: dict[str, BaseException] = {}

            def _thread_main() -> None:
                try:
                    result_box["value"] = asyncio.run(coro)
                except BaseException as exc:
                    error_box["error"] = exc

            worker = threading.Thread(target=_thread_main, daemon=True, name="faust-memory-run")
            worker.start()
            worker.join(timeout=120)
            if worker.is_alive():
                raise TimeoutError("memory coroutine execution timed out")
            if "error" in error_box:
                raise error_box["error"]
            return result_box.get("value")# ?:Review Needed
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


def _m() -> GraphStore:
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
        import httpx
        prompt_path = Path(__file__).parent / "extraction_prompt.md"
        system_prompt = prompt_path.read_text(encoding="utf-8")
        from faust_backend.runtime import state as runtime_state
        from faust_backend.provider import get_main_credentials
        api_model, api_key, api_base_raw = get_main_credentials(runtime_state.get_model_providers())
        api_base = (api_base_raw or None).rstrip("/") if api_base_raw else None
        api_key = api_key or None
        api_model = api_model or None
        if not api_key or not api_base or not api_model:
            log.critical("LLM extraction API not configured, skipping extraction")
            raise ValueError("LLM extraction API not configured")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        doc_path = ("/" + str(doc_path).strip("/")) if doc_path else ""
        m = get_memory()
        if doc_path:
            m.register_extraction(doc_path)

        # async with httpx.AsyncClient(timeout=120.0) as hc:
        #     payload = {
        #         "model": api_model,
        #         "messages": [
        #             {"role": "system", "content": system_prompt},
        #             {"role": "user", "content": text},
        #         ],
        #         "temperature": 0.1,
        #         "response_format": {"type": "json_object"},
        #     }
        #     resp = await hc.post(f"{api_base}/chat/completions", headers=headers, json=payload)
        #     if resp.status_code != 200:
        #         log.warning("LLM extraction API error %d: %s", resp.status_code, resp.text[:300])
        #         # 如果 qwen 不支持 json_object，异常已经被底层捕获
        #         # 直接返回空结果
        #         raw = "{}"
        #     else:
        #         data = resp.json()
        #         raw = str(data.get("choices", [{}])[0].get("message", {}).get("content", "{}"))
        async with AsyncOpenAI(api_key=api_key, base_url=api_base) as client:
            raw = await client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                temperature=0.1,
                response_format={"type": "json_object"},
                model=api_model,
            )
            raw= str(raw.choices[0].message.content)
        log.info("_bg_extract_and_save raw=%s", raw)
        result = json.loads(raw)
        log.info("_bg_extract_and_save result=%s", result)
        entities = result.get("entities", [])
        relations = result.get("relations", [])
        name_to_id: dict[str, str] = {}
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

        for item in relations:
            src_name = str(item.get("source", ""))
            tgt_name = str(item.get("target", ""))
            src_id = name_to_id.get(src_name)
            tgt_id = name_to_id.get(tgt_name)
            if not src_id or not tgt_id:
                log.warning("relation skipped: source=%s target=%s not found in current extraction", src_name, tgt_name)
                continue
            m.relation_add(
                source_id=src_id,
                target_id=tgt_id,
                rel_type=str(item.get("type", "relates_to")),
            )
        m.flush()
        log.info("_bg_extract_and_save done entities=%d relations=%d",
                 len(entities) if entities else 0, len(relations) if relations else 0)
        m.complete_extraction(doc_path, success=True)
    except Exception as e:
        log.error("bg_extract_and_save failed: %s", e)
        log.error("Traceback:\n%s", traceback.format_exc())
        try:
            m_ref = get_memory()
            m_ref.complete_extraction(doc_path, success=False, error=str(e))
        except Exception:
            log.error("Failed to complete extraction for doc_path=%s", doc_path)