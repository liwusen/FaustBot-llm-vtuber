"""
Write tool — creates or overwrites files on disk or in the memory store.

Path prefix determines the backend:
  - memory:// → GraphStore.file_write (auto-indexed)
  - bare path → os filesystem write
"""

from __future__ import annotations

from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.logger import get_logger
from faust_backend.tools.vfs import get_faustbot_vfs, refresh_runtime_nodes

log = get_logger("faust.tools.write")


@register
@tool
async def write(path: str, content: str) -> str:
    """Create or overwrite a file on disk or in the memory store.

    This is your PRIMARY tool for writing content.  It replaces the old
    writeTextFileTool and kbWriteTool.  The path prefix determines the backend.

    FILESYSTEM WRITES (bare path):
    - `write("notes/summary.md", "# Summary\\n...")` → writes to disk.
    - Paths are relative to the project directory.
    - Parent directories are created automatically.
    - Security: writes outside the project/config root are rejected.
    - Use this when: saving code, config files, notes, or any persistent file.

    MEMORY STORE WRITES (memory:// prefix):
    - `write("memory://notes/math", "勾股定理: a²+b²=c²")` → writes to memory.
    - The content is automatically chunked, embedded, and indexed for search.
    - Use this when: storing knowledge you want to retrieve later via search()
      or read("memory://...").  This is better than filesystem writes for
      information you'll need to semantic-search later.

    CHOOSING BETWEEN FILESYSTEM AND MEMORY:
    - Use filesystem (bare path) when: the file needs to be executed, imported,
      or accessed by other programs; or when the content is code/config.
    - Use memory (memory://) when: storing notes, knowledge, facts, summaries
      that you want to search semantically later.

    Args:
        path: Target path. memory:// prefix writes to memory store.
        content: Full content to write (plain text).

    Returns:
        Confirmation with path and byte count.
    """
    path_str = str(path or "").strip()
    if not path_str:
        return "错误: path 不能为空"

    content = str(content or "")
    raw = path_str
    log.info("write INPUT path=%s content_len=%d", path_str, len(content))

    from faust_backend.runtime.uri import (
        detect_unsupported_protocol,
        SCHEME_FILE,
        SCHEME_MEMORY,
        SCHEME_FAUSTBOT,
    )

    err = detect_unsupported_protocol(raw, {SCHEME_FILE, SCHEME_MEMORY, SCHEME_FAUSTBOT})
    if err:
        result = f"write: {err}"
        log.info("write OUTPUT %s", result[:120])
        return result

    # memory:// backend
    if raw.startswith("memory://"):
        result = await _write_memory(raw[len("memory://"):].strip("/"), content)
        log.info("write OUTPUT %s", result[:120])
        return result
    if raw.startswith("faustbot://"):
        result = await _write_faustbot(raw[len("faustbot://"):].strip("/"), content)
        log.info("write OUTPUT %s", result[:120])
        return result

    # Filesystem backend
    result = _write_file(raw, content)
    log.info("write OUTPUT %s", result[:120])
    return result


def _write_file(raw: str, content: str) -> str:
    from faust_backend.config_loader import PROJECT_ROOT, CONFIG_ROOT, WORKDIR_ROOT

    file_path = Path(raw)
    if not file_path.is_absolute():
        # Resolve relative to agent workdir first, then source root.
        for base in (WORKDIR_ROOT, PROJECT_ROOT, CONFIG_ROOT):
            candidate = Path(base) / raw
            if candidate.parent.exists() or str(base) == str(WORKDIR_ROOT):
                file_path = candidate
                break

    file_path = file_path.resolve()

    # Safety: restrict to project/config roots
    project_root = Path(PROJECT_ROOT).resolve()
    config_root = Path(CONFIG_ROOT).resolve()
    if not (str(file_path).startswith(str(project_root)) or
            str(file_path).startswith(str(config_root))):
        return f"错误: 不允许写入项目目录外的路径: {file_path}"

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
    except Exception as e:
        return f"写入文件出错: {e}"

    size = len(content.encode("utf-8"))
    return f"已写入 {file_path} ({size} bytes)"


async def _write_faustbot(path: str, content: str) -> str:
    if not path:
        return "错误: faustbot:// 路径不能为空"
    vfs = await get_faustbot_vfs(refresh=True)
    await refresh_runtime_nodes(vfs)
    target_path = "/" + path.strip("/")
    try:
        await vfs.write(target_path, content)
    except Exception as e:
        return f"写入 faustbot 资源出错: {e}"
    size = len(content.encode("utf-8"))
    return f"已写入 faustbot://{path} ({size} bytes)"


async def _write_memory(path: str, content: str) -> str:
    try:
        from faust_backend.memory import get_memory
    except ImportError:
        return "(记忆模块不可用)"

    if not path:
        return "错误: memory:// 路径不能为空"

    store = get_memory()
    try:
        await store.file_write(path, content)
    except Exception as e:
        return f"写入记忆库出错: {e}"

    # 触发 LLM 实体抽取（后台异步）
    try:
        from faust_backend.memory.tools import schedule_extract
        schedule_extract(content, path)
    except Exception:
        pass

    size = len(content.encode("utf-8"))
    return f"已写入 memory://{path} ({size} bytes)"
