"""触发器优先级分流与 batched 队列测试。"""
import sys
from pathlib import Path

import csv
import json
import queue

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faust_backend.trigger_manager as tm


@pytest.fixture(autouse=True)
def _clean_queues():
    """每个用例前后清空两个队列，隔离状态。"""
    for q in (tm.trigger_queue, tm.batched_queue):
        with q.mutex:
            q.queue.clear()
    yield
    for q in (tm.trigger_queue, tm.batched_queue):
        with q.mutex:
            q.queue.clear()


def test_base_trigger_priority_default_normal():
    trig = tm.EventTrigger(id="t1", type="event", event_name="e")
    assert trig.priority == "normal"


def test_base_trigger_priority_rejects_invalid():
    with pytest.raises(Exception):
        tm.EventTrigger(id="t1", type="event", event_name="e", priority="urgent")


def test_emit_routes_batched_to_batched_queue():
    tm._emit_trigger({"id": "a::x", "type": "event", "event_name": "e", "priority": "batched"})
    assert tm.trigger_queue.empty()
    assert not tm.batched_queue.empty()
    item = tm.batched_queue.get_nowait()
    assert item["priority"] == "batched"


def test_emit_routes_interrupt_and_normal_to_urgent_queue():
    tm._emit_trigger({"id": "a::x", "type": "event", "event_name": "e", "priority": "interrupt"})
    tm._emit_trigger({"id": "a::x", "type": "event", "event_name": "e"})
    assert tm.batched_queue.empty()
    assert tm.trigger_queue.qsize() == 2


def test_event_csv_logged(tmp_path, monkeypatch):
    monkeypatch.setattr(tm.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(tm.conf, "AGENT_NAME", "faust")
    tm._emit_trigger({
        "id": "agileEngine::tick",
        "type": "event",
        "event_name": "tick",
        "recall_description": "每分钟滴答",
    })
    day = tm.datetime.datetime.now().strftime("%Y%m%d")
    path = tmp_path / "agents" / "faust" / "events" / f"{day}.csv"
    assert path.exists()
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert len(rows) == 1
    ts, source, ttype, priority, tid, summary, payload_json = rows[0]
    assert source == "agileEngine"
    assert ttype == "event"
    assert priority == "normal"
    assert tid == "agileEngine::tick"
    assert summary == "每分钟滴答"
    assert json.loads(payload_json)["event_name"] == "tick"


def test_event_csv_batched_marks_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(tm.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(tm.conf, "AGENT_NAME", "faust")
    tm._emit_trigger({"id": "x::y", "type": "event", "event_name": "e", "priority": "batched"})
    day = tm.datetime.datetime.now().strftime("%Y%m%d")
    path = tmp_path / "agents" / "faust" / "events" / f"{day}.csv"
    rows = list(csv.reader(path.open(encoding="utf-8")))
    assert rows[0][3] == "batched"


def test_drain_batched_takes_all_without_touching_urgent():
    tm._emit_trigger({"id": "a::1", "type": "event", "event_name": "e", "priority": "batched"})
    tm._emit_trigger({"id": "a::2", "type": "event", "event_name": "e", "priority": "batched"})
    tm._emit_trigger({"id": "b::1", "type": "event", "event_name": "e"})
    assert tm.has_batched()
    items = tm.drain_batched()
    assert len(items) == 2
    assert not tm.has_batched()
    assert tm.trigger_queue.qsize() == 1  # urgent 不受影响
    assert tm.drain_batched() == []


def test_drain_batched_empty_returns_empty_list():
    assert tm.drain_batched() == []


def test_batch_injection_text_format():
    items = [
        {"id": "a::1", "type": "event", "event_name": "danmaku", "recall_description": "弹幕: hi"},
        {"id": "a::2", "type": "event", "event_name": "devwatch"},
    ]
    text = tm.format_batch_injection(items, first_ts=1_000_000_000.0)
    assert text.startswith("<Trigger>")
    assert "2 条" in text
    assert "弹幕: hi" in text
    assert "devwatch" in text


def test_pyeval_context_contains_all_constants():
    ctx = tm._pyeval_context()
    for key in ("HOUR", "MINUTE", "SECOND", "WEEKDAY", "MONTH", "DAY",
                "EPOCH", "FREETIME_MIN", "USER_IDLE_SEC"):
        assert key in ctx
    now = tm.datetime.datetime.now()
    assert ctx["HOUR"] == now.hour
    assert ctx["WEEKDAY"] == now.weekday()  # 0=周一


def test_freetime_zero_after_interaction(monkeypatch):
    monkeypatch.setattr(tm, "last_interaction_ts", tm.time.time())
    ctx = tm._pyeval_context()
    assert ctx["FREETIME_MIN"] < 0.01
    assert ctx["USER_IDLE_SEC"] < 0.01


def test_note_user_interaction_updates_timestamp(monkeypatch):
    monkeypatch.setattr(tm.time, "time", lambda: 12345.0)
    tm.note_user_interaction()
    assert tm.last_interaction_ts == 12345.0


def test_pyeval_eval_uses_constants(monkeypatch):
    monkeypatch.setattr(tm, "last_interaction_ts", 0.0)
    import builtins
    code = "HOUR >= 0 and FREETIME_MIN > 0 and WEEKDAY >= 0 and EPOCH > 0"
    result = eval(code, {"__builtins__": builtins}, tm._pyeval_context())
    assert result is True


def test_snapshot_merges_both_queues():
    tm._emit_trigger({"id": "a::1", "type": "event", "event_name": "e", "priority": "batched"})
    tm._emit_trigger({"id": "b::1", "type": "event", "event_name": "e", "priority": "normal"})
    snap = tm.get_trigger_queue_snapshot()
    assert len(snap) == 2
    prios = {s.get("priority") for s in snap}
    assert "batched" in prios and "normal" in prios
    # 不消费：再取快照仍完整
    assert len(tm.get_trigger_queue_snapshot()) == 2
