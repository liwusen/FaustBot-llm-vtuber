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
    """[已弃用] 发出蜂鸣声（仅 Windows）。无核心工具替代。"""
    if os.name == 'nt':
        import winsound
        import faust_backend.logger as _log
        _log.getLogger("faust.tools.system").info("beepTool deprecated — emitting beep")
        winsound.Beep(frequency, min(duration, 3000))
        return "蜂鸣声已发出。"
    else:
        return "蜂鸣声工具仅在Windows系统上可用。"
