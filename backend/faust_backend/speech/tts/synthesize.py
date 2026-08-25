import asyncio
import base64
from pathlib import Path
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

MIMO_TTS_MODEL_BUILT_IN = "mimo-v2.5-tts"
MIMO_TTS_MODEL_VOICE_DESIGN = "mimo-v2.5-tts-voicedesign"
MIMO_TTS_MODEL_VOICE_CLONE = "mimo-v2.5-tts-voiceclone"


def _resolve_mimo_api_key() -> str:
    api_key = str(conf.MIMO_API_KEY or "").strip()
    if not api_key:
        raise SpeechRuntimeError("未配置 MIMO_API_KEY（faust.config.private.json）")
    return api_key


def _mimo_sample_data_uri(refer_path: str) -> tuple[str, str]:
    """读取克隆参考音频，返回 (data URI, mime)。仅支持 mp3/wav（平台限制）。"""
    sample = Path(str(refer_path or "").strip()).expanduser()
    if not sample.is_absolute():
        sample = Path(conf.CONFIG_ROOT) / sample
    if not sample.exists():
        raise SpeechRuntimeError(f"MiMo 克隆参考音频不存在: {sample}")
    suffix = sample.suffix.lower()
    if suffix in (".mp3", ".mpeg"):
        mime = "audio/mpeg"
    elif suffix == ".wav":
        mime = "audio/wav"
    else:
        raise SpeechRuntimeError(f"MiMo 克隆参考音频仅支持 mp3/wav: {sample.name}")
    raw = sample.read_bytes()
    if len(raw) > 10 * 1024 * 1024:
        raise SpeechRuntimeError(f"MiMo 克隆参考音频超过 10MB 上限: {sample.name}")
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}", mime


async def _synthesize_mimo(payload_text: str) -> tuple[bytes, str]:
    """小米 MiMo 开放平台 TTS（chat/completions 风格）。

    - 配置了 MIMO_TTS_REFER_WAV_PATH → voiceclone 模型（样本以 data URI 传入 audio.voice）
    - 否则按 MIMO_TTS_MODEL：内置音色传 voice；voicedesign 仅用风格描述（不传 voice）
    """
    api_key = _resolve_mimo_api_key()
    base_url = str(conf.MIMO_TTS_BASE_URL or "https://api.xiaomimimo.com/v1")
    model = str(conf.MIMO_TTS_MODEL or MIMO_TTS_MODEL_BUILT_IN).strip() or MIMO_TTS_MODEL_BUILT_IN
    style_prompt = str(conf.MIMO_TTS_STYLE_PROMPT or "")
    audio_cfg: dict[str, Any] = {"format": str(conf.MIMO_TTS_FORMAT or "wav")}

    refer_path = str(conf.MIMO_TTS_REFER_WAV_PATH or "").strip()
    if refer_path:
        audio_cfg["voice"], _mime = _mimo_sample_data_uri(refer_path)
        model = MIMO_TTS_MODEL_VOICE_CLONE
    else:
        voice = str(conf.MIMO_TTS_VOICE or "").strip()
        if voice and model != MIMO_TTS_MODEL_VOICE_DESIGN:
            audio_cfg["voice"] = voice

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "user", "content": style_prompt},
            {"role": "assistant", "content": payload_text},
        ],
        "audio": audio_cfg,
    }
    url = _resolve_api_url(base_url, "/chat/completions")
    resp = await asyncio.to_thread(
        requests.post,
        url,
        json=payload,
        headers={"api-key": api_key, "Content-Type": "application/json"},
        timeout=120,
    )
    if not resp.ok:
        raise SpeechRuntimeError(f"MiMo TTS 服务错误: {resp.status_code} {resp.text}")
    try:
        data = resp.json()
        b64_audio = str(data["choices"][0]["message"]["audio"]["data"])
    except Exception as exc:
        raise SpeechRuntimeError(f"MiMo TTS 响应格式异常: {resp.text[:200]}") from exc
    fmt = str(audio_cfg.get("format") or "wav")
    return base64.b64decode(b64_audio), f"audio/{fmt}"


async def _apply_tts_text_hook(text: str) -> str:
    """tts_text hook: plugins may rewrite the text fed to TTS (speech only,
    subtitles unaffected). First non-empty string result wins."""
    try:
        from faust_backend.runtime import state

        pm = getattr(state, "plugin_manager", None)
        if pm is None:
            return text
        results = await pm._call_pluggy_hook("tts_text", text=text, ctx=None)
        for r in results:
            if isinstance(r, str) and r:
                return r
    except Exception:
        pass
    return text


async def _fire_tts_start(text: str) -> None:
    """tts_start hook: notified when a TTS segment is synthesized and about to
    be delivered to the frontend for playback."""
    try:
        from faust_backend.runtime import state

        pm = getattr(state, "plugin_manager", None)
        if pm is not None:
            await pm._call_pluggy_hook("tts_start", text=text, ctx=None)
    except Exception:
        pass


async def synthesize_tts(text: str, lang: str | None = None) -> tuple[bytes, str]:
    payload_text = str(text or "").strip()
    if not payload_text:
        raise SpeechRuntimeError("TTS 文本不能为空")
    payload_text = await _apply_tts_text_hook(payload_text)
    try:
        return await _synthesize_impl(payload_text, lang)
    finally:
        await _fire_tts_start(payload_text)


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

    if current_tts_mode() == "mimo":
        try:
            return await _synthesize_mimo(payload_text)
        except SpeechRuntimeError:
            raise
        except Exception as exc:
            raise SpeechRuntimeError(f"MiMo TTS 失败: {exc}") from exc

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
