"""触发器播放闸门(turn_gate)判据测试。

覆盖:
1. 登记后 normal 被 hold、interrupt 恒不 hold
2. ack 的 turn_id 对不上时忽略
3. ack 早到: 仍要等 read_floor
4. ack 晚到(超过 read_floor): 立即放行
5. 收不到 ack: 等满 max_hold 安全网才放行
6. read_floor 随回复字数增长并封顶
7. chat 连接断开: 立即释放
8. 新一轮覆盖旧一轮
9. 释放后状态清空

时间一律用注入的 now, 不 sleep。
"""
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faust_backend.core.config as conf
import faust_backend.agent.runtime.turn.gate as tg


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(conf, "TRIGGER_HOLD_MAX_SEC", 30.0)
    monkeypatch.setattr(conf, "TRIGGER_READ_FLOOR_SEC", 1.5)
    tg.reset()
    yield
    tg.reset()


def test_holds_normal_but_never_interrupt():
    tg.arm("t1", text_len=0, now=100.0)
    assert tg.should_hold("normal", now=100.0) is True
    assert tg.should_hold("interrupt", now=100.0) is False
    assert tg.should_hold("batched", now=100.0) is True


def test_ack_with_wrong_turn_id_is_ignored():
    tg.arm("t1", text_len=0, now=100.0)
    assert tg.note_playback_done("t2", now=100.5) is False
    assert tg.should_hold("normal", now=101.0) is True


def test_early_ack_still_waits_for_read_floor():
    # text_len=200 → read_floor = min(200/20, 8) = 8 秒
    tg.arm("t1", text_len=200, now=100.0)
    assert tg.note_playback_done("t1", now=101.0) is True
    assert tg.should_hold("normal", now=107.9) is True
    assert tg.should_hold("normal", now=108.0) is False


def test_late_ack_releases_immediately():
    tg.arm("t1", text_len=0, now=100.0)
    # read_floor 到 101.5; ack 在 105 到达(音频比阅读下限长) → 立刻放行
    assert tg.note_playback_done("t1", now=105.0) is True
    assert tg.should_hold("normal", now=105.0) is False


def test_missing_ack_waits_for_max_hold():
    tg.arm("t1", text_len=0, now=100.0)
    assert tg.should_hold("normal", now=101.5) is True   # 不能按 read_floor 提前放行
    assert tg.should_hold("normal", now=129.9) is True
    assert tg.should_hold("normal", now=130.0) is False  # 安全网到期


def test_ack_cannot_exceed_max_hold():
    tg.arm("t1", text_len=0, now=100.0)
    # ack 迟到得离谱(超过 max_hold) → 仍以 deadline 为上限
    assert tg.note_playback_done("t1", now=999.0) is True
    assert tg.should_hold("normal", now=999.0) is False


def test_read_floor_scales_with_text_len(monkeypatch):
    monkeypatch.setattr(conf, "TRIGGER_READ_FLOOR_SEC", 0.0)
    tg.arm("short", text_len=20, now=0.0)
    assert tg.hold_remaining(now=0.0) == pytest.approx(30.0)  # 未 ack → 安全网
    tg.note_playback_done("short", now=0.0)
    assert tg.should_hold("normal", now=0.9) is True
    assert tg.should_hold("normal", now=1.0) is False   # 20/20 = 1 秒

    tg.arm("long", text_len=100000, now=0.0)
    tg.note_playback_done("long", now=0.0)
    assert tg.should_hold("normal", now=7.9) is True
    assert tg.should_hold("normal", now=8.0) is False   # 封顶 8 秒


def test_release_for_websocket():
    ws = object()
    other = object()
    tg.arm("t1", text_len=0, websocket=ws, now=100.0)
    assert tg.release_for_websocket(other) is False
    assert tg.should_hold("normal", now=100.0) is True
    assert tg.release_for_websocket(ws) is True
    assert tg.should_hold("normal", now=100.0) is False


def test_new_arm_overrides_previous_turn():
    tg.arm("t1", text_len=0, now=100.0)
    tg.arm("t2", text_len=0, now=110.0)
    assert tg.note_playback_done("t1", now=110.5) is False   # 旧 turn 的 ack 已无意义
    assert tg.should_hold("normal", now=110.5) is True
    assert tg.note_playback_done("t2", now=111.0) is True
    assert tg.should_hold("normal", now=111.5) is False


def test_stats_after_release_is_empty():
    tg.arm("t1", text_len=0, now=100.0)
    tg.note_playback_done("t1", now=100.1)
    tg.should_hold("normal", now=200.0)
    snap = tg.stats(now=200.0)
    assert snap["held"] is False
    assert snap["turn_id"] == ""


# ── 接线：chat._run_agent_stream 必须真的 arm，并回带 turn_id ──

class _OneShotAgent:
    """astream_events 兼容假 Agent：产出一条 delta 就结束。"""

    async def astream_events(self, payload, config=None, version="v2"):
        from langchain_core.messages import AIMessageChunk

        yield {
            "event": "on_chat_model_stream",
            "data": {"chunk": AIMessageChunk(content="好了")},
        }


class _FakeWebSocket:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, raw: str):
        import json as _json

        self.sent.append(_json.loads(raw))


@pytest.mark.asyncio(loop_scope="module")
async def test_run_agent_stream_arms_gate_and_echoes_turn_id(monkeypatch):
    from faust_backend.api.chat import chat as chat
    from faust_backend.agent.runtime import state

    # 不写真实记忆库(该用例会正常跑完一轮流, 不拦的话会往 ~/.faustbot 落聊天记录)
    monkeypatch.setattr(chat, "schedule_memory_record_sync", lambda *a, **k: None)
    # 阅读下限置 0: 只验证 arm/ack 接线, 由纯逻辑用例覆盖 read_floor
    monkeypatch.setattr(conf, "TRIGGER_READ_FLOOR_SEC", 0.0)

    ws = _FakeWebSocket()
    try:
        await chat._run_agent_stream(ws, "你好", agent=_OneShotAgent(), turn_id="t-arm")
    finally:
        if state.agent_lock.locked():
            state.agent_lock.release()
            state.mark_agent_lock_released()

    done = next(m for m in ws.sent if m.get("type") == "done")
    assert done.get("turn_id") == "t-arm"
    # arm 之后必须处于 hold 状态，等前端 ack 或安全网
    assert tg.should_hold("normal") is True
    assert tg.should_hold("interrupt") is False
    assert tg.note_playback_done("t-arm") is True
    # 回复只有 2 个字 → read_floor 仍≥0.1s; 用注入的 now 断言"到点即放行", 不 sleep
    assert tg.should_hold("normal", now=time.time() + 1.0) is False


@pytest.mark.asyncio(loop_scope="module")
async def test_run_agent_stream_without_turn_id_does_not_arm(monkeypatch):
    from faust_backend.api.chat import chat as chat
    from faust_backend.agent.runtime import state

    monkeypatch.setattr(chat, "schedule_memory_record_sync", lambda *a, **k: None)

    ws = _FakeWebSocket()
    try:
        await chat._run_agent_stream(ws, "你好", agent=_OneShotAgent())
    finally:
        if state.agent_lock.locked():
            state.agent_lock.release()
            state.mark_agent_lock_released()

    assert tg.should_hold("normal") is False
