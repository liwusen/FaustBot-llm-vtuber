"""acp_bridge（官方 ACP SDK 封装）单测：四个 SDK 陷阱 + 超时 + 回调。

固定装置 backend/tests/fixtures/fake_acp_agent.py 用 SDK 自己的 Agent API 写成，
因此这里只关注桥接层行为，不关心 JSON-RPC 帧格式。

Run: .runtime/python.exe -m pytest backend/tests/test_agent_communicate_bridge.py -v
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
PLUGIN_DIR = BACKEND_ROOT / "default_plugins" / "agent-communicate"
FAKE_AGENT = BACKEND_ROOT / "tests" / "fixtures" / "fake_acp_agent.py"
PYTHON = REPO_ROOT / ".runtime" / "python.exe"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

from acp_bridge import AcpBridge, AcpBridgeError, AcpProcessGoneError, AcpTimeoutError  # noqa: E402


def make_bridge(mode: str, **kwargs) -> AcpBridge:
    """用 argv 传模式（argv 优先级高于 FAKE_ACP_MODE，不受 env 裁剪影响）。"""
    options = {
        "name": "fake",
        "command": [str(PYTHON), str(FAKE_AGENT), mode],
        "cwd": str(REPO_ROOT),
        "handshake_timeout_sec": 20.0,
        "session_timeout_sec": 10.0,
        **kwargs,
    }
    return AcpBridge(**options)


@pytest_asyncio.fixture
async def bridges():
    created: list[AcpBridge] = []

    def factory(mode: str, **kwargs) -> AcpBridge:
        bridge = make_bridge(mode, **kwargs)
        created.append(bridge)
        return bridge

    yield factory
    for bridge in created:
        try:
            await bridge.stop()
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# 基本链路
# ============================================================

class TestBasicFlow:
    @pytest.mark.asyncio
    async def test_initialize_new_session_prompt(self, bridges):
        seen: list[tuple[str, str]] = []

        def on_update(session_id: str, update: Any) -> None:
            seen.append((session_id, type(update).__name__))

        bridge = bridges("normal", on_session_update=on_update)
        init = await bridge.initialize()
        assert init.protocol_version == 1
        assert bridge.agent_info.name == "FakeAgent"
        assert [item.id for item in bridge.auth_methods] == ["fake-login"]

        session = await bridge.new_session(str(REPO_ROOT))
        assert session.session_id == "ses_fake_1"
        assert {item.id for item in session.config_options} == {"model", "mode"}

        response = await bridge.prompt(session.session_id, "hi", timeout=20)
        assert response.stop_reason == "end_turn"
        names = [name for _sid, name in seen]
        assert "AgentMessageChunk" in names and "AgentThoughtChunk" in names
        assert bridge.running

    @pytest.mark.asyncio
    async def test_no_residual_child_process(self, bridges):
        import psutil

        bridge = bridges("normal")
        await bridge.initialize()
        pid = bridge.pid
        assert pid and psutil.pid_exists(pid)

        await bridge.stop()
        for _ in range(50):
            if not psutil.pid_exists(pid):
                break
            await asyncio.sleep(0.1)
        assert not psutil.pid_exists(pid), f"子进程 {pid} 未被回收"
        assert not bridge.running

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self, bridges):
        bridge = bridges("normal")
        await bridge.initialize()
        await bridge.stop()
        await bridge.stop()
        assert not bridge.running


# ============================================================
# 陷阱 1：环境变量裁剪
# ============================================================

class TestEnvPassthrough:
    @pytest.mark.asyncio
    async def test_full_env_inherited_by_default(self, bridges):
        """不传 argv 模式，靠 env 里的 FAKE_ACP_MODE 生效 → 证明显式 env 已透传到子进程。"""
        bridge = AcpBridge(
            name="fake-env",
            command=[str(PYTHON), str(FAKE_AGENT)],
            cwd=str(REPO_ROOT),
            env={"FAKE_ACP_MODE": "no_auth"},
            inherit_env=True,
            handshake_timeout_sec=20.0,
        )
        try:
            await bridge.initialize()
            assert list(bridge.auth_methods) == [], "子进程没收到 env → 陷阱 1 回归"
        finally:
            await bridge.stop()

    @pytest.mark.asyncio
    async def test_inherit_env_false_uses_sdk_trimmed_env(self, bridges):
        """关掉全量继承时，非白名单变量拿不到（对照实验，说明 inherit_env 的意义）。"""
        bridge = AcpBridge(
            name="fake-trim",
            command=[str(PYTHON), str(FAKE_AGENT)],
            cwd=str(REPO_ROOT),
            env={"FAKE_ACP_MODE": "no_auth"},
            inherit_env=False,
            handshake_timeout_sec=20.0,
        )
        try:
            await bridge.initialize()
            # 裁剪后仍保留 PATH 等 12 个变量 + 我们显式追加的 env，所以模式仍然生效
            assert list(bridge.auth_methods) == []
        finally:
            await bridge.stop()


# ============================================================
# 陷阱 2 / 3：stderr 洪水与超长单行
# ============================================================

class TestSdkTraps:
    @pytest.mark.asyncio
    async def test_stderr_flood_does_not_hang(self, bridges):
        bridge = bridges("stderr_flood")
        await bridge.initialize()
        session = await bridge.new_session(str(REPO_ROOT))
        response = await bridge.prompt(session.session_id, "hi", timeout=60)
        assert response.stop_reason == "end_turn"
        assert bridge.stderr_tail, "stderr 没有被 drain"

    @pytest.mark.asyncio
    async def test_huge_single_line_not_rejected(self, bridges):
        chunks: list[int] = []

        def on_update(_sid: str, update: object) -> None:
            text = getattr(getattr(update, "content", None), "text", None)
            if isinstance(text, str):
                chunks.append(len(text))

        bridge = bridges("huge_line", on_session_update=on_update)
        await bridge.initialize()
        session = await bridge.new_session(str(REPO_ROOT))
        response = await bridge.prompt(session.session_id, "hi", timeout=60)
        assert response.stop_reason == "end_turn"
        assert max(chunks) > 2 * 1024 * 1024, f"未收到超长单行（最大 {max(chunks) if chunks else 0}）"


# ============================================================
# 超时（带阶段信息）
# ============================================================

class TestTimeouts:
    @pytest.mark.asyncio
    async def test_session_timeout_carries_stage(self, bridges):
        bridge = bridges("session_hang", session_timeout_sec=1.0)
        await bridge.initialize()
        with pytest.raises(AcpTimeoutError) as excinfo:
            await bridge.new_session(str(REPO_ROOT))
        assert excinfo.value.stage == "session/new"
        assert excinfo.value.waited is not None and excinfo.value.waited >= 0.9
        assert "session/new 超时" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_handshake_timeout_stage(self):
        """对"活着但不说话"的进程，initialize 必须按 handshake_timeout 超时并带阶段名。"""
        import psutil

        bridge = AcpBridge(
            name="mute",
            command=[str(PYTHON), "-c", "import time; time.sleep(120)"],
            cwd=str(REPO_ROOT),
            handshake_timeout_sec=4.0,
        )
        try:
            with pytest.raises(AcpTimeoutError) as excinfo:
                await bridge.initialize()
            assert excinfo.value.stage == "initialize"
            assert "initialize 超时" in str(excinfo.value)
            assert excinfo.value.waited is not None
        finally:
            pid = bridge.pid
            await bridge.stop()
        assert pid is not None
        for _ in range(50):
            if not psutil.pid_exists(pid):
                break
            await asyncio.sleep(0.1)
        assert not psutil.pid_exists(pid), "超时后子进程没有被回收"

    @pytest.mark.asyncio
    async def test_spawn_failure_is_process_gone(self):
        bridge = AcpBridge(
            name="missing",
            command=[str(PYTHON), str(FAKE_AGENT.parent / "definitely_missing_agent.py")],
            cwd=str(REPO_ROOT),
            handshake_timeout_sec=10.0,
        )
        with pytest.raises(AcpProcessGoneError) as excinfo:
            await bridge.initialize()
        assert excinfo.value.stage in ("spawn", "initialize")


# ============================================================
# 进程死亡 / 协议错误
# ============================================================

class TestFailures:
    @pytest.mark.asyncio
    async def test_abrupt_exit_raises_bridge_error(self, bridges):
        bridge = bridges("exit_before_prompt")
        with pytest.raises(AcpBridgeError):
            await bridge.initialize()
            await bridge.new_session(str(REPO_ROOT))

    @pytest.mark.asyncio
    async def test_prompt_error_propagates(self, bridges):
        bridge = bridges("prompt_error")
        await bridge.initialize()
        session = await bridge.new_session(str(REPO_ROOT))
        with pytest.raises(Exception) as excinfo:
            await bridge.prompt(session.session_id, "hi", timeout=20)
        assert "internal" in str(excinfo.value).lower() or "-32603" in str(excinfo.value)


# ============================================================
# 未声明能力：行为保持 + 留痕
# ============================================================

class TestUndeclaredCapabilities:
    @pytest.mark.asyncio
    async def test_terminal_calls_return_sdk_default_and_are_noted(self, bridges):
        noted: list[tuple[str, object]] = []
        bridge = bridges("normal", on_unhandled=lambda method, params: noted.append((method, params)))
        created = await bridge.create_terminal("ses_fake_1", "echo")
        assert created is None, "SDK 对 terminal/* 的默认结果是静默 null"
        assert noted and noted[0][0] == "terminal/create"

    @pytest.mark.asyncio
    async def test_fs_calls_raise_method_not_found(self, bridges):
        noted: list[tuple[str, object]] = []
        bridge = bridges("normal", on_unhandled=lambda method, params: noted.append((method, params)))
        import acp

        with pytest.raises(acp.RequestError) as excinfo:
            await bridge.read_text_file("ses_fake_1", "/tmp/x")
        assert excinfo.value.code == -32601
        assert noted and noted[0][0] == "fs/read_text_file"
