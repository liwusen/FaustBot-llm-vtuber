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


class _FakeGetResponse(_FakeResponse):
    pass


def test_cloud_tts_branch_uses_service_key_and_default_refer_hash(monkeypatch):
    monkeypatch.setattr(speech_runtime.conf, "TTS_MODE", "faustbot-cloud")
    monkeypatch.setattr(speech_runtime.conf, "FAUSTBOT_CLOUD_BASE_URL", "http://cloud.example")
    monkeypatch.setattr(speech_runtime.conf, "FAUSTBOT_CLOUD_SERVICE_KEY", "FSK-test")
    monkeypatch.setattr(speech_runtime.conf, "FAUSTBOT_CLOUD_DEFAULT_REFER_HASH", "hash-123")
    monkeypatch.setattr(speech_runtime.conf, "FAUSTBOT_CLOUD_TIMEOUT_SECONDS", 9)

    def fake_get(url, headers=None, timeout=None):
        assert url == "http://cloud.example/v1/references/by-signature/432f520f2d198094b0742271a554eb597fe14d4933c7b9946d7ded4d4de85d4e"
        assert headers == {"Authorization": "Bearer FSK-test"}
        assert timeout == 9
        return _FakeGetResponse(status_code=404, text="not found")

    def fake_post(url, json=None, headers=None, timeout=None, files=None, data=None):
        if url == "http://cloud.example/v1/references":
            assert headers == {"Authorization": "Bearer FSK-test"}
            assert timeout == 9
            assert files and "file" in files
            assert data and data.get("prompt_text")
            assert data and data.get("prompt_language")
            return _FakeResponse(json_body={"refer_hash": "hash-123"})
        assert url == "http://cloud.example/v1/tts"
        assert json == {"refer_hash": "hash-123", "text": "你好", "text_language": "zh"}
        assert headers == {"Authorization": "Bearer FSK-test"}
        assert timeout == 9
        return _FakeResponse(content=b"AUDIO", headers={"content-type": "audio/wav"})

    monkeypatch.setattr(speech_runtime.requests, "get", fake_get)
    monkeypatch.setattr(speech_runtime.requests, "post", fake_post)

    audio, content_type = speech_runtime.synthesize_tts("你好")
    assert audio == b"AUDIO"
    assert content_type == "audio/wav"


def test_cloud_asr_branch_uses_service_key(monkeypatch):
    monkeypatch.setattr(speech_runtime.conf, "ASR_MODE", "faustbot-cloud")
    monkeypatch.setattr(speech_runtime.conf, "FAUSTBOT_CLOUD_BASE_URL", "http://cloud.example/")
    monkeypatch.setattr(speech_runtime.conf, "FAUSTBOT_CLOUD_SERVICE_KEY", "FSK-test")
    monkeypatch.setattr(speech_runtime.conf, "FAUSTBOT_CLOUD_TIMEOUT_SECONDS", 11)

    def fake_post(url, files=None, headers=None, timeout=None):
        assert url == "http://cloud.example/v1/asr"
        assert files["file"][0] == "chunk.wav"
        assert files["file"][1] == b"WAV"
        assert files["file"][2] == "audio/wav"
        assert headers == {"Authorization": "Bearer FSK-test"}
        assert timeout == 11
        return _FakeResponse(json_body={"status": "success", "text": "测试"})

    monkeypatch.setattr(speech_runtime.requests, "post", fake_post)

    payload = speech_runtime.transcribe_audio("chunk.wav", b"WAV", "audio/wav")
    assert payload["status"] == "success"
    assert payload["text"] == "测试"