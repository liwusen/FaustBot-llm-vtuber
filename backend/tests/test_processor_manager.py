from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np
import psutil
import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# ── Task 1: 异常层 ──────────────────────────────────────────


def test_exception_hierarchy_and_payloads():
    from faust_backend.processors import errors

    for exc in (
        errors.ProcessorNotFoundError,
        errors.ProcessorStartError,
        errors.ProcessorNotReadyError,
        errors.ProcessorTimeoutError,
        errors.ProcessorCrashedError,
        errors.ProcessorConfigMismatchError,
        errors.ProcessorInvokeError,
        errors.ProcessorLeaseError,
        errors.ProcessorFrameError,
    ):
        assert issubclass(exc, errors.ProcessorError)

    not_found = errors.ProcessorNotFoundError("VAD")
    assert not_found.name == "VAD"
    assert "VAD" in str(not_found)

    start_error = errors.ProcessorStartError(
        "启动失败", last_error="ModuleNotFoundError: torch", logs=[{"level": "ERROR", "message": "boom"}]
    )
    assert start_error.last_error == "ModuleNotFoundError: torch"
    assert start_error.logs == [{"level": "ERROR", "message": "boom"}]

    timeout = errors.ProcessorTimeoutError("start 超时", phase="start")
    assert timeout.phase == "start"

    crashed = errors.ProcessorCrashedError("worker 退出了", exit_code=7)
    assert crashed.exit_code == 7

    invoke_error = errors.ProcessorInvokeError("调用失败", error_type="ValueError", traceback_text="Traceback ...")
    assert invoke_error.error_type == "ValueError"
    assert invoke_error.traceback_text == "Traceback ..."


# ── Task 2: 帧协议 ──────────────────────────────────────────


