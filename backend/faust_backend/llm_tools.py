from langchain.tools import tool
import os

import faust_backend.config_loader as conf
os.environ["SEARCHAPI_API_KEY"]=conf.SEARCH_API_KEY
import faust_backend.backend2front as backend2frontend


import functools,inspect,os,sys
import socket
import io
from faust_backend.searchapi_patched import SearchApiAPIWrapper
from langchain_community.utilities import WikipediaAPIWrapper
import faust_backend.gui_llm_lib as gui_llm_lib
import winsound
toollist=[]
DIARY_DIR="data/faust_diary/"
HumanInTheLoopConfig={  
                "pythonExecTool": {  
                    "allowed_decisions": ["approve", "edit", "reject"]  
                },
                "sysExecTool": {  
                    "allowed_decisions": ["approve", "edit", "reject"]  
                },
                "listDirectoryTool": {  
                    "allowed_decisions": ["approve", "edit", "reject"]  
                },
                "readTextFileTool": {  
                    "allowed_decisions": ["approve", "edit", "reject"]  
                },
                "writeTextFileTool": {  
                    "allowed_decisions": ["approve", "edit", "reject"]  
                }
            } 

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
def listDiaryFilesTool() -> str:
    """
    Description:
        列出日记目录下的所有文件。
        你可以自行决定何时使用此工具。
    Args:
        None
    Returns:
        str: 日记目录下的文件列表，或者错误信息。
    """
    try:
        print("[Faust.backend.llm_tools.listDiaryFilesTool] Listing diary files in directory:", DIARY_DIR)
        files = os.listdir(DIARY_DIR)
        files=[f for f in files if f.endswith('.txt')]
        return "\n".join(files) if files else "日记目录为空。"
    except Exception as e:
        return f"列出日记文件出错: {str(e)}"
@add_to_tool_list
@tool
def readDiaryFileTool(filename: str) -> str:
    """
    Description:
        读取指定日记文件的内容。
        你可以自行决定何时使用此工具。
    Args:
        filename (str): 需要读取的日记文件名。
    Returns:
        str: 文件内容的字符串表示，或者错误信息。
    """
    file_path=os.path.join(DIARY_DIR,filename)
    try:
        print("[Faust.backend.llm_tools.readDiaryFileTool] Reading diary file:", file_path)
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    except Exception as e:
        return f"读取日记文件出错: {str(e)}"
@add_to_tool_list
@tool
def writeDiaryFileTool(content: str) -> str:
    """
    Description:
        将指定内容写入日记文件，使用UTF-8编码。
        文件名根据当前日期时间生成，格式为YYYYMMDD_HHMMSS.txt
        你可以自行决定何时使用此工具。
    Args:
        content (str): 需要写入文件的内容字符串。
    Returns:
        str: 写入成功的确认信息，或者错误信息。
    """    
    from datetime import datetime
    now = datetime.now()
    filename = now.strftime("%Y%m%d_%H%M%S") + ".txt"
    file_path=os.path.join(DIARY_DIR,filename)
    try:
        print("[Faust.backend.llm_tools.writeDiaryFileTool] Writing to diary file:", file_path)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"日记文件写入成功，文件名为: {filename}"
    except Exception as e:
        return f"写入日记文件出错: {str(e)}"

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
@add_to_tool_list
@tool
def musicPlayTool(url: str) -> str:
    """
    Description:
        播放指定URL的音乐。
        会同步口型。
        请注意 如果使用这个工具，则请在正文中一字不差的输出 <NO_TTS_OUTPUT>
    Args:
        url (str): 音乐的URL地址,支持file://和http(s)://等协议。
    Returns:
        str: 结果信息。
    """
    print("[Faust.backend.llm_tools.musicPlayTool] Playing music from URL:", url)
    backend2frontend.FrontEndPlayMusic(url)
    return "音乐播放命令已发送到前端。"
@add_to_tool_list
@tool
def bgPlayTool(url: str) -> str:
    """
    Description:
        播放指定URL的背景音乐。
        播放一次。
        不会同步口型。
    Args:
        url (str): 背景音乐的URL地址,支持file://和http(s)://等协议。
    Returns:
        str: 结果信息。
    """
    print("[Faust.backend.llm_tools.bgPlayTool] Playing background music from URL:", url)
    backend2frontend.FrontEndPlayBG(url)
    return "背景音乐播放命令已发送到前端。"
@add_to_tool_list
@tool
def guiOpTool(command: str) -> str:
    """
    Description:
        执行语言形式的GUI操作命令，并返回结果。
        这个工具只应该在用户需要时执行。
        这会调用一个专用LLM来处理GUI操作。
        你只需清晰描述你的需求即可。
        如 “关闭VSCode软件”
    Args:
        command (str): 需要执行的GUI操作命令字符串。
    Returns:
        str: GUI操作的结果字符串，或者错误信息。
    """
    try:
        print("[Faust.backend.llm_tools.guiOpTool] Executing GUI operation command:", command)
        result_str=gui_llm_lib.gui_op(command)
        return result_str
    except Exception as e:
        return f"执行GUI操作出错: {str(e)}"
# @add_to_tool_list
# @tool
# def getUserFullScreenOCRResultTool() -> str:
#     """
#     Description:
#         获取用户全屏截图的OCR识别结果。
#         这个工具只应该在用户需要时执行。
#     Returns:
#         str: OCR识别结果的字符串表示，或者错误信息。
#     """
#     try:
#         print("[Faust.backend.llm_tools.getUserFullScreenOCRResultTool] Getting full screen OCR result.")
#         # 这里调用实际的OCR处理逻辑
#         ocr_result = "模拟的OCR识别结果"
#         return ocr_result
#     except Exception as e:
#         return f"获取全屏OCR结果出错: {str(e)}"

if __name__ == "__main__":
    for tool in toollist:
        print(f"Tool name: {tool.name},\nDescription: {tool.description}")