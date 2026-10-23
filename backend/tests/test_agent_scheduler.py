"""S1 调度器核心单测（时钟注入、不 sleep、不碰 LLM）。

覆盖 §4/S1-6：档位出队顺序、抢占、gate 关闭时不出队、合并/降级、僵死判定、
单 worker 串行断言、``cancel_owner``（D21）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faust_backend.config_loader as conf
import faust_backend.events as events
import faust_backend.turn_gate as tg
from faust_backend.runtime import scheduler as sched
from faust_backend.runtime import state

pytestmark = pytest.mark.asyncio(loop_scope="module")


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


@pytest_asyncio.fixture
async def sched_env(monkeypatch):
    """隔离的调度器 + 闸门 + 事件标志；收尾 cancel worker。"""
    from faust_backend.runtime import frontend_presence

    monkeypatch.setattr(conf, "TRIGGER_READ_FLOOR_SEC", 0.0)
    monkeypatch.setattr(conf, "TRIGGER_HOLD_MAX_SEC", 0.2)
    frontend_presence.reset()
    frontend_presence.mark_online()
    events.ignore_trigger_event.clear()
    tg.reset()
    clock = FakeClock()
    scheduler = sched.AgentScheduler(clock=clock)
    yield scheduler, clock
    await scheduler.shutdown(timeout=1.0)
    events.ignore_trigger_event.clear()
    tg.reset()
    frontend_presence.reset()


def _recorder(log: list[str]):
    def _make(label: str, *, gate: str = "exempt", droppable: bool = False,
              merge_key: str | None = None, owner=None, kind=sched.TurnKind.TRIGGER,
              preemptible: bool = True, block: asyncio.Event | None = None):
        async def _run():
            log.append(f"{label}:start")
            if block is not None:
                await block.wait()
            log.append(f"{label}:end")
            return label

        return sched.AgentRequest(
            kind, _run, label=label, gate=gate, droppable=droppable,
            merge_key=merge_key, owner=owner, preemptible=preemptible,
        )

    return _make


async def _wait_status(handle, status: sched.TurnStatus, timeout: float = 2.0) -> None:
    for _ in range(int(timeout / 0.01)):
        if handle.status == status:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"等待 {status} 超时，当前={handle.status}")


async def test_priority_order_and_fifo(sched_env):
    scheduler, _clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    # 同一个同步块里提交：worker 没有机会插队，顺序完全由档位决定
    handles = [
        scheduler.submit(make("batch", kind=sched.TurnKind.BATCH)),
        scheduler.submit(make("trigger")),
        scheduler.submit(make("user", kind=sched.TurnKind.USER)),
        scheduler.submit(make("interrupt", kind=sched.TurnKind.INTERRUPT)),
        scheduler.submit(make("trigger2")),
    ]
    await asyncio.gather(*[h.wait() for h in handles])
    assert log == [
        "interrupt:start", "interrupt:end",
        "user:start", "user:end",
        "trigger:start", "trigger:end",
        "trigger2:start", "trigger2:end",
        "batch:start", "batch:end",
    ]


async def test_single_worker_never_overlaps(sched_env):
    scheduler, _clock = sched_env
    active = 0
    max_active = 0

    def _make(label: str, kind):
        async def _run():
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.01)
            active -= 1

        return sched.AgentRequest(kind, _run, label=label, gate="exempt")

    handles = [scheduler.submit(_make(f"t{i}", sched.TurnKind.TRIGGER)) for i in range(5)]
    await asyncio.gather(*[h.wait() for h in handles])
    assert max_active == 1, "单 worker 必须串行执行"


async def test_gate_hold_blocks_trigger_but_not_interrupt(sched_env):
    scheduler, _clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    tg.arm("turn-1", text_len=0)
    assert tg.should_hold("normal") is True

    trigger = scheduler.submit(make("trigger"))
    alarm = scheduler.submit(make("alarm", kind=sched.TurnKind.INTERRUPT))
    await alarm.wait()
    assert log == ["alarm:start", "alarm:end"]
    assert trigger.status == sched.TurnStatus.PENDING, "闸门 hold 期间不得放行 trigger"

    tg.note_playback_done("turn-1")
    scheduler.wake()
    await trigger.wait()
    assert "trigger:start" in log


async def test_ignore_trigger_event_blocks_trigger_not_user(sched_env):
    scheduler, _clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    events.ignore_trigger_event.set()

    trigger = scheduler.submit(make("trigger"))
    user = scheduler.submit(make("user", kind=sched.TurnKind.USER))
    await user.wait()
    assert log == ["user:start", "user:end"]
    assert trigger.status == sched.TurnStatus.PENDING

    events.ignore_trigger_event.clear()
    scheduler.wake()
    await trigger.wait()
    assert trigger.status == sched.TurnStatus.DONE


async def test_merge_same_key_covers_pending(sched_env):
    scheduler, _clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    block = asyncio.Event()
    runner = scheduler.submit(make("running", block=block))
    await _wait_status(runner, sched.TurnStatus.RUNNING)

    first = scheduler.submit(make("old", merge_key="same-id"))
    second = scheduler.submit(make("new", merge_key="same-id"))
    await _wait_status(first, sched.TurnStatus.MERGED)

    block.set()
    await runner.wait()
    await second.wait()
    assert "new:start" in log and "old:start" not in log
    assert second.status == sched.TurnStatus.DONE


async def test_trigger_overflow_degrades_oldest_to_batch(sched_env, monkeypatch):
    scheduler, _clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    block = asyncio.Event()
    running = scheduler.submit(make("running", block=block))
    await _wait_status(running, sched.TurnStatus.RUNNING)

    monkeypatch.setattr(sched, "TRIGGER_QUEUE_MAX", 3)
    handles = [scheduler.submit(make(f"t{i}")) for i in range(5)]
    assert handles[0].degraded is True and handles[1].degraded is True
    assert handles[0].request.kind == sched.TurnKind.BATCH
    assert handles[4].status == sched.TurnStatus.PENDING, "新来的那条永远能进队"

    block.set()
    await running.wait()
    await asyncio.gather(*[h.wait() for h in handles])
    # 降级的两条排在真正的 batch 档，最后执行
    assert log.index("t0:start") > log.index("t4:end")


async def test_droppable_shed_when_congested(sched_env, monkeypatch):
    scheduler, _clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    block = asyncio.Event()
    running = scheduler.submit(make("running", block=block))
    await _wait_status(running, sched.TurnStatus.RUNNING)
    monkeypatch.setattr(sched, "TRIGGER_QUEUE_MAX", 1)
    a = scheduler.submit(make("a"))
    dropped = scheduler.submit(make("proactive", droppable=True))
    assert dropped.status == sched.TurnStatus.DROPPED
    block.set()
    await running.wait()
    await a.wait()


async def test_stall_detection_cancels_stuck_turn(sched_env):
    scheduler, clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    stuck = asyncio.Event()
    stuck_turn = scheduler.submit(make("stuck", kind=sched.TurnKind.USER, block=stuck))
    await _wait_status(stuck_turn, sched.TurnStatus.RUNNING)

    # 推进假时钟超过阈值，再提交一条请求（"有人要排队"才检查）
    clock.advance(sched.STALL_IDLE_SEC + 1.0)
    fresh = scheduler.submit(make("fresh"))
    await fresh.wait()
    assert stuck_turn.status == sched.TurnStatus.ERROR
    assert stuck_turn.cancel_reason == "stalled"
    assert isinstance(stuck_turn.error, sched.TurnCancelled)
    assert "fresh:start" in log


async def test_stall_not_triggered_below_threshold(sched_env):
    scheduler, clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    blocking = asyncio.Event()
    running = scheduler.submit(make("running", block=blocking))
    await _wait_status(running, sched.TurnStatus.RUNNING)

    # 有产出心跳（touch）→ 即使过了 STALL_IDLE_SEC 也不算僵死
    for _ in range(5):
        clock.advance(sched.STALL_IDLE_SEC / 2)
        running.touch()
    queued = scheduler.submit(make("queued"))
    await asyncio.sleep(0.05)
    assert running.status == sched.TurnStatus.RUNNING
    assert queued.status == sched.TurnStatus.PENDING
    blocking.set()
    await running.wait()
    await queued.wait()


async def test_cancel_owner_withdraws_pending_and_cancels_running(sched_env):
    scheduler, _clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    owner = object()
    block = asyncio.Event()
    running = scheduler.submit(make("running", owner=owner, kind=sched.TurnKind.USER, block=block))
    await _wait_status(running, sched.TurnStatus.RUNNING)
    queued = scheduler.submit(make("queued", owner=owner, kind=sched.TurnKind.USER))
    other = scheduler.submit(make("other", owner=object()))

    cancelled = await scheduler.cancel_owner(owner)
    assert cancelled == 2
    await _wait_status(running, sched.TurnStatus.INTERRUPTED)
    assert queued.status == sched.TurnStatus.INTERRUPTED
    assert other.status != sched.TurnStatus.INTERRUPTED, "别的连接的请求不受影响"
    block.set()
    await other.wait()


async def test_cancel_running_kinds_marks_forced(sched_env):
    scheduler, _clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    block = asyncio.Event()
    trigger = scheduler.submit(make("trigger", block=block))
    await _wait_status(trigger, sched.TurnStatus.RUNNING)

    assert await scheduler.cancel_running_kinds({sched.TurnKind.TRIGGER}) is True
    assert trigger.forced is True
    assert trigger.status == sched.TurnStatus.INTERRUPTED
    # 用户回合不在抢占范围内
    assert await scheduler.cancel_running_kinds({sched.TurnKind.TRIGGER}) is False


async def test_cancel_merge_key_only_touches_pending(sched_env):
    scheduler, _clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    block = asyncio.Event()
    running = scheduler.submit(make("running", block=block))
    await _wait_status(running, sched.TurnStatus.RUNNING)
    queued = scheduler.submit(make("queued", merge_key="trig-1"))
    assert scheduler.cancel_merge_key("trig-1") == 1
    assert queued.status == sched.TurnStatus.INTERRUPTED
    assert scheduler.cancel_merge_key("trig-1") == 0
    block.set()
    await running.wait()


async def test_snapshot_exposes_queue_and_constants(sched_env):
    scheduler, clock = sched_env
    log: list[str] = []
    make = _recorder(log)
    block = asyncio.Event()
    running = scheduler.submit(make("running", block=block))
    await _wait_status(running, sched.TurnStatus.RUNNING)
    queued = scheduler.submit(make("queued"))
    clock.advance(3.0)

    snap = scheduler.snapshot()
    assert snap["executing"]["kind"] == "running"
    assert snap["queue"] == [{"kind": "queued", "waited_sec": 3.0, "degraded": False}]
    assert snap["constants"]["TRIGGER_QUEUE_MAX"] == sched.TRIGGER_QUEUE_MAX
    assert snap["worker_running"] is True

    block.set()
    await running.wait()
    await queued.wait()
    assert scheduler.is_idle() is True
    assert scheduler.snapshot()["executing"] is None


async def test_error_turn_does_not_wedge_queue(sched_env):
    scheduler, _clock = sched_env
    log: list[str] = []

    async def _boom():
        log.append("boom")
        raise RuntimeError("炸了")

    bad = scheduler.submit(sched.AgentRequest(sched.TurnKind.TRIGGER, _boom, label="bad"))
    good = scheduler.submit(_recorder(log)("good"))
    await good.wait()
    assert bad.status == sched.TurnStatus.ERROR
    assert isinstance(bad.error, RuntimeError)
    with pytest.raises(RuntimeError):
        await bad.wait()
    assert "good:start" in log


async def test_worker_holds_agent_lock_and_releases_after_turn(sched_env):
    """迁移期保险丝（D20）：worker 执行回合期间持有主 Agent 锁。"""
    scheduler, _clock = sched_env
    seen: list[bool] = []

    async def _run():
        seen.append(state.agent_lock.locked())
        return None

    handle = scheduler.submit(sched.AgentRequest(sched.TurnKind.TRIGGER, _run, label="locked"))
    await handle.wait()
    assert seen == [True]
    assert state.agent_lock.locked() is False
