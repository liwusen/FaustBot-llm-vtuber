"""Processor 父子进程控制通道的帧协议。

承载：loopback TCP，帧格式 = 4 字节大端长度 + pickle。
独立于 stdio 的原因：torch/easyocr 会往 stdout 打印，stdio 专供日志行。
"""

from __future__ import annotations

import asyncio
import pickle
import struct
import traceback as _traceback
from dataclasses import dataclass, field
from typing import Any

from .errors import ProcessorFrameError

#: 单帧上限。4K 截图的 uint8 数组约 25 MB，留足余量。
MAX_FRAME_BYTES = 256 * 1024 * 1024

_HEADER = struct.Struct(">I")


# ── 父 -> 子 ──────────────────────────────────────────────


@dataclass
class StartOp:
    """请求启动 worker 侧 Processor。"""

    run_setup: bool
    config: dict = field(default_factory=dict)
    data_dir: str = ""


@dataclass
class InvokeOp:
    """一次计算请求；`req_id` 由父进程单调递增分配。"""

    req_id: int
    data: Any


@dataclass
class StopOp:
    """请求优雅停止。"""


# ── 子 -> 父 ──────────────────────────────────────────────


@dataclass
class Hello:
    """握手首帧。"""

    token: str
    pid: int
    name: str


@dataclass
class Phase:
    """阶段上报：``setting_up`` | ``starting``。"""

    phase: str


@dataclass
class Started:
    """setup/start 全部完成，worker 进入 ACTIVE。"""

    setup_ran: bool
    fingerprint: str


@dataclass
class StartFailed:
    """setup/start 失败，worker 即将退出。"""

    error: "ErrorInfo"


@dataclass
class Result:
    """`InvokeOp` 的应答。"""

    req_id: int
    ok: bool
    value: Any = None
    error: "ErrorInfo | None" = None


@dataclass
class LogMsg:
    """子进程日志。``source`` = ``ctx`` | ``stdout`` | ``stderr``。"""

    ts: float
    level: str
    message: str
    source: str


@dataclass
class ErrorInfo:
    """可跨进程传递的异常快照。"""

    type: str
    message: str
    traceback: str = ""

    @classmethod
    def from_exception(cls, exc: BaseException) -> "ErrorInfo":
        return cls(
            type=type(exc).__name__,
            message=str(exc),
            traceback="".join(_traceback.format_exception(type(exc), exc, exc.__traceback__)),
        )


# ── 编解码 ────────────────────────────────────────────────


def pack_frame(obj: Any) -> bytes:
    """把消息序列化为一帧。超限或序列化失败立即报错（不静默截断）。"""
    try:
        payload = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception as exc:  # noqa: BLE001 - 序列化失败必须暴露
        raise ProcessorFrameError(f"帧序列化失败: {exc}") from exc
    if len(payload) > MAX_FRAME_BYTES:
        raise ProcessorFrameError(f"帧过大: {len(payload)} 字节 > {MAX_FRAME_BYTES}")
    return _HEADER.pack(len(payload)) + payload


async def send_frame(writer: asyncio.StreamWriter, obj: Any) -> None:
    """写出一帧并 flush。"""
    writer.write(pack_frame(obj))
    await writer.drain()


async def recv_frame(reader: asyncio.StreamReader) -> Any:
    """读入一帧。连接断开抛 ``asyncio.IncompleteReadError``。"""
    header = await reader.readexactly(_HEADER.size)
    (size,) = _HEADER.unpack(header)
    if size > MAX_FRAME_BYTES:
        raise ProcessorFrameError(f"收到超限帧: 声明长度 {size} > {MAX_FRAME_BYTES}")
    payload = await reader.readexactly(size)
    try:
        return pickle.loads(payload)
    except Exception as exc:  # noqa: BLE001 - 解码失败必须暴露
        raise ProcessorFrameError(f"帧反序列化失败: {exc}") from exc
