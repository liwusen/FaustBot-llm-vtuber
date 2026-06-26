from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.memory.tools import (
    attachmentWriteTool as _attachmentWriteTool,
    attachmentReadTool as _attachmentReadTool,
)


@register
@tool
def attachmentWriteTool(file_path: str, path: str = "", *,
                        description: str = "",
                        content_type: str = "") -> str:
    """
    Description:
        Write an image to memory from a local file path. Reads the image file
        from your local filesystem and stores it in the KB. The image is
        searchable by its description text.
    Args:
        file_path (str): Local filesystem path to the image file, e.g.
                         C:\\Users\\name\\screenshot.png
        path (str): KB file path, e.g. /records/2026-05-01/screenshot.png.
                    Auto-derived from filename if empty.
        description (str): Text description of what the image shows
        content_type (str): MIME type, auto-detected from extension if empty.
    Returns:
        str(json): {path, description, content_type}
    """
    return _attachmentWriteTool(file_path, path, description=description,
                                content_type=content_type)



@tool
def attachmentReadTool(path: str) -> str:
    """[已弃用] 读取记忆图片 → 请使用 read("memory://path")。
    直接转发给 read 工具（支持多模态图片输出）。
    """
    from faust_backend.tools.read import read
    return read(f"memory://{path}")
