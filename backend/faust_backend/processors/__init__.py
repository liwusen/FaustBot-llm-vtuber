"""Processor 机制：把重计算放进受管子进程。

包导入刻意保持轻量（不 import .manager）：worker 子进程会 import
``faust_backend.processors.protocol`` 等子模块，若包导入顺带拉起 manager，
子进程就会初始化 faust logger 的 root handler（同一个日志文件 + WS 队列）
与 psutil。manager 相关名字通过 PEP 562 ``__getattr__`` 惰性导出。
"""

from __future__ import annotations

from .base import Processor, ProcessorContext
from .errors import (
    ProcessorConfigMismatchError,
    ProcessorCrashedError,
    ProcessorError,
    ProcessorFrameError,
    ProcessorInvokeError,
    ProcessorLeaseError,
    ProcessorNotFoundError,
    ProcessorNotReadyError,
    ProcessorStartError,
    ProcessorTimeoutError,
)
from .registry import processor, register_processor

__all__ = [
    "Processor",
    "ProcessorContext",
    "processor",
    "register_processor",
    "ProcessorError",
    "ProcessorNotFoundError",
    "ProcessorStartError",
    "ProcessorNotReadyError",
    "ProcessorTimeoutError",
    "ProcessorCrashedError",
    "ProcessorConfigMismatchError",
    "ProcessorInvokeError",
    "ProcessorLeaseError",
    "ProcessorFrameError",
    "ProcessorManager",
    "ProcessorHandle",
    "ProcessorLease",
    "PruneReport",
    "get_processor_manager",
]

_LAZY_MANAGER_NAMES = frozenset(
    {"ProcessorManager", "ProcessorHandle", "ProcessorLease", "PruneReport", "get_processor_manager"}
)


def __getattr__(name: str):
    if name in _LAZY_MANAGER_NAMES:
        from . import manager

        return getattr(manager, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
