"""可脚本化的假 ACP Agent，供 pytest 用 ``acp.spawn_agent_process`` 起子进程做夹具。

行为由模式串选择，模式串来自 ``FAKE_ACP_MODE``（逗号分隔）或命令行 ``argv[1]``（后者优先）::

    python fake_acp_agent.py permission
    FAKE_ACP_MODE=stderr_flood,huge_line python fake_acp_agent.py
    FAKE_ACP_MODE=session_slow=0.5,no_auth python fake_acp_agent.py

stdout 只允许 ACP 协议帧；一切日志（含权限决策）都以 JSON 行的形式写 stderr。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Final

import acp
from acp.contrib.permissions import default_permission_options
from acp.schema import (
    AllowedOutcome,
    AuthMethodAgent,
    CloseSessionResponse,
    ConfigOptionUpdate,
    Implementation,
    ListSessionsResponse,
    LoadSessionResponse,
    PermissionOption,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionInfo,
    SetSessionConfigOptionResponse,
    ToolCallUpdate,
)

SESSION_ID: Final = "ses_fake_1"
TOOL_CALL_ID: Final = "tc1"
TOOL_CALL_TITLE: Final = "Write file /tmp/x"
MODEL_OPTION_ID: Final = "model"
MODE_OPTION_ID: Final = "mode"
EFFORT_OPTION_ID: Final = "effort"

# stderr_flood：4000 行 × 600 字符 ≈ 2.4 MB
STDERR_FLOOD_LINES: Final = 4000
STDERR_FLOOD_LINE_CHARS: Final = 600
# huge_line：单行 ≥ 2 MiB 字符，无换行
HUGE_LINE_CHARS: Final = 2 * 1024 * 1024 + 64

_BASE_MODES: Final[frozenset[str]] = frozenset(
    {
        "normal",
        "no_auth",
        "session_slow",
        "session_hang",
        "dynamic_config",
        "stderr_flood",
        "huge_line",
        "exit_early",
        "exit_before_prompt",
        "permission",
        "permission_mismatch",
        "prompt_error",
        "auth_required",
        "slow_prompt",
    }
)
# 带 <seconds> 参数的模式及其默认值
_PARAM_MODES: Final[dict[str, float]] = {"session_slow": 5.0, "slow_prompt": 5.0}


def _warn(message: str) -> None:
    sys.stderr.write(f"[fake-acp] {message}\n")
    sys.stderr.flush()


def _use_utf8_stderr() -> None:
    """Windows 上 stderr 默认按本地代码页编码（如 cp936），中文告警会被读方解成乱码。"""
    reconfigure = getattr(sys.stderr, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")


def _log(event: str, **fields: Any) -> None:
    """stderr 上的一行 JSON，便于测试侧解析断言。"""
    sys.stderr.write(json.dumps({"event": event, **fields}, ensure_ascii=False) + "\n")
    sys.stderr.flush()


def _parse_seconds(raw: str, default: float, token: str) -> float:
    try:
        return float(raw)
    except ValueError:
        _warn(f"mode {token!r} 的秒数无法解析，回退到 {default}")
        return default


def parse_modes(raw: str | None) -> tuple[set[str], dict[str, float]]:
    """把模式串解析成 ``(模式集合, 带参模式的秒数)``；未知模式只告警不报错。"""
    modes: set[str] = set()
    params: dict[str, float] = {}
    for token in (raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        name, sep, value = token.partition("=")
        name = name.strip()
        if name not in _BASE_MODES:
            _warn(f"未知模式已忽略: {token!r}")
            continue
        modes.add(name)
        if name in _PARAM_MODES:
            params[name] = _parse_seconds(value.strip(), _PARAM_MODES[name], token) if sep else _PARAM_MODES[name]
    if not modes:
        modes.add("normal")
    return modes, params


def resolve_mode_string() -> str | None:
    """argv[1] 优先于环境变量 ``FAKE_ACP_MODE``。"""
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1]
    return os.environ.get("FAKE_ACP_MODE")


def _select(
    option_id: str,
    name: str,
    current_value: str,
    values: list[str],
    *,
    category: str | None = None,
) -> SessionConfigOptionSelect:
    return SessionConfigOptionSelect(
        id=option_id,
        name=name,
        type="select",
        current_value=current_value,
        options=[SessionConfigSelectOption(value=item, name=item) for item in values],
        category=category,
    )


def default_config_options() -> list[SessionConfigOptionSelect]:
    return [
        _select(MODEL_OPTION_ID, "Model", "fake/model-a", ["fake/model-a", "fake/model-b"], category="model"),
        _select(MODE_OPTION_ID, "Mode", "build", ["build", "plan"], category="mode"),
    ]


def _effort_option() -> SessionConfigOptionSelect:
    return _select(EFFORT_OPTION_ID, "Reasoning effort", "low", ["low", "medium", "high"], category="thought_level")


class FakeAcpAgent:
    """只实现夹具所需模式的最小 ``acp.Agent``。"""

    def __init__(self, modes: set[str], params: dict[str, float]) -> None:
        self._modes = set(modes)
        self._params = dict(params)
        self._conn: Any = None
        self._session_id = SESSION_ID
        self._cwd = os.getcwd()
        self._config_options = default_config_options()
        self._authenticated = False
        self._cancelled = asyncio.Event()

    # ── 内部工具 ──

    @property
    def _live_conn(self) -> Any:
        if self._conn is None:
            raise RuntimeError("on_connect 尚未调用，无法向客户端推送消息")
        return self._conn

    async def _push_thought(self, session_id: str, text: str) -> None:
        await self._live_conn.session_update(session_id=session_id, update=acp.update_agent_thought_text(text))

    async def _push_message(self, session_id: str, text: str) -> None:
        await self._live_conn.session_update(session_id=session_id, update=acp.update_agent_message_text(text))

    # ── acp.Agent ──

    def on_connect(self, conn: Any) -> None:
        self._conn = conn

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any = None,
        client_info: Any = None,
        **kwargs: Any,
    ) -> acp.InitializeResponse:
        auth_methods: list[Any] = []
        if "no_auth" not in self._modes:
            auth_methods.append(AuthMethodAgent(id="fake-login", name="Login with fake", description="fake"))
        return acp.InitializeResponse(
            protocol_version=1,
            agent_info=Implementation(name="FakeAgent", version="0.0.1"),
            auth_methods=auth_methods,
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> acp.NewSessionResponse:
        if "session_hang" in self._modes:
            await asyncio.Event().wait()
        if "exit_before_prompt" in self._modes:
            os._exit(3)
        if "session_slow" in self._modes:
            await asyncio.sleep(self._params.get("session_slow", _PARAM_MODES["session_slow"]))
        if "auth_required" in self._modes and not self._authenticated:
            _log("auth_required", session_id=SESSION_ID)
            raise acp.RequestError.auth_required({"authMethods": [{"id": "fake-login", "name": "Login with fake"}]})
        self._cwd = str(cwd)
        self._session_id = SESSION_ID
        return acp.NewSessionResponse(session_id=SESSION_ID, config_options=list(self._config_options))

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse:
        self._cwd = str(cwd)
        self._session_id = str(session_id)
        return LoadSessionResponse(config_options=list(self._config_options))

    async def list_sessions(
        self,
        cwd: str | None = None,
        cursor: str | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        return ListSessionsResponse(
            sessions=[SessionInfo(session_id=SESSION_ID, cwd=self._cwd, title="fake session")],
        )

    async def set_config_option(
        self,
        config_id: str,
        session_id: str,
        value: str | bool,
        **kwargs: Any,
    ) -> SetSessionConfigOptionResponse:
        self._config_options = self._apply_config_option(config_id, value)
        if "dynamic_config" in self._modes and not any(
            option.id == EFFORT_OPTION_ID for option in self._config_options
        ):
            self._config_options = [*self._config_options, _effort_option()]
            await self._live_conn.session_update(
                session_id=session_id,
                update=ConfigOptionUpdate(
                    session_update="config_option_update",
                    config_options=list(self._config_options),
                ),
            )
        _log("config_option_set", session_id=session_id, config_id=config_id, value=value)
        return SetSessionConfigOptionResponse(config_options=list(self._config_options))

    async def authenticate(self, method_id: str, **kwargs: Any) -> acp.AuthenticateResponse:
        self._authenticated = True
        _log("authenticated", method_id=method_id)
        return acp.AuthenticateResponse()

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse:
        _log("session_closed", session_id=session_id)
        return CloseSessionResponse()

    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> acp.PromptResponse:
        self._session_id = str(session_id)
        if "exit_early" in self._modes:
            await self._push_message(session_id, "goodbye")
            _log("exit_early", session_id=session_id)
            os._exit(3)
        if "prompt_error" in self._modes:
            raise acp.RequestError.internal_error({"details": "fake prompt failure"})
        if "permission" in self._modes or "permission_mismatch" in self._modes:
            return await self._permission_turn(session_id)
        if "slow_prompt" in self._modes:
            return await self._slow_turn(session_id)
        if "stderr_flood" in self._modes:
            self._flood_stderr()
        if "huge_line" in self._modes:
            await self._push_message(session_id, "x" * HUGE_LINE_CHARS)
        await self._push_thought(session_id, "thinking...")
        await self._push_message(session_id, "Hello ")
        await self._push_message(session_id, "world")
        return acp.PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        _log("cancel", session_id=session_id)
        self._cancelled.set()

    # ── 模式实现 ──

    def _apply_config_option(self, config_id: str, value: str | bool) -> list[SessionConfigOptionSelect]:
        updated: list[SessionConfigOptionSelect] = []
        for option in self._config_options:
            if option.id == config_id and isinstance(value, str):
                values = [item.value for item in option.options if isinstance(item, SessionConfigSelectOption)]
                if value in values:
                    option = option.model_copy(update={"current_value": value})
            updated.append(option)
        return updated

    async def _permission_turn(self, session_id: str) -> acp.PromptResponse:
        await self._live_conn.session_update(
            session_id=session_id,
            update=acp.start_tool_call(
                tool_call_id=TOOL_CALL_ID,
                title=TOOL_CALL_TITLE,
                kind="edit",
                status="pending",
            ),
        )
        if "permission_mismatch" in self._modes:
            options = [PermissionOption(option_id="reject", name="Reject", kind="reject_once")]
        else:
            options = list(default_permission_options())
        response = await self._live_conn.request_permission(
            session_id=session_id,
            tool_call=ToolCallUpdate(tool_call_id=TOOL_CALL_ID, title=TOOL_CALL_TITLE),
            options=options,
        )
        outcome = response.outcome
        selected: str
        if isinstance(outcome, AllowedOutcome):
            selected = outcome.option_id
        else:
            selected = "cancelled"
        _log(
            "permission_decision",
            session_id=session_id,
            tool_call_id=TOOL_CALL_ID,
            outcome=outcome.outcome,
            option_id=selected,
            offered=[{"option_id": option.option_id, "kind": option.kind} for option in options],
        )
        await self._push_message(session_id, f"permission outcome: {selected}")
        return acp.PromptResponse(stop_reason="end_turn")

    async def _slow_turn(self, session_id: str) -> acp.PromptResponse:
        self._cancelled.clear()
        await self._push_message(session_id, "working")
        timeout = self._params.get("slow_prompt", _PARAM_MODES["slow_prompt"])
        try:
            await asyncio.wait_for(self._cancelled.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            await self._push_message(session_id, "done")
            return acp.PromptResponse(stop_reason="end_turn")
        await self._push_message(session_id, "CANCELLED")
        return acp.PromptResponse(stop_reason="cancelled")

    def _flood_stderr(self) -> None:
        line = ("FAKE-ACP-STDERR-FLOOD " + "x" * STDERR_FLOOD_LINE_CHARS)[:STDERR_FLOOD_LINE_CHARS] + "\n"
        try:
            sys.stderr.write(line * STDERR_FLOOD_LINES)
            sys.stderr.flush()
        except OSError as exc:  # stderr 管道被对端关掉时不阻塞 prompt
            _warn(f"stderr flood 中断: {exc}")


def main() -> None:
    _use_utf8_stderr()
    modes, params = parse_modes(resolve_mode_string())
    _log("startup", modes=sorted(modes), params=params, pid=os.getpid())
    agent = FakeAcpAgent(modes, params)
    # close_session / fork / resume 在 SDK 里属于 unstable 路由，不开这个开关 Agent 会直接回 method_not_found。
    asyncio.run(acp.run_agent(agent, use_unstable_protocol=True))


if __name__ == "__main__":
    main()
