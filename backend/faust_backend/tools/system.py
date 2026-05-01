import io
import os
import subprocess
import sys

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.logger import get_logger

log = get_logger("faust.tools.system")


@register
@tool
def pythonExecTool(code: str) -> str:
    """
    Description:
        执行传入的Python代码，并返回执行结果或错误信息。
    Args:
        code (str): 需要执行的Python代码字符串。
    Returns:
        str: 执行结果的字符串表示（包括变量名和对应值,以及stdout），或者错误信息。
    """
    try:
        local_namespace = {}
        sio = io.StringIO()
        log.info("Executing code: %s", code)
        sys.stdout = sio
        try:
            exec(code, {}, local_namespace)
        finally:
            sys.stdout = sys.__stdout__
        result = "\n".join(f"{key} = {value}" for key, value in local_namespace.items())
        stdout_result = sio.getvalue()
        return result + "\n" + stdout_result if result or stdout_result else "代码执行成功，但没有返回值。"
    except Exception as e:
        return f"代码执行出错: {str(e)}"


@register
@tool
def sysExecTool(command: str, timeout: int = 15) -> str:
    """
    Description:
        执行传入的系统命令，并返回命令的输出结果或错误信息。
        这个工具只应该在用户需要时执行。
    Args:
        command (str): 需要执行的系统命令字符串。
        timeout (int): 超时时间
    Returns:
        str: 命令的输出结果字符串，或者错误信息。
    """
    try:
        log.info("Executing command: %s", command)
        subp = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8')
        subp.wait(timeout)
        stdout, stderr = subp.communicate()
        stdout = stdout.decode(errors='ignore').strip()
        stderr = stderr.decode(errors='ignore').strip()
        return f"""执行完成。标准输出:\n{stdout}\n标准错误:\n{stderr}\n返回值{subp.returncode}"""
    except subprocess.TimeoutExpired:
        return "命令超时"
    except Exception as e:
        return f"命令执行出错: {str(e)}"


@register
@tool
def beepTool(frequency: int, duration: int) -> str:
    """
    Description:
        发出指定频率和持续时间的蜂鸣声。
    Args:
        frequency (int): 蜂鸣声的频率（Hz）。
        duration (int): 蜂鸣声的持续时间（毫秒）。
    Returns:
        str: 结果信息。
    """
    if os.name == 'nt':
        import winsound
        log.info("Emitting beep: frequency=%d duration=%d", frequency, duration)
        winsound.Beep(frequency, min(duration, 3000))
        return "蜂鸣声已发出。"
    else:
        return "蜂鸣声工具仅在Windows系统上可用。"
