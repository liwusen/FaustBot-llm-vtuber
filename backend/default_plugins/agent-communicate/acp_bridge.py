"""agent-communicate 的 ACP 桥接层：用官方 SDK 管理外部 Agent 子进程与连接。

职责边界（见 docs/agent-communicate-spec.md §4 / §7）：

- 起停子进程、持有 ``ClientSideConnection``、实现 ``acp.Client`` 回调；
- 给 SDK 的每一次调用套 ``asyncio.wait_for``（**SDK 自身没有超时机制**），超时异常带上
  阶段名与已等秒数，供 ``status.md`` 如实渲染；
- 规避 SDK 的四个陷阱（env 裁剪 / stderr 无 drain / 行缓冲 64KB / terminal 静默 null）。

不负责：任务语义、VFS 渲染、JSON-RPC 帧（全部交给 SDK）。
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import time
from typing import Any, Awaitable, Callable, Mapping, Sequence

import acp
from acp import RequestPermissionResponse, text_block
from acp.schema import (
    ClientCapabilities,
    DeniedOutcome,
    Implementation,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    RequestPermissionRequest,
    SetSessionConfigOptionResponse,
)
from acp.contrib.session_state import SessionAccumulator  # noqa: F401  (对外转出，供 tasks 层使用)

from faust_backend.logger import get_logger

log = get_logger("faust.plugins.agent-communicate.bridge")

# 陷阱 3：SDK 默认 limit=None → asyncio 64KB，超长单行 JSON 会炸。对齐 SDK 给 agent 侧用的值。
STDIO_BUFFER_LIMIT_BYTES = 50 * 1024 * 1024

# 对外身份（clientCapabilities 全部留空：不声明 fs / terminal / elicitation）
CLIENT_NAME = "faustbot-agent-communicate"
CLIENT_VERSION = "0.1.0"

# SDK 退出块之后仍需清理 Windows 上的孙进程（SDK 只 terminate/kill 直接子进程）
_TASKKILL_TIMEOUT_SEC = 10.0
_SHUTDOWN_GRACE_SEC = 8.0

STDERR_RING_LIMIT = 40


class AcpBridgeError(RuntimeError):
    """桥接层错误基类；``stage`` 与 ``waited`` 供 status.md / events.md 如实呈现。"""

    def __init__(self, stage: str, message: str, *, waited: float | None = None) -> None:
        super().__init__(message)
        self.stage = str(stage)
        self.waited = waited


class AcpTimeoutError(AcpBridgeError):
    """某个阶段在超时时间内没有应答（不含"进程死亡"这一类）。"""


class AcpProcessGoneError(AcpBridgeError):
    """子进程不存在、已退出或连接已断开。"""


SessionUpdateHandler = Callable[[str, Any], Any]
PermissionHandler = Callable[[str, Any, list[PermissionOption]], Awaitable[RequestPermissionResponse]]
ExitHandler = Callable[[str], Any]
UnhandledHandler = Callable[[str, Any], Any]


async def _kill_process_tree(proc: "asyncio.subprocess.Process") -> None:
    """兜底回收进程树（对齐 tools/execute.py 的做法）。"""
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32" and proc.pid:
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/F",
                "/T",
                "/PID",
                str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=_TASKKILL_TIMEOUT_SEC)
            except asyncio.TimeoutError:
                killer.kill()
        else:
            proc.kill()
    except Exception as exc:  # noqa: BLE001 - 兜底失败不应掩盖主流程
        log.warning("回收子进程树失败: %s", exc)
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except Exception:  # noqa: BLE001
        pass


class AcpBridge:
    """单个外部 Agent 进程的连接桥。一个实例 = 一个进程 + 一条连接。"""

    def __init__(
        self,
        *,
        name: str,
        command: Sequence[str],
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        inherit_env: bool = True,
        handshake_timeout_sec: float = 30.0,
        session_timeout_sec: float = 60.0,
        on_session_update: SessionUpdateHandler | None = None,
        on_permission: PermissionHandler | None = None,
        on_exit: ExitHandler | None = None,
        on_unhandled: UnhandledHandler | None = None,
    ) -> None:
        if not command:
            raise ValueError("AcpBridge command 不能为空")
        self.name = str(name)
        self.command = [str(item) for item in command]
        self.cwd = str(cwd) if cwd else None
        self.env_extra = {str(k): str(v) for k, v in dict(env or {}).items()}
        self.inherit_env = bool(inherit_env)
        self.handshake_timeout_sec = float(handshake_timeout_sec)
        self.session_timeout_sec = float(session_timeout_sec)

        self._on_session_update = on_session_update
        self._on_permission = on_permission
        self._on_exit = on_exit
        self._on_unhandled = on_unhandled

        self._task: asyncio.Task[None] | None = None
        self._conn: Any = None
        self._proc: Any = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()
        self._closing = asyncio.Event()
        self._proc_dead = asyncio.Event()
        self._locks = {"start": asyncio.Lock()}

        self.exit_code: int | None = None
        self.exit_detail: str | None = None
        self.stderr_tail: list[str] = []

        # initialize 结果缓存
        self.protocol_version: int | None = None
        self.agent_info: Any = None
        self.auth_methods: list[Any] = []
        self.agent_capabilities: Any = None

    # ── 状态 ──

    @property
    def running(self) -> bool:
        return self._conn is not None and not self._proc_dead.is_set()

    @property
    def pid(self) -> int | None:
        return getattr(self._proc, "pid", None)

    # ── 进程生命周期 ──

    async def start(self, *, timeout: float | None = None) -> None:
        """启动子进程并建立连接（幂等：已在跑则直接返回）。"""
        if self.running:
            return
        async with self._locks["start"]:
            if self.running:
                return
            if self._task is not None and not self._task.done():
                # 上一次的 runner 还在收尾，等它彻底结束再重启
                await asyncio.gather(self._task, return_exceptions=True)
            self._ready = asyncio.Event()
            self._closing = asyncio.Event()
            self._proc_dead = asyncio.Event()
            self.exit_code = None
            self.exit_detail = None
            self._task = asyncio.create_task(self._runner(), name=f"acp-bridge-{self.name}")
            limit = float(timeout if timeout is not None else self.handshake_timeout_sec)
            # 与"进程已死"竞争，spawn 失败要立刻失败，而不是干等到超时
            ready_task = asyncio.create_task(self._ready.wait())
            dead_task = asyncio.create_task(self._proc_dead.wait())
            try:
                await asyncio.wait(
                    {ready_task, dead_task}, timeout=limit, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for pending_task in (ready_task, dead_task):
                    if not pending_task.done():
                        pending_task.cancel()
                await asyncio.gather(ready_task, dead_task, return_exceptions=True)
            if not self.running:
                ready_before, dead_before = self._ready.is_set(), self._proc_dead.is_set()
                await self.stop()
                detail = self.exit_detail or "进程未就绪"
                if dead_before or ready_before:
                    raise AcpProcessGoneError("spawn", f"启动 {self.name} 失败: {detail}")
                raise AcpTimeoutError(
                    "spawn", f"启动 {self.name} 超时（已等 {limit:.1f}s / 上限 {limit:.0f}s）", waited=limit
                ) from None

    async def _runner(self) -> None:
        command = self.command
        proc: Any = None
        watcher: asyncio.Task[Any] | None = None
        try:
            async with acp.spawn_agent_process(
                self,
                command[0],
                *command[1:],
                env=self._build_env(),
                cwd=self.cwd,
                transport_kwargs={"limit": STDIO_BUFFER_LIMIT_BYTES},
                use_unstable_protocol=False,
            ) as (conn, proc):
                self._conn = conn
                self._proc = proc
                watcher = asyncio.create_task(self._watch_process(proc), name=f"acp-watch-{self.name}")
                self._stderr_task = asyncio.create_task(
                    self._drain_stderr(proc), name=f"acp-stderr-{self.name}"
                )
                self._ready.set()
                log.info("%s: ACP 子进程已启动 pid=%s", self.name, getattr(proc, "pid", None))
                await self._closing.wait()
        except asyncio.CancelledError:
            if not self.exit_detail:
                self.exit_detail = "桥接任务被取消"
            raise
        except Exception as exc:  # noqa: BLE001 - 启动/退出异常都要落到状态里
            self.exit_detail = f"{type(exc).__name__}: {exc}"
            log.warning("%s: ACP 进程上下文异常退出: %s", self.name, exc)
        finally:
            self._conn = None
            self._proc = None
            self._ready.clear()
            self._closing.set()
            for task in (watcher, self._stderr_task):
                if task is not None and not task.done():
                    task.cancel()
            for task in (watcher, self._stderr_task):
                if task is not None:
                    await asyncio.gather(task, return_exceptions=True)
            self._stderr_task = None
            if proc is not None:
                await _kill_process_tree(proc)
                if self.exit_code is None:
                    self.exit_code = proc.returncode
                if not self.exit_detail:
                    self.exit_detail = f"进程退出（code={proc.returncode}）"
            self._proc_dead.set()
            if not self.exit_detail:
                self.exit_detail = "进程未启动"
            log.info("%s: ACP 进程已收尾（%s）", self.name, self.exit_detail)
            await self._notify_exit()

    async def _watch_process(self, proc: Any) -> None:
        code = await proc.wait()
        self.exit_code = code
        if not self.exit_detail:
            self.exit_detail = f"进程退出（code={code}）"
        self._proc_dead.set()
        self._closing.set()

    async def _notify_exit(self) -> None:
        if self._on_exit is None:
            return
        try:
            res = self._on_exit(self.exit_detail or "进程退出")
            if inspect.isawaitable(res):
                await res
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: on_exit 回调失败: %s", self.name, exc)

    async def stop(self) -> None:
        """优雅关闭：退出 ``async with`` 块即完成 SDK 的关连接/关 stdin/terminate/kill 链。"""
        task = self._task
        self._closing.set()
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=_SHUTDOWN_GRACE_SEC)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        elif task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self._task = None

    def _build_env(self) -> dict[str, str]:
        """陷阱 1：SDK 默认只保留 12 个环境变量，凭据全丢。默认全量继承 os.environ。"""
        base = dict(os.environ) if self.inherit_env else dict(acp.default_environment())
        base.update(self.env_extra)
        return base

    async def _drain_stderr(self, proc: Any) -> None:
        """陷阱 2：SDK 用 stderr=PIPE 启动却从不读取，写满即卡死。必须持续 drain。"""
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        try:
            while True:
                chunk = await stream.read(65536)
                if not chunk:
                    return
                text = chunk.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if not line.strip():
                        continue
                    self.stderr_tail.append(line[:500])
                    if len(self.stderr_tail) > STDERR_RING_LIMIT:
                        del self.stderr_tail[: len(self.stderr_tail) - STDERR_RING_LIMIT]
                    log.debug("%s[stderr] %s", self.name, line[:500])
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.debug("%s: stderr drain 结束: %s", self.name, exc)

    # ── 调用包装（SDK 无超时机制） ──

    async def _connection(self, stage: str, timeout: float) -> Any:
        if not self.running:
            await self.start(timeout=min(timeout, max(self.handshake_timeout_sec, 1.0)))
        if self._conn is None:
            raise AcpProcessGoneError(stage, f"{stage} 失败：{self.exit_detail or '进程不可用'}")
        return self._conn

    async def _request(self, stage: str, timeout: float, factory: Callable[[Any], Awaitable[Any]]) -> Any:
        conn = await self._connection(stage, timeout)
        started = time.monotonic()
        try:
            return await asyncio.wait_for(factory(conn), timeout=timeout)
        except asyncio.TimeoutError:
            waited = time.monotonic() - started
            message = f"{stage} 超时（已等 {waited:.1f}s / 上限 {timeout:.0f}s）"
            log.warning("%s: %s", self.name, message)
            raise AcpTimeoutError(stage, message, waited=waited) from None
        except ConnectionError as exc:
            raise AcpProcessGoneError(
                stage, f"{stage} 失败：{self.exit_detail or exc}", waited=time.monotonic() - started
            ) from exc

    # ── 出站方法 ──

    async def initialize(self) -> Any:
        response = await self._request(
            "initialize",
            self.handshake_timeout_sec,
            lambda conn: conn.initialize(
                protocol_version=acp.PROTOCOL_VERSION,
                client_capabilities=ClientCapabilities(),
                client_info=Implementation(name=CLIENT_NAME, version=CLIENT_VERSION),
            ),
        )
        self.protocol_version = getattr(response, "protocol_version", None)
        self.agent_info = getattr(response, "agent_info", None)
        self.auth_methods = list(getattr(response, "auth_methods", None) or [])
        self.agent_capabilities = getattr(response, "agent_capabilities", None)
        return response

    async def authenticate(self, method_id: str) -> Any:
        return await self._request(
            "authenticate", self.session_timeout_sec, lambda conn: conn.authenticate(method_id)
        )

    async def new_session(self, cwd: str) -> NewSessionResponse:
        return await self._request(
            "session/new",
            self.session_timeout_sec,
            lambda conn: conn.new_session(cwd=str(cwd), mcp_servers=[]),
        )

    async def load_session(self, cwd: str, session_id: str) -> Any:
        return await self._request(
            "session/load",
            self.session_timeout_sec,
            lambda conn: conn.load_session(cwd=str(cwd), session_id=str(session_id), mcp_servers=[]),
        )

    async def list_sessions(self, cwd: str | None = None) -> Any:
        return await self._request(
            "session/list", self.session_timeout_sec, lambda conn: conn.list_sessions(cwd=cwd)
        )

    async def set_config_option(self, config_id: str, session_id: str, value: Any) -> SetSessionConfigOptionResponse:
        return await self._request(
            "session/set_config_option",
            self.session_timeout_sec,
            lambda conn: conn.set_config_option(config_id, session_id, value),
        )

    async def close_session(self, session_id: str) -> Any:
        return await self._request(
            "session/close", self.session_timeout_sec, lambda conn: conn.close_session(str(session_id))
        )

    async def prompt(self, session_id: str, prompt: str, *, timeout: float) -> PromptResponse:
        # conn.prompt() 返回前会 drain 该 session 仍在传送的 session/update（SDK _SessionUpdateTracker），
        # 因此终态判定不会与最后一段流式输出竞争。
        return await self._request(
            "session/prompt",
            float(timeout),
            lambda conn: conn.prompt(str(session_id), [text_block(str(prompt))]),
        )

    async def cancel(self, session_id: str) -> None:
        """session/cancel 是通知，无应答；用短超时兜住写管道阻塞。"""
        await self._request(
            "session/cancel", min(self.session_timeout_sec, 15.0), lambda conn: conn.cancel(str(session_id))
        )

    # ── acp.Client 回调 ──

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        del kwargs
        if self._on_session_update is None:
            return
        try:
            res = self._on_session_update(session_id, update)
            if inspect.isawaitable(res):
                await res
        except Exception as exc:  # noqa: BLE001 - 回调异常不得打断读循环
            log.warning("%s: session_update 处理失败: %s", self.name, exc)

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[PermissionOption], **kwargs: Any
    ) -> RequestPermissionResponse:
        del kwargs
        if self._on_permission is None:
            # 禁止静默放行：没有处理者时明确拒绝
            log.warning("%s: 收到权限请求但没有处理者，按默认拒绝应答", self.name)
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return await self._on_permission(session_id, tool_call, list(options))

    # ── 未声明能力：保持 SDK 既有行为，但留痕（规格 §7.5 陷阱 4 / §16.1） ──

    async def _note_unhandled(self, method: str, params: Any) -> None:
        if self._on_unhandled is None:
            return
        try:
            res = self._on_unhandled(method, params)
            if inspect.isawaitable(res):
                await res
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: unhandled 记录失败: %s", self.name, exc)

    async def create_terminal(self, session_id: str, command: str, **kwargs: Any) -> None:
        """不声明 terminal 能力；SDK 默认对此路由静默返回 null，这里保持同样结果并留痕。"""
        del kwargs
        await self._note_unhandled("terminal/create", {"session_id": session_id, "command": command})
        return None

    async def terminal_output(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        del kwargs
        await self._note_unhandled("terminal/output", {"session_id": session_id, "terminal_id": terminal_id})
        return None

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kwargs: Any) -> None:
        del kwargs
        await self._note_unhandled(
            "terminal/wait_for_exit", {"session_id": session_id, "terminal_id": terminal_id}
        )
        return None

    async def release_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        await self._note_unhandled("terminal/release", {"session_id": session_id, "terminal_id": terminal_id})
        return {}

    async def kill_terminal(self, session_id: str, terminal_id: str, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        await self._note_unhandled("terminal/kill", {"session_id": session_id, "terminal_id": terminal_id})
        return {}

    async def read_text_file(self, session_id: str, path: str, **kwargs: Any) -> Any:
        """不声明 fs 能力：明确 method_not_found（诚实报错），并留痕。"""
        del kwargs
        await self._note_unhandled("fs/read_text_file", {"session_id": session_id, "path": path})
        raise acp.RequestError.method_not_found("fs/read_text_file")

    async def write_text_file(self, session_id: str, path: str, content: str, **kwargs: Any) -> Any:
        del kwargs
        await self._note_unhandled("fs/write_text_file", {"session_id": session_id, "path": path})
        raise acp.RequestError.method_not_found("fs/write_text_file")


__all__ = [
    "AcpBridge",
    "AcpBridgeError",
    "AcpProcessGoneError",
    "AcpTimeoutError",
    "RequestPermissionRequest",
    "SessionAccumulator",
    "STDIO_BUFFER_LIMIT_BYTES",
]
