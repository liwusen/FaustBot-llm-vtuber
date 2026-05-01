from typing import Any

import requests

import faust_backend.config_loader as conf
from faust_backend.speech.errors import SpeechRuntimeError
from faust_backend.speech.config import (
    _resolve_api_url,
    _split_csv_values,
    should_start_local_asr,
    current_asr_mode,
)
from faust_backend.speech.cloud.client import (
    resolve_cloud_url,
    current_cloud_headers,
)


LOCAL_ASR_ENDPOINT = "http://127.0.0.1:1000/v1/upload_audio"


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

    if current_asr_mode() == "faustbot-cloud":
        resp = requests.post(
            resolve_cloud_url("/v1/asr"),
            files={"file": (safe_name, audio_bytes, mime_type)},
            headers=current_cloud_headers(),
            timeout=int(conf.FAUSTBOT_CLOUD_TIMEOUT_SECONDS or 120),
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

    api_key = str(conf.OPENAI_ASR_API_KEY or "").strip()
    if not api_key:
        raise SpeechRuntimeError("未配置 OPENAI_ASR_API_KEY")

    payload: dict[str, Any] = {
        "model": conf.OPENAI_ASR_MODEL,
        "response_format": conf.OPENAI_ASR_RESPONSE_FORMAT,
        "temperature": float(conf.OPENAI_ASR_TEMPERATURE or 0.0),
    }
    language = str(conf.OPENAI_ASR_LANGUAGE or "").strip()
    prompt = str(conf.OPENAI_ASR_PROMPT or "").strip()
    if language:
        payload["language"] = language
    if prompt:
        payload["prompt"] = prompt
    request_data: list[tuple[str, Any]] = list(payload.items())
    timestamp_granularities = _split_csv_values(conf.OPENAI_ASR_TIMESTAMP_GRANULARITIES)
    if payload.get("response_format") == "verbose_json" and timestamp_granularities:
        for item in timestamp_granularities:
            request_data.append(("timestamp_granularities[]", item))

    url = _resolve_api_url(str(conf.OPENAI_ASR_BASE_URL or "https://api.openai.com/v1"), "/audio/transcriptions")
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
