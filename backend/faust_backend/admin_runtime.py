import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

try:
    import faust_backend.config_loader as conf
    import faust_backend.backend2front as backend2frontend
except ImportError:
    import config_loader as conf
    import backend2front as backend2frontend

BACKEND_ROOT = Path(conf.CONFIG_ROOT)
AGENTS_ROOT = BACKEND_ROOT / "agents"
PUBLIC_CONFIG_PATH = BACKEND_ROOT / "faust.config.json"
PRIVATE_CONFIG_PATH = BACKEND_ROOT / "faust.config.private.json"
PRIVATE_EXAMPLE_PATH = BACKEND_ROOT / "faust.config.private.example"
OBSOLETE_PUBLIC_CONFIG_KEYS = {
    "OPENAI_ASR_ENERGY_THRESHOLD",
    "OPENAI_ASR_SILENCE_MS",
    "OPENAI_ASR_MIN_SPEECH_MS",
    "OPENAI_ASR_PREROLL_MS",
}

AGENT_CORE_FILES = ["AGENT.md", "ROLE.md", "COREMEMORY.md", "TASK.md"]
PUBLIC_CONFIG_DEFAULTS = {
    "GUI_OPERATOR_LLM_MODEL": "gui-plus",
    "GUI_OPERATOR_LLM_BASE": "https://www.dmxapi.cn/v1/chat/completions",
    "CHAT_MODEL": "gpt-4o",
    "CHAT_API_BASE": "https://www.dmxapi.cn/v1",
    "AGENT_NAME": "faust",
    "SECURITY_VERIFIER_API_ENDPOINT": "https://www.dmxapi.cn/v1",
    "SECURITY_VERIFIER_LLM_MODEL": "qwen3.5-flash",
    "SECURITY_SYS_ENABLED": False,
    "KB_ENABLED": True,
    "KB_EMBED_MODEL": "text-embedding-3-small",
    "KB_ASYNC_INDEX_ON_WRITE": True,
    "MC_OPERATOR_URL": "ws://127.0.0.1:18901",
    "MC_EVENT_TRIGGER_ENABLED": True,
    "LIVE2D_MODEL_PATH": "2D/hiyori_pro_zh/hiyori_pro_t11.model3.json",
    "LIVE2D_MODEL_SCALE": 0.3,
    "LIVE2D_MODEL_X": None,
    "LIVE2D_MODEL_Y": None,
    "TEXT_CHAT_BAR_Y_FACTOR": 0.53,
    "FRONTEND_QUICK_CONTROLLER_X_OFFSET": -12,
    "FRONTEND_CLICK_THROUGH": True,
    "FRONTEND_DEFAULT_TTS_LANG": "zh",
    "TTS_MODE": "local",
    "ASR_MODE": "local",
    "OPENAI_TTS_BASE_URL": "https://api.openai.com/v1",
    "OPENAI_TTS_MODEL": "gpt-4o-mini-tts",
    "OPENAI_TTS_VOICE": "alloy",
    "OPENAI_TTS_RESPONSE_FORMAT": "mp3",
    "OPENAI_TTS_SPEED": 1.0,
    "OPENAI_TTS_INSTRUCTIONS": "",
    "OPENAI_ASR_BASE_URL": "https://api.openai.com/v1",
    "OPENAI_ASR_MODEL": "gpt-4o-transcribe",
    "OPENAI_ASR_LANGUAGE": "",
    "OPENAI_ASR_PROMPT": "",
    "OPENAI_ASR_RESPONSE_FORMAT": "json",
    "OPENAI_ASR_TEMPERATURE": 0.0,
    "OPENAI_ASR_TIMESTAMP_GRANULARITIES": "",
    # TTS 参考音频配置
    "TTS_REFER_WAV_PATH": str(BACKEND_ROOT / "voices" / "neuro.wav"),
    "TTS_PROMPT_TEXT": "Hold on please, I'm busy. Okay, I think I heard him say he wants me to stream Hollow Knight on Tuesday and Thursday.",
    "TTS_PROMPT_LANGUAGE": "en",
}
PRIVATE_CONFIG_DEFAULTS = {
    "CHAT_API_KEY": "",
    "SEARCH_API_KEY": "",
    "GUI_OPERATOR_LLM_KEY": "",
    "SECURITY_VERIFIER_LLM_KEY": "",
    "KB_OPENAI_API_KEY": "",
    "OPENAI_TTS_API_KEY": "",
    "OPENAI_ASR_API_KEY": "",
}

