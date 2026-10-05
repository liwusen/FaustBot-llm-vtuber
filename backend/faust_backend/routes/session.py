"""会话上下文统计路由（供前端输入栏的上下文占用药丸使用）。"""

from __future__ import annotations

from fastapi import APIRouter

from faust_backend.runtime.session_stats import collect_session_stats

router = APIRouter(tags=["session"])
router.description = "会话上下文统计：上下文占用、窗口上限、自动压缩阈值"


@router.get("/faust/session/context")
async def session_context_api():
    """返回当前会话的上下文占用快照（字段说明见 runtime/session_stats.py）。"""
    return await collect_session_stats()
