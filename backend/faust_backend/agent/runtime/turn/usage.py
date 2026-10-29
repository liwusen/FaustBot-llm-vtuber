"""跨格式的 LLM 用量（token）归一化。

四种 API 的用量口径不同，直接相加/直接读 `total_tokens` 会得到错的上下文占用
（进而让自动压缩要么永不触发、要么过早触发）：

- **OpenAI Chat / Responses**：`prompt_tokens` 已包含缓存命中的部分，
  `cached_tokens` 是它的**子集**，不能重复相加。
- **Google Gemini**：`prompt_token_count` 已包含 `cached_content_token_count`，
  同样是子集；`thoughts_token_count` 已包含在 `candidates_token_count` 之外
  （SDK 会把它折叠进 output）。
- **Anthropic**：`input_tokens` **不含**缓存读/写，真实输入 =
  `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`。
  这是最容易踩的坑：只看 `input_tokens` 时，prompt 缓存命中率高的长会话会显示
  很小的上下文占用，自动压缩永远不触发。
- **Anthropic 流式**：`message_start` 里就带 usage，SDK 会做累计。

本模块只做「读 API 上报值 + 归一化」，**绝不做本地估算**。没有上报值时返回
None，调用方如实显示「—」。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from langchain_core.messages import AIMessage, AnyMessage


@dataclass(frozen=True)
class UsageSnapshot:
    """一次请求的归一化用量。"""

    api_format: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    #: 该次请求真实占用的上下文 token（压缩触发判据）
    context_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    reasoning_tokens: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


def _int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value > 0:
        return int(value)
    return 0


def _usage_metadata(message: Any) -> dict[str, Any]:
    usage = getattr(message, "usage_metadata", None)
    return dict(usage) if isinstance(usage, dict) else {}


def _raw_usage(message: Any) -> dict[str, Any]:
    meta = getattr(message, "response_metadata", None)
    if not isinstance(meta, dict):
        return {}
    usage = meta.get("usage")
    return dict(usage) if isinstance(usage, dict) else {}


def normalize_usage(message: Any, api_format: str = "openai_chat") -> UsageSnapshot | None:
    """把一条 AIMessage 的用量归一化；没有任何上报值时返回 None。"""
    usage = _usage_metadata(message)
    raw = _raw_usage(message)
    if not usage and not raw:
        return None

    details = usage.get("input_token_details") or {}
    output_details = usage.get("output_token_details") or {}
    cache_read = _int(details.get("cache_read"))
    cache_creation = _int(details.get("cache_creation"))
    reasoning = _int(output_details.get("reasoning"))

    if api_format == "anthropic_messages":
        # Anthropic 原始口径：input 不含缓存读写，需要补加
        raw_input = _int(raw.get("input_tokens"))
        raw_cache_read = _int(raw.get("cache_read_input_tokens")) or cache_read
        raw_cache_creation = _int(raw.get("cache_creation_input_tokens")) or cache_creation
        input_tokens = raw_input + raw_cache_read + raw_cache_creation
        # langchain-anthropic 新版本已把缓存折进 usage_metadata.input_tokens；
        # 取较大值，既不会重复相加，也能兼容旧版本/流式缺失的情况。
        input_tokens = max(input_tokens, _int(usage.get("input_tokens")))
        output_tokens = _int(raw.get("output_tokens")) or _int(usage.get("output_tokens"))
        cache_read = raw_cache_read or cache_read
        cache_creation = raw_cache_creation or cache_creation
        context = input_tokens + output_tokens
        total = context
    else:
        input_tokens = _int(usage.get("input_tokens")) or _int(raw.get("prompt_tokens"))
        output_tokens = (
            _int(usage.get("output_tokens")) or _int(raw.get("completion_tokens"))
        )
        # 缓存命中是 input 的子集：这里只展示，绝不加到 input/total 上
        total = _int(usage.get("total_tokens")) or _int(raw.get("total_tokens"))
        if total <= 0:
            total = input_tokens + output_tokens
        context = total

    if input_tokens == 0 and output_tokens == 0 and total == 0:
        return None
    return UsageSnapshot(
        api_format=api_format,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total,
        context_tokens=context,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        reasoning_tokens=reasoning,
        raw=raw or usage,
    )


def last_usage(
    messages: Sequence[AnyMessage], api_format: str = "openai_chat"
) -> UsageSnapshot | None:
    """最近一条**带用量**的 AIMessage 的归一化用量。

    不能「遇到最后一条 AIMessage 就定论」：它可能是本地构造/插件注入的消息
    （没有 usage_metadata）。那样面板会显示「—」，自动压缩也永不触发。
    """
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            snapshot = normalize_usage(message, api_format)
            if snapshot is not None:
                return snapshot
    return None


def context_tokens(messages: Sequence[AnyMessage], api_format: str = "openai_chat") -> int:
    """当前上下文的真实 token 占用（无 API 上报值时返回 0）。"""
    snapshot = last_usage(messages, api_format)
    return snapshot.context_tokens if snapshot else 0
