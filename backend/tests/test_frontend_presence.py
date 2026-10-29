"""S0 离线禁触（D19）回归测试。

契约：前端（``/faust/command`` 连接）离线时，**一切触发在产生侧被拒绝**——
不入队、不写 fire 留痕、不推进 interval 的 ``last_triggered``；拒绝必须对调用者
可见（HTTP 409 / 插件异常 / 内部回调日志 + refused 计数）。
"""
from __future__ import annotations

import asyncio
import csv
import json
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faust_backend.core.config as conf
import faust_backend.api.public as public_api
import faust_backend.agent.trigger as tm
from faust_backend.agent.runtime.turn import presence as frontend_presence

# 只有少数几个用例需要真事件循环；其余保持同步（内部用 asyncio.run 跑协程），
# 因此不使用模块级 pytestmark。
asyncio_mark = pytest.mark.asyncio(loop_scope="module")


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "AGENT_NAME", "faust")
    frontend_presence.reset()
    tm._store = None
    for q in (tm.trigger_queue, tm.batched_queue):
        with q.mutex:
            q.queue.clear()
    yield
    frontend_presence.reset()
    tm._store = None
    for q in (tm.trigger_queue, tm.batched_queue):
        with q.mutex:
            q.queue.clear()


def _events_csv(tmp_path: Path) -> Path:
    day = tm.datetime.datetime.now().strftime("%Y%m%d")
    return tmp_path / "agents" / "faust" / "events" / f"{day}.csv"


# ── 产生侧拒绝：不入队、不写留痕、计数 ──


def test_emit_refused_when_offline_no_queue_no_csv(tmp_path):
    assert frontend_presence.is_online() is False
    ok = tm._emit_trigger({"id": "agile::x", "type": "event", "event_name": "e"})
    assert ok is False
    assert tm.trigger_queue.empty() and tm.batched_queue.empty()
    assert not _events_csv(tmp_path).exists(), "离线拒绝不得写 fire 留痕"
    snap = frontend_presence.snapshot()
    assert snap["refused_total"] == 1
    assert snap["refused_by_reason"] == {"no_frontend": 1}
    assert snap["refused_by_source"] == {"agile": 1}


def test_emit_online_behavior_unchanged(tmp_path):
    frontend_presence.mark_online("conn")
    assert tm._emit_trigger({"id": "agile::x", "type": "event", "event_name": "e"}) is True
    assert tm.trigger_queue.qsize() == 1
    assert _events_csv(tmp_path).exists()
    assert frontend_presence.snapshot()["refused_total"] == 0


def test_batched_refused_when_offline():
    tm._emit_trigger({"id": "a::1", "type": "event", "event_name": "e", "priority": "batched"})
    assert not tm.has_batched()
    assert tm.drain_batched() == []


def test_mark_offline_only_removes_that_connection():
    frontend_presence.mark_online("a")
    frontend_presence.mark_online("b")
    frontend_presence.mark_offline("a")
    assert frontend_presence.is_online() is True
    frontend_presence.mark_offline("b")
    assert frontend_presence.is_online() is False


# ── append 侧可见性：event 型立即拒绝并抛异常（插件 API 路径） ──


@asyncio_mark
async def test_append_event_trigger_raises_when_offline():
    with pytest.raises(tm.TriggerRefused) as excinfo:
        await tm.append_trigger({"id": "agile::x", "type": "event", "event_name": "e"})
    assert excinfo.value.reason == "no_frontend"
    assert tm.list_triggers() == [], "被拒绝的 event 触发器不得写进 store"


@asyncio_mark
async def test_append_scheduled_trigger_allowed_when_offline():
    """定时类触发器是"排期"不是"触发"：离线也允许先排期，fire 时再拒绝。"""
    await tm.append_trigger({"id": "t::interval", "type": "interval", "interval_seconds": 60})
    assert [t["id"] for t in tm.list_triggers()] == ["t::interval"]


