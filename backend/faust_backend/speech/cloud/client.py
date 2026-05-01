import hashlib
from pathlib import Path
from typing import Any

import requests
import faust_backend.config_loader as conf
from faust_backend.speech.errors import SpeechRuntimeError


def current_cloud_base_url() -> str:
    return str(conf.FAUSTBOT_CLOUD_BASE_URL or "http://127.0.0.1:18980").strip()


def current_cloud_headers() -> dict[str, str]:
    service_key = str(conf.FAUSTBOT_CLOUD_SERVICE_KEY or "").strip()
    if not service_key:
        raise SpeechRuntimeError("未配置 FAUSTBOT_CLOUD_SERVICE_KEY")
    return {"Authorization": f"Bearer {service_key}"}


def resolve_cloud_url(path: str) -> str:
    base = current_cloud_base_url()
    if not base:
        raise SpeechRuntimeError("未配置 FAUSTBOT_CLOUD_BASE_URL")
    return base.rstrip("/") + path


def cloud_reference_signature() -> str:
    refer_path = str(conf.TTS_REFER_WAV_PATH or "").strip()
    prompt_text = str(conf.TTS_PROMPT_TEXT or "").strip()
    prompt_language = str(conf.TTS_PROMPT_LANGUAGE or "zh").strip()
    if not refer_path:
        raise SpeechRuntimeError("未配置 TTS_REFER_WAV_PATH")
    normalized_path = Path(refer_path).expanduser()
    if not normalized_path.is_absolute():
        normalized_path = Path(conf.CONFIG_ROOT) / normalized_path
    if not normalized_path.exists():
        raise SpeechRuntimeError(f"TTS 参考音频不存在: {normalized_path}")
    payload = normalized_path.read_bytes() + b"\n" + prompt_text.encode("utf-8") + b"\n" + prompt_language.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def cloud_get_reference(signature: str) -> dict[str, Any] | None:
    resp = requests.get(
        resolve_cloud_url(f"/v1/references/by-signature/{signature}"),
        headers=current_cloud_headers(),
        timeout=int(conf.FAUSTBOT_CLOUD_TIMEOUT_SECONDS or 120),
    )
    if resp.status_code == 404:
        return None
    if not resp.ok:
        raise SpeechRuntimeError(f"FaustBot Cloud reference 查询失败: {resp.status_code} {resp.text}")
    try:
        return resp.json()
    except Exception as exc:
        raise SpeechRuntimeError(f"FaustBot Cloud reference 返回非 JSON: {resp.text}") from exc


def cloud_upload_reference() -> str:
    refer_path = str(conf.TTS_REFER_WAV_PATH or "").strip()
    prompt_text = str(conf.TTS_PROMPT_TEXT or "").strip()
    prompt_language = str(conf.TTS_PROMPT_LANGUAGE or "zh").strip()
    normalized_path = Path(refer_path).expanduser()
    if not normalized_path.is_absolute():
        normalized_path = Path(conf.CONFIG_ROOT) / normalized_path
    if not normalized_path.exists():
        raise SpeechRuntimeError(f"TTS 参考音频不存在: {normalized_path}")
    with normalized_path.open("rb") as f:
        resp = requests.post(
            resolve_cloud_url("/v1/references"),
            headers=current_cloud_headers(),
            files={"file": (normalized_path.name, f.read(), "audio/wav")},
            data={"prompt_text": prompt_text, "prompt_language": prompt_language},
            timeout=int(conf.FAUSTBOT_CLOUD_TIMEOUT_SECONDS or 120),
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