def test_frame_roundtrip_over_tcp():
    from faust_backend.processors import protocol as proto

    async def _roundtrip() -> object:
        received: list[object] = []
        done = asyncio.Event()

        async def _handler(reader, writer):
            received.append(await proto.recv_frame(reader))
            writer.close()
            done.set()

        server = await asyncio.start_server(_handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await proto.send_frame(writer, proto.InvokeOp(req_id=7, data={"audio": b"\x00\x01", "n": 3}))
        await asyncio.wait_for(done.wait(), 5)
        writer.close()
        server.close()
        await server.wait_closed()
        return received[0]

    got = asyncio.run(_roundtrip())
    assert isinstance(got, proto.InvokeOp)
    assert got.req_id == 7
    assert got.data == {"audio": b"\x00\x01", "n": 3}


def test_frame_size_limit_is_enforced(monkeypatch):
    from faust_backend.processors import protocol as proto
    from faust_backend.processors.errors import ProcessorFrameError

    monkeypatch.setattr(proto, "MAX_FRAME_BYTES", 16)
    with pytest.raises(ProcessorFrameError):
        proto.pack_frame(b"x" * 64)


def test_error_info_from_exception_keeps_type_and_traceback():
    from faust_backend.processors.protocol import ErrorInfo

    try:
        raise ValueError("坏输入")
    except ValueError as exc:
        info = ErrorInfo.from_exception(exc)

    assert info.type == "ValueError"
    assert info.message == "坏输入"
    assert "ValueError: 坏输入" in info.traceback


# ── Task 3: 注册表 ──────────────────────────────────────────


def test_registry_rejects_incomplete_and_duplicate_processors():
    from faust_backend.processors.base import Processor
    from faust_backend.processors.errors import ProcessorError, ProcessorNotFoundError
    from faust_backend.processors import registry

    class Incomplete(Processor):
        NAME = "TEST_INCOMPLETE_REG"

    with pytest.raises(ProcessorError):
        registry.register_processor(Incomplete, owner="test_tmp_registry")

    class Ok(Processor):
        NAME = "TEST_OK_REG"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    name = registry.register_processor(Ok, owner="test_tmp_registry", loader="/tmp/fake_plugin.py")
    assert name == "TEST_OK_REG"
    entry = registry.get_processor(name)
    assert entry.owner == "test_tmp_registry"
    assert entry.loader == "/tmp/fake_plugin.py"
    assert entry.cls is Ok

    with pytest.raises(ProcessorError):
        registry.register_processor(Ok, owner="test_tmp_registry", loader="/tmp/fake_plugin.py")

    assert registry.unregister_owner("test_tmp_registry") == [name]
    with pytest.raises(ProcessorNotFoundError):
        registry.get_processor(name)


# ── Task 4: 基类与 Context ──────────────────────────────────


def test_resolve_data_dir_default_and_override(tmp_path, monkeypatch):
    import faust_backend.config_loader as conf
    from faust_backend.processors.base import resolve_data_dir

    monkeypatch.setattr(conf, "DATA_ROOT", str(tmp_path / "data"))
    assert resolve_data_dir("DEMO", None) == (tmp_path / "data" / "processors" / "DEMO")
    assert resolve_data_dir("DEMO", str(tmp_path / "legacy")) == (tmp_path / "legacy")


def test_setup_marker_roundtrip_and_broken_file(tmp_path):
    from faust_backend.processors.base import read_setup_marker, write_setup_marker

    assert read_setup_marker(tmp_path) == {}
    write_setup_marker(tmp_path, "1:ch_sim|en")
    marker = read_setup_marker(tmp_path)
    assert marker["fingerprint"] == "1:ch_sim|en"
    assert marker["completed_at"]

    (tmp_path / "setup.json").write_text("{ 不是 json", encoding="utf-8")
    assert read_setup_marker(tmp_path) == {}


def test_context_log_stringifies_and_uppercases_level(tmp_path):
    from faust_backend.processors.base import ProcessorContext

    seen: list[tuple[str, str]] = []
    ctx = ProcessorContext(
        name="DEMO",
        data_dir=tmp_path,
        config={"a": 1},
        log_sink=lambda level, message: seen.append((level, message)),
    )
    ctx.log(123)
    ctx.log("boom", level="warning")
    assert seen == [("INFO", "123"), ("WARNING", "boom")]
    assert ctx.config == {"a": 1}


def test_processor_defaults_and_fingerprint():
    from faust_backend.processors.base import Processor

    class Demo(Processor):
        NAME = "TEST_DEFAULTS"
        SETUP_VERSION = "7"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    demo = Demo()
    assert demo.setup_fingerprint({"any": "config"}) == "7"
    assert Demo.INVOKE_TIMEOUT is None
    assert Demo.LOG_BUFFER == 500
    assert Demo.DATA_DIR is None
    assert Processor.start is Demo.start or Demo.start is not Processor.start

# ── Task 5: worker + manager 最小闭环 ───────────────────────

from faust_backend.processors.base import Processor, ProcessorContext  # noqa: E402
from faust_backend.processors.registry import register_processor  # noqa: E402


class EchoProcessor(Processor):
    """最小可用 Processor：setup 留痕、invoke 回显。"""

    NAME = "TEST_ECHO"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0
    STOP_TIMEOUT = 5.0

    def setup(self, ctx: ProcessorContext) -> None:
        with (ctx.data_dir / "setup_runs.txt").open("a", encoding="utf-8") as fh:
            fh.write("setup\n")

    def start(self, ctx: ProcessorContext) -> None:
        ctx.log("echo 已 start")
        print("[echo] stdout 也能被采集")

    def invoke(self, ctx: ProcessorContext, data):
        with (ctx.data_dir / "invokes.txt").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False) + "\n")
        return {"echo": data}

    def stop(self, ctx: ProcessorContext) -> None:
        ctx.log("echo 已 stop")


class FailSetupProcessor(Processor):
    NAME = "TEST_FAIL_SETUP"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx: ProcessorContext) -> None:
        raise ValueError("模型下载失败")

    def start(self, ctx: ProcessorContext) -> None:
        pass

    def invoke(self, ctx: ProcessorContext, data):
        return data


