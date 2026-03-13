from langchain.tools import tool
import os

import faust_backend.config_loader as conf
os.environ["SEARCHAPI_API_KEY"]=conf.SEARCH_API_KEY
import faust_backend.backend2front as backend2frontend
from faust_backend.utils import *

import functools,inspect,os,sys
import socket
import io
from faust_backend.searchapi_patched import SearchApiAPIWrapper
from langchain_community.utilities import WikipediaAPIWrapper
import faust_backend.gui_llm_lib as gui_llm_lib
import faust_backend.trigger_manager as trigger_manager
import faust_backend.nimble as nimble
import winsound
import asyncio
import faust_backend.events as events
import json
toollist=[]
DIARY_DIR="data/faust_diary/"
STARTED=False
#define add to TOOLLIST wrapper
def __init__():
    print("[Faust.backend.llm_tools] Initializing llm_tools module...")
def add_to_tool_list(func):
    toollist.append(func)
    return func
async def HILRequest(id,title,summary):
    if not STARTED:
        return False,"cannot call HILRequest before the system is fully started."
    backend2frontend.FrontendHIL({"ID": id,"request": title,"summary": summary})
    events.HIL_feedback_event.clear()
    events.HIL_feedback_fail_event.clear()
    ok_callback=asyncio.create_task(events.HIL_feedback_event.wait())
    fail_callback=asyncio.create_task(events.HIL_feedback_fail_event.wait())
    timeout_callback=asyncio.create_task(asyncio.sleep(30)) # 30 seconds timeout
    done,_=await asyncio.wait([ok_callback,fail_callback,timeout_callback],return_when=asyncio.FIRST_COMPLETED)
    if ok_callback in done:
        events.HIL_feedback_event.clear()
        events.HIL_feedback_fail_event.clear()
        return True,"approved"
    elif fail_callback in done:
        events.HIL_feedback_fail_event.clear()
        events.HIL_feedback_event.clear()
        return False,"rejected"
    elif timeout_callback in done:
        return False,"timeout"
    else:
        return False,"unknown"
    
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
        你只需清晰简单描述你的需求即可。
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
# @add_to_tool_list
# @tool
# async def test_HIL_tool():
#     """
#     Description:
#         这是一个测试人类反馈工具的工具。
#         它会向前端发送一个人类反馈请求，并等待用户的批准或拒绝。
#     Args:
#         None
#     Returns:
#         str: 用户的反馈结果，可能是 "approved", "rejected", "timeout" 或 "unknown"。
#     """
#     print("[Faust.backend.llm_tools.test_HIL_tool] Sending human-in-the-loop feedback request.")
#     result=await HILRequest(id="test_request",title="这是一个测试请求",summary="请批准或拒绝这个测试请求。")
#     print("[Faust.backend.llm_tools.test_HIL_tool] Received feedback result:", result)
#     return f"用户反馈结果: {str(result)}"

