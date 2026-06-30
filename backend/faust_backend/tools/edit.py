"""
Edit tool — surgical line-based edits to files.

Uses a patch language similar to the Oh-My-Pi Edit tool:
  SWAP N.=M:  → replace lines N through M (inclusive)
  DEL N.=M    → delete lines N through M
  INS.PRE N:  → insert before line N
  INS.POST N: → insert after line N
"""

from __future__ import annotations

from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.logger import get_logger

log = get_logger("faust.tools.edit")


@register
@tool
def edit(path: str, patch: str) -> str:
    """Apply precise line-based edits to a file without rewriting the whole thing.

    Use this when you need to change a few lines in a file — it is much more
    efficient and safer than reading the entire file and writing it back.

    THE PATCH LANGUAGE (each operation begins with a header line):

    **SWAP N.=M:** — Replace lines N through M (inclusive) with new content.
        SWAP 10.=12:
        +replacement line 1
        +replacement line 2

    **DEL N.=M** — Delete lines N through M.  No body lines needed.
        DEL 5.=7

    **INS.PRE N:** — Insert new lines BEFORE line N.
        INS.PRE 3:
        +new line inserted before line 3

    **INS.POST N:** — Insert new lines AFTER line N.
        INS.POST 3:
        +new line inserted after line 3

    CRITICAL RULES:
    - Line numbers refer to the ORIGINAL file before any edits.
    - Apply edits from BOTTOM to TOP (highest line numbers first) so earlier
      edits don't shift the line numbers of later edits.
    - Each body line MUST start with '+' (the '+' is stripped before writing).
    - Separate operations with a blank line.
    - The body after a header is the FINAL content — never include old/context lines.

    TYPICAL WORKFLOW:
    1. read("src/main.py") to get a structural summary with line numbers.
    2. read("src/main.py:40-60") to see the exact lines you want to edit.
    3. edit("src/main.py", "SWAP 42.=44:\\n+new line 1\\n+new line 2\\n")

    **Editing memory documents:**
    - `edit("memory://notes/todo", "SWAP 3.=3:\\n+updated entry\\n")` — edits a memory doc.
    - Works exactly like file edits but targets the memory store.

    Args:
        path: File path (relative to project root) or memory:// URI.
        patch: Patch instructions in the described format.
    """
    import asyncio as _asyncio
    from faust_backend.config_loader import PROJECT_ROOT

    log.info("edit INPUT path=%s patch_len=%d", path, len(patch))

    # Detect memory:// scheme
    is_memory = path.startswith("memory://")
    if is_memory:
        mem_path = path[len("memory://"):]
        try:
            from faust_backend.memory import get_memory
            store = get_memory()
            result = _asyncio.run(store.file_read(mem_path))
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
    else:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = Path(PROJECT_ROOT) / path
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

    # Parse patch
    try:
        ops = _parse_patch(patch)
    except ValueError as e:
        _msg = f"Patch 格式错误: {e}\n"
        _msg += "支持的指令: SWAP N.=M:, DEL N.=M, INS.PRE N:, INS.POST N:\n"
        _msg += "每行正文必须以 '+' 开头，多个操作以空行分隔。"
        log.info("edit OUTPUT %s", _msg[:120])
        return _msg

    # Apply
    lines = original.split("\n")
    changes = 0
    ops.sort(key=lambda o: o["offset"], reverse=True)  # apply from bottom up

    for op in ops:
        kind = op["kind"]
        start = op["start"] - 1  # 0-indexed start
        end = op["end"]          # inclusive, make exclusive below
        body = op["body"]

        if kind == "SWAP":
            end_ex = min(end, len(lines))  # exclusive
            lines[start:end_ex] = body
            changes += abs(len(body) - (end_ex - start))
        elif kind == "DEL":
            end_ex = min(end, len(lines))
            del lines[start:end_ex]
            changes += (end_ex - start)
        elif kind == "INS_PRE":
            for i, b in enumerate(body):
                lines.insert(start + i, b)
            changes += len(body)
        elif kind == "INS_POST":
            for i, b in enumerate(body):
                lines.insert(end + i, b)
            changes += len(body)

    result = "\n".join(lines)

    if is_memory:
        try:
            _asyncio.run(store.file_write(mem_path, result))
            # 触发 LLM 实体抽取
            try:
                from faust_backend.memory.tools import _bg_extract_and_save, _run_bg
                _run_bg("auto_extract", _bg_extract_and_save(result, mem_path))
            except Exception:
                pass
        except Exception as e:
            _msg = f"无法写入记忆文档 memory://{mem_path}: {e}"
            log.info("edit OUTPUT %s", _msg[:120])
            return _msg
        _msg = f"已编辑 memory://{mem_path}\n变更: {changes} 行 (原文件 {len(lines)} 行 → 新文件 {len(result.split(chr(10)))} 行)"
        log.info("edit OUTPUT %s", _msg[:120])
        return _msg
    else:
        try:
            file_path.write_text(result, encoding="utf-8")
        except Exception as e:
            _msg = f"无法写入文件 {file_path}: {e}"
            log.info("edit OUTPUT %s", _msg[:120])
            return _msg
        _msg = f"已编辑 {file_path}\n变更: {changes} 行 (原文件 {len(lines)} 行 → 新文件 {len(result.split(chr(10)))} 行)"
        log.info("edit OUTPUT %s", _msg[:120])
        return _msg

def _parse_patch(patch_text: str) -> list[dict]:
    """Parse patch instructions into a list of operations."""
    ops = []
    current = None
    current_body: list[str] = []

    lineno = 0
    for raw_line in patch_text.split("\n"):
        lineno += 1
        line = raw_line.strip()
        if not line:
            if current is not None:
                # Flush on blank line — DEL ops have no body, still valid
                current["body"] = current_body
                ops.append(current)
                current = None
                current_body = []
            continue

        if current is not None:
            # Body line
            if line.startswith("+"):
                current_body.append(line[1:])
            elif not line.startswith("+"):
                # Next operation header → flush current, treat as new header
                current["body"] = current_body
                ops.append(current)
                current = None
                current_body = []

        if current is None:
            # New operation header
            if " " in line:
                header, rest = line.split(" ", 1)
            else:
                header = line
                rest = ""
            header = header.upper().rstrip(":")
            rest = rest.rstrip(":")

            if header == "SWAP":
                start, end = _parse_range(rest)
                current = {"kind": "SWAP", "start": start, "end": end, "offset": start}
            elif header == "DEL":
                start, end = _parse_range(rest)
                current = {"kind": "DEL", "start": start, "end": end, "offset": start}
            elif header in ("INS.PRE", "INS.POST"):
                pos = int(rest) if rest else 1
                kind = "INS_PRE" if header == "INS.PRE" else "INS_POST"
                current = {"kind": kind, "start": pos, "end": pos, "offset": pos}
            else:
                raise ValueError(f"第{lineno}行: 未知指令 \"{header}\"，支持: SWAP, DEL, INS.PRE, INS.POST")

    if not ops:
        raise ValueError("Patch 不包含任何操作")
    return ops


def _parse_range(text: str) -> tuple[int, int]:
    """Parse 'N.=M' into (start_inclusive, end_inclusive)."""
    text = text.strip().rstrip(":")
    if ".=" in text:
        start, end = text.split(".=", 1)
        s = int(start)
        e = int(end)
        return (s, e)
    else:
        n = int(text)
        return (n, n)
