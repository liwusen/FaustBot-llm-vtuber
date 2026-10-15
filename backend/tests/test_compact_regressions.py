"""会话压缩回归测试（离线，不调用真实 LLM）。

覆盖三处已确认缺陷，以及对应的修复：

1. ``_reported_tokens`` 遇到第一条没有 ``usage_metadata`` 的 AIMessage 就返回 0。
   本地构造 / 插件注入的消息会命中这一分支，于是 ``_should_summarize`` 永远判定
   "未超阈值"，**自动压缩静默失效**，会话一路涨到必须手动 ``/compact`` ——
   而手动压缩要把整段历史读一遍，正是最贵的那条路径。

2. 压缩写回的摘要被 ``_determine_cutoff_index`` 当成"一轮对话"。没有 SystemMessage
   时摘要写成首条 HumanMessage，于是"摘要 + 保留 N 轮"永远比 ``keep_rounds``
   多一轮，切点恒 > 0，**压缩后每回合都会再压一次**（每次都是一整轮模型调用）。

3. 手动 ``/compact`` 只推 ``compact_*`` 卡片事件、不推通用 ``done``。前端
   ``sendToChat`` 的 Promise 只在 ``done`` / ``interrupted`` / ``error`` 时 settle，
   因此它永不结束，``currentChatRequest`` 一直非 null，输入框永久卡在"发送中"——
   表现为"压缩完成后再也无法互动"。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import (  # noqa: E402
    AIMessage,
    HumanMessage,
    SystemMessage,
)

from faust_backend.runtime.compact import (  # noqa: E402
    CompactMiddleware,
    _is_compact_summary,
    _reported_tokens,
)


# ── 测试替身 ──


class _FakeStream:
    """假模型：bind_tools / with_retry / astream（与 test_compact_middleware 一致）。"""

    _llm_type = "openai-chat"
    profile: dict = {}

    def __init__(self, *, summary: str = "## 目标与进度\n已压缩", fail: bool = False):
        self.summary = summary
        self.fail = fail
        self.calls: list[list] = []
        self.bound_log: list[list] = []

    def bind_tools(self, tools):
        clone = _FakeStream(summary=self.summary, fail=self.fail)
        clone.calls = self.calls
        clone.bound_log = self.bound_log
        clone.bound_log.append(list(tools))
        return clone

    def with_retry(self, **_kwargs):
        return self

    async def astream(self, payload, config=None):  # noqa: ANN001
        self.calls.append(list(payload))
        if self.fail:
            raise RuntimeError("上游 500")
        yield AIMessage(content=self.summary)


def _make_middleware(*, fake=None, keep_rounds: int = 1,
                     context_length: int = 1000,
                     threshold_ratio: float = 0.5) -> CompactMiddleware:
    fake = fake or _FakeStream()
    return CompactMiddleware(
        model=fake,
        compact_model=fake,
        tools=[],
        context_length=context_length,
        threshold_ratio=threshold_ratio,
        keep_rounds=keep_rounds,
    )


def _ai(content: str, *, tokens: int = 0) -> AIMessage:
    msg = AIMessage(content=content)
    if tokens:
        # 直接赋属性：构造期设 usage_metadata 与既有用法保持一致
        msg.usage_metadata = {  # type: ignore[assignment]
            "input_tokens": tokens,
            "output_tokens": 1,
            "total_tokens": tokens + 1,
        }
    return msg


def _summary(content: str = "【摘要】") -> HumanMessage:
    return HumanMessage(content=content, additional_kwargs={"lc_source": "faust_compact"})


# ── 1. API 用量提取：不能因为末尾消息没有 usage 就放弃 ──


def test_reported_tokens_skips_ai_without_usage():
    """末尾 AIMessage 没有 usage 时，应继续往前找真实上报值（而不是返回 0）。"""
    messages = [
        HumanMessage(content="早先请求"),
        _ai("早先回复", tokens=900_000),
        HumanMessage(content="最近请求"),
        _ai("本地构造的回复"),          # 无 usage_metadata
    ]
    assert _reported_tokens(messages) == 900_001


def test_reported_tokens_zero_when_no_usage_anywhere():
    """全都没有真实上报值时才返回 0（绝不本地估算）。"""
    assert _reported_tokens([HumanMessage(content="q"), _ai("a")]) == 0


def test_reported_tokens_ignores_non_ai_messages():
    messages = [_ai("a", tokens=500), HumanMessage(content="q")]
    assert _reported_tokens(messages) == 501


def test_reported_tokens_zero_on_empty():
    assert _reported_tokens([]) == 0


# ── 2. 轮次边界：压缩摘要不算一轮 ──


def test_is_compact_summary_marker():
    assert _is_compact_summary(_summary()) is True
    assert _is_compact_summary(HumanMessage(content="普通轮次")) is False
    assert _is_compact_summary(AIMessage(content="x")) is False


def test_cutoff_skips_when_only_old_summary_is_compressible():
    """只有「摘要 + 1 轮」时不应压缩：否则只是把摘要重新摘要一遍，白花一次调用。"""
    messages = [
        _summary(),
        HumanMessage(content="r1"),
        _ai("a1", tokens=100),
    ]
    mw = _make_middleware(keep_rounds=3)
    assert mw._determine_cutoff_index(messages) == 0


def test_cutoff_compacts_real_rounds_not_the_summary():
    """「摘要 + 3 轮」时切点必须落在真实轮次上，而不是把摘要本身当一轮。"""
    messages = [
        _summary(),
        HumanMessage(content="r1"),
        _ai("a1", tokens=100),
        HumanMessage(content="r2"),
        _ai("a2", tokens=100),
        HumanMessage(content="r3"),
        _ai("a3", tokens=100),
    ]
    mw = _make_middleware(keep_rounds=3)
    cutoff = mw._determine_cutoff_index(messages)
    # 修复前：摘要被算作第 1 轮 → len(round_starts)=4 > 3 → cutoff=1（只摘要摘要）
    assert cutoff != 1, "把摘要当成了可压缩轮次"
    # 真实轮次 3 个 = keep_rounds → 退化为只保留最后一轮，丢弃摘要与 r1/r2
    assert cutoff == messages.index(messages[5])
    assert not _is_compact_summary(messages[cutoff])


def test_cutoff_requires_a_real_discarded_round():
    """丢弃段里没有真实轮次时返回 0（System 与摘要都不算轮次）。"""
    messages = [
        SystemMessage(content="sys"),
        _summary(),
        HumanMessage(content="r1"),
        _ai("a1", tokens=100),
    ]
    mw = _make_middleware(keep_rounds=1)
    # round_starts = [2]，len <= keep_rounds → cutoff=2；丢弃段只有 sys+摘要 → 0
    assert mw._determine_cutoff_index(messages) == 0


# ── 3. /compact 必须发出终结事件（前端靠它释放请求状态） ──

TERMINAL_EVENTS = {"done", "interrupted", "error"}


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    def event_types(self) -> list[str]:
        return [json.loads(t).get("type") for t in self.sent]


_UNSET = object()


class _FakeCompactMW:
    """假压缩中间件：按 mode 复现四种真实结果，并流式吐两段摘要。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    async def force_compact(self, messages, on_delta=None):  # noqa: ANN001
        if self.mode == "raise":
            raise RuntimeError("模拟压缩调用失败")
        if self.mode == "empty":
            return None
        if on_delta:
            await on_delta("## 目标与进度\n")
            await on_delta("摘要正文")
        return {"messages": [HumanMessage(content="摘要")]}


