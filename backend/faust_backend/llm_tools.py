from langchain.tools import tool
import os
os.environ["DEEPSEEK_API_KEY"]="sk-3b6954e22333401d9cbb033a0ae9e8bb"
os.environ["SEARCHAPI_API_KEY"]="zNj5f2XcQWnbzuHnk2Vi31hR"
import functools,inspect,os,sys
import socket
import io
from faust_backend.searchapi_patched import SearchApiAPIWrapper
from langchain_community.utilities import WikipediaAPIWrapper
import winsound
toollist=[]
#define add to TOOLLIST wrapper
def __init__():
    print("[Faust.backend.llm_tools] Initializing llm_tools module...")
def add_to_tool_list(func):
    toollist.append(func)
    return func
@add_to_tool_list
@tool
def getDateTimeTool()->str:
    """
    Description:
        获取当前的日期和时间，格式为YYYY-MM-DD HH:MM:SS
    Args:
        None
    Returns:
        str: 当前的日期和时间字符串
    """
    from datetime import datetime
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")
@add_to_tool_list
@tool
def userHostNameTool()->str:
    """
    Description:
        获取当前用户的电脑相关信息,包括用户名等
    Args:
        None
    Returns:
        str(json): 包含电脑相关信息的字典
    """

    hostname = socket.gethostname()
    with os.popen('whoami') as f:
        username = f.read().strip()
    ip=socket.gethostbyname(hostname)
    ip=ip.strip()
    os_type=os.name
    return str({"hostname": hostname,"username":username,"ip":ip,"os_type":os_type})
@add_to_tool_list
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
        # 定义一个局部命名空间来执行代码
        # 捕获它的stdout
        # 以便在返回时包含输出结果
        local_namespace = {}
        sio = io.StringIO()
        print("[Faust.backend.llm_tools.pythonExecTool] Executing code:", code)
        sys.stdout = sio
        exec(code, {}, local_namespace)
        sys.stdout = sys.__stdout__
        # 获取所有局部变量的字符串表示
        result = "\n".join(f"{key} = {value}" for key, value in local_namespace.items())
        # 获取stdout的内容
        stdout_result = sio.getvalue()
        return result + "\n" + stdout_result if result or stdout_result else "代码执行成功，但没有返回值。"
    except Exception as e:
        return f"代码执行出错: {str(e)}"
@add_to_tool_list
@tool
def sysExecTool(command: str) -> str:
    """
    Description:
        执行传入的系统命令，并返回命令的输出结果或错误信息。
        这个工具只应该在用户需要时执行。
    Args:
        command (str): 需要执行的系统命令字符串。
    Returns:
        str: 命令的输出结果字符串，或者错误信息。
    """
    try:
        print("[Faust.backend.llm_tools.sysExecTool] Executing command:", command)
        with os.popen(command) as f:
            output = f.read()
        return output if output else "命令执行成功，但没有输出。"
    except Exception as e:
        return f"命令执行出错: {str(e)}"
@add_to_tool_list
@tool
def listDirectoryTool(path: str) -> str:
    """
    Description:
        列出指定目录下的所有文件和子目录。
        这个工具只应该在用户需要时执行。
        如果用户未说明，请勿擅自使用此工具。
    Args:
        path (str): 需要列出的目录路径。
    Returns:
        str: 目录下的文件和子目录列表，或者错误信息。
    """
    #dir commands/ls command
    try:
        print("[Faust.backend.llm_tools.listDirectoryTool] Listing directory:", path)
        if os.name == 'nt':  # Windows
            with os.popen(f'dir "{path}"') as f:
                output = f.read()
        else:  # Unix/Linux/Mac
            with os.popen(f'ls "{path}"') as f:
                output = f.read()
        return output if output else "目录为空。"
    except Exception as e:
        return f"列出目录出错: {str(e)}"
@add_to_tool_list
@tool
def readTextFileTool(file_path: str) -> str:
    """
    Description:
        读取指定文本文件的内容。
        使用UTF-8编码读取文件。
        这个工具只应该在用户需要时执行。
        如果用户未说明，请勿擅自使用此工具。
    Args:
        file_path (str): 需要读取的文本文件路径。
    Returns:
        str: 文件内容的字符串表示，或者错误信息。
    """
    try:
        print("[Faust.backend.llm_tools.readTextFileTool] Reading file:", file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        return f"读取文件出错: {str(e)}"
@add_to_tool_list
@tool
def writeTextFileTool(file_path: str, content: str) -> str:
    """
    Description:
        将指定内容写入文本文件，使用UTF-8编码。
        这个工具只应该在用户需要时执行。
        如果用户未说明，请勿擅自使用此工具。
    Args:
        file_path (str): 需要写入的文本文件路径。
        content (str): 需要写入文件的内容字符串。
    Returns:
        str: 写入成功的确认信息，或者错误信息。
    """
    try:
        print("[Faust.backend.llm_tools.writeTextFileTool] Writing to file:", file_path)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return "文件写入成功。"
    except Exception as e:
        return f"写入文件出错: {str(e)}"

swrapper=SearchApiAPIWrapper()
@add_to_tool_list
@tool
def webSearchTool(query: str) -> str:
    """
    Description:
        使用SearchApi进行网络搜索，并返回搜索结果的摘要。
    Args:
        query (str): 需要搜索的查询字符串。
    Returns:
        str: 搜索结果的摘要。
    """
    print("[Faust.backend.llm_tools.webSearchTool] Searching web for query:", query)
    return swrapper.run(query=query)
wwrapper=WikipediaAPIWrapper()
@add_to_tool_list
@tool
def wikiSearchTool(query: str) -> str:
    """
    Description:
        使用Wikipedia进行搜索，并返回搜索结果的摘要。
    Args:
        query (str): 需要搜索的查询字符串。
    Returns:
        str: 搜索结果的摘要。
    """
    print("[Faust.backend.llm_tools.wikiSearchTool] Searching Wikipedia for query:", query)
    return wwrapper.run(query=query)
@add_to_tool_list
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
        print("[Faust.backend.llm_tools.beepTool] Emitting beep sound with frequency:", frequency, "duration:", duration)
        winsound.Beep(frequency, min(duration,3000))
        return "蜂鸣声已发出。"
    else:
        return "蜂鸣声工具仅在Windows系统上可用。"
if __name__ == "__main__":
    for tool in toollist:
        print(f"Tool name: {tool.name},\nDescription: {tool.description}")