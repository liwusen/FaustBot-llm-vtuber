import sys
from pathlib import Path
from unittest import mock

import asyncio
import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.argv = [sys.argv[0]]

import faust_backend.speech_runtime as sr
import faust_backend.tts_edge as tts_edge_module


def test_speech_runtime_edge_mode(monkeypatch):
    # Ensure when TTS_MODE=edge-tts, synthesize_tts delegates to adapter
    monkeypatch.setattr(sr, "_current_tts_mode", lambda: "edge-tts")
    monkeypatch.setattr(sr.conf, "EDGE_TTS_VOICE", "voice-a")
    monkeypatch.setattr(sr.conf, "EDGE_TTS_RATE", "+10%")
    monkeypatch.setattr(sr.conf, "EDGE_TTS_PITCH", "-5%")
    monkeypatch.setattr(sr.conf, "EDGE_TTS_TIMEOUT_SECONDS", 33)

    # 直接 mock tts_edge 模块的函数，而非 sr.tts_edge
    mock_impl = mock.AsyncMock(return_value=(b"OK", "audio/mpeg"))
    monkeypatch.setattr(tts_edge_module, "synthesize_edge_tts", mock_impl)

    async def _run():
        return await sr.synthesize_tts("hello")

    data, ctype = asyncio.run(_run())
    assert data == b"OK"
    assert ctype == "audio/mpeg"
    mock_impl.assert_awaited_once_with(
        "hello",
        voice="voice-a",
        rate="+10%",
        pitch="-5%",
        timeout=33,
    )


def test_should_start_local_asr_treats_whisper_as_local(monkeypatch):
    monkeypatch.setattr(sr.conf, "ASR_MODE", "whisper")
    assert sr.should_start_local_asr() is True