@asyncio_mark
async def test_append_event_trigger_ok_when_online():
    frontend_presence.mark_online("conn")
    await tm.append_trigger({"id": "agile::x", "type": "event", "event_name": "e"})
    assert [t["id"] for t in tm.list_triggers()] == ["agile::x"]


# ── watchdog：离线整轮不 fire、不动 store；回连后 interval 至多补一次 ──


def test_watchdog_offline_does_not_fire_or_advance():
    store = tm.TriggerStore(
        watchdog=[tm.IntervalTrigger(id="i::1", type="interval", interval_seconds=1, last_triggered=0.0)]
    )
    tm._poll_watchdog_once(store)
    assert tm.trigger_queue.empty()
    assert store.watchdog[0].last_triggered == 0.0, "离线不得推进 last_triggered"

    frontend_presence.mark_online("conn")
    tm._poll_watchdog_once(store)
    assert tm.trigger_queue.qsize() == 1, "回连后到期触发器正常 fire 一次"
    assert store.watchdog[0].last_triggered > 0.0


def test_watchdog_offline_keeps_datetime_trigger(tmp_path):
    store = tm.TriggerStore(
        watchdog=[
            tm.DateTimeTrigger(
                id="d::1",
                type="datetime",
                target=tm.datetime.datetime.now() - tm.datetime.timedelta(seconds=1),
            )
        ]
    )
    tm._poll_watchdog_once(store)
    assert [t.id for t in store.watchdog] == ["d::1"], "离线不得丢弃未 fire 的触发器"
    assert not _events_csv(tmp_path).exists()


# ── D19-c：断开清空 inbox 并逐条记 refused ──


def test_drain_inbox_as_refused_records_each_item():
    frontend_presence.mark_online("conn")
    tm._emit_trigger({"id": "blive::1", "type": "event", "event_name": "blive_danmaku"})
    tm._emit_trigger({"id": "blive::2", "type": "event", "event_name": "blive_danmaku", "priority": "batched"})
    frontend_presence.mark_offline("conn")

    dropped = tm.drain_inbox_as_refused()
    assert dropped == 2
    assert tm.get_trigger_queue_snapshot() == []
    snap = frontend_presence.snapshot()
    assert snap["refused_by_reason"] == {"frontend_disconnected": 2}
    assert snap["refused_by_source"] == {"blive": 2}


def test_debugging_trigger_queue_reports_presence():
    from faust_backend.api.system import debugging

    frontend_presence.mark_offline("conn")
    tm._emit_trigger({"id": "x::1", "type": "event", "event_name": "e"})
    resp = asyncio.run(debugging.trigger_queue())
    assert resp["queue_size"] == 0
    assert resp["frontend_presence"]["online"] is False
    assert resp["frontend_presence"]["refused_total"] == 1


# ── 外部 HTTP：409 + reason ──


def test_public_api_event_push_returns_409_when_offline():
    req = public_api.EventTriggerPushRequest(event_name="ping")
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(public_api.event_trigger_push(req))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["reason"] == "no_frontend"


def test_public_api_generic_push_returns_409_when_offline():
    req = public_api.GenericTriggerPushRequest(trigger={"id": "t1", "type": "interval", "interval_seconds": 5})
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(public_api.generic_trigger_push(req))
    assert excinfo.value.status_code == 409
    assert excinfo.value.detail["reason"] == "no_frontend"


def test_public_api_succeeds_when_online():
    frontend_presence.mark_online("conn")
    req = public_api.GenericTriggerPushRequest(trigger={"id": "t1", "type": "interval", "interval_seconds": 5})
    resp = asyncio.run(public_api.generic_trigger_push(req))
    assert resp["status"] == "success"


def test_public_api_event_push_succeeds_when_online():
    frontend_presence.mark_online("conn")
    req = public_api.EventTriggerPushRequest(event_name="ping", payload={"a": 1})
    resp = asyncio.run(public_api.event_trigger_push(req))
    assert resp == {"status": "success", "type": "event", "event_name": "ping"}
    stored = tm.list_triggers()
    assert len(stored) == 1 and stored[0]["event_name"] == "ping"
    assert json.loads(json.dumps(stored[0]["payload"])) == {"a": 1}