class _FakeAgent:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail

    async def aupdate_state(self, config, update, as_node=None):  # noqa: ANN001
        if self.fail:
            raise RuntimeError("模拟 aupdate_state 失败")


def _install_compact_env(monkeypatch, *, mode: str, agent_fail: bool = False,
                         messages=("hi",), middleware=_UNSET) -> "_FakeWS":
    """把 /compact 依赖的全局状态全部改成 monkeypatch，测试结束自动还原。

    直接给 state.compact_middleware / state.agent 赋值会泄漏给同一 pytest 进程里
    后续所有测试，因此这里统一用 monkeypatch.setattr。
    """
    from faust_backend.routes import chat as chat_route
    from faust_backend.runtime import state

    ws = _FakeWS()
    monkeypatch.setattr(
        state, "compact_middleware",
        _FakeCompactMW(mode) if middleware is _UNSET else middleware,
        raising=False,
    )
    monkeypatch.setattr(state, "agent", _FakeAgent(fail=agent_fail), raising=False)

    async def _messages():
        return [HumanMessage(content=text) for text in messages]

    monkeypatch.setattr(chat_route, "get_checkpoint_messages", _messages)
    return ws


def _run_compact(monkeypatch, *, mode: str, agent_fail: bool = False, messages=("hi",),
                 middleware=_UNSET):
    from faust_backend.routes import chat as chat_route

    ws = _install_compact_env(
        monkeypatch, mode=mode, agent_fail=agent_fail, messages=messages,
        middleware=middleware,
    )
    handled, reply = asyncio.run(chat_route._handle_slash_command("/compact", ws))
    return handled, reply, ws


def _payloads(ws) -> list[dict]:
    return [json.loads(t) for t in ws.sent]


def _delta_text(ws) -> str:
    return "".join(
        p.get("content", "") for p in _payloads(ws) if p.get("type") == "compact_delta"
    )


