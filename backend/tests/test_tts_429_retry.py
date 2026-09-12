"""MiMo / Edge TTS 的 429 限流退避重试（speech/tts/retry.py）。

覆盖：429 重试后成功、重试耗尽报错、非 429 错误不做无谓重试。
"""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import base64  # noqa: E402
import types  # noqa: E402

import aiohttp  # noqa: E402
import pytest  # noqa: E402
from multidict import CIMultiDict  # noqa: E402
from yarl import URL  # noqa: E402

import faust_backend.config_loader as conf  # noqa: E402
import faust_backend.tts_edge as tts_edge  # noqa: E402
from faust_backend.speech.errors import SpeechRuntimeError  # noqa: E402
from faust_backend.speech.tts import retry as tts_retry  # noqa: E402
from faust_backend.speech.tts import synthesize as tts_synthesize  # noqa: E402

_REQUEST_INFO = aiohttp.RequestInfo(
    URL("wss://example.invalid/tts"), "GET", CIMultiDict(), URL("wss://example.invalid/tts")
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.text = "" if self.ok else f"upstream status {status_code}"
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


def _mimo_audio_payload(audio: bytes = b"MIMOAUDIO") -> dict:
    return {
        "choices": [{"message": {"audio": {"data": base64.b64encode(audio).decode("ascii")}}}]
    }


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """去掉退避等待，保持重试次数不变。"""
    monkeypatch.setattr(tts_retry, "RETRY_DELAYS", (0.0, 0.0))


@pytest.fixture(autouse=True)
def _mimo_conf(monkeypatch):
    monkeypatch.setattr(conf, "MIMO_API_KEY", "test-key")
    monkeypatch.setattr(conf, "MIMO_TTS_BASE_URL", "https://mimo.invalid/v1")
    monkeypatch.setattr(conf, "MIMO_TTS_MODEL", "mimo-v2.5-tts")
    monkeypatch.setattr(conf, "MIMO_TTS_VOICE", "mimo_default")
    monkeypatch.setattr(conf, "MIMO_TTS_STYLE_PROMPT", "")
    monkeypatch.setattr(conf, "MIMO_TTS_FORMAT", "wav")
    monkeypatch.setattr(conf, "MIMO_TTS_REFER_WAV_PATH", "")


def _patch_mimo_posts(monkeypatch, statuses: list[int]):
    """按顺序返回给定状态码；末次状态码复用到底。记录实际请求次数。"""
    calls: list[int] = []

    def fake_post(url, **kwargs):
        calls.append(len(calls))
        status = statuses[min(len(calls) - 1, len(statuses) - 1)]
        if status == 200:
            return _FakeResponse(200, _mimo_audio_payload())
        return _FakeResponse(status)

    monkeypatch.setattr(tts_synthesize.requests, "post", fake_post)
    return calls


@pytest.mark.asyncio
async def test_mimo_429_retried_then_succeeds(monkeypatch):
    calls = _patch_mimo_posts(monkeypatch, [429, 429, 200])

    audio, content_type = await tts_synthesize._synthesize_mimo("你好")

    assert (audio, content_type) == (b"MIMOAUDIO", "audio/wav")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_mimo_429_exhausted_raises(monkeypatch):
    calls = _patch_mimo_posts(monkeypatch, [429])

    with pytest.raises(SpeechRuntimeError, match="速率限制"):
        await tts_synthesize._synthesize_mimo("你好")

    assert len(calls) == 3  # 1 次首试 + 2 次重试


@pytest.mark.asyncio
async def test_mimo_non_rate_limit_error_not_retried(monkeypatch):
    calls = _patch_mimo_posts(monkeypatch, [500])

    with pytest.raises(SpeechRuntimeError, match="MiMo TTS 服务错误: 500"):
        await tts_synthesize._synthesize_mimo("你好")

    assert len(calls) == 1


class _FakeCommunicate:
    """edge_tts.Communicate 替身：前 fail_times 次抛 429 握手错误。"""

    attempts = 0
    fail_times = 1
    fail_status = 429

    def __init__(self, text, voice):
        self.text = text
        self.voice = voice

    async def save(self, path):
        type(self).attempts += 1
        if type(self).attempts <= type(self).fail_times:
            raise aiohttp.ClientResponseError(
                _REQUEST_INFO, (), status=type(self).fail_status, message="Too Many Requests"
            )
        Path(path).write_bytes(b"EDGEAUDIO")


@pytest.fixture
def _edge(monkeypatch):
    monkeypatch.setattr(conf, "EDGE_TTS_VOICE", "en-US-AriaNeural")
    monkeypatch.setattr(conf, "EDGE_TTS_RATE", "0%")
    monkeypatch.setattr(conf, "EDGE_TTS_PITCH", "0%")
    monkeypatch.setattr(tts_edge, "edge_tts", types.SimpleNamespace(Communicate=_FakeCommunicate))
    _FakeCommunicate.attempts = 0
    _FakeCommunicate.fail_times = 1
    _FakeCommunicate.fail_status = 429
    return _FakeCommunicate


@pytest.mark.asyncio
async def test_edge_429_retried_then_succeeds(_edge):
    _edge.fail_times = 1

    audio, content_type = await tts_edge.synthesize_edge_tts("hello", timeout=5)

    assert (audio, content_type) == (b"EDGEAUDIO", "audio/mpeg")
    assert _edge.attempts == 2


@pytest.mark.asyncio
async def test_edge_429_exhausted_raises(_edge):
    _edge.fail_times = 99

    with pytest.raises(SpeechRuntimeError, match="速率限制") as exc_info:
        await tts_edge.synthesize_edge_tts("hello", timeout=5)

    assert "合成失败" not in str(exc_info.value)
    assert _edge.attempts == 3


@pytest.mark.asyncio
async def test_edge_non_rate_limit_error_not_retried(_edge):
    _edge.fail_status = 500

    with pytest.raises(SpeechRuntimeError, match="Edge TTS 合成失败"):
        await tts_edge.synthesize_edge_tts("hello", timeout=5)

    assert _edge.attempts == 1
