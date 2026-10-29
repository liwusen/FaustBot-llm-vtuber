"""Subagent 事件广播器的回归测试。

核心回归点：`rebuild_runtime()` 会整体替换 `SubagentManager`，而 `/faust/chat`
的 WS 转发任务只在连接建立时订阅一次 —— 事件通道必须与 manager 实例解耦，
否则重建之后前端 Subagent Panel 的实时事件会永久断流。
"""

import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import faust_backend.agent.subagent.manager as subagent_manager
from faust_backend.agent.subagent import events as subagent_events


class FakeAgent:
    def __init__(self, events=None):
        self.events = list(events or [])

    async def astream_events(self, payload, config=None, version=None):
        for event in self.events:
            yield event


def _chunk(text: str):
    return type(
        "Chunk",
        (),
        {"type": "ai", "content": text, "additional_kwargs": {}},
    )()


def _delta_events(*texts: str) -> list[dict]:
    return [
        {"event": "on_chat_model_stream", "data": {"chunk": _chunk(text)}}
        for text in texts
    ]


async def _run_subagent_once(manager, name: str, *texts: str) -> None:
    manager.subagents[name] = subagent_manager.Subagent(
        name=name, agent=FakeAgent(events=_delta_events(*texts))
    )
    async for _ in manager.ainvokeSubagent(name, {"messages": []}):
        pass


@pytest.mark.asyncio
async def test_publish_reaches_all_subscribers_and_unsubscribe_stops_delivery():
    q1 = subagent_events.subscribe()
    q2 = subagent_events.subscribe()
    try:
        subagent_events.publish(
            {"agent_id": "subagent-worker", "type": "delta", "content": "a"}
        )
        assert q1.qsize() == 1
        assert q2.qsize() == 1

        subagent_events.unsubscribe(q2)
        subagent_events.publish(
            {"agent_id": "subagent-worker", "type": "delta", "content": "b"}
        )
        assert q1.qsize() == 2
        assert q2.qsize() == 1
        assert q1.get_nowait()["content"] == "a"
        assert q1.get_nowait()["content"] == "b"
    finally:
        subagent_events.unsubscribe(q1)
        subagent_events.unsubscribe(q2)


@pytest.mark.asyncio
async def test_publish_rejects_main_agent_event():
    with pytest.raises(ValueError):
        subagent_events.publish({"agent_id": "main", "type": "delta", "content": "x"})


@pytest.mark.asyncio
async def test_subagent_manager_publishes_through_process_broadcaster(tmp_path):
    queue = subagent_events.subscribe()
    try:
        manager = subagent_manager.SubagentManager(
            checkpointerPath=str(tmp_path / "subagents.db")
        )
        await _run_subagent_once(manager, "worker", "hello")

        assert queue.qsize() == 1
        published = queue.get_nowait()
        assert published["agent_id"] == "subagent-worker"
        assert published["type"] == "delta"
        assert published["content"] == "hello"
    finally:
        subagent_events.unsubscribe(queue)


@pytest.mark.asyncio
async def test_subscription_survives_subagent_manager_rebuild(tmp_path):
    """rebuild_runtime() 换掉 manager 后，同一次订阅仍要收到新 manager 的事件。"""
    queue = subagent_events.subscribe()
    try:
        manager_v1 = subagent_manager.SubagentManager(
            checkpointerPath=str(tmp_path / "subagents-v1.db")
        )
        await _run_subagent_once(manager_v1, "worker", "before-rebuild")

        # lifecycle._rebuild_subagent_manager 的实际行为：全新实例 + 赋值 state
        manager_v2 = subagent_manager.SubagentManager(
            checkpointerPath=str(tmp_path / "subagents-v2.db")
        )
        await _run_subagent_once(manager_v2, "worker", "after-rebuild")

        assert queue.qsize() == 2
        first = queue.get_nowait()
        second = queue.get_nowait()
        assert (first["agent_id"], first["content"]) == ("subagent-worker", "before-rebuild")
        assert (second["agent_id"], second["content"]) == ("subagent-worker", "after-rebuild")
    finally:
        subagent_events.unsubscribe(queue)


@pytest.mark.asyncio
async def test_slow_subscriber_keeps_latest_event():
    queue = subagent_events.subscribe(maxsize=2)
    try:
        for index in range(5):
            subagent_events.publish(
                {"agent_id": "subagent-worker", "type": "delta", "content": str(index)}
            )
        assert queue.qsize() == 2
        assert [queue.get_nowait()["content"], queue.get_nowait()["content"]] == ["3", "4"]
    finally:
        subagent_events.unsubscribe(queue)


@pytest.mark.asyncio
async def test_chat_ws_forward_survives_manager_rebuild(tmp_path, monkeypatch):
    """端到端回归：真实 /faust/chat handler 在 manager 被重建后仍转发 Subagent 事件。"""
    import asyncio
    import json

    from starlette.websockets import WebSocketDisconnect

    from faust_backend.routes import chat as chat_route
    from faust_backend.agent.runtime import state as runtime_state

    class FakeWebSocket:
        def __init__(self):
            self.sent: list[str] = []
            self.inbox: asyncio.Queue = asyncio.Queue()
            self.accepted = False

        async def accept(self):
            self.accepted = True

        async def receive_text(self):
            item = await self.inbox.get()
            if isinstance(item, BaseException):
                raise item
            return item

        async def send_text(self, text):
            self.sent.append(text)

    manager_v1 = subagent_manager.SubagentManager(
        checkpointerPath=str(tmp_path / "ws-v1.db")
    )
    monkeypatch.setattr(runtime_state, "subagent_manager", manager_v1)

    websocket = FakeWebSocket()
    handler = asyncio.create_task(chat_route.chat_websocket(websocket))
    try:
        await asyncio.sleep(0.1)  # 等 handler 完成 subscribe 与转发任务创建
        assert websocket.accepted is True

        # 模拟 rebuild_runtime()：整体替换 manager（新实例、新队列）
        manager_v2 = subagent_manager.SubagentManager(
            checkpointerPath=str(tmp_path / "ws-v2.db")
        )
        monkeypatch.setattr(runtime_state, "subagent_manager", manager_v2)
        await _run_subagent_once(manager_v2, "worker", "after-rebuild")

        await asyncio.sleep(0.8)
        payloads = [json.loads(text) for text in websocket.sent]
        forwarded = [
            item
            for item in payloads
            if item.get("agent_id") == "subagent-worker"
        ]
        assert forwarded, payloads
        assert forwarded[0]["content"] == "after-rebuild"
    finally:
        websocket.inbox.put_nowait(WebSocketDisconnect())
        await asyncio.wait_for(handler, timeout=5)
