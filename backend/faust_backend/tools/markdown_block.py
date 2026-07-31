from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.backend2front as backend2frontend
from faust_backend.logger import get_logger

log = get_logger("faust.tools.markdown_block")


@register
@tool
def RenderMarkdownBlock(content: str) -> str:
    """
    Description:
        在前端聊天气泡中渲染一个 Markdown 内容块。
        支持标准 Markdown 语法，以及 fenced ```mermaid 代码块绘制图表。
        该内容块只做可视化展示，不会被语音朗读(TTS)。
        适合展示表格、代码、列表、流程图等结构化内容。
    Args:
        content (str): 要渲染的 Markdown 文本。
    Returns:
        str: 结果信息。
    """
    text = str(content or "").strip()
    if not text:
        return "内容为空，未发送 Markdown 块。"
    log.info("Rendering markdown block (%d chars)", len(text))
    backend2frontend.FrontEndMarkdownBlock(text)
    return "Markdown 块已发送到前端渲染。"
