import os
from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.tools._patch_utils import safe_read_file_range, apply_patch_text
from faust_backend.logger import get_logger

log = get_logger("faust.tools.file_ops")
log.error("faust.tools.file_ops is deprecated.")

@register
@tool
def getCwdTool() -> str:
    """获取当前工作目录

    Returns:
        str: 当前工作目录
    """
    try:
        return os.getcwd()
    except Exception as e:
        return f"出错{str(e)}"


@register
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
    try:
        log.info("Listing directory: %s", path)
        if os.name == 'nt':
            with os.popen(f'dir "{path}"') as f:
                output = f.read()
        else:
            with os.popen(f'ls "{path}"') as f:
                output = f.read()
        return output if output else "目录为空。"
    except Exception as e:
        return f"列出目录出错: {str(e)}"


@register
@tool
def readTextFileTool(file_path: str, start_line: int = 1, end_line: int = 0) -> str:
    """
    Description:
        读取指定文本文件的内容（支持按行范围读取）。
    Args:
        file_path (str): 需要读取的文本文件路径。
        start_line (int): 起始行号（从1开始）。
        end_line (int): 结束行号（包含该行）。
    Returns:
        str: 指定行范围内容，或错误信息。
    """
    from faust_backend.live_mode import is_live_mode
    if is_live_mode():
        from fnmatch import fnmatch
        norm_path = os.path.normpath(file_path).replace("\\", "/")
        if not fnmatch(norm_path, "*/agents/*.md"):
            return f"直播模式下不允许读取该文件: {file_path}"
    try:
        log.info("Reading file: %s", file_path)
        return safe_read_file_range(file_path, int(start_line), int(end_line))
    except Exception as e:
        return f"读取文件出错: {str(e)}"


@register
@tool
def writeTextFileTool(file_path: str = "", content: str = "", patch_text: str = "") -> str:
    """
    Description:
        修改文件内容，支持两种模式：

        1) Patch 模式（推荐）：
           传入 patch_text，格式与 apply_patch 风格一致，例如：
           *** Begin Patch
           *** Update File: d:/a.txt
           @@
           -old
           +new
           *** End Patch

        2) 覆写模式（向后兼容）：
           传入 file_path + content，直接整文件写入。

    Args:
        file_path (str): 覆写模式下的目标文件路径。
        content (str): 覆写模式下写入内容。
        patch_text (str): Patch 文本。
    Returns:
        str: 写入成功的确认信息，或者错误信息。
    """
    try:
        if patch_text and str(patch_text).strip():
            log.info("Applying patch text")
            return apply_patch_text(patch_text)

        if not file_path:
            return "写入失败：未提供 file_path，或未提供 patch_text。"

        log.info("Writing to file: %s", file_path)
        p = Path(file_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"文件写入成功: {str(p)}"
    except Exception as e:
        return f"写入文件出错: {str(e)}"
