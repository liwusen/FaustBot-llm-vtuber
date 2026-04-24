import asyncio
import pytest
from unittest import mock

import faust_backend.tts_edge as tts_edge
from faust_backend.speech_runtime import SpeechRuntimeError


@pytest.mark.asyncio
async def test_synthesize_success(monkeypatch, tmp_path):
    # Mock edge_tts.Communicate.save to write bytes
    class DummyComm:
        def __init__(self, text, voice):
            pass

        async def save(self, path):
            Path = __import__("pathlib").Path
            p = Path(path)
            p.write_bytes(b"FAKEAUDIO")

    monkeypatch.setattr(tts_edge, "edge_tts", mock.Mock(Communicate=DummyComm))
    data, ctype = await tts_edge.synthesize_edge_tts("hello")
    assert data == b"FAKEAUDIO"
    assert ctype.startswith("audio/")


@pytest.mark.asyncio
async def test_synthesize_timeout(monkeypatch):
    class SlowComm:
        def __init__(self, text, voice):
            pass

        async def save(self, path):
            await asyncio.sleep(5)

    monkeypatch.setattr(tts_edge, "edge_tts", mock.Mock(Communicate=SlowComm))
    with pytest.raises(SpeechRuntimeError):
        await tts_edge.synthesize_edge_tts("hello", timeout=0.01)
