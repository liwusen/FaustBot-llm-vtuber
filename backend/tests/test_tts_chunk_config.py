# backend/tests/test_tts_chunk_config.py
"""TTS_CHUNK_IDEAL_TOKENS 配置：默认值、public 配置文件读取、前端配置下发。"""
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import faust_backend.config_loader as conf  # noqa: E402
from faust_backend.speech.config import frontend_speech_config  # noqa: E402


def test_tts_chunk_ideal_tokens_default_and_propagation():
    conf.reload_configs()
    v = int(conf.TTS_CHUNK_IDEAL_TOKENS)
    assert 10 <= v <= 100
    fe = frontend_speech_config()
    assert fe["tts_chunk_ideal_tokens"] == v


def test_tts_chunk_ideal_tokens_respects_config_file(tmp_path, monkeypatch):
    cfg_file = tmp_path / "faust.config.json"
    cfg_file.write_text('{"TTS_CHUNK_IDEAL_TOKENS": 55}', encoding="utf-8")
    monkeypatch.setattr(conf, "CONFIG_FILE_PATH", str(cfg_file))
    monkeypatch.setattr(conf, "MODEL_PROVIDERS", None)
    conf.load_configs()
    try:
        assert int(conf.TTS_CHUNK_IDEAL_TOKENS) == 55
    finally:
        conf.reload_configs()
