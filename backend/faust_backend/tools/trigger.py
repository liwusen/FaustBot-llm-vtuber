from langchain.tools import tool

from faust_backend.tools._registry import register
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
        添加一个新的触发器（同 id 会覆盖旧触发器）。
        通用字段: id, type, recall_description(可选), lifespan(可选,秒),
        run_background(可选,默认 false)。
        run_background=true 时触发器在后台运行，你被触发时输出的内容/工具调用不会被推送给用户，只有你知道；
        HEARTBEAT 触发器应该是 run_background=false 的
        false 时结果会通过聊天窗口流式展示给用户。
        类型专属字段: interval → interval_seconds; datetime → target("YYYY-MM-DD HH:MM:SS");
        py-eval → eval_code。
        例子:
        {
            "id": "my_trigger",
            "type": "interval",
            "interval_seconds": 60,
            "recall_description": "每分钟触发一次",
            "run_background": false
        }
    Args:
        trigger_json (str): 触发器的JSON字符串表示。
    Returns:
        str: 添加结果的确认信息，或者错误信息。
    """
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
    try:
        log.info("Removing trigger with ID: %s", trigger_id)
        trigger_manager.delete_trigger(trigger_id)
        return f"触发器移除成功，ID: {trigger_id}"
    except Exception as e:
        return f"移除触发器出错: {str(e)}"
