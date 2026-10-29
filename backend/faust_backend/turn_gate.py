"""回合级播放闸门: 触发器必须等上一轮回复播完(或超时)才允许被消费。

问题背景
--------
触发器消费的唯一门槛是 ``events.ignore_trigger_event``, 它在 ``_on_agent_task_done``
里被清掉 —— 那个时机是 **LLM 流结束**, 不是 **音频播放结束**。于是用户刚拿到回复
(甚至语音还在念) 时, 队列里的感知触发器会被立刻消费并向前端推 ``start``,
把气泡整屏替换掉、同时掐断正在播放的语音。

本模块把"上一轮回复有没有被用户消费完"变成一个带截止时间的判据:

* ``arm()``                一轮回复的 LLM 流结束时登记: turn_id / 字数 / 连接
* ``note_playback_done()`` 前端回报"这一轮念完了"(走 chat WS), 提前结束等待
* ``should_hold()``        消费侧询问: 现在能不能唤醒触发器
* ``release_for_websocket()`` 连接断开时立即释放, 避免队列停摆

放行时刻 = ``done_ts + max(read_floor, min(ack - done_ts, max_hold))``

* ``read_floor`` 按回复字数估算的阅读下限, 覆盖 TTS 关闭 / 纯工具回合等没有音频的情况
* ``max_hold``   安全网: 前端崩溃或从不回报时, 触发器队列也不会一直卡住

即: 收到 ack → 取"ack 时刻"与"阅读下限"的较晚者; 收不到 ack → 等满 max_hold 再放行
(没有 ack 说明前端不可信, 不能假设它已经念完)。

仅由事件循环内的协程使用(chat WS 接收循环 + command WS 消费循环), 不加锁;
trigger watchdog 线程不访问本模块。时间参数一律可注入, 便于单测不 sleep。
"""
from __future__ import annotations

import time
import uuid
from typing import Any

import faust_backend.core.config as conf

# 阅读下限的封顶值: 再长的回复也不会因为 read_floor 把触发器压太久
READ_FLOOR_CAP_SEC = 8.0
# 每多少字符折算 1 秒阅读时间
READ_CHARS_PER_SEC = 20.0


def new_turn_id() -> str:
    """生成一轮前台回复的标识, 随 start/done 下发, 由前端 ack 回带。"""
    return f"{int(time.time() * 1000):x}-{uuid.uuid4().hex[:8]}"


def _read_floor(text_len: int) -> float:
    try:
        floor = float(conf.TRIGGER_READ_FLOOR_SEC)
    except (TypeError, ValueError):
        floor = 1.5
    return min(max(float(text_len or 0) / READ_CHARS_PER_SEC, floor), READ_FLOOR_CAP_SEC)


def _max_hold() -> float:
    try:
        return max(0.0, float(conf.TRIGGER_HOLD_MAX_SEC))
    except (TypeError, ValueError):
        return 30.0


_record: dict[str, Any] | None = None


def arm(turn_id: str, text_len: int, websocket: Any = None, now: float | None = None) -> None:
    """登记一轮回复(LLM 流已结束、音频尚未播完时调用)。

    必须在该轮 ``done`` 发出之前调用, 否则前端可能在登记之前就把 ack 送回来。
    新一轮覆盖旧一轮: 用户在 hold 期间又发消息时, 以最新一轮的播放为准。
    """
    global _record
    ts = time.time() if now is None else now
    deadline = ts + _max_hold()
    _record = {
        "turn_id": str(turn_id or ""),
        "done_ts": ts,
        "deadline_ts": deadline,
        # 初始放行时刻 = 安全网: 收不到 ack 就等满 max_hold, 收到 ack 再提前
        "release_ts": deadline,
        "floor_ts": ts + _read_floor(text_len),
        "websocket": websocket,
        "acked": False,
    }


def note_playback_done(turn_id: str, now: float | None = None) -> bool:
    """前端回报"这一轮念完了"。turn_id 对不上则忽略, 返回是否命中。"""
    global _record
    rec = _record
    if rec is None or str(turn_id or "") != rec["turn_id"]:
        return False
    ts = time.time() if now is None else now
    rec["acked"] = True
    # ack 把放行时刻提前到「ack 与阅读下限的较晚者」; 绝不越过 max_hold 的安全网
    rec["release_ts"] = min(max(rec["floor_ts"], ts), rec["deadline_ts"])
    if ts >= rec["release_ts"]:
        _record = None
    return True


def release_for_websocket(websocket: Any, now: float | None = None) -> bool:
    """发起的 chat 连接断开时释放 hold(不回带 ack 就永远不会放行的兜底)。"""
    global _record
    rec = _record
    if rec is None or rec["websocket"] is not websocket:
        return False
    _record = None
    return True


def should_hold(priority: str = "normal", now: float | None = None) -> bool:
    """消费侧判据: True 表示现在还不能唤醒触发器。

    ``interrupt``(告警/紧急)恒不 hold; 其余在 release_ts 之前为 True,
    到点即释放(顺带清空记录)。
    """
    global _record
    rec = _record
    if rec is None:
        return False
    if str(priority or "") == "interrupt":
        return False
    ts = time.time() if now is None else now
    if ts >= rec["release_ts"] or ts >= rec["deadline_ts"]:
        _record = None
        return False
    return True


def hold_remaining(now: float | None = None) -> float:
    """距离可以唤醒触发器还剩多少秒; 没有 hold 时返回 0.0(供消费循环算等待时长)。"""
    rec = _record
    if rec is None:
        return 0.0
    ts = time.time() if now is None else now
    return max(0.0, min(rec["release_ts"], rec["deadline_ts"]) - ts)


def stats(now: float | None = None) -> dict[str, Any]:
    """当前 hold 状态快照(日志/调试用)。"""
    rec = _record
    if rec is None:
        return {"held": False, "turn_id": "", "remaining_sec": 0.0, "acked": False}
    ts = time.time() if now is None else now
    remaining = max(0.0, min(rec["release_ts"], rec["deadline_ts"]) - ts)
    return {
        "held": remaining > 0,
        "turn_id": rec["turn_id"],
        "remaining_sec": round(remaining, 3),
        "acked": bool(rec["acked"]),
        "held_total_sec": round(min(rec["release_ts"], rec["deadline_ts"]) - rec["done_ts"], 3),
    }


def reset() -> None:
    """清空状态(单测隔离用)。"""
    global _record
    _record = None
