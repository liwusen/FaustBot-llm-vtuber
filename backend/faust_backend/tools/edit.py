"""
Edit tool — exact string replacement in files.

Claude Code style: match old_str verbatim in the file and replace with new_str.
Requires a UNIQUE match; on 0 or multiple matches returns actionable guidance
instead of guessing.
"""

from __future__ import annotations

from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.logger import get_logger
from faust_backend.tools.vfs import get_faustbot_vfs
from ._patch_utils import replace_exact
log = get_logger("faust.tools.edit")


@register
@tool
async def edit(path: str, old_str: str, new_str: str) -> str:
    """Replace an exact text snippet in a file with new content.

    Reads the file, finds ALL occurrences of old_str, and replaces it with
    new_str ONLY if old_str matches exactly once. This guarantees you never
    accidentally corrupt unrelated parts of the file.

    MATCH RULES (strict):
    - old_str must match the file content EXACTLY, character for character:
      including indentation, trailing spaces, quotes, and comments.
    - Content copied from `read` output may include line-number prefixes or
      anchors (e.g. "42|", "->") — strip them; they are display-only.
    - If old_str appears MORE THAN ONCE, the edit FAILS. Include more
      surrounding lines in old_str to make it unique.
    - To create a new file, use the write tool instead. To append, include
      the anchor line in old_str.

    TYPICAL WORKFLOW:
    1. read("src/main.py") to see the file content.
    2. edit("src/main.py", "def foo():\\n    return 1", "def foo():\\n    return 2")

    **Editing memory documents:**
    - `edit("memory://notes/todo", "old line", "new line")` — same rules apply.

    Args:
        path: File path (relative to project root), memory:// or faustbot:// URI.
        old_str: Exact text to replace. Must be unique in the file.
        new_str: Replacement text (empty string deletes old_str).
    """
    from faust_backend.config_loader import WORKDIR_ROOT

    log.info("edit INPUT path=%s old_len=%d new_len=%d", path, len(old_str), len(new_str))

    # ── 参数预检 ──
    if old_str == "":
        _msg = (
            "edit: old_str 不能为空（空串会匹配整个文件）。\n"
            "处理: 新建文件用 write(path, content)；在文件末尾追加时，old_str 应包含"
            "文件末尾的锚点行（先用 read 确认最后一行）。"
        )
        log.info("edit OUTPUT %s", _msg[:120])
        return _msg
    if old_str == new_str:
        _msg = "edit: old_str 与 new_str 完全相同，本次编辑无任何变更，未写入。"
        log.info("edit OUTPUT %s", _msg[:120])
        return _msg

    from faust_backend.runtime.uri import (
        detect_unsupported_protocol,
        SCHEME_FILE,
        SCHEME_MEMORY,
        SCHEME_FAUSTBOT,
    )

    err = detect_unsupported_protocol(path, {SCHEME_FILE, SCHEME_MEMORY, SCHEME_FAUSTBOT})
    if err:
        _msg = f"edit: {err}"
        log.info("edit OUTPUT %s", _msg[:120])
        return _msg

    # ── 读取原文 + 绑定写回 ──
    is_memory = path.startswith("memory://")
    is_faustbot = path.startswith("faustbot://")
    if is_memory:
        mem_path = path[len("memory://"):]
        try:
            from faust_backend.memory import get_memory
            store = get_memory()
            result = await store.file_read(mem_path)
            original = result.get("content", "")
        except FileNotFoundError:
            _msg = f"文档不存在: memory://{mem_path}\n"
            _msg += "建议: 先用 write(\"memory://{mem_path}\", content) 创建文档，再编辑。"
            log.info("edit OUTPUT %s", _msg[:120])
            return _msg
        except Exception as e:
            _msg = f"无法读取记忆文档 memory://{mem_path}: {e}"
            log.info("edit OUTPUT %s", _msg[:120])
            return _msg

        async def write_back(content: str) -> str | None:
            try:
                await store.file_write(mem_path, content)
                # 触发 LLM 实体抽取
                try:
                    from faust_backend.memory.tools import schedule_extract
                    schedule_extract(content, mem_path)
                except Exception:
                    pass
            except Exception as e:
                return f"无法写入记忆文档 memory://{mem_path}: {e}"
            return None
    elif is_faustbot:
        vfs_path = "/" + path[len("faustbot://"):].strip("/")
        try:
            vfs = await get_faustbot_vfs(refresh=True)
            original = await vfs.read_text(vfs_path, default="")
        except Exception as e:
            _msg = f"无法读取 faustbot 文档 {path}: {e}"
            log.info("edit OUTPUT %s", _msg[:120])
            return _msg
        if original == "":
            _msg = f"文档不存在: {path}"
            log.info("edit OUTPUT %s", _msg[:120])
            return _msg

        async def write_back(content: str) -> str | None:
            try:
                await vfs.write(vfs_path, content)
            except Exception as e:
                return f"无法写入 faustbot 文档 {path}: {e}"
            return None
    else:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path(WORKDIR_ROOT) / path
        file_path = file_path.resolve()
        try:
            original = file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            _msg = f"文件不存在: {file_path}\n"
            _msg += "建议: 先用 read(\"{path}\") 确认路径，或 write(\"{path}\", content) 创建文件。"
            log.info("edit OUTPUT %s", _msg[:120])
            return _msg
        except Exception as e:
            _msg = f"无法读取文件 {file_path}: {e}"
            log.info("edit OUTPUT %s", _msg[:120])
            return _msg

        async def write_back(content: str) -> str | None:
            try:
                file_path.write_text(content, encoding="utf-8")
            except Exception as e:
                return f"无法写入文件 {file_path}: {e}"
            return None

    # ── 精确替换 ──
    result, match_count = replace_exact(original, old_str, new_str)
    if result is None:
        if match_count == 0:
            _msg = (
                f"edit: old_str 在 {path} 中匹配 0 处，文件未被修改。\n"
                "处理: 1) 用 read(\"" + path + "\") 重新读取，从输出中逐字复制 old_str；"
                "2) 检查缩进、行尾空格、全角/半角字符是否一致；"
                "3) old_str 是普通字符串不是正则，不要包含 \\n 转义符——多行直接写真实换行。"
            )
            log.info("edit OUTPUT %s", _msg[:120])
            return _msg
        _msg = (
            f"edit: old_str 在 {path} 中匹配 {match_count} 处"
            f"（要求恰好 1 处），文件未被修改。\n"
            f"处理: 在 old_str 前后多包含几行上下文使其唯一"
            f"（可用 read(\"{path}\") 查看各处差异）；"
            f"若确实要替换所有 {match_count} 处相同片段，"
            f"请改为逐处编辑（每处 old_str 带不同上下文）。"
        )
        log.info("edit OUTPUT %s", _msg[:120])
        return _msg

    # ── 写回 ──
    write_err = await write_back(result)
    if write_err:
        log.info("edit OUTPUT %s", write_err[:120])
        return write_err
    _msg = f"已编辑 {path} (1 处替换)"
    log.info("edit OUTPUT %s", _msg[:120])
    return _msg
