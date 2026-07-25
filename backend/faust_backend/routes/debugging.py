from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Body
from faust_backend.routes.subagents import subagent_status_overrides
from faust_backend.runtime import state

from faust_backend.logger import get_logger
from faust_backend.runtime import state
from faust_backend.tools.vfs import get_faustbot_vfs

log = get_logger("faust.debugging")

router = APIRouter(tags=["debugging"])
router.description = "调试接口：手动聊天、覆写 Subagent 状态"


def _main_event_payload(event_type: str, **kwargs) -> dict:
    payload = {"agent_id": "main", "type": event_type}
    payload.update(kwargs)
    return payload


def _subagents_summary_payload() -> dict:
    from faust_backend.routes.subagents import subagent_status_overrides

    if subagent_status_overrides:
        return {"agent_id": "subagents", "type": "subagents_summary", "items": list(subagent_status_overrides)}
    manager = state.subagent_manager
    if manager is None:
        return {"agent_id": "subagents", "type": "subagents_summary", "items": []}
    return {"agent_id": "subagents", "type": "subagents_summary", "items": manager.list_statuses_light()}


async def _collect_events(text: str) -> list[dict[str, Any]]:
    """Run one message through streaming and return all WS-like events."""
    if not state.RUNTIME_READY or state.agent is None:
        return [{"agent_id": "main", "type": "error", "error": state.runtime_not_ready_message()}]

    events: list[dict[str, Any]] = []
    abort_evt = state.reset_abort_event()

    try:
        events.append(_main_event_payload("start"))

        reply_parts: list[str] = []
        async for event in stream_chat_agent_events(
            state.agent,
            {"messages": [{"role": "user", "content": text}]},
            abort_event=abort_evt,
        ):
            if not isinstance(event, dict):
                continue
            event_type = str(event.get("type") or "")

            if event_type == "reasoning_delta":
                events.append(_main_event_payload("reasoning_delta",
                                                  content=event.get("content", "")))
            elif event_type == "delta":
                delta_text = state.message_content_to_text(event.get("content"))
                if delta_text:
                    reply_parts.append(delta_text)
                    events.append(_main_event_payload("delta", content=delta_text))
            elif event_type in {"tool_start", "tool_result"}:
                payload = dict(event)
                payload["agent_id"] = "main"
                events.append(payload)

            if state.subagent_manager and state.subagent_manager.consume_status_dirty():
                events.append(_subagents_summary_payload())

        reply = "".join(reply_parts)
        if state.subagent_manager and state.subagent_manager.consume_status_dirty():
            events.append(_subagents_summary_payload())
        events.append(_main_event_payload("done", reply=reply))

    except asyncio.CancelledError:
        events.append(_main_event_payload("interrupted"))

    return events


@router.post("/faust/debugging/manual-chat")
async def manual_chat(body: dict = Body(...)):
    """接收 JSON 并触发聊天 Agent 流，返回事件列表。

    Body:
        {"text": "创建一个名为debug_test的Subagent，工具组只用BASESET..."}
    """
    text = ""
    if isinstance(body, dict):
        text = body.get("text") or body.get("message") or ""
    text = str(text or "").strip()
    if not text:
        return {"status": "error", "error": "no text provided", "events": []}

    if not state.RUNTIME_READY or state.agent is None:
        return {"status": "error",
                "error": state.runtime_not_ready_message(),
                "runtime": state.runtime_status_payload(),
                "events": []}

    events = await _collect_events(text)
    return {"status": "ok", "text": text, "events": events, "event_count": len(events)}


@router.post("/faust/debugging/subagent-override")
async def subagent_override(body: dict = Body(...)):
    """覆写 Subagent 状态列表。JSON 应包含 items 数组。

    Body:
        {"items": [{"name": "debug_test", "status": "running", "agent_id": "subagent-debug_test",
                     "toolsets": ["BASESET"], "last_event_summary": "tool_start: read",
                     "recent_events": [{"type": "input", "content": "hello"}],
                     "system_prompt_summary": "debug agent", "last_error": ""}]}

    用空数组恢复：{"items": []}
    """
    items = []
    if isinstance(body, dict):
        items = body.get("items") or []

    if not isinstance(items, list):
        items = []

    subagent_status_overrides.clear()
    for item in items:
        if isinstance(item, dict):
            normalized = dict(item)
            name = str(normalized.get("name") or "unknown")
            if "agent_id" not in normalized:
                normalized["agent_id"] = f"subagent-{name}"
            subagent_status_overrides.append(normalized)

    log.info("Subagent overrides updated: %d items", len(subagent_status_overrides))
    return {"status": "ok", "overrides": list(subagent_status_overrides)}

@router.get("/faust/debugging/vfs-read/{path:path}")
async def vfs_read(path: str):
    """调试接口：读取虚拟文件系统内容"""
    if get_faustbot_vfs().is_dir(path):
        return await get_faustbot_vfs().list_dir(path)
    else:
        return await get_faustbot_vfs().read(path)