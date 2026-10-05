"""agent-communicate 的 Agent 注册、自动探测与运行时会话管理。

规格依据：docs/agent-communicate-spec.md §8（注册与探测）、§7（超时/认证）。

- 探测只做 **PATH 查找 + `<cmd> --help` 静态校验（≤5s）**，绝不跑活握手
  （omp 的 `session/new` 实测 >50s 无响应，会拖死插件加载路径）。
- 用户显式配置优先于自动发现；`enabled: false` 屏蔽。
- 懒启动：本模块不负责 spawn 时机，只负责"要进程时确保有进程"。
"""

from __future__ import annotations

import asyncio
import json
import shutil
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger

from acp_bridge import AcpBridge, AcpBridgeError

log = get_logger("faust.plugins.agent-communicate.agents")

DISCOVER_HELP_TIMEOUT_SEC = 5.0
DISCOVER_CACHE_KEY = "discover_results"

# 超时缺省（规格 §7.7）
DEFAULT_HANDSHAKE_TIMEOUT_SEC = 30.0
DEFAULT_SESSION_TIMEOUT_SEC = 60.0
DEFAULT_TASK_TIMEOUT_SEC = 1800.0
DEFAULT_PERMISSION_TIMEOUT_SEC = 300.0


@dataclass(frozen=True)
class Candidate:
    name: str
    command: str
    acp_args: tuple[str, ...]
    description: str


# v1 只认这两项（用户决议）
CANDIDATES: tuple[Candidate, ...] = (
    Candidate("opencode", "opencode", ("acp",), "本机 opencode CLI（ACP 模式）"),
    Candidate("omp", "omp", ("acp",), "本机 omp / Oh My Pi（ACP 模式，握手较慢）"),
)

# 明确不自动注册的候选：只作为结论写进 discover.md，**不发起探测**（v1 决议）
NOT_PROBED: tuple[tuple[str, str], ...] = (
    ("claude", "未探测（v1 决议）：claude 无 acp 子命令，需额外的 claude-code-acp 适配器"),
    ("codex", "未探测（v1 决议）：codex 只提供 mcp-server，走 MCP 而非 ACP"),
    ("gemini", "未探测（v1 决议）：需 --experimental-acp，本机未安装"),
)


# ============================================================
# 配置解析
# ============================================================

@dataclass
class AgentSettings:
    name: str
    command: list[str]
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    description: str = ""
    handshake_timeout_sec: float = DEFAULT_HANDSHAKE_TIMEOUT_SEC
    session_timeout_sec: float = DEFAULT_SESSION_TIMEOUT_SEC
    task_timeout_sec: float = DEFAULT_TASK_TIMEOUT_SEC
    permission_timeout_sec: float = DEFAULT_PERMISSION_TIMEOUT_SEC
    permission_default: str = "deny"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": list(self.command),
            "cwd": self.cwd,
            "env": dict(self.env),
            "enabled": self.enabled,
            "description": self.description,
            "handshake_timeout_sec": self.handshake_timeout_sec,
            "session_timeout_sec": self.session_timeout_sec,
            "task_timeout_sec": self.task_timeout_sec,
            "permission_timeout_sec": self.permission_timeout_sec,
            "permission_default": self.permission_default,
        }


@dataclass
class RegistrySettings:
    agents: list[AgentSettings] = field(default_factory=list)
    auto_start: bool = False
    inherit_env: bool = True
    env_extra: dict[str, str] = field(default_factory=dict)
    idle_ttl_sec: int = 900
    max_queue: int = 5
    task_timeout_sec: int = 1800
    permission_timeout_sec: int = 300
    permission_default: str = "deny"
    record_thoughts: bool = True
    notify_default: str = "batched"
    restore_tasks: int = 20
    discover_enabled: bool = True
    discover_cache_ttl_sec: int = 3600


def _as_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_str_dict(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items()}


