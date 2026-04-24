import sys
from pathlib import Path
from unittest import mock

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.argv = [sys.argv[0]]

import faust_backend.speech_runtime as sr


def test_speech_runtime_edge_mode(monkeypatch):
    # Ensure when TTS_MODE=edge-tts, synthesize_tts delegates to adapter
    monkeypatch.setattr(sr, "_current_tts_mode", lambda: "edge-tts")
    monkeypatch.setattr(sr.conf, "EDGE_TTS_VOICE", "voice-a")
    monkeypatch.setattr(sr.conf, "EDGE_TTS_RATE", "+10%")
    monkeypatch.setattr(sr.conf, "EDGE_TTS_PITCH", "-5%")
    monkeypatch.setattr(sr.conf, "EDGE_TTS_TIMEOUT_SECONDS", 33)

    dummy_module = mock.Mock(synthesize_edge_tts=mock.AsyncMock(return_value=(b"OK", "audio/mpeg")))
    monkeypatch.setattr(sr, "tts_edge", dummy_module, raising=False)
    data, ctype = sr.synthesize_tts("hello")
    assert data == b"OK"
    assert ctype == "audio/mpeg"
    dummy_module.synthesize_edge_tts.assert_awaited_once_with(
        "hello",
        voice="voice-a",
        rate="+10%",
        pitch="-5%",
        timeout=33,
    )


def test_should_start_local_asr_treats_whisper_as_local(monkeypatch):
    monkeypatch.setattr(sr.conf, "ASR_MODE", "whisper")
    assert sr.should_start_local_asr() is True
