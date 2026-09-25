"""ProcessorManager：主进程侧的引用计数、生命周期与调用入口。"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import sys
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psutil

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger

from . import protocol as proto
from .base import Processor, read_setup_marker, resolve_data_dir
from .errors import (
    ProcessorConfigMismatchError,
    ProcessorCrashedError,
    ProcessorError,
    ProcessorFrameError,
    ProcessorInvokeError,
    ProcessorLeaseError,
    ProcessorNotFoundError,
    ProcessorNotReadyError,
    ProcessorStartError,
    ProcessorTimeoutError,
)
from .registry import RegisteredProcessor, get_processor, list_processors

log = get_logger("faust.processor")

STATE_STOPPED = "STOPPED"
STATE_SETTING_UP = "SETTING_UP"
STATE_STARTING = "STARTING"
STATE_ACTIVE = "ACTIVE"
STATE_STOPPING = "STOPPING"

#: 父进程等待 worker TCP 连接 / Hello 帧的上限
HANDSHAKE_TIMEOUT = 30.0
#: terminate() 后等待退出的宽限期
KILL_GRACE_SECONDS = 5.0
#: worker 入口脚本（本文件同目录）
WORKER_SCRIPT = Path(__file__).resolve().with_name("worker.py")

_LEVEL_NO = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "WARN": 30, "ERROR": 40, "CRITICAL": 50}
_LEVEL_LINE_RE = re.compile(r"^(ERROR|WARN|WARNING|INFO|DEBUG|CRITICAL)\b[:：]?\s*", re.IGNORECASE)


def _swallow(future: "asyncio.Future[Any]") -> None:
    """丢弃无人 await 的 future 异常，避免 "exception was never retrieved" 噪音。"""
    if not future.cancelled():
        future.exception()


class _InvokeJob:
    """一次待执行/在途的 invoke。"""

    __slots__ = ("req_id", "data", "future")

    def __init__(self, req_id: int, data: Any, future: "asyncio.Future[Any]") -> None:
        self.req_id = req_id
        self.data = data
        self.future = future


class ProcessorLease:
    """一次引用。``requirer`` 持有，支持 ``async with``。"""

    def __init__(self, name: str, requirer: str, handle: "ProcessorHandle", config: dict[str, Any] | None) -> None:
        self.name = str(name)
        self.requirer = str(requirer)
        self.handle = handle
        self.config = dict(config or {})
        self._acquired = False
        self._released = False

    async def acquire(self) -> "ProcessorLease":
        """登记这次引用（触发启动）。重复 acquire / 已 release 后复用会报错。"""
        if self._released or self._acquired:
            raise ProcessorLeaseError(f"{self.name} 的 lease 只能 acquire 一次（requirer={self.requirer}）")
        await self.handle.acquire_lease(self)
        self._acquired = True
        return self

    def release(self) -> None:
        """释放这次引用（幂等报错：重复 release 抛 ``ProcessorLeaseError``）。"""
        if not self._acquired:
            raise ProcessorLeaseError(f"{self.name} 的 lease 尚未 acquire，不能 release（requirer={self.requirer}）")
        if self._released:
            raise ProcessorLeaseError(f"{self.name} 的 lease 已 release（requirer={self.requirer}）")
        self.handle.release_lease(self)
        self._released = True

    async def __aenter__(self) -> "ProcessorLease":
        return await self.acquire()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.release()

    async def wait_until_ready(self, timeout: float | None = None) -> None:
        """等 handle 进入 ACTIVE；启动失败/崩溃抛 ``ProcessorStartError``，超时抛 ``ProcessorTimeoutError``。"""
        self._require_acquired("wait_until_ready")
        await self.handle.wait_until_ready(timeout)

    async def invoke(self, data: Any, *, timeout: float | None = None) -> Any:
        """一次计算调用（FIFO 串行）。"""
        self._require_acquired("invoke")
        return await self.handle.invoke(data, timeout=timeout)

    async def get_log(self, level: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """读该 Processor 的日志（最新在前）。"""
        return await self.handle.get_log(level=level, limit=limit)

    def _require_acquired(self, action: str) -> None:
        if not self._acquired or self._released:
            raise ProcessorLeaseError(f"{self.name} 的 lease 未持有，不能 {action}（requirer={self.requirer}）")


class ProcessorHandle:
    """某个 Processor 的运行时状态对象（按名字单例，归 ProcessorManager 所有）。"""

    def __init__(self, name: str, entry: RegisteredProcessor) -> None:
        self.name = str(name)
        self.entry = entry
        self.state = STATE_STOPPED
        self.phase: str | None = None
        self.last_error: str | None = None
        self.pid: int | None = None
        self.config: dict[str, Any] = {}
        self.fingerprint: str | None = None
        self.started_at: float | None = None
        self.idle_since: float | None = None
        self.invokes_total = 0
        self.invokes_failed = 0
        self.invokes_cancelled = 0
        self.last_invoke_seconds: float | None = None
        self.leases: list[ProcessorLease] = []
        self.logs: deque[dict[str, Any]] = deque(maxlen=max(1, int(entry.cls.LOG_BUFFER)))

        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._server: asyncio.AbstractServer | None = None
        self._token = ""
        self._connected: "asyncio.Future[bool] | None" = None
        self._start_result: "asyncio.Future[None] | None" = None
        self._start_task: asyncio.Task | None = None
        self._stop_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._stdio_tasks: list[asyncio.Task] = []
        self._pump_task: asyncio.Task | None = None
        self._queue: asyncio.Queue[_InvokeJob] | None = None
        self._pending: dict[int, "asyncio.Future[Any]"] = {}
        self._next_req_id = 1
        self._state_event = asyncio.Event()

    # ── 状态 ──────────────────────────────────────────────

    def _notify(self) -> None:
        self._state_event.set()
        self._state_event = asyncio.Event()

    def _set_state(self, state: str) -> None:
        if self.state == state:
            return
        log.debug("Processor %s: %s -> %s", self.name, self.state, state)
        self.state = state
        self._notify()

    @property
    def refcount(self) -> int:
        """未释放 lease 总数。"""
        return len(self.leases)

    @property
    def holders(self) -> dict[str, int]:
        """按 requirer 分类的引用数。"""
        return dict(Counter(lease.requirer for lease in self.leases))

    @property
    def pid_alive(self) -> bool:
        return bool(self.pid) and psutil.pid_exists(int(self.pid))

    @property
    def busy(self) -> bool:
        """是否有在途或排队的 invoke（prune 空闲判定用）。"""
        if self._pending:
            return True
        return self._queue is not None and not self._queue.empty()

    def status(self) -> dict[str, Any]:
        """状态快照（admin 接口与排查用）。"""
        now = time.time()
        return {
            "name": self.name,
            "owner": self.entry.owner,
            "state": self.state,
            "phase": self.phase,
            "pid": self.pid,
            "pid_alive": self.pid_alive,
            "last_error": self.last_error,
            "refcount": self.refcount,
            "holders": self.holders,
            "queue_depth": self._queue.qsize() if self._queue is not None else 0,
            "idle_seconds": (now - self.idle_since) if self.idle_since is not None else None,
            "uptime_seconds": (now - self.started_at) if self.started_at is not None else None,
            "setup_done": self._setup_done(),
            "fingerprint": self.fingerprint,
            "config": dict(self.config),
            "invokes_total": self.invokes_total,
            "invokes_failed": self.invokes_failed,
            "invokes_cancelled": self.invokes_cancelled,
            "last_invoke_seconds": self.last_invoke_seconds,
        }

    async def get_log(self, level: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """读日志缓冲，最新在前；``level`` 表示最低等级（如 ``"ERROR"``）。"""
        threshold = _LEVEL_NO.get(str(level).upper(), 0) if level else 0
        items = [dict(item) for item in self.logs if int(item["levelno"]) >= threshold]
        items.reverse()
        if limit is not None and int(limit) > 0:
            items = items[: int(limit)]
        return items

    def _record_log(self, level: str, message: str, source: str, *, ts: float | None = None) -> None:
        level_text = str(level or "INFO").upper()
        item = {
            "ts": float(ts if ts is not None else time.time()),
            "level": level_text,
            "levelno": _LEVEL_NO.get(level_text, 20),
            "message": str(message),
            "source": str(source),
        }
        self.logs.append(item)
        get_logger(f"faust.processor.{self.name}").log(item["levelno"], "[%s] %s", item["source"], item["message"])

    def _log_tail(self, count: int = 50) -> list[dict[str, Any]]:
        return list(self.logs)[-count:]

    def _setup_done(self) -> bool:
        try:
            return not self._needs_setup()
        except Exception:  # noqa: BLE001 - 指纹计算失败只影响展示
            return False

    def _fingerprint(self) -> str:
        return str(self.entry.cls().setup_fingerprint(dict(self.config)))

    def _needs_setup(self) -> bool:
        data_dir = resolve_data_dir(self.name, self.entry.cls.DATA_DIR)
        marker = read_setup_marker(data_dir)
        return str(marker.get("fingerprint") or "") != self._fingerprint()

    # ── 启动 ──────────────────────────────────────────────

    def ensure_started(self) -> None:
        """触发启动（同一 handle 上并发 require 复用同一次启动）。"""
        if self.state in (STATE_SETTING_UP, STATE_STARTING, STATE_ACTIVE):
            return
        if self._start_task is not None and not self._start_task.done():
            return
        self._start_task = asyncio.create_task(self._run_start())
        self._start_task.add_done_callback(self._on_start_done)

    def _on_start_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            log.error("Processor %s 启动失败: %s", self.name, exc)

    async def wait_until_ready(self, timeout: float | None = None) -> None:
        """等 ACTIVE。启动失败/崩溃 → ``ProcessorStartError``；超时 → ``ProcessorTimeoutError``。"""
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + float(timeout)
        while True:
            event = self._state_event
            if self.state == STATE_ACTIVE:
                return
            if self._start_task is not None and not self._start_task.done():
                pass  # 启动进行中，继续等
            elif self.state == STATE_STOPPING:
                pass  # 等停止完成
            else:
                raise ProcessorStartError(
                    f"{self.name} 无法就绪: {self.last_error or self.state}",
                    last_error=self.last_error,
                    logs=self._log_tail(),
                )
            if deadline is None:
                await event.wait()
                continue
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ProcessorTimeoutError(f"等待 {self.name} 就绪超时（{timeout}s）", phase="wait_until_ready")
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except asyncio.TimeoutError:
                raise ProcessorTimeoutError(
                    f"等待 {self.name} 就绪超时（{timeout}s）", phase="wait_until_ready"
                ) from None

    async def _run_start(self) -> None:
        cls = self.entry.cls
        # 上一次崩溃/超时的收尾可能还在跑（会 cancel 后台任务）：先让它结束，
        # 否则新 worker 的 reader task 会被旧的 teardown 取消
        cleanup = self._cleanup_task
        if cleanup is not None and not cleanup.done():
            await asyncio.shield(cleanup)
        self.last_error = None
        self.phase = None
        data_dir = resolve_data_dir(self.name, cls.DATA_DIR)
        self.fingerprint = self._fingerprint()
        run_setup = self._needs_setup()
        try:
            await self._spawn_worker()
            await self._await_start(run_setup, cls, data_dir)
        except ProcessorError as exc:
            self.last_error = str(exc)
            await self._kill_worker()
            raise
        except Exception as exc:  # noqa: BLE001
            message = f"{self.name} 启动失败: {exc}"
            self.last_error = message
            await self._kill_worker()
            raise ProcessorStartError(message, last_error=message, logs=self._log_tail()) from exc
        self.started_at = time.time()
        self.idle_since = None if self.refcount else time.time()
        self._set_state(STATE_ACTIVE)
        log.info("Processor %s 已就绪（pid=%s, setup_ran=%s）", self.name, self.pid, run_setup)

    async def _await_start(self, run_setup: bool, cls: type[Processor], data_dir: Path) -> None:
        loop = asyncio.get_running_loop()
        self._start_result = loop.create_future()
        await self._send(
            proto.StartOp(run_setup=run_setup, config=dict(self.config), data_dir=str(data_dir))
        )
        if run_setup:
            await self._wait_for(
                lambda: self.phase == "starting" or self._start_done(), cls.SETUP_TIMEOUT, phase="setup"
            )
        await self._wait_for(self._start_done, cls.START_TIMEOUT, phase="start")
        assert self._start_result is not None
        exc = self._start_result.exception()
        if exc is not None:
            raise exc

    def _start_done(self) -> bool:
        return self._start_result is not None and self._start_result.done()

    async def _wait_for(self, predicate: Any, timeout: float, *, phase: str) -> None:
        """等条件成立（事件驱动 + 超时）。超时不杀进程，由调用方处理。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        while True:
            event = self._state_event
            if predicate():
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ProcessorTimeoutError(f"{self.name} {phase} 超时（{timeout}s）", phase=phase)
            try:
                await asyncio.wait_for(event.wait(), remaining)
            except asyncio.TimeoutError:
                raise ProcessorTimeoutError(f"{self.name} {phase} 超时（{timeout}s）", phase=phase) from None

    async def _on_worker_connect(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._connected is None or self._connected.done():
            writer.close()  # 一个 handle 只允许一条控制通道
            return
        self._reader = reader
        self._writer = writer
        self._connected.set_result(True)

    async def _spawn_worker(self) -> None:
        loop = asyncio.get_running_loop()
        self._connected = loop.create_future()
        self._token = secrets.token_hex(16)
        self._server = await asyncio.start_server(self._on_worker_connect, host="127.0.0.1", port=0)
        port = int(self._server.sockets[0].getsockname()[1])

        backend_root = Path(conf.PROJECT_ROOT)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        # 用「脚本路径」而不是 "-m 包名"：.runtime 是内嵌发行版（有 python311._pth），
        # 它忽略 PYTHONPATH 也不把 cwd/脚本目录放进 sys.path，只有 worker.py 自己
        # bootstrap（见 worker.py 顶部）才能 import faust_backend。
        cmd = [
            sys.executable,
            str(WORKER_SCRIPT),
            "--name",
            self.name,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--token",
            self._token,
            "--parent-pid",
            str(os.getpid()),
            "--log-level",
            "INFO",
            "--registry-module",
            self.entry.loader,
        ]
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(backend_root),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except NotImplementedError as exc:
            await self._close_server()
            raise ProcessorStartError(
                f"{self.name} 无法启动子进程：当前事件循环不支持 subprocess（需要 ProactorEventLoop）"
            ) from exc
        self.pid = self._proc.pid

        try:
            await asyncio.wait_for(self._connected, timeout=HANDSHAKE_TIMEOUT)
        except asyncio.TimeoutError:
            raise ProcessorStartError(
                f"{self.name} 握手超时（{HANDSHAKE_TIMEOUT}s）：{await self._drain_child_output()}",
                last_error="handshake timeout",
                logs=self._log_tail(),
            ) from None
        assert self._reader is not None
        try:
            first = await asyncio.wait_for(proto.recv_frame(self._reader), timeout=HANDSHAKE_TIMEOUT)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError) as exc:
            raise ProcessorStartError(
                f"{self.name} 握手帧读取失败: {exc}\n{await self._drain_child_output()}",
                logs=self._log_tail(),
            ) from exc
        if (
            not isinstance(first, proto.Hello)
            or first.name != self.name
            or not secrets.compare_digest(str(first.token), self._token)
        ):
            raise ProcessorStartError(
                f"{self.name} 握手校验失败：token 或名字不匹配\n{await self._drain_child_output()}",
                logs=self._log_tail(),
            )
        self.pid = int(first.pid)

        await self._close_server()
        self._reader_task = asyncio.create_task(self._reader_loop())
        assert self._proc.stdout is not None and self._proc.stderr is not None
        self._stdio_tasks = [
            asyncio.create_task(self._pipe_loop(self._proc.stdout, "stdout")),
            asyncio.create_task(self._pipe_loop(self._proc.stderr, "stderr")),
        ]
        self._queue = asyncio.Queue()
        self._pump_task = asyncio.create_task(self._pump())

    async def _drain_child_output(self) -> str:
        """握手失败时把子进程已有输出捞回来（此时 stdio 管道还没开始按行采集）。

        先 terminate 再读到 EOF，避免 read() 一直等下去；输出同时写进日志缓冲，
        保证「启动失败必须能看到原因」。
        """
        proc = self._proc
        if proc is None:
            return ""
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001 - 进程已消失
                    pass
        # 按行读到「窗口内没有更多输出」为止：不能等 EOF——子进程可能又派生了进程，
        # 它们会一直hold住管道的写端，read() 会一直挂着。
        loop = asyncio.get_running_loop()
        lines: list[str] = []
        for stream in (proc.stdout, proc.stderr):
            if stream is None:
                continue
            deadline = loop.time() + KILL_GRACE_SECONDS
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    chunk = await asyncio.wait_for(stream.readline(), remaining)
                except Exception:  # noqa: BLE001 - 拿不到输出也要继续报错
                    break
                if not chunk:
                    break
                lines.append(chunk.decode("utf-8", errors="replace").rstrip("\r\n"))
        output = "\n".join(line for line in lines if line.strip())
        for line in output.splitlines():
            self._record_log("ERROR", line, "stderr")
        return output

    async def _close_server(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        server.close()
        try:
            await server.wait_closed()
        except Exception:  # noqa: BLE001 - 关闭 listener 失败不影响后续
            pass

    async def _send(self, obj: Any) -> None:
        writer = self._writer
        if writer is None or writer.is_closing():
            raise ProcessorCrashedError(f"{self.name} 控制通道不可用（worker 未连接）")
        try:
            await proto.send_frame(writer, obj)
        except ProcessorFrameError:
            raise
        except (ConnectionError, OSError) as exc:
            raise ProcessorCrashedError(f"{self.name} 控制通道写入失败: {exc}") from exc

    # ── 控制通道读取 ──────────────────────────────────────

    async def _reader_loop(self) -> None:
        reader = self._reader
        assert reader is not None
        try:
            while True:
                self._dispatch(await proto.recv_frame(reader))
        except asyncio.CancelledError:
            raise
        except asyncio.IncompleteReadError:
            pass
        except ProcessorFrameError as exc:
            self._record_log("ERROR", f"控制通道帧错误: {exc}", "stderr")
        except Exception as exc:  # noqa: BLE001
            if self.state in (STATE_STOPPING, STATE_STOPPED):
                log.debug("Processor %s 控制通道在停止过程中关闭: %s", self.name, exc)
            else:
                self._record_log("ERROR", f"控制通道读取失败: {exc}", "stderr")
        finally:
            if self.state not in (STATE_STOPPING, STATE_STOPPED):
                await self._on_worker_gone()

    def _dispatch(self, frame: Any) -> None:
        if isinstance(frame, proto.Phase):
            self.phase = str(frame.phase)
            self._set_state(STATE_SETTING_UP if self.phase == "setting_up" else STATE_STARTING)
            return
        if isinstance(frame, proto.Started):
            self.fingerprint = str(frame.fingerprint)
            self._resolve_start()
            return
        if isinstance(frame, proto.StartFailed):
            info = frame.error
            self._record_log("ERROR", f"{info.type}: {info.message}", "ctx")
            for line in str(info.traceback or "").rstrip().splitlines():
                self._record_log("ERROR", line, "ctx")
            self._resolve_start(
                ProcessorStartError(
                    f"{self.name} {info.type}: {info.message}",
                    last_error=info.message,
                    logs=self._log_tail(),
                )
            )
            return
        if isinstance(frame, proto.Result):
            pending = self._pending.pop(int(frame.req_id), None)
            if pending is None or pending.done():
                return
            if frame.ok:
                pending.set_result(frame.value)
            else:
                info = frame.error
                error_type = info.type if info is not None else "ProcessorError"
                message = info.message if info is not None else "未知错误"
                pending.set_exception(
                    ProcessorInvokeError(
                        f"{self.name} invoke 失败: {error_type}: {message}",
                        error_type=error_type,
                        traceback_text=(info.traceback if info is not None else ""),
                    )
                )
            return
        if isinstance(frame, proto.LogMsg):
            self._record_log(frame.level, frame.message, frame.source, ts=frame.ts)
            return
        log.warning("Processor %s 收到未知帧: %r", self.name, frame)

    def _resolve_start(self, exc: ProcessorStartError | None = None) -> None:
        future = self._start_result
        if future is not None and not future.done():
            if exc is None:
                future.set_result(None)
            else:
                future.set_exception(exc)
        self._notify()

    async def _pipe_loop(self, stream: asyncio.StreamReader, source: str) -> None:
        try:
            while True:
                raw = await stream.readline()
                if not raw:
                    return
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line.strip():
                    continue
                match = _LEVEL_LINE_RE.match(line)
                self._record_log(match.group(1).upper() if match else "INFO", line, source)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 管道关闭属正常退出路径
            log.debug("Processor %s %s 管道结束: %s", self.name, source, exc)

    # ── invoke ────────────────────────────────────────────

    async def invoke(self, data: Any, *, timeout: float | None = None) -> Any:
        """FIFO 串行调用；非 ACTIVE 立即抛 ``ProcessorNotReadyError``。"""
        if self.state != STATE_ACTIVE:
            raise ProcessorNotReadyError(f"{self.name} 当前状态 {self.state}，不能 invoke")
        loop = asyncio.get_running_loop()
        limit = timeout if timeout is not None else self.entry.cls.INVOKE_TIMEOUT
        job = _InvokeJob(req_id=self._next_req_id, data=data, future=loop.create_future())
        self._next_req_id += 1
        job.future.add_done_callback(_swallow)
        self.invokes_total += 1
        self.idle_since = None
        assert self._queue is not None
        self._queue.put_nowait(job)

        started = loop.time()
        try:
            if limit:
                result = await asyncio.wait_for(asyncio.shield(job.future), float(limit))
            else:
                result = await asyncio.shield(job.future)
        except asyncio.TimeoutError:
            self.last_error = f"invoke 超时（{limit}s）"
            log.error("Processor %s %s，回收 worker", self.name, self.last_error)
            await self._kill_worker()
            raise ProcessorTimeoutError(f"{self.name} invoke 超时（{limit}s）", phase="invoke") from None
        except asyncio.CancelledError:
            self.invokes_cancelled += 1
            raise
        return result

    async def _pump(self) -> None:
        """单条 pump：FIFO 依次发送、等结果（并发调用因此严格串行）。"""
        queue = self._queue
        assert queue is not None
        loop = asyncio.get_running_loop()
        while True:
            job = await queue.get()
            if self.state != STATE_ACTIVE or self._writer is None or self._writer.is_closing():
                if not job.future.done():
                    job.future.set_exception(
                        ProcessorNotReadyError(f"{self.name} 当前状态 {self.state}，请求被丢弃")
                    )
                continue
            self._pending[job.req_id] = job.future
            started = loop.time()
            try:
                await self._send(proto.InvokeOp(req_id=job.req_id, data=job.data))
                await asyncio.shield(job.future)
                self.last_invoke_seconds = loop.time() - started
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 失败已经在 future 上抛给调用方
                self.invokes_failed += 1
                log.debug("Processor %s invoke #%d 失败: %s", self.name, job.req_id, exc)
            finally:
                self._pending.pop(job.req_id, None)
                if self.refcount == 0 and queue.empty() and self.state == STATE_ACTIVE:
                    # 空闲计时统一用 time.time()（prune/status 也用它算差值，别混 loop.time()）
                    self.idle_since = time.time()

    # ── 引用计数 ──────────────────────────────────────────

    async def acquire_lease(self, lease: ProcessorLease) -> None:
        """登记一次引用并（必要时）触发启动。

        config 规则（规格 §10）：ACTIVE 且无人引用时 config 变化 → 先停再按新
        config 重启；仍有其它引用时 config 不一致 → 明确报错，不偷偷重启。
        """
        if self.state == STATE_STOPPING and self._stop_task is not None and not self._stop_task.done():
            await asyncio.shield(self._stop_task)   # 等上一轮停止收尾，避免启动/停止交叉
        desired = dict(lease.config)
        if desired and desired != self.config:
            if self.state == STATE_ACTIVE and self.refcount == 0:
                await self.stop(reason="config 变更，按新配置重启")
            elif self.state in (STATE_ACTIVE, STATE_SETTING_UP, STATE_STARTING):
                raise ProcessorConfigMismatchError(
                    f"{self.name} 正在使用 config={self.config}（holders={self.holders}），"
                    f"无法切换到 {desired}"
                )
            self.config = desired
        elif not self.config and desired:
            self.config = desired
        self.leases.append(lease)
        self.idle_since = None
        self.ensure_started()

    def release_lease(self, lease: ProcessorLease) -> None:
        """注销一次引用；无引用且无在途请求时开始计时空闲。"""
        try:
            self.leases.remove(lease)
        except ValueError as exc:
            raise ProcessorLeaseError(f"{self.name} 的 lease 不属于该 handle") from exc
        if self.refcount == 0 and (self._queue is None or self._queue.empty()) and self.state == STATE_ACTIVE:
            self.idle_since = time.time()

    # ── 停止 ──────────────────────────────────────────────

    async def stop(self, *, reason: str = "manual") -> None:
        """停止 worker（等 StopOp → terminate → kill）。幂等。"""
        if self.state == STATE_STOPPED and self._proc is None:
            return
        if self._stop_task is not None and not self._stop_task.done():
            await asyncio.shield(self._stop_task)
            return
        self._set_state(STATE_STOPPING)
        log.info("停止 Processor %s（原因: %s）", self.name, reason)
        self._stop_task = asyncio.create_task(self._run_stop(reason))
        await asyncio.shield(self._stop_task)

    async def _run_stop(self, reason: str) -> None:
        try:
            proc = self._proc
            if self._writer is not None and not self._writer.is_closing():
                try:
                    await asyncio.wait_for(self._send(proto.StopOp()), timeout=float(self.entry.cls.STOP_TIMEOUT))
                except Exception as exc:  # noqa: BLE001
                    log.warning("Processor %s 发送 StopOp 失败: %s", self.name, exc)
            if proc is not None and proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    log.warning("Processor %s 未按时退出，terminate()", self.name)
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
                    except asyncio.TimeoutError:
                        log.warning("Processor %s 仍未退出，kill()", self.name)
                        proc.kill()
                        await proc.wait()
        finally:
            self._fail_pending(ProcessorCrashedError(f"{self.name} 已停止（{reason}），请求被丢弃"))
            await self._teardown()

    async def _kill_worker(self, *, reason: str | None = None) -> None:
        """异常路径的强制回收：terminate → kill → teardown（reason 会并入 last_error）。"""
        if reason:
            self.last_error = f"{self.last_error}；{reason}" if self.last_error else reason
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                try:
                    await proc.wait()
                except Exception:  # noqa: BLE001 - 进程已消失
                    pass
            except ProcessLookupError:
                pass
        self._fail_pending(ProcessorCrashedError(f"{self.name} worker 被回收，请求被丢弃"))
        await self._teardown()

    def _fail_pending(self, exc: ProcessorError) -> None:
        pending = list(self._pending.items())
        self._pending.clear()
        for _req_id, future in pending:
            if not future.done():
                future.set_exception(exc)

    async def _teardown(self) -> None:
        """取消后台任务、关通道、清空运行时字段；保留 last_error（供诊断）。"""
        tasks = [task for task in (self._pump_task, self._reader_task, *self._stdio_tasks) if task is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # noqa: BLE001 - 收尾阶段不因任务异常中断
                log.debug("Processor %s 后台任务收尾异常: %s", self.name, exc)
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
        await self._close_server()
        self._proc = None
        self._reader = None
        self._writer = None
        self._queue = None
        self._pending.clear()
        self._pump_task = None
        self._reader_task = None
        self._stdio_tasks = []
        self._connected = None
        self.pid = None
        self.phase = None
        self.started_at = None
        self.idle_since = None
        self._set_state(STATE_STOPPED)

    async def _on_worker_gone(self) -> None:
        """worker 意外退出：让在途/排队请求失败，等下一次 require 重启。

        连接先断、进程随后被 OS 回收，因此这里先等一小会儿拿到真实退出码，
        再把原因**追加**到已有 last_error 上（例如「invoke 超时（0.7s）；worker exited with code 1」），
        避免覆盖掉真正的根因。
        """
        proc = self._proc
        exit_code: int | None = None
        if proc is not None:
            if proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=KILL_GRACE_SECONDS)
                except asyncio.TimeoutError:
                    pass
            exit_code = proc.returncode
        reason = f"worker exited with code {exit_code}" if exit_code is not None else "worker 连接中断"
        log.error("Processor %s %s", self.name, reason)
        self.last_error = f"{self.last_error}；{reason}" if self.last_error else reason
        self._fail_pending(ProcessorCrashedError(f"{self.name} 子进程已退出: {reason}", exit_code=exit_code))
        self._resolve_start(
            ProcessorStartError(
                f"{self.name} 启动期间子进程退出: {reason}",
                last_error=self.last_error,
                logs=self._log_tail(),
            )
        )
        self._set_state(STATE_STOPPED)
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._teardown())