def parse_agent_entry(raw: dict[str, Any], defaults: RegistrySettings) -> AgentSettings | None:
    name = str(raw.get("name") or "").strip()
    if not name:
        return None
    command = raw.get("command")
    if isinstance(command, str):
        command = [command]
    command = [str(item) for item in (command or []) if str(item).strip()]
    return AgentSettings(
        name=name,
        command=command,
        cwd=str(raw.get("cwd") or "").strip(),
        env=_as_str_dict(raw.get("env")),
        enabled=bool(raw.get("enabled", True)),
        description=str(raw.get("description") or ""),
        handshake_timeout_sec=_as_float(raw.get("handshake_timeout_sec"), DEFAULT_HANDSHAKE_TIMEOUT_SEC),
        session_timeout_sec=_as_float(raw.get("session_timeout_sec"), DEFAULT_SESSION_TIMEOUT_SEC),
        task_timeout_sec=_as_float(raw.get("task_timeout_sec"), float(defaults.task_timeout_sec)),
        permission_timeout_sec=_as_float(
            raw.get("permission_timeout_sec"), float(defaults.permission_timeout_sec)
        ),
        permission_default=str(raw.get("permission_default") or defaults.permission_default),
    )


def build_settings(config: dict[str, Any]) -> RegistrySettings:
    """从插件配置快照构造设置（缺项走默认，非法值不抛异常，避免插件加载失败）。"""
    raw_agents = config.get("agents")
    if isinstance(raw_agents, str):
        try:
            raw_agents = json.loads(raw_agents) if raw_agents.strip() else []
        except json.JSONDecodeError:
            log.warning("agents 配置不是合法 JSON，已忽略：%s", raw_agents[:120])
            raw_agents = []
    if not isinstance(raw_agents, list):
        raw_agents = []

    settings = RegistrySettings(
        auto_start=bool(config.get("auto_start", False)),
        inherit_env=bool(config.get("inherit_env", True)),
        env_extra=_as_str_dict(config.get("env_extra")),
        idle_ttl_sec=_as_int(config.get("idle_ttl_sec"), 900),
        max_queue=_as_int(config.get("max_queue"), 5),
        task_timeout_sec=_as_int(config.get("task_timeout_sec"), 1800),
        permission_timeout_sec=_as_int(config.get("permission_timeout_sec"), 300),
        permission_default=str(config.get("permission_default") or "deny"),
        record_thoughts=bool(config.get("record_thoughts", True)),
        notify_default=str(config.get("notify_default") or "batched"),
        restore_tasks=_as_int(config.get("restore_tasks"), 20),
        discover_enabled=bool(config.get("discover_enabled", True)),
        discover_cache_ttl_sec=_as_int(config.get("discover_cache_ttl_sec"), 3600),
    )
    parsed: list[AgentSettings] = []
    for item in raw_agents:
        if not isinstance(item, dict):
            continue
        entry = parse_agent_entry(item, settings)
        if entry is not None:
            parsed.append(entry)
    settings.agents = parsed
    return settings


# ============================================================
# 自动探测
# ============================================================

def _resolve_executable(command: str) -> str:
    """把命令解析为绝对路径（避免依赖子进程 PATH）。"""
    raw = str(command or "").strip()
    if not raw:
        return ""
    path = Path(raw)
    if path.is_absolute() and path.exists():
        return str(path)
    found = shutil.which(raw)
    return str(Path(found).resolve()) if found else ""


