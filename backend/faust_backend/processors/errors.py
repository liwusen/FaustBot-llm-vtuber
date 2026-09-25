"""Processor 机制异常层级。"""

from __future__ import annotations

from typing import Any


class ProcessorError(Exception):
    """Processor 机制异常基类。"""


class ProcessorNotFoundError(ProcessorError):
    """名字未在注册表中登记。"""

    def __init__(self, name: str) -> None:
        super().__init__(f"Processor 未注册: {name}")
        self.name = name


class ProcessorStartError(ProcessorError):
    """setup/start 失败、父进程握手失败，或等待就绪期间 worker 崩溃。"""

    def __init__(
        self,
        message: str,
        *,
        last_error: str | None = None,
        logs: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.last_error = last_error
        self.logs = list(logs or [])


class ProcessorNotReadyError(ProcessorError):
    """非 ACTIVE 状态下调用 invoke。"""


class ProcessorTimeoutError(ProcessorError):
    """setup/start/stop/invoke 超时。"""

    def __init__(self, message: str, *, phase: str | None = None) -> None:
        super().__init__(message)
        self.phase = phase


class ProcessorCrashedError(ProcessorError):
    """worker 意外退出导致在途/排队请求失败。"""

    def __init__(self, message: str, *, exit_code: int | None = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class ProcessorConfigMismatchError(ProcessorError):
    """Processor 已 ACTIVE 且仍被他人引用时，新请求的 config 与当前不一致。"""


class ProcessorInvokeError(ProcessorError):
    """Processor.invoke 在子进程内抛错。"""

    def __init__(self, message: str, *, error_type: str = "", traceback_text: str = "") -> None:
        super().__init__(message)
        self.error_type = error_type
        self.traceback_text = traceback_text


class ProcessorLeaseError(ProcessorError):
    """lease 重复 release / 未 acquire 就使用 / endRequire 无匹配 lease。"""


class ProcessorFrameError(ProcessorError):
    """控制通道帧超限或解码失败。"""
