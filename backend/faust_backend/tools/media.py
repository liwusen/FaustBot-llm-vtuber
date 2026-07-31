from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.backend2front as backend2frontend
from faust_backend.logger import get_logger

log = get_logger("faust.tools.media")


@register
@tool
def musicPlayTool(url: str) -> str:
    """
    Description:
        播放指定URL的音乐。
        会同步口型。
    Args:
        url (str): 音乐的URL地址,支持file://和http(s)://等协议。
    Returns:
        str: 结果信息。
    """
    log.info("Playing music from URL: %s", url)
    backend2frontend.FrontEndPlayMusic(url)
    return "音乐播放命令已发送到前端。"


@register
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
    log.info("Playing background music from URL: %s", url)
    backend2frontend.FrontEndPlayBG(url)
    return "背景音乐播放命令已发送到前端。"
