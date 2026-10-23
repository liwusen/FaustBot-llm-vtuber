"""command WS 消费循环 × 播放闸门：被 hold 的触发器不许被提前消费。

覆盖的是本次改动最容易出错的那段接线（chat.command_websocket 里的两个消费点），
用假 websocket + 打桩的前台推送跑真实循环，不碰 LLM / 音频 / 前端：

1. hold 期间：队列里的 normal 触发器不被消费
2. hold 期间：interrupt 级仍要放行（不能被队首的 normal 挡住）——即队头阻塞回归
3. ack 放行后：积压的 normal 触发器才被消费
"""
import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faust_backend.config_loader as conf
import faust_backend.events as events
import faust_backend.routes.chat as chat
import faust_backend.trigger_manager as tm
import faust_backend.turn_gate as tg
from faust_backend.runtime import frontend_presence
from faust_backend.runtime import state

pytestmark = pytest.mark.asyncio(loop_scope="module")


class _FakeWs:
    def __init__(self):
        self.sent: list[str] = []

    async def accept(self):
        return None

    async def send_text(self, raw: str):
        self.sent.append(raw)

    async def receive_text(self):
        """command WS 是只发不收的：读端一直挂着，只有断开才结束。"""
        await asyncio.sleep(3600)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))   # 事件 CSV 不写真实 agent 目录
    monkeypatch.setattr(conf, "AGENT_NAME", "faust")
    monkeypatch.setattr(conf, "TRIGGER_READ_FLOOR_SEC", 0.0)
    monkeypatch.setattr(conf, "TRIGGER_HOLD_MAX_SEC", 30.0)
    monkeypatch.setattr(chat.nimble, "push_persistent_sessions_to_frontend", lambda: None)
    monkeypatch.setattr(state, "RUNTIME_READY", True)
    monkeypatch.setattr(state, "agent", object())
    events.ignore_trigger_event.clear()
    tg.reset()
    frontend_presence.reset()
    frontend_presence.mark_online()  # D19：本文件测的是“在线”时的消费行为
    for q in (tm.trigger_queue, tm.batched_queue):
        with q.mutex:
            q.queue.clear()
    yield
    events.ignore_trigger_event.clear()
    tg.reset()
    frontend_presence.reset()
    for q in (tm.trigger_queue, tm.batched_queue):
        with q.mutex:
            q.queue.clear()


async def _run_loop(monkeypatch, calls: list[str]) -> asyncio.Task:
    async def _record(chat_ws, trigger_text: str) -> None:
        calls.append(trigger_text)

    monkeypatch.setattr(chat, "_run_trigger_stream_frontend", _record)
    ws = _FakeWs()
    # 连接登记是插入有序的 dict（前台触发器推给最新接入的连接）
    chat._active_chat_websockets[ws] = None
    task = asyncio.create_task(chat.command_websocket(ws))
    return task


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass
    chat._active_chat_websockets.clear()


async def test_normal_trigger_is_held_until_ack(monkeypatch, caplog):
    calls: list[str] = []
    task = await _run_loop(monkeypatch, calls)
    try:
        # text_len=0 → 无 ack 时一直压到 max_hold(30s)；ack 一到立即放行
        tg.arm("t1", text_len=0)
        tm._emit_trigger({"id": "n::1", "type": "event", "event_name": "e"})
        await asyncio.sleep(0.5)
        assert calls == [], "hold 期间不应消费 normal 触发器"

        tg.note_playback_done("t1")
        events.backend2frontendQueue_event.set()          # 模拟 chat WS 的 ack 唤醒
        await asyncio.sleep(0.5)
        assert len(calls) == 1, "ack 放行后积压的触发器必须被消费"
        assert "n::1" in calls[0]
    finally:
        await _stop(task)
    # 可观测性：放行时必须留下"被压了多久"的日志（供 D4 调参）
    assert any("触发器闸门等待" in r.message for r in caplog.records)


async def test_interrupt_trigger_bypasses_hold(monkeypatch):
    """队头阻塞回归：队首是 normal、后面是 interrupt 时，告警必须能单独放行。

    触发器已从 inbox 搬进调度器队列（S2-2）：被压住的 normal 现在表现为
    "调度器队列里挂着一个 trigger 档请求"，而不是"还留在 trigger_queue 里"。
    """
    calls: list[str] = []
    task = await _run_loop(monkeypatch, calls)
    try:
        tg.arm("t1", text_len=0)
        tm._emit_trigger({"id": "n::1", "type": "event", "event_name": "e"})
        tm._emit_trigger({"id": "i::1", "type": "event", "event_name": "e", "priority": "interrupt"})
        await asyncio.sleep(0.5)

        assert len(calls) == 1, "interrupt 级应当被放行"
        assert "i::1" in calls[0]
        queued = chat._scheduler().snapshot()["queue"]
        assert [q["kind"] for q in queued] == ["trigger"], "normal 触发器仍应等 ack"
    finally:
        await _stop(task)


async def test_no_hold_consumes_immediately(monkeypatch):
    calls: list[str] = []
    task = await _run_loop(monkeypatch, calls)
    try:
        tm._emit_trigger({"id": "n::1", "type": "event", "event_name": "e"})
        await asyncio.sleep(0.5)
        assert len(calls) == 1, "没有 hold 时应保持原有即时消费行为"
    finally:
        await _stop(task)
