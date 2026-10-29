"""主 Agent 调度器：唯一仲裁执行权的组件（S1）。

设计要点（见 ``docs/superpowers/plans/2026-10-17-agent-scheduler-and-trigger.md``）：

* **4 档 + 4 个正交标志**：档位只有 ``interrupt > user|exclusive > trigger > batch``；
  「静默 / 可丢 / 豁免播放闸门 / 可抢占」全部是标志，不再是档位（§1.1）。
* **单 worker 单执行位**：worker 懒启动、常驻（首个 ``submit()`` 时创建，import 期
  不产生任何副作用），执行每个 turn 前 ``acquire_agent_lock()``，因此与外部
  ``TimedAgentLock`` 使用者（admin 路由、rebuild）互斥（D20）。
* **准入**：播放闸门（``turn_gate``）、``ignore_trigger_event``、
  惰性僵死判定（D7：只在有人排队时检查执行位多久没产出事件）。
* **合并/降级**：同 ``merge_key`` 覆盖；trigger 队列超 ``TRIGGER_QUEUE_MAX`` 时把
  最旧的 trigger 降级进 batch（D6/D10）。不丢唤醒。
* 时间可注入（``clock``）：单测不 sleep、不依赖真实时钟。

锁的语义（迁移期保险丝）
------------------------
worker 持锁期间，**本回合临界区内的所有子孙任务**看到的都是"锁已持有"：
``acquire_agent_lock`` 在 ``in_scheduled_turn()`` 为真时直接返回，
``release_agent_lock`` 只释放一次（``TurnHandle.lock_held``）。
这样 ``stream_chat_agent_events`` 的生产者不会二次获取（asyncio.Lock 不可重入），
而它结束时的释放仍然成立——与修复前"锁只覆盖 LLM 会话本身、不覆盖 websocket
发送"的语义完全一致。
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections import deque
from contextvars import ContextVar
from enum import Enum, IntEnum
from typing import Any, Awaitable, Callable

from faust_backend.agent.runtime.invoke import stream_silent_reply
from faust_backend.agent.runtime.lock import acquire_agent_lock, release_agent_lock
import faust_backend.core.events as events
import faust_backend.agent.runtime.turn.gate as turn_gate
from faust_backend.core.log import get_logger

log = get_logger("faust.scheduler")

# ── 常量（D6/D7，不可配置：本机源码运行，调参改代码即可） ──
TRIGGER_QUEUE_MAX = 50
STALL_IDLE_SEC = 300.0
BATCH_WINDOW_SEC = 30.0
TASK_CANCEL_TIMEOUT_SEC = 3.0


class TurnKind(IntEnum):
    """4 档：数字越小越优先；档内 FIFO。"""

    INTERRUPT = 0
    USER = 1  # exclusive 与 user 同档（用 preemptible=False 区分）
    TRIGGER = 2
    BATCH = 3


class TurnStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    INTERRUPTED = "interrupted"
    ERROR = "error"
    MERGED = "merged"
    DROPPED = "dropped"


_TERMINAL = {
    TurnStatus.DONE,
    TurnStatus.INTERRUPTED,
    TurnStatus.ERROR,
    TurnStatus.MERGED,
    TurnStatus.DROPPED,
}


class TurnCancelled(RuntimeError):
    """调度器收尾/撤单导致的终态。"""


class LockBusyError(RuntimeError):
    """回合开始前拿不到主 Agent 锁（外部 ``TimedAgentLock`` 使用者正占着）。

    与"回合执行中失败"区分开：调用者据此把"主 Agent 正忙"渲染成人话，
    而不是一个笼统的 error 事件。
    """


_current_turn: ContextVar["TurnHandle | None"] = ContextVar(
    "faust_scheduler_current_turn", default=None
)


def _now() -> float:
    return time.monotonic()


# ── 请求 / 句柄 ──


class AgentRequest:
    """一次提交给调度器的执行请求。

    ``run`` 在 worker **已持有主 Agent 锁**的前提下执行，返回值作为回合结果。
    ``sink`` / ``droppable`` / ``gate`` / ``preemptible`` 是 §1.1 的四个正交标志。
    ``on_terminal`` 在**恰好一次**终态时被调用（包括尚未开始就被撤单的情况），
    供入口清理由提交方设置的全局状态（例如 ``events.ignore_trigger_event``）。
    """

    __slots__ = (
        "kind",
        "run",
        "label",
        "sink",
        "droppable",
        "gate",
        "preemptible",
        "merge_key",
        "owner",
        "on_terminal",
    )

    def __init__(
        self,
        kind: TurnKind,
        run: Callable[[], Awaitable[Any]],
        *,
        label: str = "",
        sink: str = "silent",
        droppable: bool = False,
        gate: str = "playback",
        preemptible: bool = True,
        merge_key: str | None = None,
        owner: Any = None,
        on_terminal: Callable[["TurnHandle"], None] | None = None,
    ) -> None:
        self.kind = kind
        self.run = run
        self.label = str(label or kind.name.lower())
        self.sink = str(sink)
        self.droppable = bool(droppable)
        self.gate = str(gate)
        self.preemptible = bool(preemptible)
        self.merge_key = merge_key
        self.owner = owner
        self.on_terminal = on_terminal

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<AgentRequest {self.label} kind={self.kind.name}>"


class TurnHandle:
    """一个回合的句柄：终态恰好一次，等待者必被唤醒。"""

    __slots__ = (
        "request",
        "seq",
        "status",
        "result",
        "error",
        "created_ts",
        "started_ts",
        "finished_ts",
        "last_progress_ts",
        "task",
        "lock_held",
        "forced",
        "degraded",
        "cancel_reason",
        "gate_wait_since",
        "_clock",
        "_done",
        "_terminal_hint",
        "_cancel_requested",
    )

    def __init__(self, request: AgentRequest, seq: int, clock: Callable[[], float]) -> None:
        self.request = request
        self.seq = seq
        self.status = TurnStatus.PENDING
        self.result: Any = None
        self.error: BaseException | None = None
        self.created_ts = clock()
        self.started_ts: float | None = None
        self.finished_ts: float | None = None
        self.last_progress_ts = self.created_ts
        self.task: asyncio.Task | None = None
        self.lock_held = False
        self.forced = False
        self.degraded = False
        self.cancel_reason = ""
        self.gate_wait_since: float | None = None
        self._clock = clock
        self._done = asyncio.Event()
        self._terminal_hint: TurnStatus | None = None
        self._cancel_requested = False

    # ── 状态 ──

    @property
    def label(self) -> str:
        return self.request.label

    def is_terminal(self) -> bool:
        return self.status in _TERMINAL

    def touch(self) -> None:
        """执行位"有产出"的心跳（供惰性僵死判定）。"""
        self.last_progress_ts = self._clock()

    def mark_running(self) -> None:
        if self.status == TurnStatus.PENDING:
            self.status = TurnStatus.RUNNING
            self.started_ts = self._clock()
            self.touch()

    def finish(self, status: TurnStatus, *, result: Any = None, error: BaseException | None = None) -> bool:
        """终结本回合（幂等）。返回是否是本次调用完成的终结。"""
        if self.is_terminal():
            return False
        if self._terminal_hint is not None and status not in (
            TurnStatus.MERGED,
            TurnStatus.DROPPED,
            TurnStatus.ERROR,
        ):
            status = self._terminal_hint
        self.status = status
        self.result = result
        self.error = error
        self.finished_ts = self._clock()
        self._done.set()
        hook = self.request.on_terminal
        if hook is not None:
            try:
                hook(self)
            except Exception as exc:  # noqa: BLE001 - 清理钩子不得影响终态
                log.error("回合终态钩子失败（%s）: %s", self.request.label, exc)
        return True

    def cancel(self, reason: str = "cancelled") -> bool:
        """撤单（未开始）或取消正在跑的回合（在跑）；返回是否生效。

        "正在跑但任务还没建好"（worker 还在等主 Agent 锁）这一段也覆盖：
        ``_cancel_requested`` 会记住意图，``_run_handle`` 建好任务后立刻取消。
        """
        if self.is_terminal():
            return False
        self.cancel_reason = str(reason or "cancelled")
        self._cancel_requested = True
        if self.status == TurnStatus.PENDING:
            self._terminal_hint = None
            return self.finish(TurnStatus.INTERRUPTED, error=TurnCancelled(self.cancel_reason))
        self._terminal_hint = (
            TurnStatus.ERROR if self.cancel_reason == "stalled" else TurnStatus.INTERRUPTED
        )
        task = self.task
        if task is not None and not task.done():
            task.cancel()
        return True

    async def wait(self) -> Any:
        await self._done.wait()
        if self.error is not None:
            raise self.error
        return self.result

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<TurnHandle {self.label} {self.status.value} seq={self.seq}>"


# ── 调度器 ──


class AgentScheduler:
    """单 worker、优先级队列、准入仲裁。所有方法都在事件循环内调用。"""

    def __init__(self, clock: Callable[[], float] = _now) -> None:
        self._clock = clock
        self._pending: dict[TurnKind, deque[TurnHandle]] = {kind: deque() for kind in TurnKind}
        self._handles: set[TurnHandle] = set()
        self._executing: TurnHandle | None = None
        self._worker: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None
        self._seq = 0
        self._closing = False

    # ── 提交 ──

    def submit(self, request: AgentRequest) -> TurnHandle:
        """提交请求并返回句柄。同步方法（只做入队与唤醒，不等待准入）。

        惰性僵死判定挂在这里（D7）："有人要排队"这件事只会在 ``submit()`` 时发生，
        而 worker 此时正卡在僵死的执行位上，没有机会自己检查。
        """
        self._check_stall(waiting=True)
        self._seq += 1
        handle = TurnHandle(request, self._seq, self._clock)

        merged = self._merge_into_pending(request, handle)
        if merged:
            return handle

        if request.droppable and self.pending_count() >= TRIGGER_QUEUE_MAX:
            # D2：`droppable` 是拥塞时唯一允许硬丢的请求；事件本身已留痕。
            log.warning("调度器拥塞，丢弃可丢请求（%s）", request.label)
            handle.finish(TurnStatus.DROPPED)
            return handle

        self._pending[request.kind].append(handle)
        self._handles.add(handle)
        self._degrade_overflow()
        self._ensure_worker()
        self.wake()
        return handle

    async def run_exclusive(
        self,
        factory: Callable[[], Awaitable[Any]],
        *,
        label: str = "exclusive",
        owner: Any = None,
    ) -> Any:
        """``submit(kind=user, preemptible=False)`` + ``await handle``（S2-5）。"""
        handle = self.submit(
            AgentRequest(
                TurnKind.USER,
                factory,
                label=label,
                sink="silent",
                gate="exempt",
                preemptible=False,
                owner=owner,
            )
        )
        return await handle.wait()

    # ── 撤单 ──

    async def cancel_owner(self, owner: Any, reason: str = "owner_gone") -> int:
        """撤掉该 owner 队列里未开始的请求 + 停掉它在跑的 turn（D21）。

        有界等待在跑的那个回合进入终态：连接断开时"停跑"必须真的停下来
        （否则后端会在无人的连接上继续执行用户消息）。
        """
        if owner is None:
            return 0
        cancelled = 0
        running: TurnHandle | None = None
        for handle in list(self._handles):
            if handle.request.owner is not owner or handle.is_terminal():
                continue
            if handle.status != TurnStatus.PENDING:
                running = handle
            if self._cancel_handle(handle, reason):
                cancelled += 1
        if running is not None:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(running._done.wait(), timeout=TASK_CANCEL_TIMEOUT_SEC)
        return cancelled

    def cancel_merge_key(self, merge_key: str, reason: str = "trigger_deleted") -> int:
        """删除触发器时清掉调度器队列里同 id 的未开始请求（D10）。

        刻意不动正在跑的回合：D19-b 规定断线/删除都不主动停止已开跑的触发器 turn。
        """
        if not merge_key:
            return 0
        cancelled = 0
        for handle in list(self._handles):
            if handle.request.merge_key != merge_key or handle.is_terminal():
                continue
            if handle.status != TurnStatus.PENDING:
                continue
            if self._cancel_handle(handle, reason):
                cancelled += 1
        return cancelled

    async def cancel_running_kinds(self, kinds: set[TurnKind], reason: str = "preempted") -> bool:
        """抢占正在跑的指定档位回合（用户插话），有界等待其进入终态。"""
        handle = self._executing
        if handle is None or handle.is_terminal() or handle.request.kind not in kinds:
            return False
        handle.forced = True
        self._cancel_handle(handle, reason)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(handle._done.wait(), timeout=TASK_CANCEL_TIMEOUT_SEC)
        return True

    def has_running_kinds(self, kinds: set[TurnKind]) -> bool:
        handle = self._executing
        return (
            handle is not None
            and not handle.is_terminal()
            and handle.request.kind in kinds
        )

    def _cancel_handle(self, handle: TurnHandle, reason: str) -> bool:
        if handle.status == TurnStatus.PENDING:
            self._remove_pending(handle)
        return handle.cancel(reason)

    # ── 查询 ──

    def pending_count(self) -> int:
        return sum(
            1
            for kind in TurnKind
            for handle in self._pending[kind]
            if handle.status == TurnStatus.PENDING
        )

    def is_idle(self) -> bool:
        return self._executing is None and self.pending_count() == 0

    def snapshot(self) -> dict[str, Any]:
        now = self._clock()
        executing = None
        handle = self._executing
        if handle is not None and not handle.is_terminal():
            executing = {
                "kind": handle.label,
                "since": handle.started_ts,
                "elapsed": round(now - (handle.started_ts or now), 3),
            }
        queue: list[dict[str, Any]] = []
        for kind in TurnKind:
            for item in self._pending[kind]:
                if item.status != TurnStatus.PENDING:
                    continue
                queue.append(
                    {
                        "kind": item.label,
                        "waited_sec": round(now - item.created_ts, 3),
                        "degraded": item.degraded,
                    }
                )
        return {
            "executing": executing,
            "queue": queue,
            "pending_total": len(queue),
            "worker_running": self._worker is not None and not self._worker.done(),
            "constants": {
                "TRIGGER_QUEUE_MAX": TRIGGER_QUEUE_MAX,
                "STALL_IDLE_SEC": STALL_IDLE_SEC,
                "BATCH_WINDOW_SEC": BATCH_WINDOW_SEC,
                "TASK_CANCEL_TIMEOUT_SEC": TASK_CANCEL_TIMEOUT_SEC,
            },
        }

    # ── worker ──

    def _ensure_worker(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._closing = False
        self._worker = asyncio.get_running_loop().create_task(
            self._worker_main(), name="agent_scheduler_worker"
        )

    def wake(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def _wake_event(self) -> asyncio.Event:
        if self._wake is None:
            self._wake = asyncio.Event()
        return self._wake

    async def _worker_main(self) -> None:
        log.info("主 Agent 调度器 worker 已启动")
        try:
            while not self._closing:
                handle = await self._next_handle()
                if handle is None:
                    continue
                await self._run_handle(handle)
        except asyncio.CancelledError:
            raise
        finally:
            log.info("主 Agent 调度器 worker 退出")

    async def _next_handle(self) -> TurnHandle | None:
        """等待下一个可准入的请求（含闸门/忽略标志/惰性僵死判定）。"""
        while not self._closing:
            handle = self._take_admissible()
            if handle is not None:
                return handle
            wake = self._wake_event()
            wake.clear()  # 先清后查：submit() 在两者之间不会丢唤醒
            handle = self._take_admissible()
            if handle is not None:
                return handle
            if self._closing:
                return None
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(wake.wait(), timeout=self._wait_timeout())
        return None

    def _wait_timeout(self) -> float:
        """没有可放行的请求时的等待上限：闸门剩余时间是上限的一部分。"""
        remaining = float(turn_gate.hold_remaining() or 0.0)
        if remaining > 0:
            return max(0.02, min(0.5, remaining))
        return 0.5

    def _take_admissible(self) -> TurnHandle | None:
        for kind in TurnKind:
            queue = self._pending[kind]
            while queue and queue[0].status != TurnStatus.PENDING:
                queue.popleft()
            if not queue:
                continue
            head = queue[0]
            if not self._admissible(head):
                # 队首不可准入：不越过它（档内 FIFO + 档间优先级）。
                # 闸门只作用于 trigger/batch（最低两档），因此不会挡住 interrupt/user。
                return None
            queue.popleft()
            if head.gate_wait_since is not None:
                # 可观测性：放行时留下"被播放闸门压了多久"的日志（调参依据）
                log.info(
                    "触发器闸门等待 %.2fs 后放行（%s）",
                    self._clock() - head.gate_wait_since,
                    head.label,
                )
                head.gate_wait_since = None
            return head
        return None

    def _admissible(self, handle: TurnHandle) -> bool:
        request = handle.request
        if request.kind in (TurnKind.TRIGGER, TurnKind.BATCH) and events.ignore_trigger_event.is_set():
            return False
        if request.gate != "exempt" and turn_gate.should_hold("normal"):
            if handle.gate_wait_since is None:
                handle.gate_wait_since = self._clock()
            return False
        return True

    def _check_stall(self, *, waiting: bool | None = None) -> None:
        """惰性僵死判定（D7）：有人要排队时，检查执行位多久没产出事件。"""
        handle = self._executing
        if handle is None or handle.is_terminal():
            return
        if not (self.pending_count() > 0 if waiting is None else waiting):
            return
        idle = self._clock() - handle.last_progress_ts
        if idle <= STALL_IDLE_SEC:
            return
        log.error(
            "执行位 %.0fs 无产出（>%.0fs，label=%s），判定僵死：取消并放行",
            idle,
            STALL_IDLE_SEC,
            handle.label,
        )
        self._cancel_handle(handle, "stalled")

    async def _run_handle(self, handle: TurnHandle) -> None:

        request = handle.request
        handle.mark_running()
        self._executing = handle
        try:
            await acquire_agent_lock(f"turn:{request.label}")
            handle.lock_held = True
        except Exception as exc:  # noqa: BLE001 - 拿不到锁必须给出终态，不能卡住队列
            self._executing = None
            log.error("调度器获取主 Agent 锁失败（%s）: %s", request.label, exc)
            handle.finish(TurnStatus.ERROR, error=LockBusyError(str(exc)))
            return

        token = _current_turn.set(handle)
        try:
            if handle.is_terminal():
                # 等锁期间被撤单：不再执行
                return
            task = asyncio.get_running_loop().create_task(
                self._invoke_run(handle), name=f"agent_turn:{request.label}"
            )
            handle.task = task
            if handle._cancel_requested and not task.done():
                # 撤单发生在"标记 RUNNING"与"任务建好"之间
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({task})
            if task.cancelled():
                handle.finish(TurnStatus.INTERRUPTED, error=TurnCancelled("task cancelled"))
        except asyncio.CancelledError:
            task = handle.task
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
                    await asyncio.wait({task}, timeout=TASK_CANCEL_TIMEOUT_SEC)
            handle.finish(TurnStatus.INTERRUPTED, error=TurnCancelled("worker shutdown"))
            raise
        finally:
            self._executing = None
            handle.task = None
            release_turn_lock()  # 幂等：正常情况下生产者早已释放
            _current_turn.reset(token)

    async def _invoke_run(self, handle: TurnHandle) -> None:
        try:
            result = await handle.request.run()
        except asyncio.CancelledError:
            self._finalize(
                handle,
                TurnStatus.INTERRUPTED,
                error=TurnCancelled(handle.cancel_reason or "cancelled"),
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 未处理的异常统一落成 error 终态
            self._finalize(handle, TurnStatus.ERROR, error=exc)
            log.error("回合执行失败（%s）: %s", handle.label, exc, exc_info=True)
        else:
            self._finalize(handle, TurnStatus.DONE, result=result)

    def _finalize(
        self,
        handle: TurnHandle,
        status: TurnStatus,
        *,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        """先放锁、再腾执行位，最后让终态可见。

        顺序很重要：``await handle.wait()`` 返回时，执行位必须已经空了
        （否则提交方会看到"已完成的回合仍占着执行位"这种假僵死状态）。
        """
        if self._executing is handle:
            self._executing = None
        release_turn_lock()
        handle.finish(status, result=result, error=error)

    # ── 合并 / 降级 ──

    def _merge_into_pending(self, request: AgentRequest, handle: TurnHandle) -> bool:
        """同 ``merge_key`` 覆盖：新请求占据旧请求的位置（档内 FIFO 不变）。"""
        if not request.merge_key:
            return False
        for kind in TurnKind:
            queue = self._pending[kind]
            for index, old in enumerate(queue):
                if old.status != TurnStatus.PENDING or old.request.merge_key != request.merge_key:
                    continue
                queue[index] = handle
                self._handles.discard(old)
                self._handles.add(handle)
                old.finish(TurnStatus.MERGED)
                log.debug("调度器合并同 id 请求（%s → %s）", old.label, request.label)
                return True
        return False

    def _degrade_overflow(self) -> None:
        """trigger 档超限：把最旧的 trigger 降级进 batch（不丢）。"""
        queue = self._pending[TurnKind.TRIGGER]
        while len(queue) > TRIGGER_QUEUE_MAX:
            oldest = queue.popleft()
            oldest.degraded = True
            # 档位随之下调：_remove_pending / 快照都以 request.kind 为准
            oldest.request.kind = TurnKind.BATCH
            self._pending[TurnKind.BATCH].appendleft(oldest)
            log.warning(
                "trigger 队列超过 %d，最旧的一条（%s）降级进 batch",
                TRIGGER_QUEUE_MAX,
                oldest.label,
            )

    def _remove_pending(self, handle: TurnHandle) -> None:
        queue = self._pending[handle.request.kind]
        with contextlib.suppress(ValueError):
            queue.remove(handle)

    # ── 收尾 ──

    async def shutdown(self, timeout: float = TASK_CANCEL_TIMEOUT_SEC) -> None:
        """lifespan 收尾：cancel worker + 有界等待，**不 drain**（未执行的直接终结）。"""
        self._closing = True
        self.wake()
        worker = self._worker
        self._worker = None
        if worker is not None and not worker.done():
            worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
                await asyncio.wait_for(worker, timeout=timeout)
        for handle in list(self._handles):
            if not handle.is_terminal():
                handle.finish(TurnStatus.INTERRUPTED, error=TurnCancelled("scheduler shutdown"))
        for kind in TurnKind:
            self._pending[kind].clear()
        self._handles.clear()
        self._executing = None

    def reset(self) -> None:
        """单测隔离：丢弃所有内部状态（不 await）。"""
        self._pending = {kind: deque() for kind in TurnKind}
        self._handles = set()
        self._executing = None
        self._worker = None
        self._wake = None
        self._closing = False


# ── 模块级单例与上下文助手 ──

_scheduler: AgentScheduler | None = None


def get_scheduler() -> AgentScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AgentScheduler()
    return _scheduler


def reset_scheduler() -> None:
    """单测隔离：换一个全新的调度器（含事件循环绑定）。"""
    global _scheduler
    previous = _scheduler
    _scheduler = None
    if previous is not None:
        worker = previous._worker
        if worker is not None and not worker.done():
            worker.cancel()
        previous.reset()


def current_handle() -> TurnHandle | None:
    return _current_turn.get()


def in_scheduled_turn() -> bool:
    """当前任务是否运行在调度器 worker 的临界区内（主 Agent 锁已由它持有）。"""
    handle = _current_turn.get()
    return handle is not None and not handle.is_terminal()


def touch() -> None:
    """标记执行位有产出（流式事件每来一条调一次）。"""
    handle = _current_turn.get()
    if handle is not None:
        handle.touch()


def release_turn_lock() -> None:
    """释放调度器 worker 持有的主 Agent 锁（幂等：只有第一个调用者真正释放）。"""
    from faust_backend.agent.runtime import state

    handle = _current_turn.get()
    if handle is None or not handle.lock_held:
        return
    handle.lock_held = False
    if state.agent_lock.locked():
        state.agent_lock.release()
        state.mark_agent_lock_released()


def submit(request: AgentRequest) -> TurnHandle:
    return get_scheduler().submit(request)


def submit_silent_call(
    payload: dict,
    *,
    kind: TurnKind = TurnKind.TRIGGER,
    droppable: bool = False,
    label: str = "silent",
    owner: Any = None,
    merge_key: str | None = None,
    gate: str = "playback",
) -> TurnHandle:
    """提交一次"静默调用"（不推前端）：proactive 情绪寒暄、后台触发器等入口共用。"""

    return get_scheduler().submit(
        AgentRequest(
            kind,
            lambda: stream_silent_reply(payload),
            label=label,
            sink="silent",
            droppable=droppable,
            gate=gate,
            owner=owner,
            merge_key=merge_key,
        )
    )
