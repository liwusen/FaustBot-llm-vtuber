import asyncio

from langchain.tools import tool

from faust_backend.tools._registry import register


@register
@tool
async def writeDiaryFileTool(content: str) -> str:
    """
    Description:
        将指定内容写入知识库，使用UTF-8编码。
        文件名根据当前日期时间生成，格式为YYYYMMDD_HHMMSS.txt
        你可以自行决定何时使用此工具。
    Args:
        content (str): 需要写入文件的内容字符串。
    Returns:
        str: 写入成功的确认信息，或者错误信息。
    """
    try:
        from faust_backend.memory import get_memory
        result = await get_memory().write_diary(content)
        return f"日记已写入知识库: {result.get('path')}"
    except Exception as e:
        return f"写入日记文件出错: {str(e)}"
