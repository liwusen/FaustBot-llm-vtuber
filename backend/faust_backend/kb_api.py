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
        if not path:
            raise HTTPException(status_code=400, detail="缺少 path")
        manager = get_kb_manager(refresh=True)
        try:
            result = await manager.write_node(path, content, declared_by=declared_by, index=index)
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
            )
            return {"status": "ok", "items": results}
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