"""所有 `create_agent` 构建的 Agent 统一挂载的模型调用重试中间件。

主 Agent(`runtime/lifecycle.py`)、Subagent(`subagent_manager.py`)、Araya
(`araya_runtime.py`) 三处 Agent 构造都必须经过 `with_model_retry()`，保证限流 /
超时 / 5xx 等瞬时故障有一致的指数退避重试。

重试耗尽后原样抛出异常（`on_failure="error"`）：不注入错误消息让 Agent 接着瞎说，
也不吞掉故障——沿用无中间件时的失败语义，只是多了重试。
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain.agents.middleware import ModelRetryMiddleware
from langchain.agents.middleware.types import AgentMiddleware

#: 首次重试前的等待秒数；之后按 BACKOFF_FACTOR 翻倍（jitter 由库默认开启）。
INITIAL_DELAY = 1.0
#: 退避倍率。默认 2 次重试 ≈ 最坏额外等待 3 秒。
BACKOFF_FACTOR = 2.0
#: 重试次数（不含首次调用）。
MAX_RETRIES = 2


def _build_model_retry_middleware() -> ModelRetryMiddleware:
    return ModelRetryMiddleware(
        max_retries=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        initial_delay=INITIAL_DELAY,
        on_failure="error",
    )


def with_model_retry(
    middlewares: Sequence[AgentMiddleware] | None = None,
) -> list[AgentMiddleware]:
    """返回保证含有且仅含一个 `ModelRetryMiddleware` 的中间件列表。

    追加到末尾：中间件链首个最外层、末尾最贴近真实模型调用，重试只重放模型请求本身。
    传入已有 `ModelRetryMiddleware` 时替换而非叠加，避免双重退避。
    """
    effective = [
        item
        for item in (middlewares or [])
        if not isinstance(item, ModelRetryMiddleware)
    ]
    effective.append(_build_model_retry_middleware())
    return effective
