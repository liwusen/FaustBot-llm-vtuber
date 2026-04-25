"""
FaustBot 统一日志系统

提供标准化的 logging 配置，支持：
- 模块分类（faust.main, faust.trigger, faust.asr 等）
- 日志等级（DEBUG/INFO/WARNING/ERROR）
- 控制台输出（彩色格式）
- 文件输出（RotatingFileHandler，自动轮转）
- WebSocket 推送（供前端日志面板订阅）
- 环状缓冲区（最多 500 条，防内存泄漏）

用法：
    from faust_backend.logger import get_logger
    log = get_logger("faust.main")
    log.info("服务启动")
    log.error("连接失败", exc_info=True)
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_FILE = LOG_DIR / "faust.log"
LOG_FILE_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
LOG_FORMAT_COLOR = (
    "%(asctime)s \033[1m%(levelname_prefix)s\033[0m %(name)s: %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# WebSocket 日志队列 — 环状缓冲区，最多 500 条
WS_LOG_QUEUE_MAX = 500
_ws_log_queue: asyncio.Queue[dict[str, Any]] | None = None
_ws_subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
_ws_sub_lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# 日志等级 → 颜色 / 控制台前缀
# ---------------------------------------------------------------------------

LEVEL_STYLES = {
    logging.DEBUG: ("\033[90m", "DEBUG  "),  # 灰
    logging.INFO: ("\033[92m", "INFO   "),   # 绿
    logging.WARNING: ("\033[93m", "WARNING"),  # 黄
    logging.ERROR: ("\033[91m", "ERROR  "),  # 红
    logging.CRITICAL: ("\033[91;1m", "CRITICAL"),  # 红+粗
}

# ---------------------------------------------------------------------------
# 自定义 Formatter — 控制台带颜色
# ---------------------------------------------------------------------------


class _ColorFormatter(logging.Formatter):
    """控制台用彩色 Formatter。"""

    def __init__(self) -> None:
        super().__init__(LOG_FORMAT_COLOR, datefmt=DATE_FORMAT)

    def format(self, record: logging.LogRecord) -> str:
        style = LEVEL_STYLES.get(record.levelno, ("\033[0m", "OTHER"))
        color, prefix = style
        record.levelname_prefix = f"{color}{prefix}\033[0m"  # type: ignore[attr-defined]
        record.name = record.name  # keep
        return super().format(record)


class _PlainFormatter(logging.Formatter):
    """文件用纯文本 Formatter。"""

    def __init__(self) -> None:
        super().__init__(LOG_FORMAT, datefmt=DATE_FORMAT)


# ---------------------------------------------------------------------------
# WebSocket Log Handler
# ---------------------------------------------------------------------------


class _WebSocketLogHandler(logging.Handler):
    """将日志记录推送到 WebSocket 异步队列。

    每个记录被序列化为 dict，推送到所有订阅者队列。
    队列满时丢弃最旧记录。
    """

    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._formatter = _PlainFormatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = self._format_record(record)
            # 推送到全局队列
            global _ws_log_queue
            if _ws_log_queue is None:
                _ws_log_queue = asyncio.Queue(maxsize=WS_LOG_QUEUE_MAX)

            try:
                _ws_log_queue.put_nowait(payload)
            except asyncio.QueueFull:
                # 丢弃最旧的一条
                try:
                    _ws_log_queue.get_nowait()
                    _ws_log_queue.put_nowait(payload)
                except asyncio.QueueEmpty:
                    pass

            # 推送到所有活跃订阅者
            self._push_to_subscribers(payload)
        except Exception:
            self.handleError(record)

    def _format_record(self, record: logging.LogRecord) -> dict[str, Any]:
        return {
            "timestamp": self.formatter.formatTime(record, self.formatter.datefmt)
            if self.formatter
            else record.asctime,
            "level": record.levelname,
            "levelno": record.levelno,
            "name": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "funcName": record.funcName,
            "lineno": record.lineno,
        }

    def _push_to_subscribers(self, payload: dict[str, Any]) -> None:
        """将日志推送到所有活跃的 WebSocket 订阅者队列。"""
        dead: list[asyncio.Queue[dict[str, Any]]] = []
        for q in _ws_subscribers:
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                dead.append(q)
            except Exception:
                dead.append(q)
        for q in dead:
            _ws_subscribers.discard(q)


# ---------------------------------------------------------------------------
# 模块级全局配置
# ---------------------------------------------------------------------------

_loggers: dict[str, logging.Logger] = {}
_initialized = False


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _build_handlers() -> list[logging.Handler]:
    _ensure_log_dir()

    # 控制台
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG)
    console.setFormatter(_ColorFormatter())

    # 文件 (轮转)
    file_handler = logging.handlers.RotatingFileHandler(
        str(LOG_FILE),
        maxBytes=LOG_FILE_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=True,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_PlainFormatter())

    # WebSocket
    ws_handler = _WebSocketLogHandler(level=logging.INFO)
    ws_handler.setFormatter(_PlainFormatter())

    return [console, file_handler, ws_handler]


def _init_root() -> None:
    """初始化 root logger。"""
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in _build_handlers():
        root.addHandler(h)

    # 抑制过于嘈杂的第三方库日志
    for noisy in (
        "httpx",
        "httpcore",
        "urllib3",
        "openai",
        "langchain",
        "langgraph",
        "langsmith",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """获取统一配置的 Logger。

    Args:
        name: Logger 名称，建议用点号分隔的层级，如 ``faust.main``、``faust.trigger``。

    Returns:
        标准 ``logging.Logger`` 实例，已附加 Fauste 的自定义 Handler。
    """
    _init_root()
    if name in _loggers:
        return _loggers[name]
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    _loggers[name] = logger
    return logger


# ---------------------------------------------------------------------------
# WebSocket 订阅管理
# ---------------------------------------------------------------------------


async def subscribe_ws() -> asyncio.Queue[dict[str, Any]]:
    """创建一个新的 WebSocket 订阅者队列。

    前端 WebSocket 端点调用此函数获取一个专属队列，
    从中读取日志记录并发送给前端。

    Returns:
        一个 ``asyncio.Queue``，包含序列化后的日志记录 dict。
    """
    q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=WS_LOG_QUEUE_MAX)

    # 如果是第一个订阅者，推送最近的缓冲区历史
    global _ws_log_queue
    if _ws_log_queue is not None:
        snapshot: list[dict[str, Any]] = []
        try:
            while True:
                snapshot.append(_ws_log_queue.get_nowait())
        except asyncio.QueueEmpty:
            pass
        for item in snapshot:
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                break

    async with _ws_sub_lock:
        _ws_subscribers.add(q)
    return q


async def unsubscribe_ws(q: asyncio.Queue[dict[str, Any]]) -> None:
    """取消 WebSocket 订阅。"""
    async with _ws_sub_lock:
        _ws_subscribers.discard(q)
