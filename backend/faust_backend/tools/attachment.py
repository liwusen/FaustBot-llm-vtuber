from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.memory.tools import (
    attachmentWriteTool as _attachmentWriteTool,
    attachmentReadTool as _attachmentReadTool,
)


@register
@tool
async def attachmentWriteTool(file_path: str, path: str = "", *,
                              description: str = "",
                              content_type: str = "") -> str:
    r"""把本地图片存进记忆库，之后可按 description 语义检索到它。

    Args:
        file_path (str): 本地图片路径，如 C:\Users\name\screenshot.png
        path (str): 记忆库内路径，如 /records/2026-05-01/screenshot.png；留空按文件名自动生成。
        description (str): 图片内容描述，这是后续检索的唯一依据。
        content_type (str): MIME 类型；留空按扩展名推断。

    Returns:
        str(json): {path, description, content_type}
    """
    return await _attachmentWriteTool(file_path, path, description=description,
                                      content_type=content_type)