@add_to_tool_list
@tool
def showNimbleWindowTool(html: str, title: str = "灵动交互", recall_text: str = "用户仍在处理这个灵动窗口，请查看用户是否已完成操作。", reminder_interval_seconds: int = 20, lifespan: int = 1800, metadata_json: str = "{}") -> str:
    """
    Description:
        非阻塞地创建一个“灵动交互”窗口，并显示在前端虚拟形象旁边。

        这是处理复杂任务确认、表单填写、选项确认、安装参数收集等场景的核心工具。
        调用后不会阻塞当前对话，也不会等待用户立即完成操作。
        相反，它会：
        1. 在前端显示一个独立的 HTML 窗口；
        2. 自动绑定一个 reminder trigger，周期性提醒你关注该窗口；
        3. 自动绑定一个 result trigger，当用户提交时再次唤醒你；
        4. 自动绑定一个 expire trigger，窗口生命周期结束时自动关闭；
        5. 当窗口被用户关闭或提交后，其关联 trigger 会一并删除。

        你应当在如下情况使用它：
        - 需要用户选择多个选项；
        - 需要用户填写文本/路径/参数；
        - 需要用户确认安装、危险操作、批量操作细节；
        - 纯语音交互效率低、歧义大、确认轮次过多时。
        - 其他需要更丰富的交互方式的任何场景

        前端窗口中的 HTML 可以包含自定义 UI 元素，例如：按钮、复选框、输入框、选择器等。
        你写入的 HTML 中可以直接调用前端注入的 JavaScript API：

        - `window.nimble.submit(data)`
            向后端提交当前窗口结果，并唤醒你继续处理。
        - `window.nimble.close(reason)`
            关闭当前窗口，并清理绑定 trigger。

        这两个 API 会自动关联当前窗口的 callback_id，因此你不需要手动拼接 callback_id。

        HTML 编写建议：
        - 尽量使用内联样式，避免依赖外部资源；
        - 明确写出提示语、确认按钮、取消按钮；
        - 在按钮中调用 `window.nimble.submit({...})` 提交结构化 JSON 结果；
        - 若用户取消，调用 `window.nimble.close('cancelled')`。

        一个常见示例：
        ```html
        <div style="padding:12px; color:#fff;">
          <h3>安装确认</h3>
          <label>安装路径 <input id="installPath" value="D:/Apps/Test" /></label>
          <label><input id="desktopShortcut" type="checkbox" checked /> 创建桌面快捷方式</label>
          <div style="margin-top:12px; display:flex; gap:8px;">
            <button onclick="window.nimble.submit({ action: 'confirm', installPath: document.getElementById('installPath').value, desktopShortcut: document.getElementById('desktopShortcut').checked })">确认</button>
            <button onclick="window.nimble.close('cancelled')">取消</button>
          </div>
        </div>
        ```

        注意：
        - 这是非阻塞工具。调用后你不应假设用户已经给出答案；
        - 真正的结果会通过 trigger 在后续再次唤醒你；
        - 你的后续逻辑应等待由 result/reminder/expire 触发的新上下文，而不是在当前轮强行继续索要结果。

    Args:
        html (str): 要展示在前端窗口中的 HTML 内容。
        title (str): 窗口标题。
        recall_text (str): reminder trigger 唤醒你时附带的提示信息。
        reminder_interval_seconds (int): 窗口打开期间，提醒你关注该窗口的周期秒数。
        lifespan (int): 窗口生命周期（秒）。到期后窗口及关联 trigger 自动删除。
        metadata_json (str): 额外元数据 JSON 字符串。
    Returns:
        str: 创建结果说明，包含 callback_id。
    """
    if not STARTED:
        return "系统尚未完全启动，无法创建灵动交互窗口。"
    try:
        metadata = json.loads(metadata_json) if metadata_json else {}
        callback_id = nimble.build_callback_id()
        session = nimble.create_nimble_session(
            callback_id,
            title=title,
            html=html,
            recall_text=recall_text,
            reminder_interval_seconds=reminder_interval_seconds,
            lifespan=lifespan,
            metadata=metadata,
        )

        trigger_manager.append_trigger({
            "id": session["result_trigger_id"],
            "type": "event",
            "event_name": "nimble_result",
            "callback_id": callback_id,
            "recall_description": f"灵动窗口 {callback_id} 收到了用户提交结果。",
            "lifespan": lifespan,
        })
        trigger_manager.append_trigger({
            "id": session["reminder_trigger_id"],
            "type": "nimble-reminder",
            "callback_id": callback_id,
            "interval_seconds": reminder_interval_seconds,
            "recall_description": recall_text,
            "lifespan": lifespan,
        })
        from datetime import datetime, timedelta
        trigger_manager.append_trigger({
            "id": session["expire_trigger_id"],
            "type": "nimble-expire",
            "callback_id": callback_id,
            "target": (datetime.now() + timedelta(seconds=lifespan)).isoformat(),
            "recall_description": f"灵动窗口 {callback_id} 已过期。",
            "lifespan": lifespan,
        })
        backend2frontend.FrontEndShowNimbleWindow(nimble.export_window_payload(callback_id))
        return f"灵动交互窗口已创建，callback_id={callback_id}。该窗口为非阻塞式，结果会在后续 trigger 唤醒时返回。"
    except Exception as e:
        return f"创建灵动交互窗口失败: {str(e)}"

@add_to_tool_list
@tool
def closeNimbleWindowTool(callback_id: str, reason: str = "closed_by_agent") -> str:
    """
    Description:
        主动关闭一个已存在的灵动交互窗口，并清理其关联的 result/reminder/expire trigger。
        当你确认这个窗口已不再需要，或者任务已经结束、用户已取消时，应调用此工具清理资源。
    Args:
        callback_id (str): 需要关闭的灵动窗口 callback_id。
        reason (str): 关闭原因。
    Returns:
        str: 关闭结果。
    """
    try:
        session = nimble.close_nimble_session(callback_id, reason=reason)
        if not session:
            return f"未找到 callback_id={callback_id} 对应的灵动窗口。"
        trigger_manager.delete_trigger(session["result_trigger_id"])
        trigger_manager.delete_trigger(session["reminder_trigger_id"])
        trigger_manager.delete_trigger(session["expire_trigger_id"])
        backend2frontend.FrontEndCloseNimbleWindow({"callback_id": callback_id, "reason": reason})
        nimble.cleanup_nimble_session(callback_id)
        return f"灵动窗口已关闭，callback_id={callback_id}"
    except Exception as e:
        return f"关闭灵动窗口失败: {str(e)}"


