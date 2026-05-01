from langchain.tools import tool

from faust_backend.tools._registry import register, STARTED
import faust_backend.trigger_manager as trigger_manager
from faust_backend.logger import get_logger

log = get_logger("faust.tools.trigger")


@register
@tool
def triggerListTool() -> str:
    """
    Description:
        列出当前所有已注册的触发器。
        触发器触发时，会唤醒你。
    Returns:
        str: 触发器列表的字符串表示，或者错误信息。
    """
    if not STARTED:
        return "系统尚未完全启动，无法列出触发器。"
    try:
        log.info("Listing all triggers.")
        return trigger_manager.get_trigger_information()
    except Exception as e:
        return f"列出触发器出错: {str(e)}"


@register
@tool
def triggerAddTool(trigger_json: str) -> str:
    """
    Description:
        添加一个新的触发器。
    Args:
        trigger_json (str): 触发器的JSON字符串表示。
    Returns:
        str: 添加结果的确认信息，或者错误信息。
    """
    if not STARTED:
        return "系统尚未完全启动，无法操作触发器。"
    try:
        log.info("Adding new trigger with JSON: %s", trigger_json)
        trigger_manager.append_trigger(trigger_json)
        return "触发器添加成功"
    except Exception as e:
        return f"添加触发器出错: {str(e)}"


@register
@tool
def triggerRemoveTool(trigger_id: str) -> str:
    """
    Description:
        移除指定ID的触发器。
    Args:
        trigger_id (str): 需要移除的触发器ID。
    Returns:
        str: 移除结果的确认信息，或者错误信息。
    """
    if not STARTED:
        return "系统尚未完全启动，无法操作触发器。"
    try:
        log.info("Removing trigger with ID: %s", trigger_id)
        trigger_manager.delete_trigger(trigger_id)
        return f"触发器移除成功，ID: {trigger_id}"
    except Exception as e:
        return f"移除触发器出错: {str(e)}"