class FailStartProcessor(Processor):
    NAME = "TEST_FAIL_START"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx: ProcessorContext) -> None:
        pass

    def start(self, ctx: ProcessorContext) -> None:
        raise RuntimeError("权重文件损坏")

    def invoke(self, ctx: ProcessorContext, data):
        return data


register_processor(EchoProcessor, owner="test")
register_processor(FailSetupProcessor, owner="test")
register_processor(FailStartProcessor, owner="test")


@pytest_asyncio.fixture
async def manager_factory(tmp_path, monkeypatch):
    """每个测试独立的 ProcessorManager；退出时统一 shutdown。"""
    from faust_backend.processors.manager import ProcessorManager

    monkeypatch.setattr(
        EchoProcessor, "DATA_DIR", str(tmp_path / "data" / "TEST_ECHO"), raising=False
    )
    monkeypatch.setattr(
        FailSetupProcessor, "DATA_DIR", str(tmp_path / "data" / "TEST_FAIL_SETUP"), raising=False
    )
    monkeypatch.setattr(
        FailStartProcessor, "DATA_DIR", str(tmp_path / "data" / "TEST_FAIL_START"), raising=False
    )
    managers: list[ProcessorManager] = []

    def _make() -> ProcessorManager:
        manager = ProcessorManager()
        managers.append(manager)
        return manager

    yield _make
    for manager in managers:
        await manager.shutdown()


@pytest.mark.asyncio
async def test_start_reaches_active_and_reports_status(manager_factory):
    manager = manager_factory()
    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)

    snapshot = manager.handle("TEST_ECHO").status()
    assert snapshot["state"] == "ACTIVE"
    assert snapshot["owner"] == "test"
    assert snapshot["refcount"] == 1
    assert snapshot["holders"] == {"t1": 1}
    assert snapshot["pid"] and snapshot["pid_alive"] is True
    assert snapshot["setup_done"] is True
    assert snapshot["uptime_seconds"] is not None

    logs = await lease.get_log()
    assert any("echo 已 start" in item["message"] for item in logs)
    lease.release()


@pytest.mark.asyncio
async def test_setup_runs_once_then_marker_skips_it(manager_factory):
    manager = manager_factory()
    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)
    lease.release()
    await manager.stop("TEST_ECHO")

    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)

    setup_runs = (Path(EchoProcessor.DATA_DIR) / "setup_runs.txt").read_text(encoding="utf-8")
    assert setup_runs.count("setup") == 1
    lease.release()


@pytest.mark.asyncio
async def test_setup_failure_surfaces_start_error_and_logs(manager_factory):
    from faust_backend.processors.errors import ProcessorStartError

    manager = manager_factory()
    lease = await manager.require("TEST_FAIL_SETUP", requirer="t1").acquire()
    with pytest.raises(ProcessorStartError) as excinfo:
        await lease.wait_until_ready(timeout=30)

    assert "模型下载失败" in str(excinfo.value)
    handle = manager.handle("TEST_FAIL_SETUP")
    assert handle.state == "STOPPED"
    assert "模型下载失败" in (handle.last_error or "")
    logs = await handle.get_log(level="ERROR")
    assert any("ValueError" in item["message"] for item in logs)
    lease.release()


@pytest.mark.asyncio
async def test_start_failure_surfaces_start_error(manager_factory):
    from faust_backend.processors.errors import ProcessorStartError

    manager = manager_factory()
    lease = await manager.require("TEST_FAIL_START", requirer="t1").acquire()
    with pytest.raises(ProcessorStartError):
        await lease.wait_until_ready(timeout=30)
    assert manager.handle("TEST_FAIL_START").state == "STOPPED"
    lease.release()


