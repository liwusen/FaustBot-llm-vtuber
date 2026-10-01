"""插件 / Agile 模块调用 Processor 的入口。

一次调用 = 借出 lease → 等就绪 → invoke → 归还（引用计数因此不会泄漏）。Processor 的 setup
（模型下载 / 量化）发生在首次借出时：``wait_timeout=None`` 表示一直等到就绪，上限由 Processor
自己的 ``SETUP_TIMEOUT`` 决定。

跨循环安全：``require()`` 是同步的；``acquire`` / ``wait_until_ready`` / ``invoke`` / ``release``
都由 ``ProcessorHandle`` 内部切回 worker 所在循环，所以插件自己的事件循环、Agile 模块的
interval 线程里都能直接 await。
"""

from __future__ import annotations

import time
from typing import Any

from faust_backend.logger import get_logger

from .manager import get_processor_manager

log = get_logger("faust.processor.plugin")

#: 等待就绪超过这么久就记一条 INFO（首次调用通常包含模型下载/量化）
SLOW_READY_LOG_SECONDS = 1.0


async def invoke_processor(name: str, data: Any, *, requirer: str, config: dict[str, Any] | None = None,
                           timeout: float | None = None, wait_timeout: float | None = None) -> Any:
    """调用一个 Processor：借出 → 等就绪 → invoke → 归还。"""
    manager = get_processor_manager()
    lease = manager.require(str(name), requirer=str(requirer), config=config)
    started = time.monotonic()
    await lease.acquire()            # 触发启动（首次含 setup）；失败时不归还，引用并未登记
    try:
        await lease.wait_until_ready(timeout=wait_timeout)
        waited = time.monotonic() - started
        if waited > SLOW_READY_LOG_SECONDS:
            log.info("%s 为 %s 就绪等待了 %.1fs（首次使用通常包含 setup）", name, requirer, waited)
        return await lease.invoke(data, timeout=timeout)
    finally:
        lease.release()


def processor_status(name: str) -> dict[str, Any]:
    """某个 Processor 的状态快照（未知名字抛 ``ProcessorNotFoundError``）。"""
    return get_processor_manager().handle(str(name)).status()
