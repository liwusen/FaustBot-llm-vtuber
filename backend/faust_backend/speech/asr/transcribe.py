from typing import Any

import base64

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
        if isinstance(data, dict) and data.get("text"):
            return {"status": "success", "text": str(data.get("text"))}
        raise SpeechRuntimeError(str(data.get("message") or data.get("error") or data))

    if current_asr_mode() == "mimo":
        api_key = str(conf.MIMO_API_KEY or "").strip()
        if not api_key:
            raise SpeechRuntimeError("未配置 MIMO_API_KEY（faust.config.private.json）")
        # 平台仅接受 mp3/wav；按上传的 MIME 推断格式
        mime_lower = str(mime_type or "audio/wav").lower()
        is_mp3 = ("mpeg" in mime_lower or "mp3" in mime_lower)
        fmt = "mp3" if is_mp3 else "wav"
        inner_fmt = "audio/mpeg" if is_mp3 else "audio/wav"
        b64 = base64.b64encode(audio_bytes).decode("ascii")
        payload: dict[str, Any] = {
            "model": str(conf.MIMO_ASR_MODEL or "mimo-v2.5-asr"),
            "messages": [{
                "role": "user",
                "content": [{
                    "type": "input_audio",
                    "input_audio": {
                        "data": f"data:{inner_fmt};base64,{b64}",
                        "format": fmt,
                    },
                }],
            }],
            "asr_options": {"language": str(conf.MIMO_ASR_LANGUAGE or "auto")},
        }
        url = _resolve_api_url(
            str(conf.MIMO_ASR_BASE_URL or conf.MIMO_TTS_BASE_URL or "https://api.xiaomimimo.com/v1"),
            "/chat/completions",
        )
        resp = requests.post(
            url,
            json=payload,
            headers={"api-key": api_key, "Content-Type": "application/json"},
            timeout=120,
        )
        if not resp.ok:
            raise SpeechRuntimeError(f"MiMo ASR 服务错误: {resp.status_code} {resp.text}")
        try:
            data = resp.json()
            text = str(data["choices"][0]["message"]["content"] or "")
        except Exception as exc:
            raise SpeechRuntimeError(f"MiMo ASR 响应格式异常: {resp.text[:200]}") from exc
        return {"status": "success", "text": text.strip(), "mode": "mimo"}

    from faust_backend.runtime import state as runtime_state
    from faust_backend.provider import get_main_credentials
    _, _speech_key, _ = get_main_credentials(runtime_state.get_model_providers())
    api_key = str(_speech_key or "").strip()
    if not api_key:
        raise SpeechRuntimeError("未配置 provider API key（provider.private.json）")

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
