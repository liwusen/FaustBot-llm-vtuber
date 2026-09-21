"""Nimble 窗口关闭链路的回归测试。

覆盖两个已修复的缺陷：
1. `closeNimbleWindowTool` 漏了 await `finalize_close`（async）→ 工具回报"已关闭"，
   但会话/trigger/VFS/前端窗口全都没动；
2. AI 主动关闭窗口（console 保留命令 `close-window`）后，关闭路由又 append 一条
   `nimble_closed::*` trigger 把 AI 自己唤醒。
"""

from __future__ import annotations

import types

import pytest

import faust_backend.backend2front as backend2frontend
import faust_backend.nimble as nimble
import faust_backend.trigger_manager as trigger_manager
from faust_backend.routes.hil_nimble import nimble_close_post
from faust_backend.tools.nimble import closeNimbleWindowTool


@pytest.fixture
def closes(monkeypatch):
    """隔离关闭链路的对外副作用，记录每次调用。

    不落盘（非持久化会话）、不推真实前端队列、不写真实 trigger 存储。
    """
    recorded = types.SimpleNamespace(
        pushed=[],
        appended=[],
        deleted_triggers=[],
        vfs_unregistered=[],
    )
    monkeypatch.setattr(nimble, "_nimble_sessions", {})

    async def _unregister(callback_id):
        recorded.vfs_unregistered.append(callback_id)

    monkeypatch.setattr(nimble, "unregister_session_vfs_nodes", _unregister)
    monkeypatch.setattr(
        backend2frontend,
        "FrontEndCloseNimbleWindow",
        lambda payload: recorded.pushed.append(payload),
    )
    monkeypatch.setattr(
        trigger_manager, "append_trigger", lambda trigger: recorded.appended.append(trigger)
    )
    monkeypatch.setattr(
        trigger_manager,
        "delete_trigger",
        lambda trigger_id: recorded.deleted_triggers.append(trigger_id),
    )
    return recorded


def _open_window(callback_id: str = "nimble_unit") -> str:
    nimble.create_nimble_session(
        callback_id,
        title="单元测试窗口",
        html="<div>hi</div>",
        recall_text="测试用途",
        lifespan=60,
    )
    return callback_id


@pytest.mark.asyncio
async def test_close_tool_actually_closes_window(closes):
    """工具关闭必须真正生效：会话消失、trigger 清掉、前端收到 NIMBLE_CLOSE。"""
    callback_id = _open_window()

    result = await closeNimbleWindowTool.ainvoke({"callback_id": callback_id})

    assert "已关闭" in result
    assert nimble.get_nimble_session(callback_id) is None
    assert closes.pushed == [{"callback_id": callback_id, "reason": "closed_by_agent"}]
    assert closes.vfs_unregistered == [callback_id]
    assert set(closes.deleted_triggers) == {
        f"nimble_expire::{callback_id}",
        nimble.message_trigger_id(callback_id),
    }
    # AI 自己关的窗口不该再唤醒自己
    assert closes.appended == []


@pytest.mark.asyncio
async def test_close_tool_reports_unknown_callback_id(closes):
    result = await closeNimbleWindowTool.ainvoke({"callback_id": "nimble_missing"})

    assert "未找到" in result
    assert closes.pushed == []


@pytest.mark.asyncio
async def test_close_route_silent_for_agent_initiated_close(closes):
    """前端因 AI 的 close-window 命令上报关闭（by_agent=true）时不产生 trigger。"""
    callback_id = _open_window()

    resp = await nimble_close_post(
        {"callback_id": callback_id, "reason": "closed_by_agent", "by_agent": True}
    )

    assert resp == {"status": "closed", "callback_id": callback_id}
    assert nimble.get_nimble_session(callback_id) is None
    assert closes.appended == []


@pytest.mark.asyncio
async def test_close_route_still_wakes_agent_for_user_close(closes):
    """用户点 × 关闭窗口：仍要 append trigger 告知 AI。"""
    callback_id = _open_window()

    resp = await nimble_close_post({"callback_id": callback_id, "reason": "closed_by_user"})

    assert resp == {"status": "closed", "callback_id": callback_id}
    assert len(closes.appended) == 1
    trigger = closes.appended[0]
    assert trigger["id"] == f"nimble_closed::{callback_id}"
    assert trigger["event_name"] == "nimble_message"
    assert trigger["payload"]["payload"]["reason"] == "closed_by_user"
