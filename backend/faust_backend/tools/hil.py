import asyncio
import uuid
import json

from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.events as events
import faust_backend.backend2front as backend2frontend


def _norm_button(button: dict) -> dict:
    """归一化一个自定义按钮：``{value, label, approved, default}``。"""
    value = str((button or {}).get("value") or "").strip()
    if not value:
        raise ValueError("HIL 自定义按钮缺少 value")
    label = str((button or {}).get("label") or value).strip()
    approved = (button or {}).get("approved")
    if approved is None:
        approved = value == "allow"
    return {"value": value, "label": label, "approved": bool(approved),
            "default": bool((button or {}).get("default"))}


async def HILPrompt(id, title, summary, *, buttons: list[dict] | None = None,
                    timeout_seconds: int = 120, severity: str = "warning"):
    """弹出人工确认窗口并等待选择。

    - ``buttons=None``：前端渲染默认的两个按钮（拒绝 / 批准）；
    - ``buttons``：前端按给定顺序渲染 ``[{value,label,approved,default}]``，按钮的 ``value`` 会回传。

    Returns: ``(approved: bool, reason: str, choice: str)``；超时为 ``(False, "timeout", "timeout")``。
    """
    request_id = str(id or f"hil_{uuid.uuid4().hex}")
    future = events.create_hil_request(request_id)
    payload = {
        "request_id": request_id,
        "title": str(title or "需要人工确认"),
        "summary": str(summary or ""),
        "severity": str(severity or "warning"),
        "timeout_seconds": int(max(5, timeout_seconds)),
    }
    if buttons:
        payload["buttons"] = [_norm_button(b) for b in buttons]
    backend2frontend.FrontendHIL(payload)
    try:
        result = await asyncio.wait_for(future, timeout=max(5, int(timeout_seconds)))
    except asyncio.TimeoutError:
        events.cancel_hil_request(request_id, "timeout")
        backend2frontend.FrontEndCloseNimbleWindow({"callback_id": request_id, "reason": "timeout"})
        return False, "timeout", "timeout"

    result = result or {}
    approved = bool(result.get("approved"))
    reason = str(result.get("reason") or ("approved" if approved else "rejected"))
    choice = str(result.get("choice") or ("allow" if approved else "reject"))
    return approved, reason, choice


async def HILRequest(id, title, summary, timeout_seconds: int = 120, severity: str = "warning"):
    """既有入口：二值批准/拒绝，返回 ``(approved, reason)``。"""
    approved, reason, _choice = await HILPrompt(
        id=id, title=title, summary=summary,
        timeout_seconds=timeout_seconds, severity=severity)
    return approved, reason


async def HILChoiceRequest(id, title, summary, buttons: list[dict],
                           timeout_seconds: int = 120, severity: str = "warning") -> str:
    """多选入口：返回被点按钮的 ``value``（超时 → ``"timeout"``）。"""
    _approved, _reason, choice = await HILPrompt(
        id=id, title=title, summary=summary, buttons=buttons,
        timeout_seconds=timeout_seconds, severity=severity)
    return choice


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