# (mode, agent_fail) -> 必须逐字匹配的事件类型序列
EXPECTED_SEQUENCES = {
    ("ok", False): ["compact_start", "compact_delta", "compact_delta", "compact_done", "done"],
    ("empty", False): ["compact_start", "compact_delta", "compact_done", "done"],
    ("ok", True): ["compact_start", "compact_delta", "compact_delta", "compact_done", "error"],
    ("raise", False): ["compact_start", "compact_done", "error"],
}


@pytest.mark.parametrize("mode,agent_fail", list(EXPECTED_SEQUENCES))
def test_compact_event_sequence_is_stable(monkeypatch, mode, agent_fail):
    """四条路径的事件顺序必须稳定，前端按这个顺序渲染卡片并释放请求。

    顺序错乱（例如 done 早于 compact_done）会让卡片状态错位；缺终结事件会让
    sendToChat 的 Promise 永不 settle，输入框永久卡在"发送中"。
    """
    handled, _reply, ws = _run_compact(monkeypatch, mode=mode, agent_fail=agent_fail)
    payloads = _payloads(ws)
    types = [p.get("type") for p in payloads]
    assert handled is True
    assert types == EXPECTED_SEQUENCES[(mode, agent_fail)]
    assert TERMINAL_EVENTS & set(types), f"缺少终结事件，前端会卡死: {types}"
    assert all(p.get("agent_id") == "main" for p in payloads)
    if mode == "ok":
        # 增量文本要能被前端拼成完整摘要（否则卡片是空的）
        assert _delta_text(ws) == "## 目标与进度\n摘要正文"


@pytest.mark.parametrize("mode,agent_fail", [("ok", True), ("raise", False)])
def test_compact_failure_marks_card_failed(monkeypatch, mode, agent_fail):
    """失败时 compact_done 必须带 ok=False + 原因，前端据此把卡片标成失败态。"""
    _handled, _reply, ws = _run_compact(monkeypatch, mode=mode, agent_fail=agent_fail)
    done = [p for p in _payloads(ws) if p.get("type") == "compact_done"]
    assert done, "失败路径也必须收尾压缩卡片"
    assert done[-1].get("ok") is False, done[-1]
    assert done[-1].get("error"), done[-1]


def test_compact_success_carries_no_failure_flag(monkeypatch):
    _handled, _reply, ws = _run_compact(monkeypatch, mode="ok")
    done = [p for p in _payloads(ws) if p.get("type") == "compact_done"][-1]
    assert done.get("ok") is not False
    assert "error" not in done


def test_compact_without_messages_still_terminates(monkeypatch):
    """会话为空时也要给出卡片与终结事件，而不是静默无反应。"""
    handled, _reply, ws = _run_compact(monkeypatch, mode="ok", messages=())
    assert handled is True
    assert [p.get("type") for p in _payloads(ws)] == [
        "compact_start", "compact_delta", "compact_done", "done",
    ]
    assert "没有可压缩的上下文" in _delta_text(ws)


def test_compact_missing_middleware_fails_visibly(monkeypatch):
    """中间件未就绪（异常发生在推 compact_start 之前）：仍要有失败卡片 + 终结事件。

    这条路径没有 compact_start，前端靠 compact_done 兜底建卡片，所以 ok/error
    字段必须带上，且必须补 error 事件释放 sendToChat 的 Promise。
    """
    handled, _reply, ws = _run_compact(monkeypatch, mode="ok", middleware=None)
    types = [p.get("type") for p in _payloads(ws)]
    assert handled is True
    assert types == ["compact_done", "error"], types
    assert _payloads(ws)[0].get("ok") is False


def test_safe_send_swallows_closed_socket():
    """连接已断时发送不能抛异常，否则会杀死整个接收循环。"""
    from faust_backend.routes import chat as chat_route

    class _Dead:
        async def send_text(self, text):
            raise RuntimeError("socket closed")

    ok = asyncio.run(chat_route._safe_send(_Dead(), {"type": "done"}))
    assert ok is False


def test_compact_survives_dead_socket(monkeypatch):
    """连接中途断开时 /compact 也不能把异常抛回接收循环，且必须尝试收尾。"""
    from faust_backend.routes import chat as chat_route

    class _DeadWS:
        def __init__(self):
            self.attempts: list[str] = []

        async def send_text(self, text):
            self.attempts.append(text)
            raise RuntimeError("socket closed")

    from faust_backend.runtime import state

    monkeypatch.setattr(state, "compact_middleware", _FakeCompactMW("ok"), raising=False)
    monkeypatch.setattr(state, "agent", _FakeAgent(), raising=False)

    async def _messages():
        return [HumanMessage(content="hi")]

    monkeypatch.setattr(chat_route, "get_checkpoint_messages", _messages)
    ws = _DeadWS()
    handled, reply = asyncio.run(chat_route._handle_slash_command("/compact", ws))
    assert handled is True and reply is None
    assert ws.attempts, "即使发送失败也应尝试过推送事件（失败由 _safe_send 吞掉）"