@pytest.mark.asyncio
async def test_invoke_roundtrip_and_stdout_capture(manager_factory):
    manager = manager_factory()
    async with manager.require("TEST_ECHO", requirer="t1") as lease:
        await lease.wait_until_ready(timeout=30)
        assert await lease.invoke({"n": 1}) == {"echo": {"n": 1}}
        logs = await lease.get_log()
        assert any(item["source"] == "stdout" and "stdout 也能被采集" in item["message"] for item in logs)
        assert any(item["message"] == "echo 已 start" for item in logs)


@pytest.mark.asyncio
async def test_invoke_requires_active_state(manager_factory):
    from faust_backend.processors.errors import ProcessorNotReadyError

    manager = manager_factory()
    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)
    await manager.stop("TEST_ECHO")
    with pytest.raises(ProcessorNotReadyError):
        await lease.invoke({"n": 1})
    lease.release()


@pytest.mark.asyncio
async def test_shutdown_stops_all_workers(manager_factory):
    manager = manager_factory()
    lease = await manager.require("TEST_ECHO", requirer="t1").acquire()
    await lease.wait_until_ready(timeout=30)
    pid = manager.handle("TEST_ECHO").pid

    await manager.shutdown()

    snapshot = manager.handle("TEST_ECHO").status()
    assert snapshot["state"] == "STOPPED"
    assert snapshot["pid"] is None
    assert not psutil.pid_exists(int(pid))


# ── Task 6: 引用计数 ────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_requirers_count_references(manager_factory):
    manager = manager_factory()
    first = await manager.startRequire("TEST_ECHO", requirer="chat")
    second = await manager.startRequire("TEST_ECHO", requirer="chat")
    third = await manager.startRequire("TEST_ECHO", requirer="ui")

    handle = manager.handle("TEST_ECHO")
    assert handle.refcount == 3
    assert handle.holders == {"chat": 2, "ui": 1}
    # startRequire 不等待就绪；三次调用复用同一次启动（同一个 _start_task）
    start_task = handle._start_task
    assert start_task is not None
    await first.wait_until_ready(timeout=30)
    assert handle.state == "ACTIVE"
    assert handle._start_task is start_task

    await manager.endRequire("TEST_ECHO", "chat")
    await manager.endRequire("TEST_ECHO", "chat")
    await manager.endRequire("TEST_ECHO", "ui")
    assert handle.refcount == 0
    assert handle.holders == {}
    assert handle.idle_since is not None


@pytest.mark.asyncio
async def test_end_require_without_match_raises(manager_factory):
    from faust_backend.processors.errors import ProcessorLeaseError

    manager = manager_factory()
    lease = await manager.startRequire("TEST_ECHO", requirer="chat")
    with pytest.raises(ProcessorLeaseError):
        await manager.endRequire("TEST_ECHO", "nobody")
    assert manager.handle("TEST_ECHO").refcount == 1
    lease.release()


@pytest.mark.asyncio
async def test_lease_lifecycle_errors(manager_factory):
    from faust_backend.processors.errors import ProcessorLeaseError

    manager = manager_factory()
    lease = manager.require("TEST_ECHO", requirer="chat")
    with pytest.raises(ProcessorLeaseError):
        lease.release()                      # 未 acquire 就 release

    await lease.acquire()
    with pytest.raises(ProcessorLeaseError):
        await lease.acquire()                # 重复 acquire
    lease.release()
    with pytest.raises(ProcessorLeaseError):
        lease.release()                      # 重复 release
    with pytest.raises(ProcessorLeaseError):
        await lease.invoke({"n": 1})         # 已 release 的 lease 不能再用


@pytest.mark.asyncio
async def test_async_with_releases_reference(manager_factory):
    manager = manager_factory()
    async with manager.require("TEST_ECHO", requirer="vad_ws:1") as lease:
        await lease.wait_until_ready(timeout=30)
        assert manager.handle("TEST_ECHO").holders == {"vad_ws:1": 1}
    handle = manager.handle("TEST_ECHO")
    assert handle.refcount == 0
    assert handle.state == "ACTIVE"          # 引用归零不自动停，交给 prune