_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _read_json(path: Path, default: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if not path.exists():
        return dict(default or {})
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        return dict(default or {})
    merged = dict(default or {})
    merged.update(data)
    return merged


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


def _sanitize_agent_name(name: str) -> str:
    candidate = (name or "").strip()
    if not candidate:
        raise ValueError("agent 名称不能为空")
    if not _AGENT_NAME_RE.match(candidate):
        raise ValueError("agent 名称只能包含字母、数字、下划线、横线和点")
    return candidate


def ensure_private_config_exists() -> None:
    if PRIVATE_CONFIG_PATH.exists():
        return
    if PRIVATE_EXAMPLE_PATH.exists():
        shutil.copy(PRIVATE_EXAMPLE_PATH, PRIVATE_CONFIG_PATH)
    else:
        _write_json(PRIVATE_CONFIG_PATH, PRIVATE_CONFIG_DEFAULTS)


def get_public_config() -> Dict[str, Any]:
    data = _read_json(PUBLIC_CONFIG_PATH, PUBLIC_CONFIG_DEFAULTS)
    for key in OBSOLETE_PUBLIC_CONFIG_KEYS:
        data.pop(key, None)
    return data


def get_private_config(mask_secrets: bool = True) -> Dict[str, Any]:
    ensure_private_config_exists()
    data = _read_json(PRIVATE_CONFIG_PATH, PRIVATE_CONFIG_DEFAULTS)
    legacy_chat = data.get("DEEPSEEK_API_KEY")
    if legacy_chat and not data.get("CHAT_API_KEY"):
        data["CHAT_API_KEY"] = legacy_chat
    if not mask_secrets:
        return data
    masked = {}
    for key, value in data.items():
        if value:
            masked[key] = "********"
        else:
            masked[key] = ""
    return masked


def get_config_view() -> Dict[str, Any]:
    public_config = get_public_config()
    private_config = get_private_config(mask_secrets=True)
    return {
        "public": public_config,
        "private": private_config,
        "meta": {
            "public_path": str(PUBLIC_CONFIG_PATH),
            "private_path": str(PRIVATE_CONFIG_PATH),
            "core_agent_files": list(AGENT_CORE_FILES),
        },
    }


def save_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    public_in = payload.get("public") or {}
    private_in = payload.get("private") or {}

    public_cfg = get_public_config()
    private_cfg = _read_json(PRIVATE_CONFIG_PATH, PRIVATE_CONFIG_DEFAULTS)

    # 兼容老键：若历史配置只有 DEEPSEEK_API_KEY，则迁移到 CHAT_API_KEY。
    if private_cfg.get("DEEPSEEK_API_KEY") and not private_cfg.get("CHAT_API_KEY"):
        private_cfg["CHAT_API_KEY"] = private_cfg.get("DEEPSEEK_API_KEY")
    private_cfg.pop("DEEPSEEK_API_KEY", None)

    for key, value in public_in.items():
        skey = str(key)
        public_cfg[skey] = value

    for key in OBSOLETE_PUBLIC_CONFIG_KEYS:
        public_cfg.pop(key, None)


    for key, value in private_in.items():
        skey = str(key)
        if value == "********":
            continue
        if skey == "DEEPSEEK_API_KEY":
            skey = "CHAT_API_KEY"
        private_cfg[skey] = value

    private_cfg.pop("DEEPSEEK_API_KEY", None)

    _write_json(PUBLIC_CONFIG_PATH, public_cfg)
    _write_json(PRIVATE_CONFIG_PATH, private_cfg)
    return get_config_view()


def list_available_models() -> List[Dict[str, str]]:
    frontend_2d = BACKEND_ROOT.parent / "frontend" / "2D"
    results: List[Dict[str, str]] = []
    if not frontend_2d.exists():
        return results
    for model_file in frontend_2d.rglob("*.model3.json"):
        rel = model_file.relative_to(frontend_2d.parent).as_posix()
        results.append({"label": model_file.parent.name, "path": rel})
    return sorted(results, key=lambda x: x["path"])


def _agent_dir(agent_name: str) -> Path:
    return AGENTS_ROOT / _sanitize_agent_name(agent_name)


def _ensure_agent_core_files(agent_dir: Path, template: Dict[str, str] | None = None) -> None:
    template = template or {}
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "diary").mkdir(parents=True, exist_ok=True)
    (agent_dir / "record").mkdir(parents=True, exist_ok=True)
    (agent_dir / "kb").mkdir(parents=True, exist_ok=True)
    (agent_dir / "kb_meta").mkdir(parents=True, exist_ok=True)
    (agent_dir / "kb_index").mkdir(parents=True, exist_ok=True)
    for filename in AGENT_CORE_FILES:
        path = agent_dir / filename
        if path.exists():
            continue
        default_text = template.get(filename, f"# {filename}\n")
        path.write_text(default_text, encoding="utf-8")
    triggers_path = agent_dir / "triggers.json"
    if not triggers_path.exists():
        triggers_path.write_text(json.dumps({"watchdog": []}, ensure_ascii=False, indent=4), encoding="utf-8")


