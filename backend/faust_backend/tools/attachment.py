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


@register
@tool
def attachmentReadTool(path: str) -> str:
    """
    Description:
        Read an image attachment from memory and return it as a multimodal
        result so you can see its contents. Use when you need to inspect a
        previously saved image.
    Args:
        path (str): KB path of the image attachment, e.g.
                    /records/2026-05-01/screenshot.png
    Returns:
        str(json): Multimodal payload with the image and its description.
    """
    return _attachmentReadTool(path)
