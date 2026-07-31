from pathlib import Path
from typing import Any

import faust_backend.config_loader as conf


def _conf_get(key: str, default: Any = None) -> Any:
    try:
        value = conf.__dict__[key]
        return value if value is not None else default
    except KeyError:
        return default


def _resolve_api_url(base_url: str, suffix: str) -> str:
    from faust_backend.speech.errors import SpeechRuntimeError
    base = str(base_url or "").strip()
    if not base:
        raise SpeechRuntimeError("未配置 OpenAI 兼容 API Base URL")
    if base.endswith(suffix):
        return base
    return base.rstrip("/") + suffix


def _split_csv_values(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def current_tts_mode() -> str:
    return str(conf.TTS_MODE or "gpt-sovits").strip().lower()


def current_asr_mode() -> str:
    return str(conf.ASR_MODE or "whisper").strip().lower()


def should_start_local_tts() -> bool:
    return current_tts_mode() == "gpt-sovits"


def should_start_local_asr() -> bool:
    return current_asr_mode() in {"whisper", "funasr"}


def frontend_speech_config() -> dict[str, Any]:
    return {
        "tts_mode": current_tts_mode(),
        "asr_mode": current_asr_mode(),
        "asr_detection_mode": "vad",
        "vad_ws_path": "/faust/audio/ws/vad",
        "frontend_default_tts_lang": _conf_get("FRONTEND_DEFAULT_TTS_LANG", "zh") or "zh",
        "openai_asr_energy_threshold": float(conf.OPENAI_ASR_ENERGY_THRESHOLD or 0.02),
        "openai_asr_silence_ms": int(conf.OPENAI_ASR_SILENCE_MS or 700),
        "openai_asr_min_speech_ms": int(conf.OPENAI_ASR_MIN_SPEECH_MS or 250),
        "openai_asr_preroll_ms": int(conf.OPENAI_ASR_PREROLL_MS or 250),
    }