# ── 连接断开：command_websocket 的在线信号与清空 ──


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


@asyncio_mark
async def test_command_websocket_marks_presence_and_drains_on_disconnect(monkeypatch):
    import faust_backend.core.frontend.api as backend2front
    import faust_backend.core.events as events
    from faust_backend.api.chat import chat as chat
    from faust_backend.agent.runtime import state

    # 模块级 Event 会绑定"首次使用它的事件循环"；跨 pytest 模块时那可能是已关闭的
    # 上一个循环。测试内换一个属于当前循环的 Event（产品代码全部走属性访问）。
    monkeypatch.setattr(events, "backend2frontendQueue_event", asyncio.Event())
    monkeypatch.setattr(backend2front, "FrontEndSay", lambda *_a, **_k: None)
    monkeypatch.setattr(chat.nimble, "push_persistent_sessions_to_frontend", lambda: None)
    monkeypatch.setattr(state, "RUNTIME_READY", True)
    monkeypatch.setattr(state, "agent", object())

    ws = _FakeWs()
    task = asyncio.create_task(chat.command_websocket(ws))
    for _ in range(100):
        if frontend_presence.is_online():
            break
        await asyncio.sleep(0.01)
    assert frontend_presence.is_online(), "command WS 接入后必须写入在线信号"

    tm._emit_trigger({"id": "blive::9", "type": "event", "event_name": "blive_danmaku"})
    assert tm.trigger_queue.qsize() == 1

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        pass

    assert frontend_presence.is_online() is False
    assert tm.trigger_queue.empty(), "断开时必须清空未消费的唤醒"
    assert frontend_presence.snapshot()["refused_by_source"] == {"blive": 1}
    events.backend2frontendQueue_event.clear()
    events.ignore_trigger_event.clear()


@asyncio_mark
async def test_command_websocket_detects_silent_disconnect(monkeypatch):
    """对端静默消失（进程被杀）也要被发现：只发不收的循环自己看不见断开。

    这是集成测试暴露的真实缺口：command WS 从不 receive，前端被 kill 后连接一直
    挂着，presence 停在"在线"，离线禁触失效、唤醒静默丢失。
    """
    import faust_backend.core.frontend.api as backend2front
    import faust_backend.core.events as events
    from faust_backend.api.chat import chat as chat
    from faust_backend.agent.runtime import state
    from starlette.websockets import WebSocketDisconnect

    class _DyingWs(_FakeWs):
        async def receive_text(self):
            await asyncio.sleep(0.2)
            raise WebSocketDisconnect(code=1006)

    monkeypatch.setattr(events, "backend2frontendQueue_event", asyncio.Event())
    monkeypatch.setattr(backend2front, "FrontEndSay", lambda *_a, **_k: None)
    monkeypatch.setattr(chat.nimble, "push_persistent_sessions_to_frontend", lambda: None)
    monkeypatch.setattr(state, "RUNTIME_READY", True)
    monkeypatch.setattr(state, "agent", object())

    ws = _DyingWs()
    task = asyncio.create_task(chat.command_websocket(ws))
    for _ in range(100):
        if frontend_presence.is_online():
            break
        await asyncio.sleep(0.01)
    assert frontend_presence.is_online() is True
    tm._emit_trigger({"id": "blive::7", "type": "event", "event_name": "blive_danmaku"})

    await asyncio.wait_for(task, timeout=5)
    assert frontend_presence.is_online() is False, "静默断开必须让 presence 掉线"
    assert tm.trigger_queue.empty()
    assert frontend_presence.snapshot()["refused_by_source"] == {"blive": 1}
    events.backend2frontendQueue_event.clear()
    events.ignore_trigger_event.clear()