class ProcessorManager:
    """全部 Processor 的注册表入口、状态查询与回收开关。"""

    def __init__(self) -> None:
        self._handles: dict[str, ProcessorHandle] = {}

    def handle(self, name: str) -> ProcessorHandle:
        """取 handle（按需创建）。名字未注册抛 ``ProcessorNotFoundError``。"""
        entry = get_processor(name)
        handle = self._handles.get(entry.name)
        if handle is None:
            handle = ProcessorHandle(entry.name, entry)
            self._handles[entry.name] = handle
        return handle

    def require(self, name: str, requirer: str, *, config: dict[str, Any] | None = None) -> ProcessorLease:
        """返回**未获取**的 lease；``async with`` 进入时 acquire、退出时 release。

        注意：``require()`` 本身不启动进程也不计数——引用计数在 ``acquire``/``async with``
        进入时才 +1，启动也在那一刻触发（避免只是构造 lease 就拉起子进程）。
        """
        handle = self.handle(name)
        return ProcessorLease(handle.name, str(requirer), handle, config)

    async def startRequire(
        self, name: str, requirer: str, *, config: dict[str, Any] | None = None
    ) -> ProcessorLease:
        """立即 acquire 并返回已持有的 lease（等价于 ``await require(...).acquire()``）。"""
        lease = self.require(name, requirer, config=config)
        return await lease.acquire()

    async def endRequire(self, name: str, requirer: str) -> None:
        """释放该 requirer 最早的一个未释放 lease；无匹配抛 ``ProcessorLeaseError``。"""
        handle = self.handle(name)
        for lease in list(handle.leases):
            if lease.requirer == str(requirer):
                lease.release()
                return
        raise ProcessorLeaseError(f"{name} 没有属于 {requirer} 的未释放引用")

    def status(self) -> list[dict[str, Any]]:
        """全部已注册 Processor 的状态快照。"""
        return [self.handle(entry.name).status() for entry in list_processors()]

    async def stop(self, name: str) -> None:
        """管理用途：忽略引用计数强制停止（写日志说明）。"""
        handle = self.handle(name)
        if handle.refcount:
            log.warning("强制停止 Processor %s：仍有 %d 个引用 %s", handle.name, handle.refcount, handle.holders)
        await handle.stop(reason="forced by manager.stop()")

    async def shutdown(self) -> None:
        """后端关闭时调用：忽略引用计数停止全部 Processor 并等待回收。"""
        for handle in list(self._handles.values()):
            try:
                await handle.stop(reason="backend shutdown")
            except Exception as exc:  # noqa: BLE001 - 关闭阶段继续处理其它 Processor
                log.error("关闭 Processor %s 失败: %s", handle.name, exc)
        alive = [handle.name for handle in self._handles.values() if handle.pid_alive]
        if alive:
            log.error("后端关闭后仍有残留 worker 进程: %s", alive)
        else:
            log.info("全部 Processor 已回收")


_MANAGER: ProcessorManager | None = None


def get_processor_manager() -> ProcessorManager:
    """进程级单例。"""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = ProcessorManager()
    return _MANAGER
