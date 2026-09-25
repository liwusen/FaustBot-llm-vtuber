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
