"""VNG（视觉小说）模式：模式通知 + 玩家候选台词生成。

设计要点（与 runtime 解耦）：

- **候选不走主 Agent**：独立一次结构化 LLM 调用，不写 checkpoint、不注册工具、
  不触发记忆同步，因此候选对主 Agent 完全不可见。
- **模式通知走对话历史**：进入/退出时向 checkpoint 末尾追加一条 HumanMessage，
  不改系统提示、不重建运行时，历史前缀逐字节不变（前缀缓存继续命中）。
- ``ensure_vng_notice`` 在主 Agent 每轮调用前做幂等校正，覆盖冷启动、/clear、
  压缩等把通知丢掉的情况。
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any, Sequence

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import faust_backend.core.config as conf
from faust_backend.core.log import get_logger
from faust_backend.core.ui_settings import get_vng_enabled

log = get_logger("faust.vng")

CHOICE_COUNT = 3
MIN_VALID_CHOICES = 2
MAX_CHOICE_CHARS = 48
CONTEXT_MESSAGES = 10
TRANSCRIPT_MAX_CHARS = 6000
PERSONA_MAX_CHARS = 3000
TIMEOUT_SECONDS = 30.0

MODE_SOURCE = "faust_vng_mode"
NOTICE_HEADER = "[系统提示｜模式切换，请勿回应本条]"
PROMPT_PATH = Path(conf.PACKAGE_ROOT) / "agent" / "vng_choice_prompt.md"

_ACTION_RE = re.compile(r"^\s*[（(].+[）)]\s*$")
_TRANSCRIPT_WRAPPERS = "\"“”「」『』'"

VNG_NOTICE_ON = (
    f"{NOTICE_HEADER} VNG（视觉小说）模式已开启。\n"
    "- 玩家会从系统生成的 3 个候选台词中选择一句发出，也可能自行输入。\n"
    "- 你的每次回复就是视觉小说里角色的一句台词：短、口语、一次只推进一小步。\n"
    "- [MUST] 不要提及候选、选项、系统、模式等元信息，也不要评价玩家如何选择台词。\n"
    "- 玩家可能发来括号动作或沉默（如「（沉默）」）；把它理解为玩家的动作并自然回应，"
    "你自己不要写括号动作或旁白。\n"
    "- 保持角色扮演，不要跳出角色解释规则。"
)
VNG_NOTICE_OFF = f"{NOTICE_HEADER} VNG 模式已结束，恢复正常对话方式。"


# ── 模式通知 ──


def _thread_config() -> dict:
    from faust_backend.agent.runtime import state

    return {"configurable": {"thread_id": state.THREAD_ID}}


def is_vng_notice(message: Any) -> bool:
    kwargs = getattr(message, "additional_kwargs", None) or {}
    return str(kwargs.get("lc_source") or "") == MODE_SOURCE


def latest_vng_notice_message(messages: Sequence[Any]) -> Any | None:
    for message in reversed(list(messages)):
        if is_vng_notice(message):
            return message
    return None


def latest_vng_notice(messages: Sequence[Any]) -> str | None:
    message = latest_vng_notice_message(messages)
    if message is None:
        return None
    kwargs = getattr(message, "additional_kwargs", None) or {}
    return str(kwargs.get("vng_mode") or "on")


async def append_vng_notice(agent: Any, enabled: bool) -> bool:
    """向会话末尾追加一条模式通知（不修改任何已有消息）。"""
    if agent is None:
        return False
    message = HumanMessage(
        content=VNG_NOTICE_ON if enabled else VNG_NOTICE_OFF,
        additional_kwargs={
            "lc_source": MODE_SOURCE,
            "vng_mode": "on" if enabled else "off",
        },
    )
    await agent.aupdate_state(
        _thread_config(), {"messages": [message]}, as_node="model"
    )
    log.info("VNG 模式通知已写入会话: %s", "on" if enabled else "off")
    return True


async def ensure_vng_notice(agent: Any) -> bool:
    """幂等校正：会话里最新的 VNG 通知必须与当前模式一致。

    返回 True 表示本轮补写了通知。任何异常都只记录，不影响本轮对话。
    """
    if agent is None:
        return False
    from faust_backend.agent.runtime.turn.session_stats import get_checkpoint_messages

    try:
        messages = await get_checkpoint_messages()
    except Exception as exc:  # noqa: BLE001 - 通知校验失败不应打断对话
        log.warning("读取会话 checkpoint 失败，跳过 VNG 通知校验: %s", exc)
        return False

    enabled = get_vng_enabled()
    latest = latest_vng_notice(messages)
    if enabled and latest != "on":
        try:
            return await append_vng_notice(agent, True)
        except Exception as exc:  # noqa: BLE001
            log.warning("补写 VNG 开启通知失败: %s", exc)
    elif not enabled and latest == "on":
        try:
            return await append_vng_notice(agent, False)
        except Exception as exc:  # noqa: BLE001
            log.warning("补写 VNG 关闭通知失败: %s", exc)
    return False


# ── 上下文与提示词 ──


def _read_role() -> str:
    from faust_backend.agent.runtime import state

    root = str(getattr(state, "AGENT_ROOT", "") or "")
    if not root:
        return ""
    try:
        text = (Path(root) / "ROLE.md").read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001 - 角色文件缺失时仍可生成候选
        return ""
    return text.strip()[:PERSONA_MAX_CHARS]


def _read_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8").strip()
    except Exception as exc:  # noqa: BLE001 - 提示词缺失必须显式报错
        raise RuntimeError(f"VNG 候选提示词文件缺失或不可读: {PROMPT_PATH}: {exc}") from exc


def render_transcript(messages: Sequence[Any]) -> tuple[str, str | None]:
    """把 checkpoint 消息渲染成候选生成用的「最近对话」。

    Returns:
        (transcript, stale_reason)。stale_reason 非空表示当前不适合生成候选
        （没有消息 / 最后一条不是有文本的 AI 回复）。
    """
    from faust_backend.core.message import message_content_to_text

    if not messages:
        return "", "no_messages"

    last = messages[-1]
    if not isinstance(last, AIMessage):
        return "", "last_message_not_assistant"
    if not message_content_to_text(getattr(last, "content", "")).strip():
        return "", "last_message_not_assistant"

    from faust_backend.agent.runtime import state

    agent_name = str(getattr(state, "AGENT_NAME", "") or "Faust")
    lines: list[str] = []
    for message in messages:
        if is_vng_notice(message):
            continue
        if isinstance(message, HumanMessage):
            speaker = "玩家"
        elif isinstance(message, AIMessage):
            speaker = agent_name
        else:
            continue
        text = message_content_to_text(getattr(message, "content", "")).strip()
        if not text:
            continue
        lines.append(f"{speaker}: {text}")

    if not lines:
        return "", "no_messages"

    recent = lines[-CONTEXT_MESSAGES:]
    kept: list[str] = []
    total = 0
    for line in reversed(recent):
        if kept and total + len(line) + 1 > TRANSCRIPT_MAX_CHARS:
            break
        kept.append(line)
        total += len(line) + 1
    kept.reverse()
    return "\n".join(kept), None


def build_choice_messages(role_text: str, transcript: str) -> list[Any]:
    human = (
        "【角色设定】\n"
        f"{role_text.strip() or '（无）'}\n\n"
        "【最近对话】\n"
        f"{transcript.strip()}\n\n"
        "【任务】生成 3 个玩家接下来可能说出的台词。严格按系统要求输出 JSON。"
    )
    return [SystemMessage(content=_read_system_prompt()), HumanMessage(content=human)]


def normalize_choices(raw: Any) -> list[dict[str, Any]]:
    """校验并归一化模型输出：最多 3 条、去重、限长、识别括号动作。"""
    if not isinstance(raw, dict):
        return []
    options = raw.get("options")
    if not isinstance(options, list):
        return []

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for option in options:
        if not isinstance(option, str):
            continue
        text = re.sub(r"\s+", " ", option).strip()
        text = text.strip(_TRANSCRIPT_WRAPPERS)
        if not text:
            continue
        if len(text) > MAX_CHOICE_CHARS:
            text = text[: MAX_CHOICE_CHARS - 1].rstrip() + "…"
        if text in seen:
            continue
        seen.add(text)
        items.append({"text": text, "is_action": bool(_ACTION_RE.match(text))})
        if len(items) >= CHOICE_COUNT:
            break
    return items if len(items) >= MIN_VALID_CHOICES else []


async def generate_choices() -> dict[str, Any]:
    """生成候选。返回值直接作为 HTTP 响应体（status 见 api/vng.py）。"""
    from faust_backend.agent.runtime import state
    from faust_backend.agent.runtime.turn.session_stats import get_checkpoint_messages
    from faust_backend.llm.formats import ainvoke_json, close_chat_model
    from faust_backend.llm.provider import build_main_chat_model, get_model_api_format

    try:
        messages = await get_checkpoint_messages()
    except Exception as exc:  # noqa: BLE001
        log.error("VNG 读取会话失败: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}

    transcript, stale_reason = render_transcript(messages)
    if stale_reason:
        return {"status": "stale", "reason": stale_reason}

    try:
        providers = state.get_model_providers()
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "error": str(exc)}
    if providers is None or not str(getattr(providers, "main_model", "") or ""):
        return {"status": "error", "error": "main_model 未配置"}

    try:
        role_text = _read_role()
        payload = build_choice_messages(role_text, transcript)
    except Exception as exc:  # noqa: BLE001
        log.error("VNG 候选提示词构建失败: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}

    model = None
    started = time.monotonic()
    try:
        model = await build_main_chat_model(providers, intensity=None)
        api_format = get_model_api_format(providers, str(providers.main_model))
        data = await asyncio.wait_for(
            ainvoke_json(model, payload, api_format, temperature=0.9),
            timeout=TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        log.error("VNG 候选生成超时（%.0fs）", TIMEOUT_SECONDS)
        return {"status": "error", "error": "候选生成超时"}
    except Exception as exc:  # noqa: BLE001
        log.error("VNG 候选生成失败: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}
    finally:
        if model is not None:
            try:
                await close_chat_model(model)
            except Exception as exc:  # noqa: BLE001 - 关闭失败不影响结果
                log.debug("关闭 VNG 候选模型失败: %s", exc)

    items = normalize_choices(data)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    if not items:
        log.warning("VNG 候选输出不合法: %s", str(data)[:500])
        return {"status": "empty", "reason": "model_output_invalid"}

    log.info(
        "VNG 候选生成完成: items=%d model=%s elapsed=%dms",
        len(items),
        providers.main_model,
        elapsed_ms,
    )
    return {
        "status": "ok",
        "items": items,
        "model": str(providers.main_model),
        "elapsed_ms": elapsed_ms,
    }