async def _run_help(exe: str) -> tuple[str, bool]:
    """执行 `<exe> --help`，≤5s；返回 (输出, 是否超时)。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            exe,
            "--help",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except Exception as exc:  # noqa: BLE001 - 命令存在但不可执行
        return f"<启动失败: {exc}>", False
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=DISCOVER_HELP_TIMEOUT_SEC)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        await asyncio.gather(proc.wait(), return_exceptions=True)
        return "", True
    return out.decode("utf-8", errors="replace"), False


async def probe_candidate(candidate: Candidate) -> dict[str, Any]:
    exe = _resolve_executable(candidate.command)
    if not exe:
        return {
            "name": candidate.name,
            "available": False,
            "reason": f"未安装：PATH 上找不到 `{candidate.command}`",
            "command": [],
            "exe": "",
        }
    output, timed_out = await _run_help(exe)
    if timed_out:
        return {
            "name": candidate.name,
            "available": False,
            "reason": f"`{exe} --help` 超过 {DISCOVER_HELP_TIMEOUT_SEC:.0f}s 无响应，无法确认 ACP 入口",
            "command": [],
            "exe": exe,
        }
    if candidate.acp_args and candidate.acp_args[0] in output:
        return {
            "name": candidate.name,
            "available": True,
            "reason": f"找到 `{candidate.acp_args[0]}` 子命令",
            "command": [exe, *candidate.acp_args],
            "exe": exe,
        }
    return {
        "name": candidate.name,
        "available": False,
        "reason": f"`{exe} --help` 未列出 `{candidate.acp_args[0]}` 子命令，没有 ACP 入口",
        "command": [],
        "exe": exe,
    }


async def run_discovery() -> list[dict[str, Any]]:
    """探测全部候选（并发，各自 ≤5s 超时）。"""
    results = await asyncio.gather(*(probe_candidate(item) for item in CANDIDATES))
    return list(results)


def render_discover_md(results: Iterable[dict[str, Any]], *, cache_age_sec: float | None = None) -> str:
    """渲染 faustbot://agents/discover.md —— 发现与未发现都必须如实写出原因。"""
    rows = list(results)
    lines = ["# 外部 Agent 探测结果", ""]
    if cache_age_sec is not None:
        lines.append(f"缓存时间：{cache_age_sec:.0f}s 前（探测只做 PATH 查找 + `--help` 静态校验，不跑活握手）")
    else:
        lines.append("探测只做 PATH 查找 + `--help` 静态校验（≤5s），不跑活握手。")
    lines.append("")
    lines.append("| 候选 | 可用 | 解析出的命令 | 原因 |")
    lines.append("|---|---|---|---|")
    for item in rows:
        command = " ".join(item.get("command") or []) or "-"
        lines.append(
            f"| {item.get('name')} | {'是' if item.get('available') else '否'} "
            f"| `{command}` | {item.get('reason') or '-'} |"
        )
    lines.append("")
    lines.append("## 明确不自动注册的候选")
    lines.append("")
    for name, reason in NOT_PROBED:
        lines.append(f"- `{name}`：{reason}（如需接入，请在插件配置 `agents` 里手填命令）")
    return "\n".join(lines)


# ============================================================
# 运行时（一个 Agent = 一个进程 + 一条连接 + 一个活跃 session）
# ============================================================

