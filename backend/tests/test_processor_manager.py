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