def list_agents() -> List[Dict[str, Any]]:
    current_agent = get_public_config().get("AGENT_NAME", "faust")
    items: List[Dict[str, Any]] = []
    if not AGENTS_ROOT.exists():
        return items
    for child in sorted(AGENTS_ROOT.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        files_present = {name: (child / name).exists() for name in AGENT_CORE_FILES}
        items.append({
            "name": child.name,
            "path": str(child),
            "is_current": child.name == current_agent,
            "can_delete": child.name not in ("faust", current_agent),
            "core_files": files_present,
        })
    return items


def create_agent(agent_name: str, template_agent: str | None = None) -> Dict[str, Any]:
    agent_name = _sanitize_agent_name(agent_name)
    target_dir = _agent_dir(agent_name)
    if target_dir.exists():
        raise FileExistsError(f"agent 已存在: {agent_name}")

    template_content: Dict[str, str] = {}
    if template_agent:
        source_dir = _agent_dir(template_agent)
        if not source_dir.exists():
            raise FileNotFoundError(f"模板 agent 不存在: {template_agent}")
        for filename in AGENT_CORE_FILES:
            src_file = source_dir / filename
            if src_file.exists():
                template_content[filename] = src_file.read_text(encoding="utf-8")

    _ensure_agent_core_files(target_dir, template_content)
    return get_agent_detail(agent_name)


def delete_agent(agent_name: str) -> None:
    agent_name = _sanitize_agent_name(agent_name)
    current_agent = get_public_config().get("AGENT_NAME", "faust")
    if agent_name == "faust":
        raise PermissionError("默认禁止删除 faust")
    if agent_name == current_agent:
        raise PermissionError("默认禁止删除当前正在使用的 Agent")
    target_dir = _agent_dir(agent_name)
    if not target_dir.exists():
        raise FileNotFoundError(f"agent 不存在: {agent_name}")
    shutil.rmtree(target_dir)


def get_agent_files(agent_name: str) -> Dict[str, str]:
    agent_dir = _agent_dir(agent_name)
    if not agent_dir.exists():
        raise FileNotFoundError(f"agent 不存在: {agent_name}")
    _ensure_agent_core_files(agent_dir)
    result: Dict[str, str] = {}
    for filename in AGENT_CORE_FILES:
        result[filename] = (agent_dir / filename).read_text(encoding="utf-8")
    return result


def save_agent_files(agent_name: str, files: Dict[str, str]) -> Dict[str, str]:
    agent_dir = _agent_dir(agent_name)
    if not agent_dir.exists():
        raise FileNotFoundError(f"agent 不存在: {agent_name}")
    _ensure_agent_core_files(agent_dir)
    for filename in AGENT_CORE_FILES:
        if filename in files:
            (agent_dir / filename).write_text(str(files[filename]), encoding="utf-8")
    return get_agent_files(agent_name)


def get_agent_detail(agent_name: str) -> Dict[str, Any]:
    return {
        "agent": next((item for item in list_agents() if item["name"] == agent_name), None),
        "files": get_agent_files(agent_name),
    }


def runtime_summary() -> Dict[str, Any]:
    public_cfg = get_public_config()
    return {
        "current_agent": public_cfg.get("AGENT_NAME", "faust"),
        "public_config": public_cfg,
        "private_config": get_private_config(mask_secrets=True),
        "available_models": list_available_models(),
        "agents": list_agents(),
    }


def apply_live2d_to_frontend(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = payload or {}
    public_cfg = get_public_config()
    model_path = str(payload.get("LIVE2D_MODEL_PATH") or public_cfg.get("LIVE2D_MODEL_PATH") or "").strip()
    model_scale = payload.get("LIVE2D_MODEL_SCALE", public_cfg.get("LIVE2D_MODEL_SCALE"))
    model_x = payload.get("LIVE2D_MODEL_X", public_cfg.get("LIVE2D_MODEL_X"))
    model_y = payload.get("LIVE2D_MODEL_Y", public_cfg.get("LIVE2D_MODEL_Y"))
    text_chat_y_factor = payload.get("TEXT_CHAT_BAR_Y_FACTOR", public_cfg.get("TEXT_CHAT_BAR_Y_FACTOR"))
    quick_controller_x_offset = payload.get("FRONTEND_QUICK_CONTROLLER_X_OFFSET", public_cfg.get("FRONTEND_QUICK_CONTROLLER_X_OFFSET"))

    if model_path:
        backend2frontend.FrontEndLoadModel(model_path)
    if model_scale not in (None, ""):
        backend2frontend.FrontEndSetModelScale(model_scale)
    if model_x not in (None, "") and model_y not in (None, ""):
        backend2frontend.FrontEndSetModelPosition(model_x, model_y)
    if text_chat_y_factor not in (None, ""):
        backend2frontend.FrontEndSetTextChatYFactor(text_chat_y_factor)
    if quick_controller_x_offset not in (None, ""):
        backend2frontend.FrontEndSetQuickControllerXOffset(quick_controller_x_offset)

    return {
        "status": "ok",
        "applied": {
            "LIVE2D_MODEL_PATH": model_path,
            "LIVE2D_MODEL_SCALE": model_scale,
            "LIVE2D_MODEL_X": model_x,
            "LIVE2D_MODEL_Y": model_y,
            "TEXT_CHAT_BAR_Y_FACTOR": text_chat_y_factor,
            "FRONTEND_QUICK_CONTROLLER_X_OFFSET": quick_controller_x_offset,
        },
    }


async def switch_agent(agent_name: str) -> Dict[str, Any]:
    agent_name = _sanitize_agent_name(agent_name)
    if not _agent_dir(agent_name).exists():
        raise FileNotFoundError(f"agent 不存在: {agent_name}")
    public_cfg = get_public_config()
    public_cfg["AGENT_NAME"] = agent_name
    _write_json(PUBLIC_CONFIG_PATH, public_cfg)
    return {"agent_name": agent_name, "kb": {"status": "ok"}}


async def get_agent_diary(agent_name: str) -> List[Dict[str, Any]]:
    agent_dir = _agent_dir(agent_name)
    diary_dir = agent_dir / "diary"
    if not diary_dir.exists():
        return []
    entries = []
    for file in sorted(diary_dir.glob("*.json"), key=lambda p: p.name, reverse=True):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            entries.append({
                "timestamp": data.get("timestamp"),
                "content": data.get("content"),
                "path": str(file),
            })
        except Exception:
            continue
    return entries


async def get_agent_records(agent_name: str, date_limit: Optional[datetime] = None) -> List[Dict[str, Any]]:
    agent_dir = _agent_dir(agent_name)
    record_dir = agent_dir / "record"
    if not record_dir.exists():
        return []
    records = []
    for file in sorted(record_dir.glob("*.md"), key=lambda p: p.name, reverse=True):
        try:
            stem = file.stem
            date_value = None
            timestamp_value = None
            if len(stem) >= 15 and "_" in stem:
                prefix = stem.split("_", 2)
                if len(prefix) >= 2:
                    date_part = prefix[0]
                    time_part = prefix[1]
                    if len(date_part) == 8 and len(time_part) == 6:
                        timestamp_value = f"{date_part} {time_part}"
                        date_value = int(date_part)
            elif len(stem) == 8 and stem.isdigit():
                date_value = int(stem)
                timestamp_value = stem

            if date_limit and isinstance(date_value, int):
                limit_day = int(date_limit.strftime("%Y%m%d"))
                if date_value != limit_day:
                    continue

            with open(file, "r", encoding="utf-8") as f:
                content = f.read()
            records.append({
                "date": date_value,
                "timestamp": timestamp_value,
                "content": content,
                "path": str(file),
            })
        except Exception:
            continue
    return records