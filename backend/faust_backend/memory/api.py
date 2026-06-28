from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from faust_backend.logger import get_logger
from faust_backend.memory import get_memory
from faust_backend.memory.tools import _bg_extract_and_save, _run_bg

log = get_logger("faust.memory.api")
router = APIRouter(prefix="/faust/memory", tags=["memory"])


def _m():
    return get_memory()


# ── tree ──

@router.get("/tree")
async def memory_tree(scope: str | None = Query(default=None)):
    result = await _m().tree_list(scope)
    log.info("GET /tree scope=%s", scope)
    return {"status": "ok", "tree": result}


@router.get("/get")
async def memory_get(path: str = Query(...)):
    try:
        result = await _m().file_read(path)
        log.info("GET /get path=%s", path)
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        log.warning("GET /get not_found path=%s", path)
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/save")
async def memory_save(payload: dict):
    path = str((payload or {}).get("path", "")).strip()
    if not path:
        raise HTTPException(status_code=400, detail="missing path")
    content = str((payload or {}).get("content", "")).strip()
    description = str((payload or {}).get("description", "")).strip()
    tags = payload.get("tags")
    declared_by = str(payload.get("declared_by", "config") or "config")
    log.info("POST /save path=%s declared_by=%s content_len=%d", path, declared_by, len(content))
    result = await _m().file_write(path, content, description=description, declared_by=declared_by, tags=tags)
    _run_bg("auto_extract", _bg_extract_and_save(content, path))
    return {"status": "ok", **result}


@router.delete("/delete")
async def memory_delete(path: str = Query(...)):
    try:
        result = await _m().file_delete(path)
        log.info("DELETE /delete path=%s", path)
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        log.warning("DELETE /delete not_found path=%s", path)
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/delete")
async def memory_delete_post(payload: dict):
    path = str((payload or {}).get("path", "")).strip()
    if not path:
        raise HTTPException(status_code=400, detail="missing path")
    try:
        result = await _m().file_delete(path)
        log.info("POST /delete path=%s", path)
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/mkdir")
async def memory_mkdir(payload: dict):
    path = str((payload or {}).get("path", "")).strip()
    if not path:
        raise HTTPException(status_code=400, detail="missing path")
    result = await _m().mkdir(path)
    log.info("POST /mkdir path=%s", path)
    return {"status": "ok", **result}


@router.post("/search")
async def memory_search(payload: dict):
    query = str((payload or {}).get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="missing query")
    scope = payload.get("scope")
    top_k = int(payload.get("top_k", 8))
    return_mode = str(payload.get("return_mode", "snippets") or "snippets")
    tags = payload.get("tags")
    use_graph = bool(payload.get("use_graph", True))
    items = await _m().search(query, scope=scope, top_k=top_k,
                              return_mode=return_mode, tags=tags,
                              use_graph=use_graph)
    log.info("POST /search query=%s scope=%s hits=%d", query, scope, len(items))
    return {"status": "ok", "items": items}


@router.post("/search-compact")
async def memory_search_compact(payload: dict):
    query = str((payload or {}).get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="missing query")
    top_k = int(payload.get("top_k", 5))
    items = await _m().search_compact(query, top_k=top_k)
    log.info("POST /search-compact query=%s hits=%d", query, len(items))
    return {"status": "ok", "items": items}


@router.post("/tags")
async def memory_set_tags(payload: dict):
    path = str((payload or {}).get("path", "")).strip()
    if not path:
        raise HTTPException(status_code=400, detail="missing path")
    tags = payload.get("tags", [])
    log.info("POST /tags path=%s tags=%s", path, tags)
    result = await _m().set_tags(path, tags)
    return {"status": "ok", **result}


