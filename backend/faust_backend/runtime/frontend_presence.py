"""前端在线信号：由传输层写，由触发器域读（D19 离线禁触）。

形状与 ``turn_gate`` 同类：**状态由一处写、由另一处读**，不加锁。写入方只有一个
—— ``/faust/command`` 连接的 accept/finally（``routes/chat.py``）；读取方是触发器
域（``trigger_manager._emit_trigger`` / ``append_trigger``）与调试接口。

为什么绑 ``/faust/command`` 连接而不是 chat 连接
------------------------------------------------
触发器消费本身就跑在 ``/faust/command`` 的接收循环里（D18：没有该连接就完全不
消费，这是 Feature 不是缺陷）。离线信号必须与"到底有没有人消费"同源，否则会出现
"判定在线、却没有任何消费者"的幽灵触发。

线程可见性
----------
``is_online()`` 会被 trigger watchdog **线程**读取（定时/事件触发器在那里 fire），
因此状态只用一个 ``dict`` 的进出 + 只读判断表达，不做需要跨线程互斥的复合更新。
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# 连接集合：任意一个 /faust/command 连接在线 = 前端在线。
# 用 dict 而不是 set 只是为了保留"谁在线"的可观测顺序（调试快照用）。
_online_connections: dict[Any, None] = {}

_refused_by_reason: Counter[str] = Counter()
_refused_by_source: Counter[str] = Counter()

_DEFAULT_CONNECTION = object()


def mark_online(connection: Any = None) -> None:
    """登记一条在线连接（``/faust/command`` accept 之后调用）。"""
    _online_connections[connection if connection is not None else _DEFAULT_CONNECTION] = None


def mark_offline(connection: Any = None) -> None:
    """注销一条连接；``connection=None`` 表示清空全部（测试/进程收尾）。"""
    if connection is None:
        _online_connections.clear()
        return
    _online_connections.pop(connection, None)


def is_online() -> bool:
    """是否至少有一条 ``/faust/command`` 连接在线。"""
    return bool(_online_connections)


def online_count() -> int:
    return len(_online_connections)


def note_refused(source: str, reason: str = "no_frontend") -> None:
    """记一次被拒绝的触发。拒绝只进日志 + 计数，不写 fire 留痕（D19）。"""
    _refused_by_reason[str(reason or "unknown")] += 1
    _refused_by_source[str(source or "unknown")] += 1


def refused_total() -> int:
    return sum(_refused_by_reason.values())


def snapshot() -> dict[str, Any]:
    """调试快照（挂到 ``GET /faust/debugging/trigger-queue``）。"""
    return {
        "online": is_online(),
        "connections": online_count(),
        "refused_total": refused_total(),
        "refused_by_reason": dict(_refused_by_reason),
        "refused_by_source": dict(_refused_by_source),
    }


def reset() -> None:
    """清空全部状态（单测隔离用）。"""
    _online_connections.clear()
    _refused_by_reason.clear()
    _refused_by_source.clear()