class AgentRuntime:
    """单个外部 Agent 的进程 / 会话 / 动态配置状态。

    本类不排队、不做任务语义；只回答"进程在不在、session 是哪个、有哪些 configOptions"。
    """

    def __init__(
        self,
        settings: AgentSettings,
        *,
        inherit_env: bool = True,
        env_extra: dict[str, str] | None = None,
        on_session_update: Callable[[str, Any], Any] | None = None,
        on_permission: Callable[[str, Any, list], Awaitable[Any]] | None = None,
        on_unhandled: Callable[[str, Any], Any] | None = None,
        on_exit: Callable[[str], Any] | None = None,
    ) -> None:
        self.settings = settings
        self.name = settings.name
        self.phase = "stopped"
        self.last_error: str | None = None
        self.session_id: str | None = None
        self.config_options: list[Any] = []
        self.available_commands: list[str] = []
        self.current_mode: str | None = None
        self.last_activity = time.time()
        self.cached_sessions: list[dict[str, Any]] = []
        self.sessions_ts: float = 0.0
        self.sessions_error: str | None = None
        self._initialized = False
        self._exit_hook = on_exit
        self.bridge = AcpBridge(
            name=settings.name,
            command=settings.command,
            cwd=settings.cwd or None,
            env={**dict(env_extra or {}), **settings.env},
            inherit_env=inherit_env,
            handshake_timeout_sec=settings.handshake_timeout_sec,
            session_timeout_sec=settings.session_timeout_sec,
            on_session_update=on_session_update,
            on_permission=on_permission,
            on_unhandled=on_unhandled,
            on_exit=self._handle_exit,
        )

    # ── 状态 ──

    @property
    def running(self) -> bool:
        return self.bridge.running

    @property
    def cwd(self) -> str:
        return self.settings.cwd or str(conf.WORKDIR_ROOT)

    @property
    def description(self) -> str:
        agent_info = self.bridge.agent_info
        name = str(getattr(agent_info, "name", "") or "").strip() if agent_info is not None else ""
        version = str(getattr(agent_info, "version", "") or "").strip() if agent_info is not None else ""
        if name:
            return f"{name} {version}".strip()
        return self.settings.description

    def error(self, stage: str, message: str) -> None:
        self.phase = "error"
        self.last_error = f"[{stage}] {message}"
        log.warning("%s: %s", self.name, self.last_error)

    def _handle_exit(self, detail: str) -> None:
        self.phase = "stopped"
        self._initialized = False
        self.config_options = []
        self.available_commands = []
        self.current_mode = None
        self.last_error = detail
        if self._exit_hook is not None:
            return self._exit_hook(detail)
        return None
    # ── 进程与握手 ──

    async def ensure_initialized(self) -> None:
        """确保进程在跑且 initialize 成功（幂等）。"""
        if self.running and self._initialized:
            self.last_activity = time.time()
            return
        self.phase = "starting"
        try:
            await self.bridge.start()
            response = await self.bridge.initialize()
        except AcpBridgeError as exc:
            self.error(exc.stage, str(exc))
            raise
        del response
        self._initialized = True
        self.phase = "ready"
        self.last_error = None
        self.last_activity = time.time()
        log.info(
            "%s: 握手成功 protocolVersion=%s agentInfo=%s",
            self.name,
            self.bridge.protocol_version,
            self.description,
        )
        # 后台刷新 sessions 缓存：VFS 的 sessions.md 只渲染缓存，读操作绝不发起 RPC
        asyncio.create_task(self.refresh_sessions_cache())

    async def refresh_sessions_cache(self) -> None:
        """拉取 session/list 到缓存（由进程启动/新建会话触发，不由 VFS 读触发）。"""
        try:
            response = await self.bridge.list_sessions(self.cwd)
        except Exception as exc:  # noqa: BLE001 - 缓存失败只记录，不影响主链路
            self.sessions_error = f"{type(exc).__name__}: {exc}"
            log.debug("%s: session/list 失败: %s", self.name, exc)
            return
        self.sessions_error = None
        self.sessions_ts = time.time()
        self.cached_sessions = [
            {
                "session_id": str(getattr(item, "session_id", "") or ""),
                "title": getattr(item, "title", None),
                "cwd": getattr(item, "cwd", None),
                "updated_at": getattr(item, "updated_at", None),
            }
            for item in (getattr(response, "sessions", None) or [])
        ]

    async def ensure_session(self, *, cwd: str | None = None, mode: str = "reuse", session_id: str | None = None) -> str:
        """按信封的 session 语义准备一个可用 session，返回 sessionId。

        ``mode``：``reuse``（复用当前/新建）、``new``（强制新建）、``close``（先关再新建）、
        具体 id（``session/load``，失败如实报错，不静默新建）。
        """
        await self.ensure_initialized()
        target_cwd = str(cwd or self.cwd)

        if mode == "close":
            await self.close_session()
            mode = "new"

        if mode == "load":
            if not session_id:
                raise AcpBridgeError("session/load", "session 模式为具体 id 时 sessionId 不能为空")
            loaded = await self._load_session(target_cwd, session_id)
            self.session_id = loaded
            return loaded

        if mode == "new" or not self.session_id:
            created = await self._new_session(target_cwd)
            self.session_id = created
            return created

        # reuse：确认旧 session 仍在（失败则如实回落到新建，并在日志留痕）
        if self.session_id:
            try:
                await self.bridge.load_session(target_cwd, self.session_id)
            except AcpBridgeError as exc:
                log.warning("%s: 复用 session %s 失败（%s），改为新建 session", self.name, self.session_id, exc)
            else:
                self.last_activity = time.time()
                return self.session_id
            self.session_id = None
        created = await self._new_session(target_cwd)
        self.session_id = created
        return created

    async def _new_session(self, cwd: str) -> str:
        try:
            response = await self.bridge.new_session(cwd)
        except AcpBridgeError as exc:
            auth = await self._try_authenticate(exc)
            if not auth:
                self.error(exc.stage, str(exc))
                raise
            response = await self.bridge.new_session(cwd)
        self.config_options = list(getattr(response, "config_options", None) or [])
        self.last_activity = time.time()
        self.phase = "ready"
        asyncio.create_task(self.refresh_sessions_cache())
        return str(response.session_id)

    async def _load_session(self, cwd: str, session_id: str) -> str:
        try:
            response = await self.bridge.load_session(cwd, session_id)
        except AcpBridgeError as exc:
            self.error(exc.stage, f"session/load {session_id} 失败：{exc}")
            raise
        config_options = getattr(response, "config_options", None)
        if config_options is not None:
            self.config_options = list(config_options)
        self.last_activity = time.time()
        self.phase = "ready"
        return session_id

    async def _try_authenticate(self, cause: Exception) -> bool:
        """仅认证失败时取 authMethods[0].id 重试一次（规格 §7.4 / §16.1）。"""
        methods = list(self.bridge.auth_methods)
        if not methods:
            return False
        method_id = str(getattr(methods[0], "id", "") or "")
        if not method_id:
            return False
        log.warning("%s: 首次 session/new 失败（%s），尝试 authenticate(%s) 一次", self.name, cause, method_id)
        try:
            await self.bridge.authenticate(method_id)
        except AcpBridgeError as exc:
            self.error(exc.stage, f"authenticate({method_id}) 失败：{exc}")
            return False
        return True

    async def apply_config(self, config_map: dict[str, Any]) -> list[str]:
        """按信封的 config 映射逐项调用 session/set_config_option。

        未知 configId 报错并列出合法值（不猜测）；返回变更说明行。
        """
        if not config_map:
            return []
        if not self.session_id:
            raise AcpBridgeError("session/set_config_option", "会话尚未建立，无法下发 config")
        known = {str(getattr(item, "id", "")): item for item in self.config_options}
        notes: list[str] = []
        for config_id, value in config_map.items():
            key = str(config_id)
            if key not in known:
                legal = ", ".join(sorted(item for item in known if item)) or "(无)"
                raise AcpBridgeError(
                    "session/set_config_option",
                    f"未知 configId `{key}`；当前会话合法 id：{legal}（详见 config.json）",
                )
            try:
                response = await self.bridge.set_config_option(key, self.session_id, value)
            except AcpBridgeError as exc:
                self.error(exc.stage, f"设置 {key}={value!r} 失败：{exc}")
                raise
            new_options = getattr(response, "config_options", None)
            if new_options:
                self.config_options = list(new_options)
            current = self._current_value(key)
            notes.append(f"{key}={current!r}")
        self.last_activity = time.time()
        return notes

    def _current_value(self, config_id: str) -> Any:
        for item in self.config_options:
            if str(getattr(item, "id", "")) == config_id:
                return getattr(item, "current_value", None)
        return None

    def on_commands_update(self, commands: list[Any]) -> None:
        self.available_commands = [str(getattr(item, "name", "") or "") for item in commands if item]
        self.last_activity = time.time()

    def on_mode_update(self, mode_id: str | None) -> None:
        self.current_mode = str(mode_id) if mode_id else None
        self.last_activity = time.time()

    def touch(self) -> None:
        self.last_activity = time.time()

    async def close_session(self) -> bool:
        if not self.session_id or not self.running:
            self.session_id = None
            return False
        session_id = self.session_id
        try:
            await self.bridge.close_session(session_id)
        except AcpBridgeError as exc:
            log.warning("%s: session/close %s 失败：%s", self.name, session_id, exc)
            self.session_id = None
            return False
        self.session_id = None
        self.config_options = []
        self.available_commands = []
        self.last_activity = time.time()
        return True

    async def list_sessions(self) -> Any:
        await self.ensure_initialized()
        return await self.bridge.list_sessions(self.cwd)

    async def stop(self) -> None:
        await self.bridge.stop()
        self.phase = "stopped"

    def status(self) -> dict[str, Any]:
        idle_sec = max(0.0, time.time() - self.last_activity)
        return {
            "name": self.name,
            "enabled": self.settings.enabled,
            "description": self.description,
            "phase": self.phase,
            "running": self.running,
            "pid": self.bridge.pid,
            "protocol_version": self.bridge.protocol_version,
            "session_id": self.session_id,
            "cwd": self.cwd,
            "idle_sec": idle_sec,
            "last_error": self.last_error,
            "available_commands": list(self.available_commands),
            "config_options": [config_option_to_dict(item) for item in self.config_options],
            "stderr_tail": list(self.bridge.stderr_tail),
            "auth_methods": [
                {"id": str(getattr(item, "id", "") or ""), "name": str(getattr(item, "name", "") or "")}
                for item in self.bridge.auth_methods
            ],
        }


