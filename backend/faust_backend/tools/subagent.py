from __future__ import annotations

import json

from langchain.tools import tool

from faust_backend.logger import get_logger
from faust_backend.runtime import state
from faust_backend.tools._registry import register
from faust_backend.tools.read import read

log = get_logger("faust.tools.subagent")


def _require_subagent_manager():
    manager = state.subagent_manager
    if manager is None:
        raise RuntimeError("subagent manager not ready")
    return manager


def _resolve_system_prompt(raw_prompt: str) -> str:
    text = str(raw_prompt or "").strip()
    if not text:
        return ""
    if not text.startswith("path:"):
        return text
    target = text[len("path:"):].strip()
    if not target:
        raise ValueError("sysPrompt path is empty")
    content = read.func(target)
    return str(content or "").strip()


@register
@tool
async def newSubagent(name: str, toolset_names: list[str], sysPrompt: str) -> str:
    """创建一个新的 Subagent。

    当你需要把一个较长、可并行、可独立观察的任务委托出去时，先创建 Subagent。

    Args:
        name: Subagent 名称。已存在同名 Subagent 时会报错。
        toolset_names: 工具组名称列表，例如 ["BASESET", "WRITESET"]。
        sysPrompt: 子代理提示词；若以 path: 开头，会读取对应路径内容作为提示词。
    Returns:
        str: 创建结果与状态摘要。
    """
    manager = _require_subagent_manager()
    resolved_prompt = _resolve_system_prompt(sysPrompt)
    status = await manager.newSubagent(
        agent_name=str(name or "").strip(),
        toolsetsNames=list(toolset_names or []),
        systemPrompt=resolved_prompt,
    )
    return json.dumps(status, ensure_ascii=False)


@register
@tool
async def invokeSubagent(name: str, message: str) -> str:
    """异步调度一个已存在的 Subagent 执行任务，不阻塞当前 Agent。

    Args:
        name: 目标 Subagent 名称。
        message: 发送给 Subagent 的用户消息文本。
    Returns:
        str: 已提交状态摘要。
    """
    manager = _require_subagent_manager()
    payload = {"messages": [{"role": "user", "content": str(message or "").strip()}]}
    status = await manager.invokeSubagent(str(name or "").strip(), payload)
    return json.dumps(status, ensure_ascii=False)


@register
@tool
async def wait_for_subagent(agent_name_list: list[str]) -> str:
    """等待一个或多个 Subagent 完成当前任务。

    这个工具用于在你需要Subagent的任务结果时显式等待，而不是猜测 Subagent 是否已经完成。

    Args:
        agent_name_list: 需要等待的 Subagent 名称列表；为空时等待所有当前 Subagent。
    Returns:
        str: 等待完成后的状态摘要。
    """
    manager = _require_subagent_manager()
    statuses = await manager.wait_for_subagents(list(agent_name_list or []))
    return json.dumps({"items": statuses}, ensure_ascii=False)


@register
@tool
async def stopSubagent(name: str) -> str:
    """停止一个正在运行的 Subagent。

    Args:
        name: 目标 Subagent 名称。
    Returns:
        str: 停止结果。
    """
    manager = _require_subagent_manager()
    stopped = await manager.abortSubagent(str(name or "").strip())
    return json.dumps({"name": name, "stopping": bool(stopped)}, ensure_ascii=False)


@register
@tool
async def removeSubagent(name: str) -> str:
    """删除一个 Subagent；若仍在运行，会先尝试停止。

    Args:
        name: 目标 Subagent 名称。
    Returns:
        str: 删除结果。
    """
    manager = _require_subagent_manager()
    removed = await manager.removeSubagent(str(name or "").strip())
    return json.dumps({"name": name, "removed": bool(removed)}, ensure_ascii=False)