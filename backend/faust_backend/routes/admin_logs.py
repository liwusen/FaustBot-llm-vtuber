from fastapi import APIRouter, HTTPException
from faust_backend.logger import get_recent_errors
from faust_backend.runtime import state

router = APIRouter(tags=["admin-logs"])
router.description = "日志管理：获取最近 ERROR 级别日志"


@router.get("/faust/admin/log/recent-errors")
async def admin_log_recent_errors():
    errors = get_recent_errors(count=5)
    return {"status": "ok", "errors": errors}
