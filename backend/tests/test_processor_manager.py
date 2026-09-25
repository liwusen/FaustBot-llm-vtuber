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
