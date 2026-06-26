"""
Unified Read tool — reads files, directories, artifact://, and memory:// URIs.

Part of the harness core toolset: the single entry point for reading any
addressable resource.
"""

from __future__ import annotations

import os
from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.runtime.uri import parse, SCHEME_FILE, SCHEME_ARTIFACT, SCHEME_MEMORY
from faust_backend.runtime.output_store import get_output_store
from faust_backend.memory.store import _path_id
from faust_backend.logger import get_logger

log = get_logger("faust.tools.read")

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})


@register
@tool
def read(uri: str) -> str:
    """Read a file, directory, tool output, or memory document — the universal read tool.

    This is your PRIMARY tool for inspecting anything on disk or in memory.
    You should use it instead of the old readTextFileTool, listDirectoryTool, kbReadTool, or kbListTool.

    URI FORMATS AND WHEN TO USE THEM:

    **Reading code files (structured summary mode):**
    - `read("src/main.py")` → returns only declarations (def/class/import lines)
      with line numbers. The body of functions is hidden to save context space.
      This is the default for .py, .ts, .js, .rs, .go, .java, .cpp files.
    - Use this when: exploring a codebase, finding a function, checking imports.

    **Reading specific line ranges:**
    - `read("src/main.py:50-100")` → returns lines 50 through 100 verbatim.
    - `read("src/main.py:42")` → returns only line 42.
    - Use this when: you saw a declaration in the summary and need to read its body,
      or when you need to verify a specific section.

    **Listing a directory:**
    - `read("src/")` or `read(".")` → returns a list of files and subdirectories.
    - Use this when: exploring what files exist, finding a file whose name you forgot.

    **Reading tool outputs (artifact://):**
    - `read("artifact://shell_3")` → full output of a previous tool execution.
    - `read("artifact://shell_3:50-100")` → lines 50-100 of that output.
    - Practical flow: execute("shell", "dir") returns a summary with artifact ID,
      then use read("artifact://<id>") to see the full output.
    - Use this when: a tool returned truncated output and you need to see more.

    **Reading memory documents (memory://):**
    - `read("memory://notes/math")` → read a document from the memory store.
    - `read("memory://notes/math:50-100")` → read a range of that document.
    - `read("memory://")` → list all documents in the memory tree.
    - Use this when: checking your knowledge base, reviewing past notes or diaries.

    Args:
        uri: Path or URI with optional :line-selector suffix.

    Returns:
        For files: structural summary (code) or first 300 lines; or specified range.
        For directories: list of entries.
        For artifacts: full or ranged tool output.
        For memory: document content or file tree.
    """
    parsed = parse(uri)
    log.debug("read parsed: scheme=%s path=%r selector=%r", parsed.scheme, parsed.path, parsed.selector)

    if parsed.scheme == SCHEME_ARTIFACT:
        return _read_artifact(parsed)
    elif parsed.scheme == SCHEME_MEMORY:
        return _read_memory(parsed)
    else:
        return _read_file(parsed)


def _read_artifact(parsed) -> str:
    store = get_output_store()
    output_id = parsed.path
    if not output_id:
        available = store.list_ids()
        if not available:
            return "(没有可用的 artifact)"
        return "可用的 artifact:\n" + "\n".join(f"  artifact://{aid}" for aid in available[-20:])

    art = store.get(output_id)
    if art is None:
        return f"[找不到 artifact: {output_id}]"

    if parsed.selector_lines:
        start, end = parsed.selector_lines
        lines = art.content.split("\n")
        selected = lines[start - 1:end]
        return "\n".join(selected)
    return art.content


def _read_memory(parsed) -> str:
    try:
        from faust_backend.memory import get_memory
    except ImportError:
        return "(记忆模块不可用)"

    store = get_memory()
    path = parsed.path

    # Check if this is an image attachment
    import asyncio as _asyncio
    nid = _path_id(path)
    if path and store._has_node(nid):
        ct = store._get_node_attr(nid, "content_type", "")
        if ct.startswith("image/"):
            try:
                result = _asyncio.run(store.attachment_read(path))
            except (FileNotFoundError, Exception) as e:
                return f"读取记忆图片出错: {e}"
            import json as _json
            payload = {
                "kind": "multimodal_tool_result",
                "text": result.get("description", f"记忆图片: {path}"),
                "images": [{
                    "url": f"data:{result['content_type']};base64,{result['content_base64']}"
                }],
            }
            return _json.dumps(payload, ensure_ascii=False)

    # empty path → tree
    if not path or parsed.is_dir:
        try:
            import asyncio
            tree = asyncio.run(store.tree_list(path or "/"))
            return _format_tree(tree)
        except Exception as e:
            return f"读取记忆树出错: {e}"

    # document read
    try:
        import asyncio
        result = asyncio.run(store.file_read(path))
    except FileNotFoundError:
        return f"[记忆文档不存在: {path}]"
    except Exception as e:
        return f"读取记忆文档出错: {e}"

    content = result.get("content", "")
    if parsed.selector_lines:
        start, end = parsed.selector_lines
        lines = content.split("\n")
        selected = lines[start - 1:end]
        return "\n".join(selected)
    return content


