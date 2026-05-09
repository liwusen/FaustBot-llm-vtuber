import asyncio
import uuid
import json

from langchain.tools import tool

from faust_backend.tools._registry import register, _run_async_in_thread
import faust_backend.events as events
import faust_backend.backend2front as backend2frontend


async def HILRequest(id, title, summary, timeout_seconds: int = 120, severity: str = "warning"):
    request_id = str(id or f"hil_{uuid.uuid4().hex}")
    future = events.create_hil_request(request_id)
    backend2frontend.FrontendHIL({
        "request_id": request_id,
        "title": str(title or "需要人工确认"),
        "summary": str(summary or ""),
        "severity": str(severity or "warning"),
        "timeout_seconds": int(max(5, timeout_seconds)),
    })
    try:
        result = await asyncio.wait_for(future, timeout=max(5, int(timeout_seconds)))
    except asyncio.TimeoutError:
        events.cancel_hil_request(request_id, "timeout")
        backend2frontend.FrontEndCloseNimbleWindow({"callback_id": request_id, "reason": "timeout"})
        return False, "timeout"

    approved = bool((result or {}).get("approved"))
    reason = str((result or {}).get("reason") or ("approved" if approved else "rejected"))
    return approved, reason


@register
@tool
async def requestHumanApprovalTool(title: str, summary: str, timeout_seconds: int = 120, severity: str = "warning") -> str:
    """
    Description:
        请求用户在前端审批窗口中批准或拒绝一项操作。
        当操作具有风险、不可逆、会安装外部资源、修改关键文件或涉及高权限行为时，应优先使用此工具。
    Args:
        title (str): 审批窗口标题，直接说明要批准什么。
        summary (str): 详细说明本次操作内容、风险、影响范围。
        timeout_seconds (int): 等待用户审批的超时时间，默认 120 秒。
        severity (str): 风险级别，可选 info、warning、danger。
    Returns:
        str: JSON 字符串，包含 approved、reason、title。
    """
    approved, reason = await HILRequest(
        id=f"hil_tool_{uuid.uuid4().hex}",
        title=title,
        summary=summary,
        timeout_seconds=timeout_seconds,
        severity=severity,
    )
    return json.dumps({"approved": bool(approved), "reason": reason, "title": title}, ensure_ascii=False)
