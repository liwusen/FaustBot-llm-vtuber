import asyncio
from typing import Any

import requests

import faust_backend.config_loader as conf
from faust_backend.speech.errors import SpeechRuntimeError
from faust_backend.speech.config import (
    _conf_get,
    _resolve_api_url,
    current_tts_mode,
    should_start_local_tts,
)
from faust_backend.speech.cloud.client import (
    cloud_reference_signature,
    cloud_get_reference,
    cloud_upload_reference,
    resolve_cloud_url,
    current_cloud_headers,
)


def _resolve_local_tts_reference() -> tuple[str, str, str]:
    refer_path = str(conf.TTS_REFER_WAV_PATH or "").strip()
    prompt_text = str(conf.TTS_PROMPT_TEXT or "").strip()
    prompt_language = str(conf.TTS_PROMPT_LANGUAGE or "zh").strip()
    if not refer_path:
        raise SpeechRuntimeError("未配置本地 TTS 参考音频路径")
    from pathlib import Path
    normalized_path = Path(refer_path).expanduser()
    if not normalized_path.is_absolute():
        normalized_path = Path(conf.CONFIG_ROOT) / normalized_path
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


LOCAL_TTS_ENDPOINT = "http://127.0.0.1:5000/"


def _apply_tts_text_hook(text: str) -> str:
    """tts_text hook: plugins may rewrite the text fed to TTS (speech only,
    subtitles unaffected). First non-empty string result wins."""
    try:
        from faust_backend.runtime import state

        pm = getattr(state, "plugin_manager", None)
        if pm is None:
            return text
        results = pm._call_pluggy_hook("tts_text", text=text, ctx=None)
        for r in results:
            if isinstance(r, str) and r:
                return r
    except Exception:
        pass
    return text


def _fire_tts_start(text: str) -> None:
    """tts_start hook: notified when a TTS segment is synthesized and about to
    be delivered to the frontend for playback."""
    try:
        from faust_backend.runtime import state

        pm = getattr(state, "plugin_manager", None)
        if pm is not None:
            pm._call_pluggy_hook("tts_start", text=text, ctx=None)
    except Exception:
        pass


async def synthesize_tts(text: str, lang: str | None = None) -> tuple[bytes, str]:
    payload_text = str(text or "").strip()
    if not payload_text:
        raise SpeechRuntimeError("TTS 文本不能为空")
    payload_text = _apply_tts_text_hook(payload_text)
    try:
        return await _synthesize_impl(payload_text, lang)
    finally:
        _fire_tts_start(payload_text)


async def _synthesize_impl(payload_text: str, lang: str | None) -> tuple[bytes, str]:
    if current_tts_mode() == "edge-tts":
        import faust_backend.tts_edge as tts_edge
        try:
            return await tts_edge.synthesize_edge_tts(
                payload_text,
                voice=conf.EDGE_TTS_VOICE,
                rate=conf.EDGE_TTS_RATE,
                pitch=conf.EDGE_TTS_PITCH,
                timeout=int(conf.EDGE_TTS_TIMEOUT_SECONDS or 120),
            )
        except SpeechRuntimeError:
            raise
        except Exception as exc:
            raise SpeechRuntimeError(f"Edge TTS 失败: {exc}") from exc

    if should_start_local_tts():
        _prime_local_tts_reference()
        payload = {
            "text": payload_text,
            "text_language": str(lang or _conf_get("FRONTEND_DEFAULT_TTS_LANG", "zh") or "zh"),
        }
        resp = await asyncio.to_thread(
            requests.post, LOCAL_TTS_ENDPOINT, json=payload, timeout=120
        )
        if not resp.ok:
            raise SpeechRuntimeError(f"本地 TTS 服务错误: {resp.status_code} {resp.text}")
        return resp.content, (resp.headers.get("content-type") or "audio/wav")

    if current_tts_mode() == "faustbot-cloud":
        signature = cloud_reference_signature()
        existing = cloud_get_reference(signature)
        refer_hash = str((existing or {}).get("refer_hash") or "").strip() if existing else ""
        if not refer_hash:
            refer_hash = cloud_upload_reference()
        payload = {
            "refer_hash": refer_hash,
            "text": payload_text,
            "text_language": str(lang or _conf_get("FRONTEND_DEFAULT_TTS_LANG", "zh") or "zh"),
        }
        resp = await asyncio.to_thread(
            requests.post,
            resolve_cloud_url("/v1/tts"),
            json=payload,
            headers=current_cloud_headers(),
            timeout=int(conf.FAUSTBOT_CLOUD_TIMEOUT_SECONDS or 120),
        )
        if not resp.ok:
            raise SpeechRuntimeError(f"FaustBot Cloud TTS 服务错误: {resp.status_code} {resp.text}")
        return resp.content, (resp.headers.get("content-type") or "audio/wav")

    from faust_backend.runtime import state as runtime_state
    from faust_backend.provider import get_main_credentials
    _, _speech_key, _ = get_main_credentials(runtime_state.get_model_providers())
    api_key = str(_speech_key or "").strip()
    if not api_key:
        raise SpeechRuntimeError("未配置 provider API key（provider.private.json）")

    payload: dict[str, Any] = {
        "model": conf.OPENAI_TTS_MODEL,
        "voice": conf.OPENAI_TTS_VOICE,
        "input": payload_text,
        "response_format": conf.OPENAI_TTS_RESPONSE_FORMAT,
        "speed": float(conf.OPENAI_TTS_SPEED or 1.0),
    }
    instructions = str(conf.OPENAI_TTS_INSTRUCTIONS or "").strip()
    if instructions:
        payload["instructions"] = instructions

    url = _resolve_api_url(str(conf.OPENAI_TTS_BASE_URL or "https://api.openai.com/v1"), "/audio/speech")
    resp = await asyncio.to_thread(
        requests.post,
        url,
        json=payload,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=120,
    )
    if not resp.ok:
        raise SpeechRuntimeError(f"OpenAI TTS 服务错误: {resp.status_code} {resp.text}")
    content_type = resp.headers.get("content-type") or f"audio/{payload['response_format']}"
    return resp.content, content_type
