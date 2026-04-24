import pytest
from unittest import mock

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