def _read_file(parsed) -> str:
    path_str = parsed.path

    # Empty path → current directory
    if not path_str:
        path_str = "."

    file_path = Path(path_str)

    # Directory
    if parsed.is_dir or (file_path.exists() and file_path.is_dir()):
        return _list_directory(file_path)

    # File
    if not file_path.exists():
        # Try as a relative path from config root
        from faust_backend.config_loader import CONFIG_ROOT, PROJECT_ROOT
        for base in (CONFIG_ROOT, PROJECT_ROOT):
            alt = Path(base) / path_str
            if alt.exists():
                file_path = alt
                break
        else:
            return f"[文件不存在: {path_str}]"

    # Image file detection
    if file_path.suffix.lower() in IMAGE_EXTENSIONS:
        return _read_image(file_path)

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"读取文件出错: {e}"

    if parsed.selector_lines:
        start, end = parsed.selector_lines
        lines = content.split("\n")
        selected = lines[start - 1:end]
        return "\n".join(selected)

    # For code files, return structural summary
    if file_path.suffix in (".py", ".ts", ".js", ".rs", ".go", ".java", ".cpp", ".c",
                            ".h", ".jsx", ".tsx", ".vue", ".rb", ".swift"):
        return _structural_summary(content, str(file_path))
    return _truncate_long(content)


def _list_directory(dir_path: Path) -> str:
    """Return a simple dirent list."""
    try:
        entries = sorted(dir_path.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    except Exception as e:
        return f"列出目录出错: {e}"
    lines = []
    for entry in entries:
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"  {entry.name}{suffix}")
    return "\n".join(lines)


def _structural_summary(content: str, path: str) -> str:
    """Return header-level structural summary of a code file."""
    lines = content.split("\n")
    result = []
    in_docstring = False
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Skip decorators
        if stripped.startswith("@"):
            continue
        # Track docstrings
        if stripped.startswith(('"""', "'''")):
            if in_docstring:
                in_docstring = False
                continue
            if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                continue
            in_docstring = True
            continue
        if in_docstring:
            if stripped.endswith(('"""', "'''")) or stripped.count('"""') >= 1 or stripped.count("'''") >= 1:
                in_docstring = False
            continue
        # Capture top-level declarations
        if stripped.startswith(("def ", "class ", "async def ", "import ", "from ",
                                "const ", "let ", "function ", "export ")):
            result.append(f"{i}: {stripped}")
    if not result:
        return _truncate_long(content)
    summary = "\n".join(result)
    total = len(lines)
    footer = f"\n[文件 {path}: {total} 行, 显示结构摘要。用 read(\"{path}:N-M\") 查看具体行范围]"
    return summary + footer


def _truncate_long(content: str, max_lines: int = 300) -> str:
    lines = content.split("\n")
    if len(lines) <= max_lines:
        return content
    return "\n".join(lines[:max_lines]) + f"\n[... 共 {len(lines)} 行, 已截断]"


def _format_tree(tree: dict, indent: int = 0) -> str:
    """Format a memory file tree dict into a text listing."""
    result = []
    name = tree.get("name", "/")
    prefix = "  " * indent
    result.append(f"{prefix}{name}/")
    for child in tree.get("children", []):
        if isinstance(child, dict):
            ctype = child.get("type", "")
            cname = child.get("name", "?")
            if ctype == "dir":
                result.append(_format_tree(child, indent + 1))
            else:
                result.append(f"{prefix}  {cname}")
    return "\n".join(result)


def _read_image(path: Path) -> str:
    """Read an image file and return a multimodal JSON string."""
    import base64, json
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                ".svg": "image/svg+xml"}
    mime = mime_map.get(path.suffix.lower(), "image/png")
    raw = path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    payload = {
        "kind": "multimodal_tool_result",
        "text": f"图片文件: {path.name} ({len(raw)} bytes)",
        "images": [{"url": f"data:{mime};base64,{b64}"}],
    }
    return json.dumps(payload, ensure_ascii=False)