def config_option_to_dict(item: Any) -> dict[str, Any]:
    """把 SDK 的 SessionConfigOption* 渲染成可读 dict（含合法取值）。"""
    options: list[dict[str, str]] = []
    raw_options = getattr(item, "options", None) or []
    for entry in raw_options:
        if hasattr(entry, "options"):  # select group
            for sub in getattr(entry, "options", None) or []:
                options.append({"value": str(getattr(sub, "value", "")), "name": str(getattr(sub, "name", ""))})
            continue
        options.append({"value": str(getattr(entry, "value", "")), "name": str(getattr(entry, "name", ""))})
    return {
        "id": str(getattr(item, "id", "") or ""),
        "name": str(getattr(item, "name", "") or ""),
        "type": str(getattr(item, "type", "") or ""),
        "current_value": getattr(item, "current_value", None),
        "description": getattr(item, "description", None),
        "category": getattr(item, "category", None),
        "options": options,
    }


# ============================================================
# 注册表
# ============================================================

class AgentRegistry:
    """配置 + 自动探测的合并结果，以及按需创建的 AgentRuntime。"""

    def __init__(
        self,
        settings: RegistrySettings,
        *,
        runtime_factory: Callable[[AgentSettings], AgentRuntime] | None = None,
    ) -> None:
        self.settings = settings
        self._explicit: dict[str, AgentSettings] = {item.name: item for item in settings.agents}
        self._discovered: dict[str, dict[str, Any]] = {}
        self._runtimes: dict[str, AgentRuntime] = {}
        self._runtime_factory = runtime_factory
        self._discover_ts: float = 0.0
        self._resolved: list[AgentSettings] | None = None

    def _invalidate(self) -> None:
        self._resolved = None

    def update_settings(self, settings: RegistrySettings) -> None:
        """就地替换设置（配置变更时用；已创建的运行时保持不变）。"""
        self.settings = settings
        self._explicit = {item.name: item for item in settings.agents}
        self._invalidate()

    # ── 探测 ──

    async def refresh_discovery(self, *, force: bool = False, cache: Any = None) -> list[dict[str, Any]]:
        """探测并缓存（缓存进插件 GLOBAL storage，TTL 可配）。"""
        ttl = max(0, int(self.settings.discover_cache_ttl_sec))
        if not self.settings.discover_enabled:
            self._discovered = {}
            self._invalidate()
            return self.discover_rows()
        if not force and cache is not None:
            payload = cache.get("global", DISCOVER_CACHE_KEY)
            if isinstance(payload, dict) and payload.get("results"):
                age = time.time() - float(payload.get("ts") or 0.0)
                if age < ttl:
                    self._discovered = {
                        str(item.get("name")): item for item in payload["results"] if item.get("name")
                    }
                    self._discover_ts = float(payload.get("ts") or 0.0)
                    self._invalidate()
                    return self.discover_rows()
        results = await run_discovery()
        self._discovered = {str(item.get("name")): item for item in results if item.get("name")}
        self._discover_ts = time.time()
        self._invalidate()
        if cache is not None:
            cache.set("global", DISCOVER_CACHE_KEY, {"ts": self._discover_ts, "results": results})
        return self.discover_rows()

    def discover_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for candidate in CANDIDATES:
            found = self._discovered.get(candidate.name)
            explicit = self._explicit.get(candidate.name)
            if found is None:
                rows.append(
                    {
                        "name": candidate.name,
                        "available": False,
                        "reason": "未探测（或探测结果已失效），可刷新探测",
                        "command": list(explicit.command) if explicit else [],
                    }
                )
                continue
            row = dict(found)
            if explicit is not None:
                row["reason"] = f"用户显式配置优先（{row.get('reason')}）"
                row["command"] = list(explicit.command)
            row["available"] = bool(row.get("available")) or explicit is not None
            rows.append(row)
        return rows

    @property
    def discover_age_sec(self) -> float:
        return max(0.0, time.time() - self._discover_ts) if self._discover_ts else -1.0

    # ── Agent 解析 ──

    def resolve_agents(self) -> list[AgentSettings]:
        """显式配置优先于自动发现；`enabled: false` 屏蔽。（结果缓存：VFS 渲染会反复调用）"""
        if self._resolved is None:
            self._resolved = self._resolve_agents_uncached()
        return self._resolved

    def _resolve_agents_uncached(self) -> list[AgentSettings]:
        merged: dict[str, AgentSettings] = {}
        for name, found in self._discovered.items():
            if not found.get("available"):
                continue
            command = [str(item) for item in (found.get("command") or [])]
            if not command:
                continue
            merged[name] = AgentSettings(
                name=name,
                command=command,
                cwd="",
                description=str(found.get("reason") or ""),
            )
        for name, explicit in self._explicit.items():
            entry = replace(explicit, command=list(explicit.command))
            if not entry.enabled:
                merged.pop(name, None)
                continue
            if entry.command:
                resolved = _resolve_executable(entry.command[0])
                if resolved:
                    entry.command = [resolved, *entry.command[1:]]
            merged[name] = entry
        for entry in merged.values():
            if not entry.cwd:
                entry.cwd = str(conf.WORKDIR_ROOT)
            if not entry.description:
                entry.description = next(
                    (c.description for c in CANDIDATES if c.name == entry.name), "外部 ACP Agent"
                )
        return [merged[name] for name in sorted(merged)]

    def agent_names(self) -> list[str]:
        return [item.name for item in self.resolve_agents()]

    # ── 运行时 ──

    def _make_runtime(self, settings: AgentSettings) -> AgentRuntime:
        if self._runtime_factory is not None:
            return self._runtime_factory(settings)
        return AgentRuntime(settings, inherit_env=self.settings.inherit_env, env_extra=self.settings.env_extra)

    def get(self, name: str) -> AgentRuntime | None:
        target = str(name or "").strip()
        if not target:
            return None
        runtime = self._runtimes.get(target)
        if runtime is not None:
            return runtime
        settings = next((item for item in self.resolve_agents() if item.name == target), None)
        if settings is None:
            return None
        runtime = self._make_runtime(settings)
        self._runtimes[target] = runtime
        return runtime

    def existing(self, name: str) -> AgentRuntime | None:
        """只取已创建的运行时（不因其配置存在而创建）。"""
        return self._runtimes.get(str(name or "").strip())

    def runtimes(self) -> list[AgentRuntime]:
        return [self._runtimes[name] for name in sorted(self._runtimes)]

    def drop_runtime(self, name: str) -> AgentRuntime | None:
        return self._runtimes.pop(str(name or "").strip(), None)

    async def stop_all(self) -> None:
        for runtime in self.runtimes():
            try:
                await runtime.stop()
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: 关闭进程失败: %s", runtime.name, exc)
        self._runtimes.clear()

    async def stop_idle(self) -> list[str]:
        """空闲超时关进程但保留 sessionId。"""
        ttl = max(0, int(self.settings.idle_ttl_sec))
        if ttl <= 0:
            return []
        stopped: list[str] = []
        for runtime in self.runtimes():
            if not runtime.running:
                continue
            if (time.time() - runtime.last_activity) < ttl:
                continue
            await runtime.stop()
            stopped.append(runtime.name)
        return stopped


__all__ = [
    "AgentRegistry",
    "AgentRuntime",
    "AgentSettings",
    "CANDIDATES",
    "NOT_PROBED",
    "RegistrySettings",
    "build_settings",
    "config_option_to_dict",
    "probe_candidate",
    "render_discover_md",
    "run_discovery",
]
