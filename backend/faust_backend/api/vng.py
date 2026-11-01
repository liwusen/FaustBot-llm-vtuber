"""VNG（视觉小说）模式：候选台词生成 HTTP 适配层。

业务逻辑全部在 ``faust_backend.agent.vng``；本模块只做模式/直播/运行时判定与
异常收口，不写 checkpoint、不占用调度器执行位。
"""

from __future__ import annotations

from fastapi import APIRouter

from faust_backend.agent import vng
from faust_backend.agent.runtime import state
from faust_backend.core.log import get_logger
from faust_backend.core.ui_settings import get_vng_enabled
from faust_backend.live.mode import is_live_mode

router = APIRouter(tags=["vng"])
router.description = "VNG（视觉小说）模式：玩家候选台词生成"

log = get_logger("faust.api.vng")


@router.post("/faust/vng/choices")
async def vng_choices():
    """生成 3 个玩家候选台词；所有失败都以 status 字段返回，不抛 500。"""
    if not get_vng_enabled():
        return {"status": "disabled"}
    if is_live_mode():
        return {"status": "blocked", "reason": "live_mode"}
    if not state.RUNTIME_READY or state.agent is None:
        return {"status": "error", "error": state.runtime_not_ready_message()}

    result = await vng.generate_choices()
    if result.get("status") != "ok":
        log.warning("VNG 候选请求未成功: %s", result)
    return result
