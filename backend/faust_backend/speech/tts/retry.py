"""上游 TTS 429 限流的退避重试（MiMo / Edge TTS 共用）。

策略与 ASR（speech/asr/transcribe.py）一致：重试 2 次，退避 2s / 5s；
重试耗尽后抛 SpeechRuntimeError —— 不隐瞒失败。
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

from faust_backend.logger import get_logger
from faust_backend.speech.errors import SpeechRuntimeError

log = get_logger("faust.speech.tts")

RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0)

T = TypeVar("T")


class TtsRateLimitError(SpeechRuntimeError):
    """上游返回 429（限流）。只有这个异常会触发重试。"""


async def with_429_retry(operation: Callable[[], Awaitable[T]], *, label: str) -> T:
    """执行 operation；遇 429 限流按 RETRY_DELAYS 退避重试，耗尽后抛 SpeechRuntimeError。

    非限流异常立即向上抛出，不做无谓重试。
    """
    attempt = 0
    while True:
        try:
            return await operation()
        except TtsRateLimitError as exc:
            if attempt >= len(RETRY_DELAYS):
                raise SpeechRuntimeError(
                    f"{label} 速率限制(重试{len(RETRY_DELAYS)}次仍429)，请稍后再说"
                ) from exc
            delay = RETRY_DELAYS[attempt]
            attempt += 1
            log.warning(
                "%s 触发限流(429)，%.0fs 后重试（第 %d/%d 次）: %s",
                label, delay, attempt, len(RETRY_DELAYS), exc,
            )
            await asyncio.sleep(delay)
