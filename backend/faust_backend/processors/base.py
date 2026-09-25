"""Processor 基类与子进程内的运行时上下文。"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Callable, ClassVar

import faust_backend.config_loader as conf

#: marker 文件名（位于 ProcessorData 目录内）
SETUP_MARKER_NAME = "setup.json"


def resolve_data_dir(name: str, override: str | Path | None) -> Path:
    """解析 ProcessorData 目录。

    ``override`` 非空（类属性 ``DATA_DIR``）时按覆盖值使用——迁移已有数据的
    Processor（VAD 的 torch hub 目录）靠它把数据留在原处；否则默认
    ``<DATA_ROOT>/processors/<NAME>``。
    """
    if override:
        return Path(override).expanduser().resolve()
    return (Path(conf.DATA_ROOT) / "processors" / str(name)).resolve()


def read_setup_marker(data_dir: str | Path) -> dict[str, Any]:
    """读取 setup marker；缺失或损坏返回空 dict（调用方据此重跑 setup）。"""
    path = Path(data_dir) / SETUP_MARKER_NAME
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_setup_marker(data_dir: str | Path, fingerprint: str) -> None:
    """写入 setup marker（目录不存在时创建）。"""
    directory = Path(data_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprint": str(fingerprint),
        "completed_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    with (directory / SETUP_MARKER_NAME).open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


class ProcessorContext:
    """Processor 钩子在子进程内拿到的上下文。

    ``log_sink`` 由 worker 注入（把日志变成 ``LogMsg`` 帧），因此钩子里调用
    ``ctx.log`` 不会经过子进程自己的 logging/文件 handler。
    """

    def __init__(
        self,
        name: str,
        data_dir: str | Path,
        config: dict[str, Any],
        log_sink: Callable[[str, str], None],
    ) -> None:
        self.name = str(name)
        self.data_dir = Path(data_dir)
        self.config = dict(config or {})
        self._log_sink = log_sink

    def log(self, message: Any, level: str = "INFO") -> None:
        """上报一条日志（任意可序列化对象，非 str 用 ``str()`` 化）。"""
        text = message if isinstance(message, str) else str(message)
        self._log_sink(str(level).upper(), text)

    def read_marker(self) -> dict[str, Any]:
        """读 setup marker。"""
        return read_setup_marker(self.data_dir)

    def write_marker(self, fingerprint: str) -> None:
        """写 setup marker。"""
        write_setup_marker(self.data_dir, fingerprint)


class Processor:
    """一类计算任务的类定义（名字唯一）。"""

    #: 全局唯一的 Processor 名（如 "VAD" / "OCR"）
    NAME: ClassVar[str] = ""
    #: ProcessorData 目录覆盖值；None = <DATA_ROOT>/processors/<NAME>
    DATA_DIR: ClassVar[str | Path | None] = None
    #: 需要重跑 setup 时 bump
    SETUP_VERSION: ClassVar[str] = "1"
    SETUP_TIMEOUT: ClassVar[float] = 1800.0
    START_TIMEOUT: ClassVar[float] = 300.0
    STOP_TIMEOUT: ClassVar[float] = 15.0
    #: None = 不限时
    INVOKE_TIMEOUT: ClassVar[float | None] = None
    #: 父进程日志环形缓冲容量
    LOG_BUFFER: ClassVar[int] = 500

    def setup(self, ctx: ProcessorContext) -> None:
        """一次性耗时初始化（例如下载模型）。默认无操作。"""

    def start(self, ctx: ProcessorContext) -> None:
        """每次进程启动的初始化（例如把模型加载进内存）。必须实现。"""
        raise NotImplementedError

    def invoke(self, ctx: ProcessorContext, data: Any) -> Any:
        """一次计算。必须实现。"""
        raise NotImplementedError

    def stop(self, ctx: ProcessorContext) -> None:
        """释放资源。默认无操作。"""

    def setup_fingerprint(self, config: dict[str, Any]) -> str:
        """setup 是否可跳过的指纹；config 变化会影响模型文件时需覆写。"""
        return str(self.SETUP_VERSION)
