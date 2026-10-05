"""agent-communicate 的任务模型：状态机、串行队列、事件累积、持久化、权限升级、终态通知。

规格依据：docs/agent-communicate-spec.md §9（任务模型）、§11（信封）、§12（权限）、§16（错误语义）。

分工：本模块只管任务语义与磁盘日志；协议细节在 acp_bridge，渲染在 vfs_surface。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from acp import RequestPermissionResponse
from acp.contrib.session_state import SessionAccumulator
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    AllowedOutcome,
    AvailableCommandsUpdate,
    CurrentModeUpdate,
    DeniedOutcome,
    PermissionOption,
    SessionNotification,
    ToolCallProgress,
    ToolCallStart,
)

from faust_backend.logger import get_logger

from agents import AgentRegistry, AgentRuntime

log = get_logger("faust.plugins.agent-communicate.tasks")

TERMINAL_STATUSES = frozenset({"completed", "cancelled", "timeout", "failed", "rejected", "interrupted"})

# 状态机（规格 §9.1）：终态不可逆
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"starting", "rejected", "cancelled", "interrupted"}),
    "starting": frozenset({"handshaking", "failed", "cancelled", "interrupted"}),
    "handshaking": frozenset({"session_pending", "failed", "cancelled", "interrupted"}),
    "session_pending": frozenset({"running", "failed", "cancelled", "interrupted"}),
    "running": frozenset(
        {"awaiting_permission", "completed", "cancelled", "timeout", "failed", "interrupted"}
    ),
    "awaiting_permission": frozenset({"running", "denied", "cancelled", "timeout", "failed", "interrupted"}),
    "denied": frozenset({"running", "cancelled", "timeout", "failed", "interrupted"}),
}

STATUS_LABELS = {
    "queued": "排队中",
    "starting": "启动中",
    "handshaking": "握手中",
    "session_pending": "建立会话中",
    "running": "运行中",
    "awaiting_permission": "等待权限裁决",
    "denied": "已按默认动作拒绝",
    "completed": "已完成",
    "cancelled": "已取消",
    "timeout": "超时",
    "failed": "失败",
    "rejected": "被拒绝",
    "interrupted": "中断",
}

NOTIFY_VALUES = ("batched", "normal", "none")
SESSION_VALUES = ("reuse", "new", "close")


class EnvelopeError(ValueError):
    """信封校验失败（write handler 直接抛它 → 工具输出即错误文本）。"""


class PermissionAnswerError(ValueError):
    """权限裁决写入内容非法。"""


# ============================================================
# 信封
# ============================================================

@dataclass
class Envelope:
    prompt: str
    cwd: str = ""
    session: str = "reuse"
    notify: str = "batched"
    config: dict[str, Any] = field(default_factory=dict)
    timeout_sec: float | None = None
    warnings: list[str] = field(default_factory=list)


def parse_envelope(raw: Any, *, default_cwd: str, default_notify: str, default_timeout: float) -> Envelope:
    """解析提交信封（规格 §11）。

    内容能解析为 JSON 对象 → 按字段解析；否则（纯文本 / 非法 JSON / 非对象 JSON）整体视为 prompt。
    """
    warnings: list[str] = []
    data: dict[str, Any] | None = None
    text_fallback: str | None = None

    if isinstance(raw, dict):
        data = raw
    else:
        text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            text_fallback = text
        else:
            if isinstance(parsed, dict):
                data = parsed
            else:
                # JSON 但不是对象（123 / "x" / [1]）→ 按纯文本 prompt，不报错
                text_fallback = text

    if data is None:
        prompt = str(text_fallback or "")
        if not prompt.strip():
            raise EnvelopeError("prompt 不能为空：写入内容既不是 JSON 对象，也不是非空文本")
        return Envelope(
            prompt=prompt,
            cwd=str(default_cwd),
            notify=str(default_notify),
            timeout_sec=float(default_timeout),
        )

    known = {"prompt", "cwd", "session", "notify", "config", "timeout_sec"}
    unknown = sorted(str(key) for key in data if str(key) not in known)
    if unknown:
        warnings.append(f"已忽略未知字段：{', '.join(unknown)}")

    prompt_raw = data.get("prompt")
    prompt = "" if prompt_raw is None else str(prompt_raw)
    if not prompt.strip():
        raise EnvelopeError("prompt 不能为空")

    cwd = str(data.get("cwd") or default_cwd).strip() or str(default_cwd)
    if not Path(cwd).is_dir():
        raise EnvelopeError(f"cwd 不是存在的目录：{cwd}")

    session = str(data.get("session") or "reuse").strip()
    if not session:
        session = "reuse"

    notify = str(data.get("notify") or default_notify).strip() or str(default_notify)
    if notify not in NOTIFY_VALUES:
        raise EnvelopeError(f"notify 非法值 `{notify}`；合法值：{', '.join(NOTIFY_VALUES)}")

    config = data.get("config") or {}
    if not isinstance(config, dict):
        raise EnvelopeError("config 必须是对象，例如 {\"model\": \"...\"}")

    timeout_raw = data.get("timeout_sec")
    timeout_sec: float = float(default_timeout)
    if timeout_raw is not None and timeout_raw != "":
        try:
            timeout_sec = float(timeout_raw)
        except (TypeError, ValueError):
            raise EnvelopeError(f"timeout_sec 必须是正数，收到 {timeout_raw!r}") from None
        if timeout_sec <= 0:
            raise EnvelopeError("timeout_sec 必须 > 0")

    return Envelope(
        prompt=prompt,
        cwd=cwd,
        session=session,
        notify=notify,
        config={str(k): v for k, v in config.items()},
        timeout_sec=timeout_sec,
        warnings=warnings,
    )


# ============================================================
# 权限裁决映射
# ============================================================

def select_permission_option(
    options: list[PermissionOption], outcome: str, scope: str
) -> tuple[PermissionOption | None, str]:
    """按 kind 前缀匹配 ACP option（规格 §12.2），返回 (选项, 容错说明)。

    `kind` 缺失（SDK 模型理论上不允许，但防御性处理）或无可匹配项时取 ``options[0]`` 并标注。
    """
    prefix = "allow" if outcome == "allow" else "reject"
    suffix = "_once" if scope == "once" else "_always"
    if not options:
        return None, "Agent 未提供任何权限选项"
    with_kind = [item for item in options if str(getattr(item, "kind", "") or "")]
    if not with_kind:
        return options[0], "Agent 未提供 kind，已取第一个选项"
    for item in with_kind:
        if str(item.kind) == prefix + suffix:
            return item, ""
    for item in with_kind:
        if str(item.kind).startswith(prefix + "_"):
            return item, f"Agent 未提供 kind={prefix + suffix}，已退化为 {item.kind}"
    return options[0], f"Agent 未提供 {prefix}_* 选项，已取第一个选项（kind={getattr(options[0], 'kind', None)}）"


def build_permission_response(
    options: list[PermissionOption], outcome: str, scope: str
) -> tuple[RequestPermissionResponse, str, str | None]:
    """构造 ACP 应答；返回 (应答, 说明, 采纳的 optionId)。"""
    option, note = select_permission_option(options, outcome, scope)
    if option is None:
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled")), note, None
    option_id = str(getattr(option, "option_id", "") or "")
    return (
        RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=option_id)),
        note,
        option_id,
    )


def parse_permission_decision(raw: Any) -> tuple[str, str, str]:
    """解析裁决写入，返回 (outcome, scope, reason)。"""
    text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    stripped = str(text or "").strip()
    lowered = stripped.lower()
    if lowered in ("allow", "deny"):
        return lowered, "once", ""
    try:
        data = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        raise PermissionAnswerError(
            "裁决内容既不是 JSON 对象，也不是 allow/deny 简写。"
            '正确格式：{"outcome": "allow"|"deny", "scope": "once"|"always", "reason": "可选"}'
        ) from None
    if not isinstance(data, dict):
        raise PermissionAnswerError('裁决内容必须是 JSON 对象：{"outcome": "allow", "scope": "once"}')
    outcome = str(data.get("outcome") or "").strip().lower()
    if outcome not in ("allow", "deny"):
        raise PermissionAnswerError(f"outcome 非法值 `{outcome}`；合法值：allow / deny")
    scope = str(data.get("scope") or "once").strip().lower()
    if scope not in ("once", "always"):
        raise PermissionAnswerError(f"scope 非法值 `{scope}`；合法值：once / always")
    return outcome, scope, str(data.get("reason") or "")


# ============================================================
# 任务与权限请求
# ============================================================

@dataclass
class Task:
    id: str
    agent: str
    prompt: str
    cwd: str
    session_mode: str = "reuse"
    notify: str = "batched"
    config: dict[str, Any] = field(default_factory=dict)
    timeout_sec: float = 1800.0
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    session_id: str | None = None
    result_text: str = ""
    stop_reason: str | None = None
    error: str | None = None
    queue_depth: int = 0
    output_parts: list[str] = field(default_factory=list)
    event_lines: list[str] = field(default_factory=list)
    permission_count: int = 0
    config_notes: list[str] = field(default_factory=list)
    accumulator: SessionAccumulator = field(default_factory=SessionAccumulator)

    # ── 派生视图 ──

    @property
    def elapsed_sec(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def output_text(self) -> str:
        return "".join(self.output_parts)

    @property
    def events_text(self) -> str:
        return "\n".join(self.event_lines)

    def result_body(self) -> str:
        if self.result_text:
            return self.result_text
        if self.status in ("failed", "timeout", "rejected", "interrupted", "cancelled"):
            return f"（无回复正文）\n状态：{STATUS_LABELS.get(self.status, self.status)}\n原因：{self.error or '-'}"
        return ""

    def prompt_summary(self, limit: int = 60) -> str:
        text = " ".join(str(self.prompt or "").split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def add_event(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.event_lines.append(f"[{stamp}] {text}")


@dataclass
class PermissionRequest:
    request_id: str
    agent: str
    session_id: str
    task_id: str | None
    tool_title: str
    tool_kind: str | None
    summary: str
    options: list[PermissionOption]
    timeout_sec: float
    created_at: float = field(default_factory=time.time)
    status: str = "pending"
    decision: dict[str, Any] | None = None
    note: str = ""
    selected_option_id: str | None = None
    future: asyncio.Future | None = None

    @property
    def deadline_ts(self) -> float:
        return self.created_at + max(0.0, self.timeout_sec)

    @property
    def remaining_sec(self) -> float:
        return max(0.0, self.deadline_ts - time.time())

    def option_rows(self) -> list[dict[str, str]]:
        return [
            {
                "option_id": str(getattr(item, "option_id", "") or ""),
                "name": str(getattr(item, "name", "") or ""),
                "kind": str(getattr(item, "kind", "") or ""),
            }
            for item in self.options
        ]


def _tool_call_summary(tool_call: Any) -> tuple[str, str | None, str]:
    """从 ToolCallUpdate 提取 (标题, kind, 参数摘要)。"""
    title = str(getattr(tool_call, "title", "") or getattr(tool_call, "tool_call_id", "") or "未知工具调用")
    kind = getattr(tool_call, "kind", None)
    raw_input = getattr(tool_call, "raw_input", None)
    summary = ""
    if raw_input is not None:
        try:
            summary = json.dumps(raw_input, ensure_ascii=False)
        except (TypeError, ValueError):
            summary = str(raw_input)
    if not summary:
        locations = getattr(tool_call, "locations", None) or []
        paths = [str(getattr(item, "path", "") or "") for item in locations]
        summary = ", ".join(item for item in paths if item)
    if len(summary) > 400:
        summary = summary[:400] + "…"
    return title, (str(kind) if kind else None), summary


def _content_text(block: Any) -> str:
    text = getattr(block, "text", None)
    if isinstance(text, str):
        return text
    return ""


# ============================================================
# 任务仓库
# ============================================================

class TaskStore:
    """全部任务的状态与持久化；队列为"每 Agent 一条串行"，不同 Agent 并行。"""

    def __init__(
        self,
        *,
        data_dir: Path,
        registry: AgentRegistry,
        enqueue_trigger: Callable[[dict], Any] | None = None,
        on_permission_node: Callable[[str, str, bool], Any] | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.registry = registry
        self._enqueue_trigger = enqueue_trigger
        self._on_permission_node = on_permission_node

        self._tasks: dict[str, Task] = {}
        self._order: list[str] = []
        self._queues: dict[str, list[str]] = {}
        self._workers: dict[str, asyncio.Task] = {}
        self._current: dict[str, str] = {}
        self._session_tasks: dict[str, str] = {}
        self._permissions: dict[str, PermissionRequest] = {}
        self._counter = 0
        self._perm_counter = 0
        self._io_lock = threading.Lock()

        self.transcripts_dir = self.data_dir / "transcripts"
        self.log_path = self.data_dir / "tasks.jsonl"

    # ── 配置读取 ──

    @property
    def max_queue(self) -> int:
        return max(1, int(self.registry.settings.max_queue))

    @property
    def notify_default(self) -> str:
        return str(self.registry.settings.notify_default or "batched")

    @property
    def record_thoughts(self) -> bool:
        return bool(self.registry.settings.record_thoughts)

    # ── 持久化 ──

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        """小体量追加写；用进程内锁串行化，避免并发 append 互相撕裂。"""
        line = json.dumps(payload, ensure_ascii=False, default=str)
        with self._io_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _log_task(self, task: Task) -> None:
        self._append_jsonl(self.log_path, task_snapshot(task))

    def _log_transcript(self, task: Task, update: Any) -> None:
        payload = {"ts": time.time(), "task_id": task.id}
        dump = getattr(update, "model_dump", None)
        if callable(dump):
            try:
                payload["update"] = dump(mode="json", by_alias=True, exclude_none=True)
            except Exception:  # noqa: BLE001
                payload["update"] = str(update)
        else:
            payload["update"] = str(update)
        self._append_jsonl(self.transcripts_dir / f"{task.id}.jsonl", payload)

    def restore(self, limit: int) -> list[Task]:
        """启动时从 tasks.jsonl 恢复最近 limit 条任务；非终态一律改写为 interrupted。"""
        if not self.log_path.exists():
            return []
        latest: dict[str, dict[str, Any]] = {}
        try:
            with self.log_path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        item = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(item, dict) and item.get("id"):
                        latest[str(item["id"])] = item
        except OSError as exc:
            log.warning("读取 tasks.jsonl 失败: %s", exc)
            return []

        rows = sorted(latest.values(), key=lambda item: float(item.get("created_at") or 0.0), reverse=True)
        restored: list[Task] = []
        max_index = 0
        for row in rows[: max(0, int(limit))]:
            task = task_from_snapshot(row)
            max_index = max(max_index, _index_of(task.id))
            if task.id in self._tasks:
                # 同一个 store 上重复 restore（例如 startup 被调用两次）：不重复挂载
                continue
            if not task.is_terminal:
                task.status = "interrupted"
                task.error = "插件重载/后端重启导致中断"
                task.finished_at = time.time()
                task.add_event("恢复时该任务未处于终态 → 改写为 interrupted（不伪造完成）")
                self._log_task(task)
            self._tasks[task.id] = task
            self._order.append(task.id)
            restored.append(task)
        self._counter = max(self._counter, max_index)
        return restored

    def next_task_id(self) -> str:
        self._counter += 1
        return f"task_{self._counter}"

    # ── 提交与队列 ──

    def queue_depth(self, agent: str) -> int:
        return len(self._queues.get(agent) or [])

    def submit(self, agent: str, envelope: Envelope) -> Task:
        """创建任务并入队；队列满则创建 rejected 任务（不抛异常，由 ACK 说明）。"""
        runtime = self.registry.get(agent)
        if runtime is None:
            raise EnvelopeError(f"未注册的 Agent `{agent}`；可用：{', '.join(self.registry.agent_names()) or '(无)'}")

        depth = self.queue_depth(agent)
        task = Task(
            id=self.next_task_id(),
            agent=agent,
            prompt=envelope.prompt,
            cwd=envelope.cwd or runtime.cwd,
            session_mode=envelope.session,
            notify=envelope.notify or self.notify_default,
            config=dict(envelope.config),
            timeout_sec=float(envelope.timeout_sec or runtime.settings.task_timeout_sec),
        )
        if depth >= self.max_queue:
            task.status = "rejected"
            task.error = f"队列已满 ({depth}/{self.max_queue})"
            task.finished_at = time.time()
            task.add_event(f"拒绝：队列已满 ({depth}/{self.max_queue})")
        else:
            task.queue_depth = depth + 1
            task.add_event("已入队")
        self._tasks[task.id] = task
        self._order.append(task.id)
        self._log_task(task)
        if not task.is_terminal:
            self._queues.setdefault(agent, []).append(task.id)
            self._ensure_worker(agent)
        return task

    def _ensure_worker(self, agent: str) -> None:
        worker = self._workers.get(agent)
        if worker is not None and not worker.done():
            return
        self._workers[agent] = asyncio.create_task(self._worker_loop(agent), name=f"acp-task-{agent}")

    async def _worker_loop(self, agent: str) -> None:
        try:
            while True:
                queue = self._queues.get(agent) or []
                if not queue:
                    break
                task_id = queue.pop(0)
                task = self._tasks.get(task_id)
                if task is None or task.status != "queued":
                    continue
                try:
                    await self._run_task(task)
                except asyncio.CancelledError:
                    await self._finish(task, "cancelled", "worker 被取消")
                    raise
                except Exception as exc:  # noqa: BLE001 - 单任务异常不得拖垮队列
                    log.exception("%s: 任务 %s 异常", agent, task.id)
                    await self._finish(task, "failed", f"{type(exc).__name__}: {exc}")
        finally:
            self._workers.pop(agent, None)
            if self._queues.get(agent):
                self._ensure_worker(agent)

    # ── 任务执行 ──

    async def _run_task(self, task: Task) -> None:
        runtime = self.registry.get(task.agent)
        if runtime is None:
            await self._finish(task, "failed", f"Agent `{task.agent}` 已不可用")
            return
        # 同 Agent 串行，因此这里不会有"上一个任务"需要恢复
        self._current[task.agent] = task.id
        task.queue_depth = max(0, self.queue_depth(task.agent))
        try:
            try:
                await asyncio.wait_for(self._execute(task, runtime), timeout=task.timeout_sec)
            except asyncio.TimeoutError:
                await self._cancel_remote(runtime, task)
                await self._finish(task, "timeout", f"任务超过硬上限 {task.timeout_sec:.0f}s")
        finally:
            if self._current.get(task.agent) == task.id:
                self._current.pop(task.agent, None)

    async def _execute(self, task: Task, runtime: AgentRuntime) -> None:
        task.started_at = task.started_at or time.time()
        self._transition(task, "starting")
        self._log_task(task)

        # spawn（进程不存在则启动）
        self._transition(task, "handshaking")
        try:
            await runtime.ensure_initialized()
        except Exception as exc:  # noqa: BLE001 - 阶段失败要如实落到任务状态
            await self._finish(task, "failed", _stage_error("会话握手", exc))
            return

        # session/new 或 load
        self._transition(task, "session_pending")
        mode = task.session_mode if task.session_mode in SESSION_VALUES else "load"
        load_id = None if task.session_mode in SESSION_VALUES else task.session_mode
        try:
            session_id = await runtime.ensure_session(cwd=task.cwd, mode=mode, session_id=load_id)
        except Exception as exc:  # noqa: BLE001
            await self._finish(task, "failed", _stage_error("建立会话", exc))
            return
        task.session_id = session_id
        self._session_tasks[session_id] = task.id
        try:
            if task.config:
                try:
                    task.config_notes = await runtime.apply_config(task.config)
                except Exception as exc:  # noqa: BLE001
                    await self._finish(task, "failed", _stage_error("下发 config", exc))
                    return
                task.add_event("会话配置：" + ", ".join(task.config_notes))
                self._log_task(task)

            self._transition(task, "running")
            task.add_event(f"session={session_id} 开始执行（timeout={task.timeout_sec:.0f}s）")
            self._log_task(task)
            remaining = max(1.0, task.timeout_sec - task.elapsed_sec)
            response = await runtime.bridge.prompt(session_id, task.prompt, timeout=remaining)
        except Exception as exc:  # noqa: BLE001
            await self._finish(task, "failed", _stage_error("任务执行", exc), session_id=session_id)
            return
        finally:
            if self._session_tasks.get(session_id) == task.id:
                self._session_tasks.pop(session_id, None)

        stop_reason = str(getattr(response, "stop_reason", "") or "")
        task.stop_reason = stop_reason or None
        if task.is_terminal:
            return
        if stop_reason == "cancelled":
            await self._finish(task, "cancelled", "外部 Agent 报告 stopReason=cancelled")
            return
        text = task.output_text.strip()
        if not text:
            task.add_event("prompt 返回但没有任何正文输出")
        task.result_text = text
        await self._finish(task, "completed", None)

    async def _cancel_remote(self, runtime: AgentRuntime, task: Task) -> None:
        if not task.session_id or not runtime.running:
            return
        try:
            await runtime.bridge.cancel(task.session_id)
        except Exception as exc:  # noqa: BLE001
            task.add_event(f"session/cancel 失败：{exc}")

    # ── 状态迁移 ──

    def _transition(self, task: Task, status: str) -> bool:
        if task.is_terminal:
            return False
        if status == task.status:
            return True
        allowed = VALID_TRANSITIONS.get(task.status, frozenset())
        if status not in allowed:
            log.warning("任务 %s 非法迁移 %s → %s（已忽略）", task.id, task.status, status)
            return False
        task.status = status
        if status == "running" and task.started_at is None:
            task.started_at = time.time()
        return True

    async def _finish(
        self, task: Task, status: str, error: str | None, *, session_id: str | None = None
    ) -> None:
        """落到终态。终态不可逆：已是终态则直接返回，不覆盖既有结论。"""
        if task.is_terminal or status not in TERMINAL_STATUSES:
            return
        task.status = status
        task.error = error
        task.finished_at = time.time()
        if session_id:
            task.session_id = session_id
        task.add_event(f"终态：{task.status_label()}" + (f"（{error}）" if error else ""))
        if status == "completed" and not task.result_text:
            task.result_text = task.output_text.strip()
        # 清理该任务仍挂着的待决权限（超时/取消/进程死亡都不能静默挂着）
        for request in self.pending_permissions(task.agent):
            if request.task_id == task.id:
                await self._settle_pending(request, f"任务 {task.id} 已进入 {task.status_label()}，请求作废")
        self._log_task(task)
        await self._notify_terminal(task)

    async def cancel(self, agent: str, task_id: str | None = None) -> tuple[bool, str]:
        """取消当前任务或指定任务（VFS cancel 节点）。"""
        target_id = str(task_id or "").strip() or self._current.get(agent) or ""
        if not target_id:
            queue = self._queues.get(agent) or []
            target_id = queue[0] if queue else ""
        task = self._tasks.get(target_id)
        if task is None:
            return False, f"没有可取消的任务（agent={agent}）"
        if task.is_terminal:
            return False, f"任务 {task.id} 已处于终态：{task.status_label()}"
        if task.status == "queued":
            queue = self._queues.get(task.agent) or []
            if task.id in queue:
                queue.remove(task.id)
            await self._finish(task, "cancelled", "在排队阶段被取消")
            return True, f"已取消排队中的 {task.id}"
        runtime = self.registry.get(task.agent)
        if runtime is not None and task.session_id:
            await self._cancel_remote(runtime, task)
        await self._finish(task, "cancelled", "被 cancel 节点取消")
        return True, f"已发送取消并标记 {task.id} 为 cancelled"

    # ── 查询 ──

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(str(task_id or ""))

    def tasks_of(self, agent: str) -> list[Task]:
        return [self._tasks[item] for item in self._order if self._tasks[item].agent == agent]

    def recent(self, limit: int = 20) -> list[Task]:
        return [self._tasks[item] for item in reversed(self._order[-limit:])]

    def current_task(self, agent: str) -> Task | None:
        task_id = self._current.get(agent)
        return self._tasks.get(task_id) if task_id else None

    def agent_summary(self, agent: str) -> dict[str, Any]:
        tasks = self.tasks_of(agent)
        return {
            "total": len(tasks),
            "queue_depth": self.queue_depth(agent),
            "current": (self.current_task(agent).id if self.current_task(agent) else None),
            "last_finished": next((item for item in reversed(tasks) if item.is_terminal), None),
        }

    # ── 事件累积（session_update 回调入口） ──

    def make_session_update_handler(self) -> Callable[[str, Any], Awaitable[None]]:
        async def handler(session_id: str, update: Any) -> None:
            await self.on_session_update(session_id, update)

        return handler

    async def on_session_update(self, session_id: str, update: Any) -> None:
        runtime = next(
            (item for item in self.registry.runtimes() if item.session_id == session_id), None
        )
        if isinstance(update, AvailableCommandsUpdate):
            commands = list(getattr(update, "available_commands", None) or [])
            if runtime is not None:
                runtime.on_commands_update(commands)
        elif isinstance(update, CurrentModeUpdate):
            if runtime is not None:
                runtime.on_mode_update(getattr(update, "current_mode_id", None))

        task_id = self._session_tasks.get(session_id)
        task = self._tasks.get(task_id) if task_id else None
        if task is None:
            return
        if runtime is not None:
            runtime.touch()

        try:
            task.accumulator.apply(SessionNotification(session_id=session_id, update=update))
        except Exception as exc:  # noqa: BLE001 - 累积器异常不得打断事件记录
            log.debug("SessionAccumulator 拒绝了一条 update: %s", exc)

        self._log_transcript(task, update)

        if isinstance(update, AgentMessageChunk):
            text = _content_text(getattr(update, "content", None))
            if text:
                task.output_parts.append(text)
            return
        if isinstance(update, AgentThoughtChunk):
            if self.record_thoughts:
                text = _content_text(getattr(update, "content", None))
                if text:
                    task.add_event(f"[思考] {text}")
            return
        if isinstance(update, ToolCallStart):
            title = str(getattr(update, "title", "") or "")
            kind = getattr(update, "kind", None)
            tool_id = str(getattr(update, "tool_call_id", "") or "")
            task.add_event(f"[工具] {title or tool_id}（kind={kind}, status={getattr(update, 'status', None)}）")
            return
        if isinstance(update, ToolCallProgress):
            tool_id = str(getattr(update, "tool_call_id", "") or "")
            status = getattr(update, "status", None)
            title = str(getattr(update, "title", "") or "")
            task.add_event(f"[工具更新] {title or tool_id} → {status}")
            return
        if isinstance(update, AvailableCommandsUpdate):
            names = ", ".join(
                str(getattr(item, "name", "") or "")
                for item in (getattr(update, "available_commands", None) or [])
            )
            task.add_event(f"[命令列表] {names}")
            return
        if isinstance(update, CurrentModeUpdate):
            task.add_event(f"[模式] {getattr(update, 'current_mode_id', None)}")
            return
        task.add_event(f"[事件] {type(update).__name__}")

    def note_unhandled(self, agent: str, method: str, params: Any) -> None:
        """记录未声明能力的入站调用（规格 §7.5 陷阱 4 / §16.1）。"""
        del params
        task = self.current_task(agent)
        line = f"[未声明能力] 收到 {method}；v1 未声明该能力"
        if method.startswith("terminal/"):
            line += "（SDK 对该路由静默返回 null，外部 Agent 不会收到明确的拒绝）"
        else:
            line += "（已按 method_not_found 明确拒绝）"
        if task is not None:
            task.add_event(line)
        log.info("%s %s", agent or "-", line)

    # ── 权限升级 ──

    def permission_count(self, agent: str) -> int:
        return len(self.pending_permissions(agent))

    def pending_permissions(self, agent: str) -> list[PermissionRequest]:
        return [
            item
            for item in self._permissions.values()
            if item.agent == agent and item.status == "pending"
        ]

    def get_permission(self, request_id: str) -> PermissionRequest | None:
        return self._permissions.get(str(request_id or ""))

    def all_permissions(self, agent: str) -> list[PermissionRequest]:
        return [item for item in self._permissions.values() if item.agent == agent]

    async def on_permission_request(
        self, agent: str, session_id: str, tool_call: Any, options: list[PermissionOption]
    ) -> RequestPermissionResponse:
        """bridge 的 request_permission 回调：登记 → 挂节点 → 唤醒主 Agent → 有界等待。"""
        self._perm_counter += 1
        request_id = f"req_{self._perm_counter}"
        title, kind, summary = _tool_call_summary(tool_call)
        runtime = self.registry.existing(agent)
        timeout_sec = float(
            runtime.settings.permission_timeout_sec if runtime else self.registry.settings.permission_timeout_sec
        )
        task_id = self._session_tasks.get(session_id)
        task = self._tasks.get(task_id) if task_id else None

        request = PermissionRequest(
            request_id=request_id,
            agent=agent,
            session_id=session_id,
            task_id=task_id,
            tool_title=title,
            tool_kind=kind,
            summary=summary,
            options=list(options),
            timeout_sec=timeout_sec,
        )
        request.future = asyncio.get_running_loop().create_future()
        self._permissions[request_id] = request
        if task is not None and not task.is_terminal:
            task.permission_count += 1
            task.add_event(f"[权限] {request_id} {title}（等待裁决，{timeout_sec:.0f}s 后默认动作）")
            self._transition(task, "awaiting_permission")

        await self._set_permission_node(agent, request_id, present=True)
        await self._wake_for_permission(request)

        try:
            decision = await asyncio.wait_for(asyncio.shield(request.future), timeout=timeout_sec)
        except asyncio.TimeoutError:
            default_outcome = str(self.registry.settings.permission_default or "deny").lower()
            if default_outcome not in ("allow", "deny"):
                default_outcome = "deny"
            decision = {"outcome": default_outcome, "scope": "once", "reason": "", "defaulted": True}
        except asyncio.CancelledError:
            await self._settle_pending(request, "连接关闭，权限请求作废")
            raise
        return await self._apply_decision(request, decision)

    async def _apply_decision(
        self, request: PermissionRequest, decision: dict[str, Any]
    ) -> RequestPermissionResponse:
        outcome = str(decision.get("outcome") or "deny")
        scope = str(decision.get("scope") or "once")
        defaulted = bool(decision.get("defaulted"))
        reason = str(decision.get("reason") or "")
        response, note, option_id = build_permission_response(request.options, outcome, scope)

        request.status = "defaulted" if defaulted else "answered"
        request.decision = {"outcome": outcome, "scope": scope, "reason": reason, "defaulted": defaulted}
        request.note = note
        request.selected_option_id = option_id

        task = self._tasks.get(request.task_id) if request.task_id else None
        if task is not None and not task.is_terminal:
            label = "超时默认动作" if defaulted else "裁决"
            task.add_event(
                f"[权限] {request.request_id} {label}={outcome}/{scope} → option={option_id}"
                + (f"（{note}）" if note else "")
                + (f"；reason={reason}（仅本地留痕）" if reason else "")
            )
            if task.status == "awaiting_permission":
                if defaulted:
                    self._transition(task, "denied")
                self._transition(task, "running")
            elif task.status == "denied":
                self._transition(task, "running")
        self._mount_permission_node_sync(request.agent, request.request_id, present=False)
        log.info(
            "%s: 权限 %s → %s/%s（option=%s%s）",
            request.agent,
            request.request_id,
            outcome,
            scope,
            option_id,
            "，超时默认动作" if defaulted else "",
        )
        return response

    def _mount_permission_node_sync(self, agent: str, request_id: str, *, present: bool) -> None:
        """同步上下文调用：把挂载/卸载排到事件循环上。"""
        if self._on_permission_node is None:
            return
        try:
            result = self._on_permission_node(agent, request_id, present)
            if inspect.isawaitable(result):
                asyncio.get_running_loop().create_task(result)
        except Exception as exc:  # noqa: BLE001
            log.warning("挂载/卸载权限节点失败 %s/%s: %s", agent, request_id, exc)

    async def _settle_pending(self, request: PermissionRequest, note: str) -> None:
        """任务结束/连接关闭时作废仍挂着的请求。"""
        request.status = "void"
        request.note = note
        future = request.future
        if future is not None and not future.done():
            future.set_result({"outcome": "deny", "scope": "once", "reason": "", "defaulted": True})
        await self._set_permission_node(request.agent, request.request_id, present=False)

    def answer_permission(self, agent: str, request_id: str, raw: Any) -> str:
        """VFS permissions/{rid}.md 的 write handler 入口；返回给 Agent 的 ACK。"""
        request = self._permissions.get(str(request_id or ""))
        if request is None or request.agent != agent:
            raise PermissionAnswerError(f"没有待决权限请求 {agent}/{request_id}（可能已过期或被清理）")
        if request.status == "pending":
            outcome, scope, reason = parse_permission_decision(raw)
            # ACK 里的"已采纳项"与应答使用同一个映射函数，因此两者必然一致
            _, note, option_id = build_permission_response(request.options, outcome, scope)
            future = request.future
            if future is not None and not future.done():
                future.set_result({"outcome": outcome, "scope": scope, "reason": reason})
            ack = f"已采纳裁决 {request_id}：{outcome}/{scope} → option={option_id}"
            if note:
                ack += f"\n   ⚠ {note}"
            if reason:
                ack += f"\n   reason 仅本地留痕：{reason}"
            return ack
        if request.status == "void":
            return f"该请求已作废（{request.note}），本次裁决未生效"
        decision = request.decision or {}
        return (
            f"该请求已按{'超时默认动作' if decision.get('defaulted') else '先前裁决'}处理："
            f"{decision.get('outcome')}/{decision.get('scope')}（option={request.selected_option_id}），"
            "本次裁决未改变已发出的应答"
        )

    def answer_permission_direct(
        self, agent: str, request_id: str, outcome: str, scope: str
    ) -> str:
        """前端直接裁决入口（等价于写节点，但入参已是结构化字段）。"""
        return self.answer_permission(
            agent, request_id, json.dumps({"outcome": outcome, "scope": scope}, ensure_ascii=False)
        )

    async def _set_permission_node(self, agent: str, request_id: str, *, present: bool) -> None:
        if self._on_permission_node is None:
            return
        try:
            result = self._on_permission_node(agent, request_id, present)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001
            log.warning("挂载/卸载权限节点失败 %s/%s: %s", agent, request_id, exc)

    async def _wake_for_permission(self, request: PermissionRequest) -> None:
        summary = request.tool_title or request.summary or "未知工具"
        text = (
            f"{request.agent} 请求权限裁决：{summary}"
            f"（reqId={request.request_id}，{request.timeout_sec:.0f}s 后默认动作）。"
            f"请 read faustbot://agents/{request.agent}/permissions/{request.request_id}.md 后 write 裁决。"
        )
        payload = _trigger_payload(
            trigger_id=f"agent-communicate::perm::{request.agent}::{request.request_id}",
            text=text,
            priority="normal",
        )
        await self._emit_trigger(payload)

    # ── 终态通知 ──

    async def _notify_terminal(self, task: Task) -> None:
        """任务进终态时按 notify 决定是否唤醒主 Agent（缺省取 notify_default）。"""
        notify = str(task.notify or self.notify_default).lower()
        if notify == "none":
            return
        priority = "batched" if notify == "batched" else "normal"
        text = (
            f"{task.agent} 的任务 {task.id} {task.status_label()}"
            f"（耗时 {task.elapsed_sec:.0f}s，{task.prompt_summary()}）。"
            f"结果读取：faustbot://agents/{task.agent}/tasks/{task.id}/result.md"
        )
        if task.status != "completed" and task.error:
            text += f"；原因：{task.error}"
        payload = _trigger_payload(
            trigger_id=f"agent-communicate::task::{task.id}", text=text, priority=priority
        )
        await self._emit_trigger(payload)

    def _default_enqueue(self, payload: dict[str, Any]) -> Any:
        """生产路径：调用 trigger_manager.append_trigger（按属性查找，便于测试替换）。"""
        from faust_backend import trigger_manager

        return trigger_manager.append_trigger(payload)

    async def _emit_trigger(self, payload: dict[str, Any]) -> None:
        enqueue = self._enqueue_trigger or self._default_enqueue
        try:
            result = enqueue(payload)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:  # noqa: BLE001 - 通知失败不得影响任务终态
            log.warning("入队触发器失败（%s）: %s", payload.get("id"), exc)

    # ── 生命周期 ──

    async def shutdown(self) -> None:
        for request in list(self._permissions.values()):
            if request.status == "pending":
                await self._settle_pending(request, "插件卸载，权限请求作废")
        for worker in list(self._workers.values()):
            worker.cancel()
        for worker in list(self._workers.values()):
            await asyncio.gather(worker, return_exceptions=True)
        self._workers.clear()


# ============================================================
# 辅助
# ============================================================

def _index_of(task_id: str) -> int:
    try:
        return int(str(task_id).rsplit("_", 1)[1])
    except (IndexError, ValueError):
        return 0


def _stage_error(stage: str, exc: Exception) -> str:
    from acp_bridge import AcpBridgeError

    if isinstance(exc, AcpBridgeError):
        waited = f"，已等 {exc.waited:.1f}s" if exc.waited is not None else ""
        return f"{stage}失败（阶段 {exc.stage}{waited}）：{exc}"
    return f"{stage}失败：{type(exc).__name__}: {exc}"


def _trigger_payload(*, trigger_id: str, text: str, priority: str) -> dict[str, Any]:
    """一次性唤醒：datetime 触发器 target=now，watchdog 下一拍（≤0.5s）取出并自动移除。"""
    return {
        "id": trigger_id,
        "type": "datetime",
        "target": datetime.now().isoformat(),
        "priority": priority if priority in ("interrupt", "normal", "batched") else "normal",
        "recall_description": text,
        "run_background": False,
    }


def task_snapshot(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "agent": task.agent,
        "prompt": task.prompt,
        "cwd": task.cwd,
        "session_mode": task.session_mode,
        "notify": task.notify,
        "config": task.config,
        "timeout_sec": task.timeout_sec,
        "status": task.status,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "session_id": task.session_id,
        "result_text": task.result_text,
        "stop_reason": task.stop_reason,
        "error": task.error,
        "queue_depth": task.queue_depth,
        "output": task.output_text,
        "events": task.event_lines,
        "permission_count": task.permission_count,
        "config_notes": task.config_notes,
    }


def task_from_snapshot(row: dict[str, Any]) -> Task:
    task = Task(
        id=str(row.get("id")),
        agent=str(row.get("agent") or ""),
        prompt=str(row.get("prompt") or ""),
        cwd=str(row.get("cwd") or ""),
        session_mode=str(row.get("session_mode") or "reuse"),
        notify=str(row.get("notify") or "batched"),
        config=dict(row.get("config") or {}),
        timeout_sec=float(row.get("timeout_sec") or 1800),
        status=str(row.get("status") or "queued"),
        created_at=float(row.get("created_at") or time.time()),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
        session_id=row.get("session_id"),
        result_text=str(row.get("result_text") or ""),
        stop_reason=row.get("stop_reason"),
        error=row.get("error"),
        queue_depth=int(row.get("queue_depth") or 0),
        output_parts=[str(row.get("output") or "")] if row.get("output") else [],
        event_lines=[str(item) for item in (row.get("events") or [])],
        permission_count=int(row.get("permission_count") or 0),
        config_notes=[str(item) for item in (row.get("config_notes") or [])],
    )
    return task


__all__ = [
    "Envelope",
    "EnvelopeError",
    "PermissionAnswerError",
    "PermissionRequest",
    "STATUS_LABELS",
    "TERMINAL_STATUSES",
    "Task",
    "TaskStore",
    "VALID_TRANSITIONS",
    "build_permission_response",
    "parse_envelope",
    "parse_permission_decision",
    "select_permission_option",
    "task_from_snapshot",
    "task_snapshot",
]
