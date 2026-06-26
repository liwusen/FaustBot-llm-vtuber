"""
Deprecated execution tools — replaced by the unified execute tool.

  sysExecTool    → execute("shell", command)
  pythonExecTool → execute("python", code)
  beepTool       → no equivalent (kept as-is)

These wrappers exist for backward compatibility only.
"""

import os

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.logger import get_logger

log = get_logger("faust.tools.system")


@register
@tool
def pythonExecTool(code: str) -> str:
    """[已弃用] 执行 Python 代码 → 请使用 execute("python", code)。
    直接转发给 execute 工具。
    """
    from faust_backend.tools.execute import execute
    return execute.invoke({"language": "python", "code": code})


@register
@tool
def sysExecTool(command: str, timeout: int = 15) -> str:
    """[已弃用] 执行系统命令 → 请使用 execute("shell", command)。
    直接转发给 execute 工具。
    """
    from faust_backend.tools.execute import execute
    return execute.invoke({"language": "shell", "code": command, "timeout": timeout})


@register
@tool
def beepTool(frequency: int, duration: int) -> str:
    """发出指定频率和持续时间的蜂鸣声（仅 Windows）。"""
    if os.name == 'nt':
        import winsound
        log.info("Emitting beep: frequency=%d duration=%d", frequency, duration)
        winsound.Beep(frequency, min(duration, 3000))
        return "蜂鸣声已发出。"
    else:
        return "蜂鸣声工具仅在Windows系统上可用。"
