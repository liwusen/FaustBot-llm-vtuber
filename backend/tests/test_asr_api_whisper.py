import io
import sys
from types import SimpleNamespace
import types

import pytest


def _load_asr_api():
    if "asr_api" in sys.modules:
        return sys.modules["asr_api"]
    import os
    from pathlib import Path

    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    sys.argv = [sys.argv[0]]
    sys.modules.setdefault("funasr", types.SimpleNamespace(AutoModel=lambda *args, **kwargs: None))
    import asr_api

    return asr_api


@pytest.mark.asyncio
async def test_upload_audio_whisper_branch(monkeypatch):
    asr_api = _load_asr_api()

    class DummyModel:
        def transcribe(self, path, **kwargs):
            assert kwargs["language"] == "zh"
            assert kwargs["prompt"] == "hello"
            assert kwargs["temperature"] == 0.1
            return {"text": "测试文本"}

    monkeypatch.setattr(asr_api, "_current_asr_mode", lambda: "whisper")
    monkeypatch.setattr(asr_api, "_get_whisper_model", lambda: DummyModel())
    monkeypatch.setattr(asr_api, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    monkeypatch.setattr(asr_api, "File", lambda default=None: default)

    import faust_backend.config_loader as conf
    monkeypatch.setattr(conf, "WHISPER_LANGUAGE", "zh")
    monkeypatch.setattr(conf, "WHISPER_PROMPT", "hello")
    monkeypatch.setattr(conf, "WHISPER_TEMPERATURE", 0.1)
    monkeypatch.setattr(conf, "WHISPER_BEST_OF", 2)
    monkeypatch.setattr(conf, "WHISPER_BEAM_SIZE", 3)
    monkeypatch.setattr(conf, "WHISPER_FP16", False)

    async def _read():
        return b"RIFF....WAVE"

    file_obj = SimpleNamespace(filename="sample.wav", read=_read)
    result = await asr_api.upload_audio(file_obj)
    assert result["status"] == "success"
    assert result["text"] == "测试文本"
    assert result["mode"] == "whisper"