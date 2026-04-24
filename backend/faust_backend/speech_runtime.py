from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import hashlib

import requests

import faust_backend.config_loader as conf
import asyncio


LOCAL_TTS_ENDPOINT = "http://127.0.0.1:5000/"
LOCAL_ASR_ENDPOINT = "http://127.0.0.1:1000/v1/upload_audio"


class SpeechRuntimeError(RuntimeError):
    pass


def _resolve_api_url(base_url: str, suffix: str) -> str:
    base = str(base_url or "").strip()
    if not base:
        raise SpeechRuntimeError("未配置 OpenAI 兼容 API Base URL")
    if base.endswith(suffix):
        return base
    return base.rstrip("/") + suffix


def _split_csv_values(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _current_tts_mode() -> str:
    return str(getattr(conf, "TTS_MODE", "local") or "local").strip().lower()


def _current_asr_mode() -> str:
    return str(getattr(conf, "ASR_MODE", "local") or "local").strip().lower()


def _current_cloud_base_url() -> str:
    return str(getattr(conf, "FAUSTBOT_CLOUD_BASE_URL", "http://127.0.0.1:18980") or "http://127.0.0.1:18980").strip()


def _current_cloud_headers() -> dict[str, str]:
    service_key = str(getattr(conf, "FAUSTBOT_CLOUD_SERVICE_KEY", "") or "").strip()
    if not service_key:
        raise SpeechRuntimeError("未配置 FAUSTBOT_CLOUD_SERVICE_KEY")
    return {"Authorization": f"Bearer {service_key}"}


def _resolve_cloud_url(path: str) -> str:
    base = _current_cloud_base_url()
    if not base:
        raise SpeechRuntimeError("未配置 FAUSTBOT_CLOUD_BASE_URL")
    return base.rstrip("/") + path


def _cloud_reference_signature() -> str:
    refer_path = str(getattr(conf, "TTS_REFER_WAV_PATH", "") or "").strip()
    prompt_text = str(getattr(conf, "TTS_PROMPT_TEXT", "") or "").strip()
    prompt_language = str(getattr(conf, "TTS_PROMPT_LANGUAGE", "zh") or "zh").strip()
    if not refer_path:
        raise SpeechRuntimeError("未配置 TTS_REFER_WAV_PATH")
    normalized_path = Path(refer_path).expanduser()
    if not normalized_path.is_absolute():
        normalized_path = Path(getattr(conf, "CONFIG_ROOT", ".")) / normalized_path
    if not normalized_path.exists():
        raise SpeechRuntimeError(f"TTS 参考音频不存在: {normalized_path}")
    payload = normalized_path.read_bytes() + b"\n" + prompt_text.encode("utf-8") + b"\n" + prompt_language.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cloud_get_reference(signature: str) -> dict[str, Any] | None:
    resp = requests.get(
        _resolve_cloud_url(f"/v1/references/by-signature/{signature}"),
        headers=_current_cloud_headers(),
        timeout=int(getattr(conf, "FAUSTBOT_CLOUD_TIMEOUT_SECONDS", 120) or 120),
    )
    if resp.status_code == 404:
        return None
    if not resp.ok:
        raise SpeechRuntimeError(f"FaustBot Cloud reference 查询失败: {resp.status_code} {resp.text}")
    try:
        return resp.json()
    except Exception as exc:
        raise SpeechRuntimeError(f"FaustBot Cloud reference 返回非 JSON: {resp.text}") from exc


def _cloud_upload_reference() -> str:
    refer_path = str(getattr(conf, "TTS_REFER_WAV_PATH", "") or "").strip()
    prompt_text = str(getattr(conf, "TTS_PROMPT_TEXT", "") or "").strip()
    prompt_language = str(getattr(conf, "TTS_PROMPT_LANGUAGE", "zh") or "zh").strip()
    normalized_path = Path(refer_path).expanduser()
    if not normalized_path.is_absolute():
        normalized_path = Path(getattr(conf, "CONFIG_ROOT", ".")) / normalized_path
    if not normalized_path.exists():
        raise SpeechRuntimeError(f"TTS 参考音频不存在: {normalized_path}")
    with normalized_path.open("rb") as f:
        resp = requests.post(
            _resolve_cloud_url("/v1/references"),
            headers=_current_cloud_headers(),
            files={"file": (normalized_path.name, f.read(), "audio/wav")},
            data={"prompt_text": prompt_text, "prompt_language": prompt_language},
            timeout=int(getattr(conf, "FAUSTBOT_CLOUD_TIMEOUT_SECONDS", 120) or 120),
        )
    if not resp.ok:
        raise SpeechRuntimeError(f"FaustBot Cloud reference 上传失败: {resp.status_code} {resp.text}")
    try:
        data = resp.json()
    except Exception as exc:
        raise SpeechRuntimeError(f"FaustBot Cloud reference 上传返回非 JSON: {resp.text}") from exc
    refer_hash = str(data.get("refer_hash") or "").strip()
    if not refer_hash:
        raise SpeechRuntimeError("FaustBot Cloud reference 上传未返回 refer_hash")
    return refer_hash


def should_start_local_tts() -> bool:
    return _current_tts_mode() == "local"


def should_start_local_asr() -> bool:
    return _current_asr_mode() == "local"


def frontend_speech_config() -> dict[str, Any]:
    return {
        "tts_mode": _current_tts_mode(),
        "asr_mode": _current_asr_mode(),
        "asr_detection_mode": "vad",
        "vad_ws_path": "/faust/audio/ws/vad",
        "frontend_default_tts_lang": getattr(conf, "FRONTEND_DEFAULT_TTS_LANG", "zh"),
        "openai_asr_energy_threshold": float(getattr(conf, "OPENAI_ASR_ENERGY_THRESHOLD", 0.02) or 0.02),
        "openai_asr_silence_ms": int(getattr(conf, "OPENAI_ASR_SILENCE_MS", 700) or 700),
        "openai_asr_min_speech_ms": int(getattr(conf, "OPENAI_ASR_MIN_SPEECH_MS", 250) or 250),
        "openai_asr_preroll_ms": int(getattr(conf, "OPENAI_ASR_PREROLL_MS", 250) or 250),
    }


def _resolve_local_tts_reference() -> tuple[str, str, str]:
    refer_path = str(getattr(conf, "TTS_REFER_WAV_PATH", "") or "").strip()
    prompt_text = str(getattr(conf, "TTS_PROMPT_TEXT", "") or "").strip()
    prompt_language = str(getattr(conf, "TTS_PROMPT_LANGUAGE", "zh") or "zh").strip()
    if not refer_path:
        raise SpeechRuntimeError("未配置本地 TTS 参考音频路径")

    normalized_path = Path(refer_path).expanduser()
    if not normalized_path.is_absolute():
        normalized_path = Path(getattr(conf, "CONFIG_ROOT", ".")) / normalized_path
    if not normalized_path.exists():
        raise SpeechRuntimeError(f"本地 TTS 参考音频不存在: {normalized_path}")
    if not prompt_text:
        raise SpeechRuntimeError("未配置本地 TTS 参考音频文本")
    return str(normalized_path), prompt_text, prompt_language


def _prime_local_tts_reference() -> None:
    refer_wav_path, prompt_text, prompt_language = _resolve_local_tts_reference()
    resp = requests.post(
        "http://127.0.0.1:5000/change_refer",
        json={
            "refer_wav_path": refer_wav_path,
            "prompt_text": prompt_text,
            "prompt_language": prompt_language,
        },
        timeout=30,
    )
    if not resp.ok:
        raise SpeechRuntimeError(f"本地 TTS 参考音频设置失败: {resp.status_code} {resp.text}")


def synthesize_tts(text: str, lang: str | None = None) -> tuple[bytes, str]:
    payload_text = str(text or "").strip()
    if not payload_text:
        raise SpeechRuntimeError("TTS 文本不能为空")

    if _current_tts_mode() == "edge-tts":
        tts_edge = globals().get("tts_edge")
        if tts_edge is None:
            import faust_backend.tts_edge as tts_edge

        try:
            return asyncio.run(
                tts_edge.synthesize_edge_tts(
                    payload_text,
                    voice=getattr(conf, "EDGE_TTS_VOICE", None),
                    rate=getattr(conf, "EDGE_TTS_RATE", None),
                    pitch=getattr(conf, "EDGE_TTS_PITCH", None),
                    timeout=int(getattr(conf, "EDGE_TTS_TIMEOUT_SECONDS", 120) or 120),
                )
            )
        except SpeechRuntimeError:
            raise
        except Exception as exc:
            raise SpeechRuntimeError(f"Edge TTS 失败: {exc}") from exc

    if should_start_local_tts():
        _prime_local_tts_reference()
        payload = {
            "text": payload_text,
            "text_language": str(lang or getattr(conf, "FRONTEND_DEFAULT_TTS_LANG", "zh") or "zh"),
        }
        resp = requests.post(LOCAL_TTS_ENDPOINT, json=payload, timeout=120)
        if not resp.ok:
            raise SpeechRuntimeError(f"本地 TTS 服务错误: {resp.status_code} {resp.text}")
        return resp.content, (resp.headers.get("content-type") or "audio/wav")

    if _current_tts_mode() == "faustbot-cloud":
        signature = _cloud_reference_signature()
        existing = _cloud_get_reference(signature)
        refer_hash = str((existing or {}).get("refer_hash") or "").strip() if existing else ""
        if not refer_hash:
            refer_hash = _cloud_upload_reference()
        payload = {
            "refer_hash": refer_hash,
            "text": payload_text,
            "text_language": str(lang or getattr(conf, "FRONTEND_DEFAULT_TTS_LANG", "zh") or "zh"),
        }
        resp = requests.post(
            _resolve_cloud_url("/v1/tts"),
            json=payload,
            headers=_current_cloud_headers(),
            timeout=int(getattr(conf, "FAUSTBOT_CLOUD_TIMEOUT_SECONDS", 120) or 120),
        )
        if not resp.ok:
            raise SpeechRuntimeError(f"FaustBot Cloud TTS 服务错误: {resp.status_code} {resp.text}")
        return resp.content, (resp.headers.get("content-type") or "audio/wav")

    api_key = str(getattr(conf, "OPENAI_TTS_API_KEY", "") or "").strip()
    if not api_key:
        raise SpeechRuntimeError("未配置 OPENAI_TTS_API_KEY")

    payload: dict[str, Any] = {
        "model": getattr(conf, "OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
        "voice": getattr(conf, "OPENAI_TTS_VOICE", "alloy"),
        "input": payload_text,
        "response_format": getattr(conf, "OPENAI_TTS_RESPONSE_FORMAT", "mp3"),
        "speed": float(getattr(conf, "OPENAI_TTS_SPEED", 1.0) or 1.0),
    }
    instructions = str(getattr(conf, "OPENAI_TTS_INSTRUCTIONS", "") or "").strip()
    if instructions:
        payload["instructions"] = instructions

    url = _resolve_api_url(getattr(conf, "OPENAI_TTS_BASE_URL", "https://api.openai.com/v1"), "/audio/speech")
    resp = requests.post(
        url,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120,
    )
    if not resp.ok:
        raise SpeechRuntimeError(f"OpenAI TTS 服务错误: {resp.status_code} {resp.text}")
    content_type = resp.headers.get("content-type") or f"audio/{payload['response_format']}"
    return resp.content, content_type


def transcribe_audio(filename: str, audio_bytes: bytes, content_type: str | None = None) -> dict[str, Any]:
    safe_name = str(filename or "audio.wav")
    mime_type = str(content_type or "audio/wav")
    if not audio_bytes:
        raise SpeechRuntimeError("ASR 音频不能为空")

    if should_start_local_asr():
        resp = requests.post(
            LOCAL_ASR_ENDPOINT,
            files={"file": (safe_name, audio_bytes, mime_type)},
            timeout=120,
        )
        if not resp.ok:
            raise SpeechRuntimeError(f"本地 ASR 服务错误: {resp.status_code} {resp.text}")
        try:
            data = resp.json()
        except Exception as exc:
            raise SpeechRuntimeError(f"本地 ASR 返回非 JSON: {resp.text}") from exc
        if isinstance(data, dict) and data.get("status") == "success":
            return data
        if isinstance(data, dict) and data.get("text"):
            return {"status": "success", "text": str(data.get("text"))}
        raise SpeechRuntimeError(str(data.get("message") or data.get("error") or data))

    if _current_asr_mode() == "faustbot-cloud":
        resp = requests.post(
            _resolve_cloud_url("/v1/asr"),
            files={"file": (safe_name, audio_bytes, mime_type)},
            headers=_current_cloud_headers(),
            timeout=int(getattr(conf, "FAUSTBOT_CLOUD_TIMEOUT_SECONDS", 120) or 120),
        )
        if not resp.ok:
            raise SpeechRuntimeError(f"FaustBot Cloud ASR 服务错误: {resp.status_code} {resp.text}")
        try:
            data = resp.json()
        except Exception as exc:
            raise SpeechRuntimeError(f"FaustBot Cloud ASR 返回非 JSON: {resp.text}") from exc
        if isinstance(data, dict) and data.get("status") == "success":
            return data
        if isinstance(data, dict) and data.get("text"):
            return {"status": "success", "text": str(data.get("text"))}
        raise SpeechRuntimeError(str(data.get("message") or data.get("error") or data))

    api_key = str(getattr(conf, "OPENAI_ASR_API_KEY", "") or "").strip()
    if not api_key:
        raise SpeechRuntimeError("未配置 OPENAI_ASR_API_KEY")

    payload: dict[str, Any] = {
        "model": getattr(conf, "OPENAI_ASR_MODEL", "gpt-4o-transcribe"),
        "response_format": getattr(conf, "OPENAI_ASR_RESPONSE_FORMAT", "json"),
        "temperature": float(getattr(conf, "OPENAI_ASR_TEMPERATURE", 0.0) or 0.0),
    }
    language = str(getattr(conf, "OPENAI_ASR_LANGUAGE", "") or "").strip()
    prompt = str(getattr(conf, "OPENAI_ASR_PROMPT", "") or "").strip()
    if language:
        payload["language"] = language
    if prompt:
        payload["prompt"] = prompt
    request_data: list[tuple[str, Any]] = list(payload.items())
    timestamp_granularities = _split_csv_values(getattr(conf, "OPENAI_ASR_TIMESTAMP_GRANULARITIES", ""))
    if payload.get("response_format") == "verbose_json" and timestamp_granularities:
        for item in timestamp_granularities:
            request_data.append(("timestamp_granularities[]", item))

    url = _resolve_api_url(getattr(conf, "OPENAI_ASR_BASE_URL", "https://api.openai.com/v1"), "/audio/transcriptions")
    resp = requests.post(
        url,
        data=request_data,
        files={"file": (safe_name, audio_bytes, mime_type)},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120,
    )
    if not resp.ok:
        raise SpeechRuntimeError(f"OpenAI ASR 服务错误: {resp.status_code} {resp.text}")

    response_format = str(payload.get("response_format") or "json")
    text = ""
    raw_body: Any = None
    if response_format in {"json", "verbose_json"}:
        try:
            raw_body = resp.json()
        except Exception as exc:
            raise SpeechRuntimeError(f"OpenAI ASR 返回非 JSON: {resp.text}") from exc
        if isinstance(raw_body, dict):
            text = str(raw_body.get("text") or "")
        else:
            text = str(raw_body or "")
    else:
        raw_body = resp.text
        text = str(raw_body or "")

    return {
        "status": "success",
        "text": text.strip(),
        "raw": raw_body,
        "mode": "openai",
    }