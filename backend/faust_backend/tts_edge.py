from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Tuple

import aiohttp
import edge_tts

import faust_backend.config_loader as conf
from faust_backend.speech.errors import SpeechRuntimeError


async def synthesize_edge_tts(
    text: str,
    voice: str | None = None,
    rate: str | None = None,
    pitch: str | None = None,
    timeout: int = 120,
) -> Tuple[bytes, str]:
    """Use edge-tts to synthesize text to audio bytes.

    上游 429(限流) 会退避重试（见 speech/tts/retry.py）。
    Returns (audio_bytes, content_type). Raises SpeechRuntimeError on failure.
    """
    from faust_backend.speech.tts.retry import TtsRateLimitError, with_429_retry

    if not text or not str(text).strip():
        raise SpeechRuntimeError("TTS 文本不能为空")

    tts_voice = str(voice or conf.EDGE_TTS_VOICE or "en-US-AriaNeural")
    tts_rate = str(rate or conf.EDGE_TTS_RATE or "0%")
    tts_pitch = str(pitch or conf.EDGE_TTS_PITCH or "0%")

    # edge-tts 走 websocket 合成；用临时文件接收音频字节
    tmp = None

    async def _save_once() -> None:
        # 每次重试都新建 Communicate，不复用上一次失败的连接状态。
        # 注：某些版本的 Communicate 支持 rate/pitch kwargs，这里不依赖未文档化参数，
        # 音色/语调仍由 voice 控制（保持原行为）。
        communicate = edge_tts.Communicate(str(text), tts_voice)
        try:
            await asyncio.wait_for(communicate.save(tmp), timeout=float(timeout or 120))
        except asyncio.TimeoutError as exc:
            raise SpeechRuntimeError(f"Edge TTS 超时 ({timeout}s)") from exc
        except aiohttp.ClientResponseError as exc:
            if exc.status == 429:
                raise TtsRateLimitError(f"Edge TTS 429: {exc}") from exc
            raise

    try:
        fd, tmp = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        await with_429_retry(_save_once, label="Edge TTS")

        path = Path(tmp)
        if not path.exists():
            raise SpeechRuntimeError("Edge TTS 未生成音频文件")
        return path.read_bytes(), "audio/mpeg"
    except SpeechRuntimeError:
        raise
    except Exception as exc:  # pragma: no cover - surface failures
        raise SpeechRuntimeError(f"Edge TTS 合成失败: {exc}") from exc
    finally:
        try:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