@add_to_tool_list
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
        print("[Faust.backend.llm_tools.triggerListTool] Listing all triggers.")
        
        triggers = trigger_manager.get_trigger_information()
        if not triggers:
            return "当前没有已注册的触发器。"
        result_lines = []
        for trig in triggers:
            result_lines.append(f"ID: {trig.id}, Type: {trig.type}, Recall Description: {trig.recall_description or 'N/A'}")
        return "\n".join(result_lines)
    except Exception as e:
        return f"列出触发器出错: {str(e)}"
@add_to_tool_list
@tool
def triggerAddTool(trigger_json: str) -> str:
    """
    Description:
        添加一个新的触发器。
        触发器触发时，会唤醒你。
        触发器 JSON 格式说明

        每个 trigger 对象必须满足下列几类之一的模式（多余字段会被拒绝）。

        通用字段（所有类型）

        - id (string) — 触发器唯一标识（用于删除/覆盖）。必须存在且在 store 中唯一。
        - type (string) — 触发器类型，取值："datetime" | "interval" | "py-eval" | "event" | "nimble-reminder" | "nimble-expire"
        - recall_description (string, optional) — 可选的描述/提示，用于回忆或展示
        - lifespan (int, optional) — 生命周期（秒）。超过后触发器自动删除。

        类型一：DateTimeTrigger（一次性时间触发器）

        - type: "datetime"
        - target: datetime 字符串或 ISO 格式（必填）
        - 支持格式示例：
            - "2024-02-28 15:30:00" （"YYYY-MM-DD HH:MM:SS"）
            - "2024-02-28T15:30:00" 或者带时区："2024-02-28T15:30:00+08:00"（ISO）
            行为：
        - 当系统时间 >= target 时触发，触发后自动从 store 中移除（一次性）。

        示例：

        ```json
        {
        "id": "buy_coffee_reminder",
        "type": "datetime",
        "target": "2026-01-31 09:00:00",
        "recall_description": "上午买咖啡"
        }
        ```

        类型二：IntervalTrigger（周期触发器）

        - type: "interval"
        - interval_seconds: integer >= 1（必填） — 周期秒数
        - last_triggered: float（可选） — 上次触发的时间戳（UNIX 时间，秒）。若缺失，系统会使用默认值（创建时设置为当前时间）。
        行为：
        - 当 (now - last_triggered) >= interval_seconds 时触发，触发后会更新 last_triggered 并保存到文件以保证下次计算正确。

        示例：

        ```json
        {
        "id": "hourly_status_check",
        "type": "interval",
        "interval_seconds": 3600,
        "recall_description": "每小时检查状态"
        }
        ```

        类型三：PyEvalTrigger（基于表达式的触发器）

        - type: "py-eval"
        - eval_code: string（必填） — Python 表达式或语句，返回值用于决定是否触发（truthy 则触发）
        行为与风险：
        - 每轮轮询会 eval(eval_code)。如果表达式结果为 True（或 truthy），触发一次（每轮会继续评估，未改变 last_triggered 行为）。

        示例：

        ```json
        {
        "id": "disk_space_low_check",
        "type": "py-eval",
        "eval_code": "import shutil; shutil.disk_usage('/').free < 10 * 1024 * 1024 * 1024",
        "recall_description": "磁盘可用空间低于 10GB 时触发"
        }
        ```
        类型四：EventTrigger（事件触发器）

        - type: "event"
        - event_name: string（必填）
        - callback_id: string（可选，nimble 常用）
        - payload: object（可选）

        类型五：NimbleRemindTrigger（灵动窗口提醒触发器）

        - type: "nimble-reminder"
        - callback_id: string（必填）
        - interval_seconds: integer >= 1（必填）

        类型六：NimbleExpireTrigger（灵动窗口过期触发器）

        - type: "nimble-expire"
        - callback_id: string（必填）
        - target: datetime 字符串（必填）

        请严格遵守上述格式添加触发器，确保字段完整且类型正确。
    Args:
        trigger_json (str): 触发器的JSON字符串表示。
    Returns:
        str: 添加结果的确认信息，或者错误信息。
    """
    if not STARTED:
        return "系统尚未完全启动，无法操作触发器。"
    try:
        print("[Faust.backend.llm_tools.triggerAddTool] Adding new trigger with JSON:", trigger_json)
        trigger_manager.append_trigger(trigger_json)
        return f"触发器添加成功"
    except Exception as e:
        return f"添加触发器出错: {str(e)}"
@add_to_tool_list
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
        print("[Faust.backend.llm_tools.triggerRemoveTool] Removing trigger with ID:", trigger_id)

        trigger_manager.delete_trigger(trigger_id)
        return f"触发器移除成功，ID: {trigger_id}"
    except Exception as e:
        return f"移除触发器出错: {str(e)}"
if __name__ == "__main__":
    for tool in toollist:
        print(f"Tool name: {tool.name},\nDescription: {tool.description}")