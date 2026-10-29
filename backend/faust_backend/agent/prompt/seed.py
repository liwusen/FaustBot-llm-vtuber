"""角色 Prompt 的 checkpoint 植入（只写状态，不发起模型调用）。

自 ``runtime/lifecycle.py`` 整块挖出（P4 布局拆分）。
"""

from __future__ import annotations

import faust_backend.agent.runtime.state as state
from faust_backend.core.log import get_logger

log = get_logger("faust.lifecycle")


def _agent_thread_config() -> dict:
    """主 Agent 的 checkpoint 配置（与 `_invoke_agent_unlocked` 的默认值一致）。"""
    return {
        "configurable": {"thread_id": state.THREAD_ID},
        "recursion_limit": 300,
    }


async def _checkpoint_messages(target_agent) -> list:
    """读取当前 thread 的 checkpoint 消息（as_node 等细节不参与判断）。"""
    snapshot = await target_agent.aget_state(_agent_thread_config())
    values = getattr(snapshot, "values", None) or {}
    return list(values.get("messages") or [])


async def _seed_system_prompt(target_agent) -> bool:
    """把角色 Prompt 写进 checkpoint 的 System 消息；**不发起任何模型调用**。

    为什么要单独做这一步：主 Agent 的系统提示词只存在于 state 的 SystemMessage
    里（``create_agent`` 没有接收 ``system_prompt``），而 ``reset_dialog`` 的语义
    就是「清空对话后重新植入角色设定」。

    历史实现用「只发一条 system 消息」的模型调用来完成这件事，这对 OpenAI
    兼容格式恰好能被容忍，但对其它格式是非法请求：

    - Gemini：system 被收进 ``system_instruction`` 后 ``contents=[]`` →
      ``ValueError: contents are required.``
    - Anthropic：``messages=[]``（system 走顶层参数）→ 422
      ``messages: at least one message is required``

    改成纯状态写入后与 provider 格式无关，也不会为一次「植入」白烧一次模型调用，
    更不会在全新会话里留下一条模型寒暄。

    写入方式与 ``CompactMiddleware._build_replacement`` 一致：先
    ``REMOVE_ALL_MESSAGES`` 再按目标顺序重排，因此 System 恒在最前面，同时保留
    非 System 的历史消息（``/faust/admin/runtime/reload-all`` 这类不删 thread 的
    入口不会丢上下文、也不会累积重复 System）。

    Returns:
        True 表示写入了新的 System 消息，False 表示已经是目标状态（跳过）。
    """
    from langchain_core.messages import RemoveMessage, SystemMessage
    from langgraph.graph.message import REMOVE_ALL_MESSAGES

    prompt = str(state.PROMPT or "")
    if not prompt.strip():
        log.warning("角色 Prompt 为空，跳过 System 消息植入（AGENT.md 可能未加载）")
        return False

    config = _agent_thread_config()
    existing = await _checkpoint_messages(target_agent)

    system_messages = [m for m in existing if isinstance(m, SystemMessage)]
    kept = [m for m in existing if not isinstance(m, SystemMessage)]
    if (
        len(system_messages) == 1
        and existing
        and existing[0] is system_messages[0]
        and system_messages[0].content == prompt
    ):
        log.debug("角色 Prompt 已是最新，跳过重复植入")
        return False

    await target_agent.aupdate_state(
        config,
        {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                SystemMessage(content=prompt),
                *kept,
            ]
        },
        # 已有消息时 LangGraph 无法推断该更新属于哪个节点，会抛
        # "Ambiguous update, specify as_node"。用模型节点（与 /compact 的写回一致），
        # 语义也正确：这批消息是"模型将要看到的历史"。
        as_node="model",
    )
    log.info(
        "已植入角色 Prompt（%d 字符，保留 %d 条历史消息），未发起模型调用",
        len(prompt),
        len(kept),
    )
    return True
