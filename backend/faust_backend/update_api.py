import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from faust_backend.update_manager import (
    UpdateManager,
    create_download_task,
    get_download_task,
    cleanup_download_task,
)

log = logging.getLogger("faust.update")
router = APIRouter(prefix="/faust/update")

_mgr: UpdateManager | None = None


def _manager() -> UpdateManager:
    global _mgr
    if _mgr is None:
        _mgr = UpdateManager()
    return _mgr


# ─── check ─────────────────────────────────────────────────


class CheckResponse(BaseModel):
    status: str
    current_tag: str
    current_version: str
    latest_tag: str | None = None
    latest_version: str | None = None
    asset_name: str | None = None
    has_update: bool = False
    published_at: str | None = None
    release_body: str | None = None
    error: str | None = None


@router.post("/check", response_model=CheckResponse)
async def check_update():
    mgr = _manager()
    result = await mgr.check_latest(force=True)
    if result.get("error"):
        return CheckResponse(
            status="error",
            current_tag=mgr.current_tag(),
            current_version=mgr.current_version(),
            error=result["error"],
        )
    return CheckResponse(
        status="ok",
        current_tag=mgr.current_tag(),
        current_version=mgr.current_version(),
        latest_tag=result.get("tag"),
        latest_version=result.get("version"),
        asset_name=result.get("asset_name"),
        has_update=result.get("has_update", False),
        published_at=result.get("published_at"),
        release_body=result.get("body"),
    )


# ─── start download ────────────────────────────────────────


class StartDownloadResponse(BaseModel):
    status: str
    download_id: str
    tag: str
    asset_name: str


@router.post("/start-download", response_model=StartDownloadResponse)
async def start_download(payload: dict | None = None):
    body = payload or {}
    tag = str(body.get("tag") or "").strip()
    asset_name = str(body.get("asset_name") or "").strip()
    use_proxy = bool(body.get("use_proxy", True))

    mgr = _manager()
    if not tag:
        info = await mgr.check_latest(force=True)
        tag = str(info.get("tag", ""))
        asset_name = str(info.get("asset_name", ""))

    if not tag or not asset_name:
        raise HTTPException(status_code=400, detail="无法获取发布信息")

    download_id = create_download_task(tag, asset_name, use_proxy)
    return StartDownloadResponse(
        status="started", download_id=download_id, tag=tag, asset_name=asset_name
    )


# ─── SSE progress ──────────────────────────────────────────


@router.get("/download/{download_id}/events")
async def download_events(download_id: str):
    task = get_download_task(download_id)
    if task is None:
        raise HTTPException(status_code=404, detail="下载任务不存在")

    async def event_stream():
        try:
            while True:
                if task.error:
                    log.error(f"Download task {download_id} failed with error: {task.error}")
                    yield f"event: error\ndata: {json.dumps({'error': task.error})}\n\n"
                    return
                if task.done:
                    log.info(f"Download task {download_id} completed successfully")
                    yield f"event: complete\ndata: {json.dumps(task.to_dict())}\n\n"
                    return
                log.debug(f"Download task {download_id} progress: {task.progress}%")
                yield f"event: progress\ndata: {json.dumps(task.to_dict())}\n\n"
                await asyncio.sleep(0.5)
        finally:
            cleanup_download_task(download_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ─── dry-run (uses already-downloaded zip) ─────────────────


class DryRunResponse(BaseModel):
    status: str
    tag: str
    preserved: list[str] = []
    overwritten: list[str] = []
    new_files: list[str] = []
    error: str | None = None


@router.post("/dry-run", response_model=DryRunResponse)
async def dry_run_update(payload: dict | None = None):
    body = payload or {}
    tag = str(body.get("tag") or "").strip()
    asset_name = str(body.get("asset_name") or "").strip()

    mgr = _manager()
    if not tag:
        info = await mgr.check_latest(force=True)
        tag = str(info.get("tag", ""))
        asset_name = str(info.get("asset_name", ""))
    if not tag or not asset_name:
        return DryRunResponse(status="error", tag="", error="无法获取最新发布信息")

    try:
        diff = await mgr.diff_release(tag, asset_name)
        return DryRunResponse(
            status="ok",
            tag=tag,
            preserved=diff["preserved"],
            overwritten=diff["overwritten"],
            new_files=diff["new_files"],
        )
    except Exception as e:
        return DryRunResponse(status="error", tag=tag, error=str(e))


# ─── apply update ──────────────────────────────────────────


class ApplyResponse(BaseModel):
    status: str
    tag: str
    script_path: str | None = None
    message: str | None = None
    error: str | None = None


@router.post("/apply", response_model=ApplyResponse)
async def apply_update(payload: dict | None = None):
    body = payload or {}
    tag = str(body.get("tag") or "").strip()
    asset_name = str(body.get("asset_name") or "").strip()

    mgr = _manager()
    if not tag:
        info = await mgr.check_latest(force=True)
        tag = str(info.get("tag", ""))
        asset_name = str(info.get("asset_name", ""))
    if not tag or not asset_name:
        return ApplyResponse(status="error", tag="", error="无法获取最新发布信息")

    try:
        result = await mgr.apply_update(tag, asset_name)
        return ApplyResponse(
            status=result["status"],
            tag=tag,
            script_path=result.get("script_path"),
            message=result.get("message"),
        )
    except Exception as e:
        return ApplyResponse(status="error", tag=tag, error=str(e))
