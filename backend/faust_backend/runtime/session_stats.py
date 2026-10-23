"""会话上下文统计：`/session` 命令与 `GET /faust/session/context` 的唯一实现。

原则（与 `runtime/compact.py` 一致，见该模块 docstring）：
token 数字**一律取 LLM API 的返回值**（最近一条 AIMessage 的 `usage_metadata`）。
拿不到 API 数据时如实标记 `has_api_usage=False`，**绝不本地估算**——
宁可显示「—」，也不猜一个看起来合理的数。

数字格式化（`format_tokens`）也只在这里实现一份，前端只消费
`*_text` 字段，避免两端各写一套规则后漂移。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from langchain_core.messages import AnyMessage

import faust_backend.config_loader as conf
from faust_backend.logger import get_logger
from faust_backend.provider import (
    DEFAULT_CONTEXT_LENGTH,
    get_context_length,
    get_model_api_format,
)
from faust_backend.runtime import state
from faust_backend.runtime import usage as usage_mod

log = get_logger("faust.session")


async def get_checkpoint_messages() -> list[AnyMessage]:
    """读取当前会话 checkpoint 中的 messages（无 checkpoint / 无 checkpointer 时返回空列表）。"""
    if state.checkpointer is None:
        return []
    cfg = {"configurable": {"thread_id": state.THREAD_ID}}
    checkpoint_tuple = await state.checkpointer.aget_tuple(cfg)
    if checkpoint_tuple is None:
        return []
    checkpoint = getattr(checkpoint_tuple, "checkpoint", None)
    if not isinstance(checkpoint, dict):
        return []
    values = checkpoint.get("channel_values") or {}
    messages = values.get("messages") or []
    return list(messages) if isinstance(messages, list) else []


def _format_millions(value: int) -> str:
    """M 分支：四舍五入到 1 位小数，去尾 `.0`。

    用整数半进位而不是 `round(value / 1e6, 1)`：浮点会让 `1_950_000`
    变成 `1.9M`（二进制表示略小于 1.95），数字展示必须确定性可复现。
    """
    sign = "-" if value < 0 else ""
    tenths = (abs(value) * 10 + 500_000) // 1_000_000
    whole, frac = divmod(tenths, 10)
    return f"{sign}{whole}M" if frac == 0 else f"{sign}{whole}.{frac}M"


def format_tokens(value: int) -> str:
    """把 token 数格式化成可读文本：`812` / `12K` / `1.5M`。

    规则：
    - `< 1000`：原样输出；
    - `1e3 ~ 1e6`：四舍五入到整数 K；进位到 1000K 时提升为 M（`999_999 -> 1M`）；
    - `>= 1e6`：保留 1 位小数并去掉尾随 `.0`（`1_500_000 -> 1.5M`，`1_000_000 -> 1M`）。

    注意：因为 `1e3 ~ 1e6` 一律用 K，形如 `0.7M` 的写法不会出现
    （700_000 会显示为 `700K`）——这是刻意的：K 区间内 M 的表达更模糊。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"format_tokens 只接受数字，收到 {type(value).__name__}")
    number = int(value)
    if abs(number) < 1000:
        return str(number)
    if abs(number) < 1_000_000:
        sign = "-" if number < 0 else ""
        thousands = (abs(number) + 500) // 1000
        # 半进位可能把 999_999 推到 1000K：这时必须进位成 M，否则会出现 "1000K"
        if thousands >= 1000:
            return _format_millions(number)
        return f"{sign}{thousands}K"
    return _format_millions(number)


def _last_usage(messages: Sequence[AnyMessage], api_format: str = "openai_chat") -> Optional[dict]:
    """取最近一条**带用量**的 AIMessage 的归一化用量（原始 dict 形式）。

    与 `compact._reported_tokens` 保持一致：遇到没有 `usage_metadata` 的
    AIMessage 要继续往前找，而不是就地定论返回 None —— 否则本地构造/插件注入的
    消息会让面板与自动压缩同时失去 API 口径，显示"—"且永不触发压缩。
    仍然不本地估算：找不到真实上报值就返回 None。
    """
    snapshot = usage_mod.last_usage(messages, api_format)
    if snapshot is None:
        return None
    return {
        "input_tokens": snapshot.input_tokens,
        "output_tokens": snapshot.output_tokens,
        "total_tokens": snapshot.total_tokens,
        "cache_read_tokens": snapshot.cache_read_tokens,
        "cache_creation_tokens": snapshot.cache_creation_tokens,
        "reasoning_tokens": snapshot.reasoning_tokens,
    }


async def collect_session_stats() -> dict[str, Any]:
    """当前会话的上下文占用快照。

    `used_tokens` 语义：最近一次 LLM 请求由 API 上报的**真实上下文占用**
    （口径见 `runtime/usage.py`：Anthropic 计入缓存读写，OpenAI/Gemini 的
    cached 只作展示、不重复相加）。无 API 计数时 `used_tokens_text="—"`。
    """
    messages = await get_checkpoint_messages()
    providers = state.get_model_providers()
    model_spec = str(getattr(providers, "main_model", "") or "")
    if providers is None:
        context_length = DEFAULT_CONTEXT_LENGTH
        api_format = "openai_chat"
    else:
        context_length = get_context_length(providers, model_spec)
        api_format = get_model_api_format(providers, model_spec)
    threshold_tokens = max(1, int(context_length * conf.COMPACT_THRESHOLD_RATIO))

    usage = _last_usage(messages, api_format)
    snapshot = usage_mod.last_usage(messages, api_format)
    used_tokens = snapshot.context_tokens if snapshot else 0
    ratio = (used_tokens / context_length) if context_length else 0.0

    return {
        "status": "ok",
        "model": model_spec,
        "api_format": api_format,
        "messages": len(messages),
        "has_api_usage": usage is not None,
        "used_tokens": used_tokens,
        "used_tokens_text": format_tokens(used_tokens) if used_tokens else "—",
        "context_length": context_length,
        "context_length_text": format_tokens(context_length),
        "threshold_tokens": threshold_tokens,
        "ratio": ratio,
        "percent": round(ratio * 100, 1),
        "input_tokens": usage.get("input_tokens") if usage else None,
        "output_tokens": usage.get("output_tokens") if usage else None,
        "total_tokens": usage.get("total_tokens") if usage else None,
        "cache_read_tokens": usage.get("cache_read_tokens") if usage else None,
        "cache_creation_tokens": (
            usage.get("cache_creation_tokens") if usage else None
        ),
        "reasoning_tokens": usage.get("reasoning_tokens") if usage else None,
        "compact_threshold_ratio": float(conf.COMPACT_THRESHOLD_RATIO),
    }
