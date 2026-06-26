"""
Deprecated tools — all functionality moved to the 6 core tools:

  sysExecTool       → execute("shell", command)
  pythonExecTool    → execute("python", code)
  readTextFileTool  → read(path)
  listDirectoryTool → read(path) or find(["path/**"])
  writeTextFileTool → write(path, content) or edit(path, patch)
  getCwdTool        → no direct equivalent

These wrappers exist for backward compatibility only.
They are NOT registered as tools for the LLM (no @register decorator).
"""

import os

from langchain.tools import tool


@tool
def getCwdTool() -> str:
    """[已弃用] 获取当前工作目录。无直接替代。"""
    try:
        return os.getcwd()
    except Exception as e:
        return f"出错{str(e)}"


@tool
def listDirectoryTool(path: str) -> str:
    """[已弃用] 列出目录 → 请使用 read(path) 或 find(patterns)。
    直接转发给 read 工具。
    """
    from faust_backend.tools.read import read
    return read(path)


@tool
def readTextFileTool(file_path: str, start_line: int = 1, end_line: int = 0) -> str:
    """[已弃用] 读取文本文件 → 请使用 read(uri)。
    直接转发给 read 工具。
    """
    from faust_backend.tools.read import read
    if end_line > 0:
        return read(f"{file_path}:{start_line}-{end_line}")
    if start_line > 1:
        return read(f"{file_path}:{start_line}")
    return read(file_path)


@tool
def writeTextFileTool(file_path: str = "", content: str = "", patch_text: str = "") -> str:
    """[已弃用] 写入/修改文件 → 请使用 write(path, content) 或 edit(path, patch)。
    直接转发给相应新工具。
    """
    if patch_text:
        from faust_backend.tools.edit import edit
        return edit(file_path, patch_text)
    if content:
        from faust_backend.tools.write import write
        return write(file_path, content)
    return "请提供 content 或 patch_text 参数"
