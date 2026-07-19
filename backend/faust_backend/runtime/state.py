import os
import json
import asyncio

import faust_backend.config_loader as conf
import faust_backend.llm_tools as llm_tools
from faust_backend.logger import get_logger

from faust_backend.subagent_manager import SubagentManager

log = get_logger("faust.runtime.state")

# ── Agent 核心状态 ──
agent = None
agent_lock = asyncio.Lock()
THREAD_ID = 84

# ── 中断信号 ──
_agent_abort: asyncio.Event | None = None


def get_abort_event() -> asyncio.Event:
    global _agent_abort
    if _agent_abort is None:
        _agent_abort = asyncio.Event()
    return _agent_abort


def reset_abort_event() -> asyncio.Event:
    global _agent_abort
    _agent_abort = asyncio.Event()
    return _agent_abort

# ── SQLite 持久化 ──
conn = None
checkpointer = None

# ── 运行时就绪状态 ──
RUNTIME_READY = False
RUNTIME_STATUS = "starting"
RUNTIME_ERROR = ""
AGENT_NAME = conf.AGENT_NAME
AGENT_ROOT = os.path.join(conf.CONFIG_ROOT, "agents", f"{AGENT_NAME}")
PROMPT = ""

# ── 系统组件 ──
forward_queue = asyncio.Queue()
plugin_heartbeat_task = None
uvicorn_server = None
plugin_manager = None  # set by lifecycle
subagent_manager:SubagentManager|None = None


# ── 状态管理 ──

def set_runtime_state(*, ready: bool, status: str, error: str = ""):
    global RUNTIME_READY, RUNTIME_STATUS, RUNTIME_ERROR
    RUNTIME_READY = bool(ready)
    RUNTIME_STATUS = str(status or ("ready" if ready else "waiting_for_config"))
    RUNTIME_ERROR = str(error or "")


def runtime_not_ready_message() -> str:
    base = "后端已启动，但 Agent 尚未就绪。请先在配置器中填写私密配置或修正 Agent 配置，然后执行重载。"
    detail = str(RUNTIME_ERROR or "").strip()
    if detail:
        return f"{base} 当前原因: {detail}"
    return base


def runtime_status_payload() -> dict:
    return {
        "ready": RUNTIME_READY,
        "status": RUNTIME_STATUS,
        "error": RUNTIME_ERROR,
        "agent_name": AGENT_NAME,
        "agent_root": AGENT_ROOT,
        "private_config_missing": bool(conf.PRIVATE_CONFIG_WAS_MISSING),
        "private_config_auto_created": bool(conf.PRIVATE_CONFIG_AUTO_CREATED),
    }


def ensure_agent_runtime_ready() -> None:
    if agent is None or not RUNTIME_READY:
        raise RuntimeError(runtime_not_ready_message())


# ── Prompt 加载 ──

def makeup_init_prompt():
    global PROMPT, AGENT_NAME, AGENT_ROOT
    AGENT_NAME = conf.AGENT_NAME
    AGENT_ROOT = os.path.join(conf.CONFIG_ROOT, "agents", f"{AGENT_NAME}")
    if not os.path.exists(AGENT_ROOT):
        PROMPT = ""
        raise FileNotFoundError(f"Agent file for '{AGENT_NAME}' not found.")
    parts = []
    for fname in ("AGENT.md", "ROLE.md", "COREMEMORY.md"):
        fpath = os.path.join(AGENT_ROOT, fname)
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                parts.append(f.read())

    # ── Skill YAML injection ──
    try:
        import faust_backend.skill_manager as skill_manager
        skill_manager._ensure_builtin_skills()
        yaml_summary = skill_manager.list_skills_yaml()
        if yaml_summary:
            parts.append("\n\n## 可用技能列表（Skill）\n")
            parts.append("用户输入 `/skill:<slug>` 表示想用该技能。收到该指令后：\n")
            parts.append("1. 用 read(\"skill://<slug>/SKILL.md\") 读取技能完整说明\n")
            parts.append("2. 按说明执行任务\n")
            parts.append("3. 无需再次询问用户确认，直接执行\n\n")
            parts.append(yaml_summary)
    except Exception as e:
        log.warning("Skill YAML 注入失败: %s", e)

    parts.append("\n\n优先使用 read(\"faustbot://index.md\") 获取 FaustBot 的只读系统说明、工具说明、Minecraft 指南和源码入口。\n")

    PROMPT = "".join(parts)


try:
    makeup_init_prompt()
except Exception as e:
    log.warning("初始 Prompt 加载跳过: %s", e)
    set_runtime_state(ready=False, status="waiting_for_config", error=str(e))


# ── 工具函数 ──

def is_rate_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return ("429" in text) or ("rate" in text and "limit" in text) or ("bad_response_status_code" in text)


def format_chat_error(exc: Exception) -> str:
    if is_rate_limit_error(exc):
        return "上游模型网关触发限流(429)，请稍后重试。若正在发送图片，请降低 MAX_PIXELS 或减少并发请求。"
    return str(exc)


def message_content_to_text(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            if str(block.get("type") or "").strip().lower() == "text":
                text_val = block.get("text")
                if text_val is not None:
                    parts.append(str(text_val))
        return "".join(parts)
    return str(content)


def is_ai_message_chunk(message_chunk) -> bool:
    msg_type = str(message_chunk.type).strip().lower()
    if msg_type == "ai":
        return True
    cls_name = message_chunk.__class__.__name__.lower()
    return "aimessage" in cls_name


def tool_value_to_text(value) -> str | dict:
    if value is None:
        return ""
    if isinstance(value, dict):
        return value  # 让外层的 json.dumps 只序列化一次
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        return str(value)


def normalize_tool_args(payload) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return {}
        try:
            decoded = json.loads(text)
        except Exception:
            return {"input": text}
        return decoded if isinstance(decoded, dict) else {"input": decoded}
    if payload is None:
        return {}
    return {"input": payload}


def has_checkpoint_db(agent_root: str) -> bool:
    return os.path.exists(os.path.join(agent_root, "faust_checkpoint.db"))