@router.post("/score-patch")
async def memory_set_score_patch(payload: dict):
    path = str((payload or {}).get("path", "")).strip()
    if not path:
        raise HTTPException(status_code=400, detail="missing path")
    score_patch = float(payload.get("score_patch", 0))
    log.info("POST /score-patch path=%s score_patch=%s", path, score_patch)
    try:
        result = await _m().set_score_patch(path, score_patch)
        return {"status": "ok", **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/changed")
async def memory_changed(since_ts: float = Query(...), scope: str | None = Query(default=None)):
    items = await _m().get_changed_nodes(since_ts, scope=scope)
    log.info("GET /changed since=%s scope=%s hits=%d", since_ts, scope, len(items))
    return {"status": "ok", "items": items}


@router.get("/tasks")
async def memory_tasks():
    items = _m().get_tasks()
    log.info("GET /tasks count=%d", len(items))
    return {"status": "ok", "items": items}


# ── entity / relation ──

@router.get("/graph/entities")
async def graph_entities():
    items = _m().entity_iter()
    log.info("GET /graph/entities count=%d", len(items))
    return {"status": "ok", "items": items}


@router.get("/graph/search")
async def graph_search(query: str = Query(default=""),
                       type_filter: str | None = Query(default=None),
                       top_k: int = 20):
    items = _m().entity_search(query, type_filter=type_filter, top_k=top_k)
    log.info("GET /graph/search query=%s filter=%s hits=%d", query, type_filter, len(items))
    return {"status": "ok", "items": items}


@router.get("/graph/neighbors")
async def graph_neighbors(entity_id: str = Query(...), depth: int = 1):
    items = _m().get_neighbors(entity_id, depth=depth)
    log.info("GET /graph/neighbors eid=%s depth=%d hits=%d", entity_id[:16], depth, len(items))
    return {"status": "ok", "items": items}


@router.get("/graph/expand")
async def graph_expand(entity_id: str = Query(...), depth: int = 1):
    items = _m().get_neighbors(entity_id, depth=depth)
    edges = []
    nid_set = {entity_id} | {it["id"] for it in items}
    for src, tgt, k, edata in _m()._graph.edges(data=True, keys=True):
        if src in nid_set and tgt in nid_set:
            etype = str(edata.get("type", "relates_to")) if edata else "relates_to"
            edges.append({"source": src, "target": tgt, "type": etype, "key": str(k)})
    log.info("GET /graph/expand eid=%s depth=%d nodes=%d edges=%d",
             entity_id[:16], depth, len(items), len(edges))
    return {"status": "ok", "items": items, "edges": edges}


@router.get("/graph/entity-children")
async def graph_entity_children(path: str = Query(...)):
    items = _m().get_entity_children(path)
    log.info("GET /graph/entity-children path=%s count=%d", path, len(items))
    return {"status": "ok", "items": items}


@router.get("/graph/relations")
async def graph_relations():
    items = _m().relation_iter()
    log.info("GET /graph/relations count=%d", len(items))
    return {"status": "ok", "items": items}


@router.get("/graph/full")
async def graph_full():
    relations = _m().relation_iter()
    entity_nodes = _m().entity_iter()
    seen_ids = {e["id"] for e in entity_nodes}
    for r in relations:
        seen_ids.add(r["source"])
        seen_ids.add(r["target"])
    all_nodes = list(entity_nodes)
    for nid in seen_ids:
        if nid in {e["id"] for e in all_nodes}:
            continue
        ndata = _m()._graph.nodes.get(nid)
        if not ndata:
            continue
        ntype = ndata.get("type", "unknown")
        ent_type = ndata.get("entity_type", ntype)
        from faust_backend.memory.store import _id_to_path
        all_nodes.append({
            "id": nid,
            "name": ndata.get("name", _id_to_path(nid) if nid.startswith("path:") else nid),
            "entity_type": ent_type,
            "description": ndata.get("description", ""),
            "properties": dict(ndata.get("properties", {})),
            "kb_refs": list(ndata.get("kb_refs", [])),
            "created_at": ndata.get("created_at", ndata.get("updated_at", "")),
        })
    log.info("GET /graph/full entities=%d relations=%d", len(all_nodes), len(relations))
    return {"status": "ok", "entities": all_nodes, "relations": relations}


@router.post("/graph/entity")
async def graph_add_entity(payload: dict):
    name = str((payload or {}).get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=400, detail="missing name")
    etype = str(payload.get("type", "custom"))
    description = str(payload.get("description", ""))
    properties = payload.get("properties", {})
    kb_refs = payload.get("kb_refs", [])
    eid = _m().entity_add(name, etype, description=description, properties=properties, kb_refs=kb_refs)
    log.info("POST /graph/entity name=%s type=%s eid=%s", name, etype, eid[:16])
    return {"status": "ok", "entity_id": eid}


@router.post("/graph/relation")
async def graph_add_relation(payload: dict):
    source = str((payload or {}).get("source", "")).strip()
    target = str((payload or {}).get("target", "")).strip()
    if not source or not target:
        raise HTTPException(status_code=400, detail="missing source or target")
    rel_type = str(payload.get("type", "relates_to"))
    log.info("POST /graph/relation src=%s tgt=%s type=%s", source[:16], target[:16], rel_type)
    key = _m().relation_add(source, target, rel_type)
    return {"status": "ok", "key": key}


@router.post("/graph/link")
async def graph_link(payload: dict):
    entity_name = str((payload or {}).get("entity_name", "")).strip()
    kb_path = str((payload or {}).get("kb_path", "")).strip()
    if not entity_name or not kb_path:
        raise HTTPException(status_code=400, detail="missing entity_name or kb_path")
    matches = _m().entity_search(entity_name, top_k=5)
    if not matches:
        raise HTTPException(status_code=404, detail=f"未找到实体: {entity_name}")
    best = matches[0]
    refs = list(best.get("kb_refs", []))
    if kb_path not in refs:
        refs.append(kb_path)
        from faust_backend.memory.store import _normalize_path as _np
        norm_path = _np(kb_path)
        eid = best["id"]
        ndata = _m()._graph.nodes[eid]
        ndata["kb_refs"] = refs
        _m()._dirty = True
    log.info("POST /graph/link entity=%s kb_path=%s refs=%d", entity_name, kb_path, len(refs))
    return {"status": "ok", "entity_id": best["id"], "kb_refs": refs}


# ── diary ──

@router.post("/diary")
async def memory_diary(payload: dict):
    content = str((payload or {}).get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=400, detail="missing content")
    log.info("POST /diary content_len=%d", len(content))
    result = await _m().write_diary(content)
    return {"status": "ok", **result}


@router.post("/declare-update")
async def memory_declare_update(payload: dict):
    file_path = str((payload or {}).get("file_path", "")).strip()
    if not file_path:
        raise HTTPException(status_code=400, detail="missing file_path")
    kb_path = str((payload or {}).get("kb_path", "")).strip() or None
    log.info("POST /declare-update file_path=%s kb_path=%s", file_path, kb_path)
    try:
        result = await _m().declare_file_update(file_path, kb_path)
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/attachment")
async def memory_attachment_write(payload: dict):
    path = str((payload or {}).get("path", "")).strip()
    if not path:
        raise HTTPException(status_code=400, detail="missing path")
    image_base64 = str((payload or {}).get("image", "")).strip()
    if not image_base64:
        file_path = str((payload or {}).get("file_path", "")).strip()
        if file_path:
            from pathlib import Path
            fp = Path(file_path)
            if fp.exists():
                raw = fp.read_bytes()
                import base64 as _b64
                image_base64 = _b64.b64encode(raw).decode("ascii")
                path = path or f"/images/{fp.name}"
                ct = str(payload.get("content_type", "") or "").strip() or {
                    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                }.get(fp.suffix.lower(), "image/png")
                payload["content_type"] = ct
    if not image_base64:
        raise HTTPException(status_code=400, detail="missing image or file_path")
    description = str((payload or {}).get("description", "")).strip()
    content_type = str((payload or {}).get("content_type", "image/png") or "image/png")
    log.info("POST /attachment path=%s content_type=%s desc_len=%d", path, content_type, len(description))
    result = await _m().attachment_write(path, image_base64,
                                          description=description,
                                          content_type=content_type)
    return {"status": "ok", **result}


@router.get("/attachment")
async def memory_attachment_read(path: str = Query(...)):
    try:
        result = await _m().attachment_read(path)
        log.info("GET /attachment path=%s content_type=%s", path, result.get("content_type"))
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        log.warning("GET /attachment not_found path=%s", path)
        raise HTTPException(status_code=404, detail=str(e))


# ── rename / copy / move ──

@router.post("/rename")
async def memory_rename(payload: dict):
    path = str((payload or {}).get("path", "")).strip()
    new_name = str((payload or {}).get("new_name", "")).strip()
    if not path or not new_name:
        raise HTTPException(status_code=400, detail="missing path or new_name")
    try:
        result = await _m().file_rename(path, new_name)
        log.info("POST /rename path=%s new_name=%s", path, new_name)
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/copy")
async def memory_copy(payload: dict):
    path = str((payload or {}).get("path", "")).strip()
    dest = str((payload or {}).get("dest", "")).strip()
    if not path or not dest:
        raise HTTPException(status_code=400, detail="missing path or dest")
    try:
        result = await _m().file_copy(path, dest)
        log.info("POST /copy path=%s dest=%s", path, dest)
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/move")
async def memory_move(payload: dict):
    path = str((payload or {}).get("path", "")).strip()
    dest_dir = str((payload or {}).get("dest_dir", "")).strip()
    if not path or not dest_dir:
        raise HTTPException(status_code=400, detail="missing path or dest_dir")
    try:
        result = await _m().file_move(path, dest_dir)
        log.info("POST /move path=%s dest_dir=%s", path, dest_dir)
        return {"status": "ok", **result}
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FileExistsError as e:
        raise HTTPException(status_code=409, detail=str(e))


# ── advanced search ──

@router.post("/advanced-search")
async def memory_advanced_search(payload: dict):
    query = str((payload or {}).get("query", "")).strip() or None
    tags = payload.get("tags")
    if isinstance(tags, list) and not tags:
        tags = None
    scope = str((payload or {}).get("scope", "")).strip() or None
    date_from = str((payload or {}).get("date_from", "")).strip() or None
    date_to = str((payload or {}).get("date_to", "")).strip() or None
    declared_by = str((payload or {}).get("declared_by", "")).strip() or None
    content_type = str((payload or {}).get("content_type", "")).strip() or None
    top_k = int(payload.get("top_k", 20))
    sort_by = str(payload.get("sort_by", "relevance"))
    sort_order = str(payload.get("sort_order", "desc"))
    tag_logic = str(payload.get("tag_logic", "AND"))
    items = await _m().advanced_search(
        query=query, tags=tags, scope=scope,
        date_from=date_from, date_to=date_to,
        declared_by=declared_by, content_type=content_type,
        top_k=top_k, sort_by=sort_by, sort_order=sort_order,
        tag_logic=tag_logic,
    )
    log.info("POST /advanced-search query=%s tags=%s hits=%d", query, tags, len(items))
    return {"status": "ok", "items": items}


# ── extraction status ──

@router.get("/extraction-status")
async def memory_extraction_status():
    status = _m().get_extraction_status()
    log.info("GET /extraction-status pending=%d running=%d",
             status.get("pending", 0), status.get("running", 0))
    return {"status": "ok", **status}


# ── entity detail ──

@router.get("/graph/entity-detail")
async def graph_entity_detail(entity_id: str = Query(...)):
    result = _m().get_entity_detail(entity_id)
    if result is None:
        raise HTTPException(status_code=404, detail="entity not found")
    log.info("GET /graph/entity-detail eid=%s name=%s", entity_id[:16], result.get("name"))
    return {"status": "ok", "detail": result}
