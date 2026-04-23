import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.argv = [sys.argv[0]]

import faust_backend.speech_runtime as speech_runtime


class _FakeResponse:
    def __init__(self, *, ok=True, content=b"", text="", json_body=None, headers=None, status_code=200):
        self.ok = ok
        self.content = content
        self.text = text
        self._json_body = json_body
        self.headers = headers or {}
        self.status_code = status_code

    def json(self):
        return self._json_body


def test_cloud_tts_uses_existing_reference_without_upload(monkeypatch, tmp_path):
    refer_file = tmp_path / "ref.wav"
    refer_file.write_bytes(b"fake-wav")
    monkeypatch.setattr(speech_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(speech_runtime.conf, "TTS_MODE", "faustbot-cloud")
    monkeypatch.setattr(speech_runtime.conf, "FAUSTBOT_CLOUD_BASE_URL", "http://cloud.example")
    monkeypatch.setattr(speech_runtime.conf, "FAUSTBOT_CLOUD_SERVICE_KEY", "FSK-test")
    monkeypatch.setattr(speech_runtime.conf, "TTS_REFER_WAV_PATH", str(refer_file))
    monkeypatch.setattr(speech_runtime.conf, "TTS_PROMPT_TEXT", "一二三。")
    monkeypatch.setattr(speech_runtime.conf, "TTS_PROMPT_LANGUAGE", "zh")

    calls = {"lookup": 0, "upload": 0, "tts": 0}

    def fake_get(url, headers=None, timeout=None):
        calls["lookup"] += 1
        assert url.startswith("http://cloud.example/v1/references/by-signature/")
        return _FakeResponse(status_code=404, text="not found")

    def fake_post(url, json=None, headers=None, timeout=None, files=None, data=None):
        if url.endswith("/v1/references"):
            calls["upload"] += 1
            return _FakeResponse(json_body={"refer_hash": "hash-123"})
        if url.endswith("/v1/tts"):
            calls["tts"] += 1
            return _FakeResponse(content=b"audio", headers={"content-type": "audio/wav"})
        raise AssertionError(url)

    monkeypatch.setattr(speech_runtime.requests, "get", fake_get)
    monkeypatch.setattr(speech_runtime.requests, "post", fake_post)

    audio, content_type = speech_runtime.synthesize_tts("你好")
    assert audio == b"audio"
    assert content_type == "audio/wav"
    assert calls == {"lookup": 1, "upload": 1, "tts": 1}