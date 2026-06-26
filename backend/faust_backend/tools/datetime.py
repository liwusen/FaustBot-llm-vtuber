import os
import socket
from datetime import datetime

from langchain.tools import tool

from faust_backend.tools._registry import register



@tool
def getDateTimeTool() -> str:
    """
    Description:
        获取当前的日期和时间，格式为YYYY-MM-DD HH:MM:SS
    Args:
        None
    Returns:
        str: 当前的日期和时间字符串
    """
    now = datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")



@tool
def userHostNameTool() -> str:
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
    ip = socket.gethostbyname(hostname).strip()
    os_type = os.name
    return str({"hostname": hostname, "username": username, "ip": ip, "os_type": os_type})
