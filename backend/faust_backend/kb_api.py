from __future__ import annotations

from fastapi import HTTPException, Query

import faust_backend.config_loader as conf
from faust_backend.kb_manager import get_kb_manager


def register_kb_routes(app):
    @app.get("/faust/kb/tree")
    async def kb_tree(scope: str | None = Query(default=None)):
        manager = get_kb_manager(refresh=True)
        return {"status": "ok", "agent": conf.AGENT_NAME, "tree": manager.list_tree(scope)}

    @app.get("/faust/kb/get")
    async def kb_get(path: str):
        manager = get_kb_manager(refresh=True)
        try:
            return {"status": "ok", **manager.read_node(path)}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/faust/kb/save")
    async def kb_save(payload: dict | None = None):
        body = payload or {}
        path = str(body.get("path") or "").strip()
        content = str(body.get("content") or "")
        declared_by = body.get("declared_by")
        index = bool(body.get("index", True))
        tags = body.get("tags") or []
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path")
        manager = get_kb_manager(refresh=True)
        try:
            result = await manager.write_node(path, content, declared_by=declared_by, index=index, tags=tags)
            return {"status": "ok", **result}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/faust/kb/mkdir")
    async def kb_mkdir(payload: dict | None = None):
        body = payload or {}
        path = str(body.get("path") or "").strip()
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path")
        manager = get_kb_manager(refresh=True)
        try:
            return {"status": "ok", **(await manager.mkdir(path))}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/faust/kb/delete")
    async def kb_delete(payload: dict | None = None):
        body = payload or {}
        path = str(body.get("path") or "").strip()
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path")
        manager = get_kb_manager(refresh=True)
        try:
            return {"status": "ok", **(await manager.delete_node(path))}
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/faust/kb/search")
    async def kb_search(payload: dict | None = None):
        body = payload or {}
        manager = get_kb_manager(refresh=True)
        try:
            results = await manager.search(
                query=str(body.get("query") or ""),
                scope=body.get("scope"),
                top_k=int(body.get("top_k") or 8),
                return_mode=str(body.get("return") or "snippets"),
                tags=body.get("tags") or [],
                ignore_score_patch=bool(body.get("ignore_score_patch", False)),
            )
            return {"status": "ok", "items": results}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/faust/kb/tags")
    async def kb_tags(payload: dict | None = None):
        body = payload or {}
        path = str(body.get("path") or "").strip()
        tags = body.get("tags") or []
        managed_by = body.get("managed_by")
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path")
        manager = get_kb_manager(refresh=True)
        try:
            return {"status": "ok", **(await manager.set_tags(path, tags, managed_by=managed_by))}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/faust/kb/score-patch")
    async def kb_score_patch(payload: dict | None = None):
        body = payload or {}
        path = str(body.get("path") or "").strip()
        managed_by = body.get("managed_by")
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path")
        manager = get_kb_manager(refresh=True)
        try:
            return {"status": "ok", **(await manager.set_score_patch(path, body.get("score_patch", 0.0), managed_by=managed_by))}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/faust/kb/changed")
    async def kb_changed(since_ts: float, scope: str | None = Query(default=None), tags: str | None = Query(default=None)):
        manager = get_kb_manager(refresh=True)
        tag_list = [item.strip() for item in str(tags or "").split(",") if item.strip()]
        try:
            return {"status": "ok", "items": manager.get_changed_nodes(since_ts, scope=scope, tags=tag_list)}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/faust/kb/reindex")
    async def kb_reindex(payload: dict | None = None):
        manager = get_kb_manager(refresh=True)
        task = await manager.enqueue_task("reindex_all", payload or {})
        return {"status": "ok", "task": task}

    @app.get("/faust/kb/tasks")
    async def kb_tasks():
        manager = get_kb_manager(refresh=True)
        return {"status": "ok", "items": manager.get_tasks()}

    @app.post("/faust/kb/declare-update")
    async def kb_declare_update(payload: dict | None = None):
        body = payload or {}
        file_path = str(body.get("file_path") or "").strip()
        kb_path = body.get("kb_path")
        if not file_path:
            raise HTTPException(status_code=400, detail="缺少 file_path")
        manager = get_kb_manager(refresh=True)
        try:
            result = await manager.declare_file_update(file_path, kb_path=kb_path)
            return {"status": "ok", **result}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))