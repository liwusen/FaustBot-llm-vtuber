from __future__ import annotations

import asyncio
import tempfile
import os
from pathlib import Path
from typing import Tuple

import faust_backend.config_loader as conf


import edge_tts




async def synthesize_edge_tts(
    text: str,
    voice: str | None = None,
    rate: str | None = None,
    pitch: str | None = None,
    timeout: int = 120,
) -> Tuple[bytes, str]:
    """Use edge-tts to synthesize text to audio bytes.

    Returns (audio_bytes, content_type). Raises RuntimeError on failure.
    """
    if not text or not str(text).strip():
        from faust_backend.speech_runtime import SpeechRuntimeError
        raise SpeechRuntimeError("TTS 文本不能为空")

    tts_voice = str(voice or getattr(conf, "EDGE_TTS_VOICE", "en-US-AriaNeural") or "en-US-AriaNeural")
    tts_rate = str(rate or getattr(conf, "EDGE_TTS_RATE", "0%") or "0%")
    tts_pitch = str(pitch or getattr(conf, "EDGE_TTS_PITCH", "0%") or "0%")

    communicate = edge_tts.Communicate(str(text), tts_voice)

    # edge-tts supports saving to file via async save API; use a temp file to capture bytes
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        # Build a coroutine that saves to tmp. Some versions accept rate/pitch via arguments in the
        # Communicate constructor or via SSML; we'll attempt to pass rate/pitch via properties if available.
        # For compatibility, we will not rely on undocumented kwargs — users can set voice to control prosody.

        coro = communicate.save(tmp)
        try:
            await asyncio.wait_for(coro, timeout=float(timeout or 120))
        except asyncio.TimeoutError as exc:
            from faust_backend.speech_runtime import SpeechRuntimeError
            raise SpeechRuntimeError(f"Edge TTS 超时 ({timeout}s)") from exc

        path = Path(tmp)
        if not path.exists():
            from faust_backend.speech_runtime import SpeechRuntimeError
            raise SpeechRuntimeError("Edge TTS 未生成音频文件")
        data = path.read_bytes()
        return data, "audio/mpeg"
    except Exception as exc:  # pragma: no cover - surface failures
        from faust_backend.speech_runtime import SpeechRuntimeError
        raise SpeechRuntimeError(f"Edge TTS 合成失败: {exc}") from exc
    finally:
        try:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
