from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.agent.trigger as trigger_manager
from faust_backend.core.log import get_logger

log = get_logger("faust.tools.trigger")


@register
@tool
async def triggerListTool() -> str:
    """
    Description:
        列出当前所有已注册的触发器。
        触发器触发时，会唤醒你。
    Returns:
        str: 触发器列表的字符串表示，或者错误信息。
    """
    try:
        log.info("Listing all triggers.")
        return trigger_manager.get_trigger_information()
    except Exception as e:
        return f"列出触发器出错: {str(e)}"


@register
@tool
async def triggerAddTool(trigger_json: str) -> str:
    r"""添加/覆盖一个触发器（同 id 覆盖旧的）。

    通用字段：id, type, recall_description(可选), lifespan(可选,秒),
    run_background(可选,默认 false), priority(可选,"normal")。
    类型专属：interval → interval_seconds；datetime → target("YYYY-MM-DD HH:MM:SS")；
    py-eval → eval_code。

    priority 三档，注意没有任何一档能抢占进行中的 turn，都只在本轮结束后送达：
    - "interrupt" — 与 "normal" 行为相同，仅标记高优先级（告警/紧急事件）
    - "normal" — 常规（默认）
    - "batched" — 低频/低价值感知事件，按 30 秒窗口合并为一次唤醒（不打扰但不丢）

    run_background=true 时在后台跑：你被唤醒后的输出与工具调用不推送给用户，只有你知道；
    false（默认）时结果在聊天窗口流式展示。HEARTBEAT 应为 false。

    触发器由前端连接驱动：用户没开界面时 event 型直接拒绝（返回"前端未连接"），
    定时型也不会触发。这是设计，不要反复重试。

    示例：{"id":"my_trigger","type":"interval","interval_seconds":60,
           "recall_description":"每分钟一次","priority":"normal"}

    Args:
        trigger_json (str): 触发器 JSON 字符串。
    """
    try:
        log.info("Adding new trigger with JSON: %s", trigger_json)
        await trigger_manager.append_trigger(trigger_json)
        return "触发器添加成功"
    except Exception as e:
        return f"添加触发器出错: {str(e)}"


@register
@tool
async def triggerRemoveTool(trigger_id: str) -> str:
    """
    Description:
        移除指定ID的触发器。
    Args:
        trigger_id (str): 需要移除的触发器ID。
    Returns:
        str: 移除结果的确认信息，或者错误信息。
    """
    try:
        log.info("Removing trigger with ID: %s", trigger_id)
        trigger_manager.delete_trigger(trigger_id)
        return f"触发器移除成功，ID: {trigger_id}"
    except Exception as e:
        return f"移除触发器出错: {str(e)}"
