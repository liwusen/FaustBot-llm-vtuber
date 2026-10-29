"""会话压缩成本控制回归测试（离线，不调用真实 LLM）。

覆盖 PR #30 的「省钱逻辑」及其开关：

1. ``COMPACT_COST_CONTROL_ENABLED`` 默认**关闭**：不设摘要输出上限、不裁剪输入，
   压缩 payload 与主请求前缀逐字节一致（完整命中上游前缀缓存）。
2. 只有显式打开后，``COMPACT_MAX_OUTPUT_TOKENS`` / ``COMPACT_MAX_INPUT_TOKENS``
   才生效。
3. 摘要被输出上限截断时按**失败**处理：不写回 checkpoint（否则摘要没覆盖到的
   历史会被永久删掉），并在本进程内自动放弃该上限，避免每回合重复付费。
4. 五个压缩配置键必须出现在公共配置默认值里，否则前端「AI 服务商/高级配置」
   根本渲染不出它们（``get_public_config() = 默认值 ∪ 配置文件``）。
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

import faust_backend.core.config as conf  # noqa: E402
from faust_backend.agent.runtime.compact import CompactMiddleware  # noqa: E402


# ── 测试替身 ──


class _FakeCostStream:
    """假压缩模型：记录 ``bind()`` 入参，可模拟「摘要被截断」。

    bind_kwargs 是成本控制开关是否真的把 max_tokens 施加到压缩调用上的唯一证据。
    """

    _llm_type = "openai-chat"
    profile: dict = {}

    def __init__(self, *, summary: str = "## 目标与进度\n已压缩",
                 finish_reason: str | None = None, output_tokens: int = 0):
        self.summary = summary
        self.finish_reason = finish_reason
        self.output_tokens = output_tokens
        self.calls: list[list] = []
        self.bind_kwargs: list[dict] = []

    def bind_tools(self, tools):  # noqa: ANN001
        return self

    def bind(self, **kwargs):
        self.bind_kwargs.append(dict(kwargs))
        return self

    def with_retry(self, **_kwargs):
        return self

    async def astream(self, payload, config=None):  # noqa: ANN001
        self.calls.append(list(payload))
        message = AIMessage(content=self.summary)
        if self.finish_reason:
            message.response_metadata = {"finish_reason": self.finish_reason}  # type: ignore[assignment]
        if self.output_tokens:
            message.usage_metadata = {  # type: ignore[assignment]
                "input_tokens": 10,
                "output_tokens": self.output_tokens,
                "total_tokens": 10 + self.output_tokens,
            }
        yield message


def _mw_with(fake: _FakeCostStream) -> CompactMiddleware:
    return CompactMiddleware(
        model=fake, compact_model=fake, tools=[],
        context_length=1000, threshold_ratio=0.5, keep_rounds=1,
    )


def _long_messages(count: int = 6, size: int = 200) -> list[HumanMessage]:
    return [HumanMessage(content="x" * size) for _ in range(count)]


# ── 开关：默认关闭 / 打开后才施加两个上限 ──


def test_cost_control_off_by_default_leaves_payload_intact(monkeypatch):
    """默认关闭：不 bind max_tokens、不裁剪输入 —— payload 与主请求前缀逐字节一致。"""
    monkeypatch.setattr(conf, "COMPACT_COST_CONTROL_ENABLED", False)
    monkeypatch.setattr(conf, "COMPACT_MAX_OUTPUT_TOKENS", 123)
    monkeypatch.setattr(conf, "COMPACT_MAX_INPUT_TOKENS", 5)
    fake = _FakeCostStream()
    messages = _long_messages()

    summary = asyncio.run(_mw_with(fake)._acompact(messages))

    assert summary == fake.summary
    assert fake.bind_kwargs == [], "默认关闭时不应设置摘要输出上限"
    # 完整 payload = 全部消息 + 尾端指令
    assert len(fake.calls[0]) == len(messages) + 1


def test_cost_control_on_applies_both_caps(monkeypatch):
    """打开开关后两个上限才生效：输出 max_tokens + 输入裁剪。"""
    monkeypatch.setattr(conf, "COMPACT_COST_CONTROL_ENABLED", True)
    monkeypatch.setattr(conf, "COMPACT_MAX_OUTPUT_TOKENS", 123)
    monkeypatch.setattr(conf, "COMPACT_MAX_INPUT_TOKENS", 5)
    fake = _FakeCostStream()

    asyncio.run(_mw_with(fake)._acompact(_long_messages()))

    assert fake.bind_kwargs == [{"max_tokens": 123}]
    # 输入上限 5 token ≈ 12 字符 → 只留下最后一条消息 + 指令
    assert len(fake.calls[0]) == 2


# ── 截断保护 ──


def test_truncated_summary_raises_and_cap_self_heals(monkeypatch):
    """摘要被截断：不返回（调用方据此不写回 checkpoint），并在本进程内放弃该上限。"""
    monkeypatch.setattr(conf, "COMPACT_COST_CONTROL_ENABLED", True)
    monkeypatch.setattr(conf, "COMPACT_MAX_OUTPUT_TOKENS", 50)
    fake = _FakeCostStream(finish_reason="length")
    mw = _mw_with(fake)

    with pytest.raises(RuntimeError, match="截断"):
        asyncio.run(mw._acompact([HumanMessage(content="hi")]))

    # 自愈：下一轮不再 bind 上限，避免每回合都用过小的上限白烧一次调用
    asyncio.run(mw._acompact([HumanMessage(content="hi")]))
    assert fake.bind_kwargs == [{"max_tokens": 50}]
    assert mw._cost_cap_bypassed is True


def test_truncation_detected_by_output_tokens_without_finish_reason(monkeypatch):
    """部分 provider 不返回 finish_reason：用上报的 output_tokens 兜住截断判定。"""
    monkeypatch.setattr(conf, "COMPACT_COST_CONTROL_ENABLED", True)
    monkeypatch.setattr(conf, "COMPACT_MAX_OUTPUT_TOKENS", 50)
    mw = _mw_with(_FakeCostStream(output_tokens=50))

    with pytest.raises(RuntimeError, match="截断"):
        asyncio.run(mw._acompact([HumanMessage(content="hi")]))


def test_usage_is_logged_for_cost_audit(monkeypatch, caplog):
    """压缩调用用量必须可审计（开关开或不开都记录），否则无法判断缓存是否命中。"""
    monkeypatch.setattr(conf, "COMPACT_COST_CONTROL_ENABLED", False)
    mw = _mw_with(_FakeCostStream(output_tokens=7))

    with caplog.at_level("INFO", logger="faust.compact"):
        asyncio.run(mw._acompact([HumanMessage(content="hi")]))

    messages = [r.getMessage() for r in caplog.records]
    assert any("压缩调用用量" in m for m in messages), messages


# ── 配置项必须能被前端渲染 ──


def test_cost_control_keys_are_exposed_to_frontend():
    """五个压缩配置键必须在公共配置默认值里，否则前端「AI 服务商/高级配置」渲染不出它们。"""
    from faust_backend.services import admin as admin_runtime

    defaults = admin_runtime.PUBLIC_CONFIG_DEFAULTS
    assert defaults["COMPACT_COST_CONTROL_ENABLED"] is False
    assert defaults["COMPACT_MAX_INPUT_TOKENS"] == 0
    assert defaults["COMPACT_MAX_OUTPUT_TOKENS"] == 4000
    assert defaults["COMPACT_THRESHOLD_RATIO"] == 0.8
    assert defaults["COMPACT_KEEP_ROUNDS"] == 3

    # 与 config_loader 读到的生效值口径一致（都在配置缺失时落到同一默认）
    reloaded = {
        "COMPACT_COST_CONTROL_ENABLED": conf.COMPACT_COST_CONTROL_ENABLED,
        "COMPACT_MAX_INPUT_TOKENS": conf.COMPACT_MAX_INPUT_TOKENS,
        "COMPACT_MAX_OUTPUT_TOKENS": conf.COMPACT_MAX_OUTPUT_TOKENS,
        "COMPACT_THRESHOLD_RATIO": conf.COMPACT_THRESHOLD_RATIO,
        "COMPACT_KEEP_ROUNDS": conf.COMPACT_KEEP_ROUNDS,
    }
    for key, value in reloaded.items():
        assert isinstance(value, type(defaults[key])), (key, value, defaults[key])
