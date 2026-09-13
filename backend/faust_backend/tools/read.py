"""
Unified Read tool — reads files, directories, artifact://, and memory:// URIs.

Part of the harness core toolset: the single entry point for reading any
addressable resource.
"""

from __future__ import annotations

import getpass
import json
import os
import platform
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path

from langchain.tools import tool
from PIL import Image, ImageDraw
import pyautogui

from faust_backend.tools._registry import register
from faust_backend.runtime.uri import (
    parse,
    SCHEME_FILE,
    SCHEME_ARTIFACT,
    SCHEME_MEMORY,
    SCHEME_SKILL,
    SCHEME_FAUSTBOT,
    SCHEME_IMG_SOURCE,
    SCHEME_SOURCE_CODE,
)
from faust_backend.runtime.output_store import get_output_store
from faust_backend.memory.store import _path_id
from faust_backend.logger import get_logger
from faust_backend.tools.vfs import (
    get_faustbot_vfs,
    refresh_runtime_nodes,
)
import faust_backend.config_loader as conf

log = get_logger("faust.tools.read")

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})

# 列举元数据里统计行数的源码扩展名白名单（与结构化摘要共用）
SOURCE_EXTENSIONS = frozenset({
    ".py",
    ".ts",
    ".js",
    ".rs",
    ".go",
    ".java",
    ".cpp",
    ".c",
    ".h",
    ".jsx",
    ".tsx",
    ".vue",
    ".rb",
    ".swift",
})
# 超过该大小不统计行数（避免列举时读大文件）
MAX_METADATA_LINE_SCAN = 2 * 1024 * 1024


@register
@tool
async def read(
    uri: str,
    *,
    force_plain_text: bool = False,
    show_line_number: bool = False,
    with_metadata: bool = False,
) -> str:
    """Read a file, directory, tool output, or memory document — the universal read tool.

    This is your PRIMARY tool for inspecting anything on disk or in memory.
    URI FORMATS AND WHEN TO USE THEM:

    **Reading code files (structured summary mode):**
    - `read("src/main.py")` → returns only declarations (def/class/import lines)
      with line numbers. The body of functions is hidden to save context space.
      This is the default for .py, .ts, .js, .rs, .go, .java, .cpp files.
    - `read("src/main.py:raw")` → returns the file verbatim, WITHOUT the structural
      summary. Works on every scheme (`skill://demo/scripts/a.py:raw`,
      `sourceCode://backend/main.py:raw`). Files longer than 300 lines are still
      truncated — use a line range for the remainder.
    - Use this when: exploring a codebase, finding a function, checking imports;
      or use `:raw` when you need the real body of a short file.

    **Reading specific line ranges:**
    - `read("src/main.py:50-100")` → returns lines 50 through 100 verbatim.
    - `read("src/main.py:42")` → returns only line 42.
    - Use this when: you saw a declaration in the summary and need to read its body,
      or when you need to verify a specific section.

    **Listing a directory:**
    - `read("src/")` or `read(".")` → returns a list of files and subdirectories.
    - The trailing `/` is optional: when the resolved target IS a directory, the
      listing is returned anyway. This holds for every scheme — `read("memory://notes")`,
      `read("faustbot://plugins")`, `read("skill://demo")`, `read("sourceCode://backend")`
      all list when the target is a directory.
    - Use this when: exploring what files exist, finding a file whose name you forgot.

    **Listing with metadata:**
    - `read("src/", with_metadata=True)` → each file entry gains an indented
      metadata line: `  [3KB, 400 lines, 03/16]` (size, line count, mtime).
      Directories never get a metadata line.
    - `read("faustbot://", with_metadata=True)` → each VFS node gains its
      `[description]`; `read("skill://", with_metadata=True)` and
      `read("sourceCode://backend/", with_metadata=True)` behave like plain
      directory listings.
    - Line counts are shown only for known source files (≤2MB, valid UTF-8);
      other files still show size/date.
    - Use this when: you need a file's size, recency, or line count before
      deciding whether to read it.

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
    - `read("memory://notes")` → list that directory (also when it has no trailing `/`).
    - Use this when: checking your knowledge base, reviewing past notes or diaries.

    **Reading system resources (faustbot://):**
    - `read("faustbot://")` → list all available faustbot resources (index.md, tool_use.md, mc.md, pc_info).
    - `read("faustbot://index.md")` → read the faustbot index.
    - `read("faustbot://pc_info")` → read system information.
    - Use this when: you need system info or tool usage guides.

    **Reading project source code (sourceCode://):**
    - `read("sourceCode://")` → list the repository root.
    - `read("sourceCode://backend/")` → list a source directory (auto listdir).
    - `read("sourceCode://backend/faust_backend/tools/read.py")` → read a source file
      (structured summary for code, same as plain file reads).
    - `read("sourceCode://backend/main.py:50-100")` → read a line range of a source file.
    - Use this when: you need to inspect FaustBot's own source code.
      (This replaces the old `faustbot://source/{PATH}` form.)

    **Reading skills (skill://):**
    - `read("skill://")` → list all available skill names.
    - `read("skill://{name}")` or `read("skill://{name}/")` → list files in that skill directory.
    - `read("skill://{name}/SKILL.md")` → read a skill's main file (must be stated explicitly).
    - `read("skill://{name}/subdir")` → list that subdirectory (no trailing `/` needed).
    - `read("skill://{name}/subdir/file.md")` → read a file in a skill subdirectory.
    - Use this when: you need to check available skills or read skill instructions.

    **Reading image sources (img_source://):**
    - `read("img_source://")` → list available image sources (screenshot, camera).
    - `read("img_source://screenshot")` → take a screenshot (base64 multimodal image).
    - `read("img_source://camera_0")` → capture from camera #0.
    - `read("img_source://screenshot?grid=true&scale=0.5")` → screenshot with grid overlay at 50% scale.
    - Use this when: you need visual information from the screen or a camera.

    **Reading images (multimodal vs plain text):**
    - `read("screenshot.png")` → returns multimodal JSON with the image in base64,
      allowing vision-capable models to see it. If you are not a vision-capable model, you MUST NOT use this.
    - `read("screenshot.png", force_plain_text=True)` → returns only the file metadata
      (name, size) as plain text, WITHOUT the base64 image data.
    - Use `force_plain_text=True` when: you only need the image metadata, or when
      you know the current model cannot process images and you want to save context.

    **Showing line numbers:**
    - `read("src/main.py:50-100", show_line_number=True)` → returns lines 50-100
      with each line prefixed by its ABSOLUTE line number in the original file:
      `50:def foo():`, `51:    return 1`.
    - Line numbers are always absolute (line 1 = first line of the file), even
      when the selector is a negative offset or the result is truncated.
    - Works without a range too: `read("src/main.py:raw", show_line_number=True)`
      numbers the whole file (the `[... 已截断]` notice line stays unnumbered).
      Applies to memory documents, text artifacts and faustbot:// nodes as well.
    - Use this when: you need to reference or edit exact lines later (e.g. with
      the edit tool), or when a range's absolute position matters.

    Args:
        uri: Path or URI with optional selector suffix (`:50-100` line range,
             `:raw` 关闭结构化摘要).
        force_plain_text: If True, images and multimodal artifacts return only
                          text description (no base64 data). Defaults to False.
        show_line_number: If True, prefix each output line with its absolute
                          line number (e.g. "36:print(xxx)"). Applies to any
                          text output: a line range, a whole-file read, or
                          `:raw`. Defaults to False.
        with_metadata: If True, directory listings add per-entry metadata
                       (size, line count, mtime), VFS nodes add their
                       description, and memory documents add date/lines/tags.
                       Only applies to listings; single-file reads ignore it.
                       Defaults to False.

    Returns:
        For files: structural summary (code) or first 300 lines; or specified range;
            `:raw` returns the file content without the structural summary.
        For images: multimodal JSON with base64 (unless force_plain_text=True).
        For directories: list of entries.
        For artifacts: full or ranged tool output.
        For memory: document content or file tree.
        For faustbot://: system resources.
        For sourceCode://: FaustBot repository source files and directory listings.
        For skill://: skill files and directory listings.
        For img_source://: screenshot or camera images (multimodal).
    """
    log.info(
        "read INPUT uri=%s force_plain_text=%s show_line_number=%s with_metadata=%s",
        uri,
        force_plain_text,
        show_line_number,
        with_metadata,
    )
    parsed = parse(uri)
    log.debug(
        "read parsed: scheme=%s path=%r selector=%r force_plain_text=%r",
        parsed.scheme,
        parsed.path,
        parsed.selector,
        force_plain_text,
    )

    if parsed.scheme == SCHEME_ARTIFACT:
        result = _read_artifact(parsed, force_plain_text=force_plain_text, show_line_number=show_line_number, with_metadata=with_metadata)
        log.info("read OUTPUT len=%d", len(result))
        return result
    elif parsed.scheme == SCHEME_MEMORY:
        result = await _read_memory(parsed, force_plain_text=force_plain_text, show_line_number=show_line_number, with_metadata=with_metadata)
        log.info("read OUTPUT len=%d", len(result))
        return result
    elif parsed.scheme == SCHEME_SKILL:
        result = _read_skill(parsed, force_plain_text=force_plain_text, show_line_number=show_line_number, with_metadata=with_metadata)
        log.info("read OUTPUT len=%d", len(result))
        return result
    elif parsed.scheme == SCHEME_FAUSTBOT:
        result = await _read_faustbot(parsed, force_plain_text=force_plain_text, show_line_number=show_line_number, with_metadata=with_metadata)
        log.info("read OUTPUT len=%d", len(result))
        return result
    elif parsed.scheme == SCHEME_IMG_SOURCE:
        result = _read_img_source(parsed, force_plain_text=force_plain_text, show_line_number=show_line_number, with_metadata=with_metadata)
        log.info("read OUTPUT len=%d", len(result))
        return result
    elif parsed.scheme == SCHEME_SOURCE_CODE:
        result = _read_source_code(parsed, force_plain_text=force_plain_text, show_line_number=show_line_number, with_metadata=with_metadata)
        log.info("read OUTPUT len=%d", len(result))
        return result
    else:
        result = _read_file(parsed, force_plain_text=force_plain_text, show_line_number=show_line_number, with_metadata=with_metadata)
        log.info("read OUTPUT len=%d", len(result))
        return result


def _read_artifact(parsed, *, force_plain_text: bool = False, show_line_number: bool = False, with_metadata: bool = False) -> str:
    store = get_output_store()
    output_id = parsed.path
    if not output_id:
        available = store.list_ids()
        if not available:
            return "(没有可用的 artifact)"
        return "可用的 artifact:\n" + "\n".join(
            f"  artifact://{aid}" for aid in available[-20:]
        )

    art = store.get(output_id)
    if art is None:
        return f"[找不到 artifact: {output_id}]"

    if parsed.selector_lines or (
        show_line_number and art.content_type not in ("image", "multimodal")
    ):
        return _apply_selector_to_text(art.content, parsed.selector_lines, show_line_number=show_line_number)

    # Image/multimodal artifacts: return plain text if requested
    if force_plain_text and art.content_type in ("image", "multimodal"):
        return art.content or f"[图片 artifact: {output_id}]"

    return art.get()


async def _read_memory(parsed, *, force_plain_text: bool = False, show_line_number: bool = False, with_metadata: bool = False) -> str:
    try:
        from faust_backend.memory import get_memory
    except ImportError:
        return "(记忆模块不可用)"

    store = get_memory()
    path = parsed.path

    # Check if this is an image attachment
    import asyncio as _asyncio

    nid = _path_id(path)
    node_type = store._get_node_attr(nid, "type", "") if path and store._has_node(nid) else ""
    if path and store._has_node(nid):
        ct = store._get_node_attr(nid, "content_type", "")
        if ct.startswith("image/"):
            try:
                result = await store.attachment_read(path)
            except Exception as e:
                return f"读取记忆图片出错: {e}"
            desc = result.get("description") or f"记忆图片: {path}"
            if force_plain_text:
                return f"[图片附件: {path}]\n描述: {desc}\n类型: {result.get('content_type', '')}"
            import json as _json

            payload = {
                "kind": "multimodal_tool_result",
                "text": desc,
                "images": [
                    {
                        "url": f"data:{result.get('content_type', 'image/png')};base64,{result.get('content_base64', '')}"
                    }
                ],
            }
            return _json.dumps(payload, ensure_ascii=False)

    # 空路径 / 显式目录 / 目标实际类型是目录（路径未以 / 结尾也算）→ 列目录
    if not path or parsed.is_dir or node_type == "dir":
        try:
            tree = await store.tree_list(path or "/", include_metadata=with_metadata,
                                         include_line_count=with_metadata)
            return _format_tree(tree, with_metadata=with_metadata)
        except Exception as e:
            return f"读取记忆树出错: {e}"

    # document read
    try:
        result = await store.file_read(path)
    except FileNotFoundError:
        return f"[记忆文档不存在: {path}]"
    except Exception as e:
        return f"读取记忆文档出错: {e}"

    content = result.get("content", "")
    if parsed.selector_lines or show_line_number:
        return _apply_selector_to_text(content, parsed.selector_lines, show_line_number=show_line_number)
    return content


def _read_skill(parsed, *, force_plain_text: bool = False, show_line_number: bool = False, with_metadata: bool = False) -> str:
    from faust_backend.runtime import state

    raw_path = str(parsed.path or "").strip("/")
    if not raw_path:
        # 列出所有 skill
        skill_root = Path(state.AGENT_ROOT) / "skill.d"
        if not skill_root.exists():
            return "(没有可用的 skill)"
        names = sorted(d for d in skill_root.iterdir() if d.is_dir())
        if not names:
            return "(没有可用的 skill)"
        lines = ["skill:// 可用 skill:"]
        for d in names:
            lines.append(f"  skill://{d.name}/")
            if with_metadata:
                from faust_backend.skill_manager import _read_skill_meta

                desc = str(_read_skill_meta(d).get("description") or "").strip()
                if desc:
                    lines.append(f"    [{desc}]")
        return "\n".join(lines)

    parts = [part for part in raw_path.split("/") if part]
    skill_name = parts[0] if parts else ""
    if not skill_name:
        return "[skill 名称不能为空]"

    relative_parts = parts[1:]
    if any(part in (".", "..") for part in relative_parts):
        return "[不允许越界访问 skill 目录]"

    skill_root = Path(state.AGENT_ROOT) / "skill.d" / skill_name
    if not skill_root.is_dir():
        return f"[skill 不存在: {skill_name}]"

    # 无子路径（skill://name，带不带结尾 / 都算）或目标子路径本身是目录 → 列目录
    target_path: Path | None = None
    is_dir_target = True
    if relative_parts:
        target_path = (skill_root / Path(*relative_parts)).resolve()
        if not str(target_path).startswith(str(skill_root.resolve())):
            return "[不允许访问 skill 目录外的文件]"
        is_dir_target = target_path.is_dir()

    if is_dir_target:
        dir_path = target_path or skill_root
        dir_label = f"{skill_name}/{'/'.join(relative_parts)}" if relative_parts else skill_name
        items = sorted(dir_path.iterdir())
        files = []
        dirs = []
        for item in items:
            if item.name.startswith("."):
                continue
            if item.is_dir():
                dirs.append(item)
            else:
                files.append(item)
        lines = [f"skill://{dir_label}/ 内容:"]
        lines += [f"  skill://{dir_label}/{d.name}/" for d in dirs]
        for f in files:
            lines.append(f"  skill://{dir_label}/{f.name}")
            if with_metadata:
                meta = _file_metadata_line(f)
                if meta:
                    lines.append(f"    [{meta}]")
        return "\n".join(lines)

    assert target_path is not None
    if not target_path.exists():
        return f"[skill 文件不存在: {skill_name}/{'/'.join(relative_parts)}]"

    file_uri = str(target_path)
    if parsed.selector:
        file_uri += parsed.selector
    return _read_file(
        parse(file_uri),
        force_plain_text=force_plain_text,
        show_line_number=show_line_number,
    )


async def _vfs_entry_lines(vfs, child_path: str, label: str, *, with_metadata: bool) -> list[str]:
    """渲染一个 VFS 列举条目：名称行 + 可选的缩进 [描述] 行。"""
    suffix = "/" if await vfs.is_dir(child_path) else ""
    lines = [f"  {label}{suffix}"]
    if with_metadata:
        node = await vfs.get_node(child_path)
        desc = (node.description if node is not None else "").strip()
        if desc:
            lines.append(f"    [{desc}]")
    return lines


async def _read_faustbot(parsed, *, force_plain_text: bool = False, show_line_number: bool = False, with_metadata: bool = False) -> str:
    del force_plain_text
    raw_path = str(parsed.path or "").strip("/")
    vfs = await get_faustbot_vfs(refresh=True)
    await refresh_runtime_nodes(vfs)
    if not raw_path:
        items = await vfs.list_dir("/") or []
        lines = ["faustbot:// 可用资源:"]
        for item in items:
            lines += await _vfs_entry_lines(vfs, "/" + item, f"faustbot://{item}", with_metadata=with_metadata)
        return "\n".join(lines)

    normalized = "/" + raw_path
    if await vfs.is_dir(normalized):
        items = await vfs.list_dir(normalized) or []
        lines = [f"faustbot://{raw_path}/ 内容:"]
        for item in items:
            child_path = normalized.rstrip("/") + "/" + item
            lines += await _vfs_entry_lines(vfs, child_path, f"faustbot://{raw_path}/{item}", with_metadata=with_metadata)
        return "\n".join(lines)

    content = await vfs.read_text(normalized, default="")
    if not content:
        return f"[未知 faustbot 资源: {raw_path}]"
    return _apply_selector_to_text(content, parsed.selector_lines, show_line_number=show_line_number)


def _repo_root() -> Path:
    """源码根目录 = backend/ 的父目录（仓库根）。"""
    return Path(conf.PROJECT_ROOT).parent


def _read_source_code(parsed, *, force_plain_text: bool = False, show_line_number: bool = False, with_metadata: bool = False) -> str:
    """读取 FaustBot 仓库源码：sourceCode://{path}。

    文件 → 与 read 普通文件一致（结构化摘要 / 行范围 / 全文）；
    目录（含尾斜杠或空路径）→ 自动列出目录内容。
    路径被限制在仓库根内，禁止 .. 越界。
    """
    repo_root = _repo_root()
    raw_path = str(parsed.path or "").strip("/")
    rel_parts = [p for p in raw_path.split("/") if p]

    if not rel_parts:
        # 根目录：列出一级条目
        lines = ["sourceCode:// 仓库根 内容:"]
        for item in sorted(repo_root.iterdir()):
            if item.name.startswith("."):
                continue
            if item.is_dir():
                lines.append(f"  sourceCode://{item.name}/")
            else:
                lines.append(f"  sourceCode://{item.name}")
                if with_metadata:
                    meta = _file_metadata_line(item)
                    if meta:
                        lines.append(f"    [{meta}]")
        return "\n".join(lines)

    if any(p in (".", "..") for p in rel_parts):
        return "[不允许越界访问源码目录]"

    target = (repo_root / Path(*rel_parts)).resolve()
    if not str(target).startswith(str(repo_root.resolve())):
        return "[不允许访问源码根目录外的文件]"

    if target.is_dir():
        items = sorted(target.iterdir())
        dirs, files = [], []
        for item in items:
            if item.name.startswith("."):
                continue
            if item.is_dir():
                dirs.append(item.name + "/")
            else:
                files.append(item.name)
        lines = [f"sourceCode://{raw_path}/ 内容:"]
        lines += [f"  sourceCode://{raw_path}/{d}" for d in dirs]
        for f in files:
            lines.append(f"  sourceCode://{raw_path}/{f}")
            if with_metadata:
                meta = _file_metadata_line(target / f)
                if meta:
                    lines.append(f"    [{meta}]")
        return "\n".join(lines)

    if not target.exists():
        return f"[源码文件不存在: {raw_path}]"

    file_uri = str(target)
    if parsed.selector:
        file_uri += parsed.selector
    return _read_file(
        parse(file_uri),
        force_plain_text=force_plain_text,
        show_line_number=show_line_number,
    )


def _read_img_source(parsed, *, force_plain_text: bool = False, show_line_number: bool = False, with_metadata: bool = False) -> str:
    path = str(parsed.path or "").strip("/")

    if not path or parsed.is_dir:
        return 'img_source:// 可用资源:\n  img_source://screenshot\n  img_source://camera_0\n使用 read("img_source://screenshot?grid=true&scale=0.5") 截图，使用 read("img_source://camera_0") 访问摄像头。'

    try:
        if path == "screenshot":
            image = _capture_screenshot_image()
            image = _apply_img_source_transforms(image, parsed.query)
            return _image_to_tool_result(
                image,
                description=f"屏幕截图: {image.width}x{image.height}",
                force_plain_text=force_plain_text,
                metadata={
                    "grid": _query_flag(parsed.query, "grid", False),
                    "scale": _query_scale(parsed.query),
                },
            )

        if path.startswith("camera_"):
            camera_id = _parse_camera_id(path)
            image = _capture_camera_image(camera_id)
            image = _apply_img_source_transforms(image, parsed.query)
            return _image_to_tool_result(
                image,
                description=f"摄像头 {camera_id}: {image.width}x{image.height}",
                force_plain_text=force_plain_text,
                metadata={
                    "camera_id": camera_id,
                    "grid": _query_flag(parsed.query, "grid", False),
                    "scale": _query_scale(parsed.query),
                },
            )
    except Exception as e:
        return f"读取图像源出错: {e}"

    return f"[未知 img_source 资源: {path}]"


def _capture_screenshot_image() -> Image.Image:
    image = pyautogui.screenshot()
    if not isinstance(image, Image.Image):
        raise RuntimeError("截图返回了无效图像对象")
    return image.convert("RGBA")


def _capture_camera_image(camera_id: int) -> Image.Image:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("OpenCV 未安装，无法读取摄像头") from exc

    cap = cv2.VideoCapture(camera_id)
    try:
        ok, frame = cap.read()
    finally:
        cap.release()
    if not ok or frame is None:
        raise RuntimeError(f"无法打开摄像头 {camera_id}")
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb).convert("RGBA")


def _apply_img_source_transforms(
    image: Image.Image, query: dict[str, list[str]]
) -> Image.Image:
    scale = _query_scale(query)
    if scale < 1.0:
        width = max(1, int(image.width * scale))
        height = max(1, int(image.height * scale))
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    if _query_flag(query, "grid", False):
        image = _overlay_grid(image)
    return image


def _query_flag(query: dict[str, list[str]], key: str, default: bool) -> bool:
    values = query.get(key) or []
    if not values:
        return default
    value = str(values[-1]).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"无效的布尔参数 {key}: {values[-1]}")


def _query_scale(query: dict[str, list[str]]) -> float:
    values = query.get("scale") or []
    if not values:
        return 1.0
    try:
        scale = float(values[-1])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"无效的 scale 参数: {values[-1]}") from exc
    if not (0 < scale <= 1):
        raise ValueError("scale 必须满足 0 < scale <= 1")
    return scale


def _overlay_grid(image: Image.Image, step: int = 64) -> Image.Image:
    draw = ImageDraw.Draw(image)
    color = (180, 180, 180, 180)
    for x in range(0, image.width, step):
        draw.line((x, 0, x, image.height), fill=color, width=1)
    for y in range(0, image.height, step):
        draw.line((0, y, image.width, y), fill=color, width=1)
    return image


def _parse_camera_id(path: str) -> int:
    try:
        return int(path.split("_", 1)[1])
    except Exception as exc:
        raise ValueError(f"无效的摄像头路径: {path}") from exc


def _image_to_tool_result(
    image: Image.Image,
    *,
    description: str,
    force_plain_text: bool,
    metadata: dict | None = None,
) -> str:
    metadata = metadata or {}
    with BytesIO() as buf:
        image.save(buf, format="PNG")
        raw = buf.getvalue()
    if force_plain_text:
        meta_text = "\n".join(f"{key}: {value}" for key, value in metadata.items())
        suffix = f"\n{meta_text}" if meta_text else ""
        return f"[{description}]\n大小: {len(raw)} bytes\n类型: image/png{suffix}"
    import base64

    payload = {
        "kind": "multimodal_tool_result",
        "text": description,
        "images": [
            {"url": f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"}
        ],
    }
    if metadata:
        payload["meta"] = metadata
    return json.dumps(payload, ensure_ascii=False)


def _read_task_section(section_title: str) -> str:
    from faust_backend.runtime import state

    task_path = Path(state.AGENT_ROOT) / "TASK.md"
    if not task_path.exists():
        return f"[找不到 TASK.md: {task_path}]"
    content = task_path.read_text(encoding="utf-8", errors="replace")
    extracted = _extract_markdown_section(content, section_title)
    return extracted or f"[TASK.md 中找不到章节: {section_title}]"


def _extract_markdown_section(content: str, section_title: str) -> str:
    lines = content.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == section_title.strip():
            start = idx
            break
    if start is None:
        return ""

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("## "):
            end = idx
            break
    return "\n".join(lines[start:end]).strip()


def _apply_selector_to_text(
    content: str,
    selector_lines: tuple[int, int] | None,
    *,
    show_line_number: bool = False,
) -> str:
    lines = content.split("\n")

    def _numbered(selected: list[str], first_line_no: int) -> str:
        return "\n".join(
            f"{first_line_no + i}:{line}"
            for i, line in enumerate(selected)
        )

    if not selector_lines:
        # 无行范围（整篇读取 / `:raw`）：开关打开时给全文编号
        if not content or not show_line_number:
            return content
        return _numbered(lines, 1)

    start, end = selector_lines

    def _resolve(line_no: int) -> int:
        if line_no < 0:
            return len(lines) + line_no + 1
        return line_no

    resolved_start = max(1, _resolve(start))
    resolved_end = max(1, _resolve(end))
    if resolved_start > resolved_end:
        resolved_start, resolved_end = resolved_end, resolved_start
    resolved_start = min(resolved_start, len(lines))
    resolved_end = min(resolved_end, len(lines))
    selected = lines[resolved_start - 1 : resolved_end]
    if show_line_number:
        # 行号始终为文件中的绝对行号（首行=1），与选择器写法无关
        return _numbered(selected, resolved_start)
    return "\n".join(selected)


def _read_file(parsed, *, force_plain_text: bool = False, show_line_number: bool = False, with_metadata: bool = False) -> str:
    path_str = parsed.path

    # Empty path → current directory
    if not path_str:
        path_str = "."

    file_path = Path(path_str)

    # Directory
    if parsed.is_dir or (file_path.exists() and file_path.is_dir()):
        return _list_directory(file_path, with_metadata=with_metadata)

    # File
    if not file_path.exists():
        # Try as a relative path from agent workdir first, then source root.
        from faust_backend.config_loader import WORKDIR_ROOT, PROJECT_ROOT

        for base in (WORKDIR_ROOT, PROJECT_ROOT):
            alt = Path(base) / path_str
            if alt.exists():
                file_path = alt
                break
        else:
            return f"[文件不存在: {path_str}]"

    # Image file detection
    if file_path.suffix.lower() in IMAGE_EXTENSIONS:
        return _read_image(file_path, force_plain_text=force_plain_text)

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"读取文件出错: {e}"

    if parsed.selector_lines:
        return _apply_selector_to_text(content, parsed.selector_lines, show_line_number=show_line_number)

    # `:raw` → 关闭结构化摘要，返回原文（长文件仍按普通文本截断）
    if parsed.is_raw:
        # 先编号再截断，截断提示行不带行号
        return _truncate_long(
            _apply_selector_to_text(content, None, show_line_number=show_line_number)
        )

    # For code files, return structural summary
    if file_path.suffix.lower() in SOURCE_EXTENSIONS:
        return _structural_summary(content, str(file_path))
    return _truncate_long(content)


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size // 1024}KB"
    return f"{size / (1024 * 1024):.1f}MB"


def _count_source_lines(path: Path) -> int | None:
    """白名单源码文件（≤2MB 且 UTF-8 可解码）返回行数，否则 None。"""
    if path.suffix.lower() not in SOURCE_EXTENSIONS:
        return None
    try:
        if path.stat().st_size > MAX_METADATA_LINE_SCAN:
            return None
        return len(path.read_text(encoding="utf-8").splitlines())
    except (OSError, UnicodeDecodeError):
        return None


def _file_metadata_line(path: Path) -> str:
    """条目元数据 `[大小, N lines, MM/DD]`；stat 失败返回空串。"""
    try:
        st = path.stat()
    except OSError:
        return ""
    parts = [_format_size(st.st_size)]
    line_count = _count_source_lines(path)
    if line_count is not None:
        parts.append(f"{line_count} lines")
    parts.append(time.strftime("%m/%d", time.localtime(st.st_mtime)))
    return ", ".join(parts)


def _list_directory(dir_path: Path, *, with_metadata: bool = False) -> str:
    """Return a dirent list; with_metadata adds an indented metadata line per file."""
    try:
        entries = sorted(
            dir_path.iterdir(), key=lambda e: (e.is_file(), e.name.lower())
        )
    except Exception as e:
        return f"列出目录出错: {e}"
    lines = []
    for entry in entries:
        is_dir = entry.is_dir()
        lines.append(f"  {entry.name}{'/' if is_dir else ''}")
        if with_metadata and not is_dir:
            meta = _file_metadata_line(entry)
            if meta:
                lines.append(f"    [{meta}]")
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
            if (
                stripped.endswith(('"""', "'''"))
                or stripped.count('"""') >= 1
                or stripped.count("'''") >= 1
            ):
                in_docstring = False
            continue
        # Capture top-level declarations
        if stripped.startswith(
            (
                "def ",
                "class ",
                "async def ",
                "import ",
                "from ",
                "const ",
                "let ",
                "function ",
                "export ",
            )
        ):
            result.append(f"{i}: {stripped}")
        else:
            for i in [
                "def",
                "class",
                "async def",
                "import",
                "from",
                "const",
                "let",
                "function",
                "export",
                "include",
                "require",
                "public",
                "private",
                "protected",
                "interface",
                "struct",
                "enum",
            ]:
                if i in stripped:
                    result.append(f"{i}: {stripped}")
                    break
    if not result:
        return _truncate_long(content)
    summary = "\n".join(result)
    total = len(lines)
    footer = f'\n[文件 {path}: {total} 行, 显示结构摘要。用 read("{path}:N-M") 查看具体行范围或用read("{path}:raw")关闭结构摘要]'
    return summary + footer


def _truncate_long(content: str, max_lines: int = 300) -> str:
    lines = content.split("\n")
    if len(lines) <= max_lines:
        return content
    return "\n".join(lines[:max_lines]) + f"\n[... 共 {len(lines)} 行, 已截断]"


def _iso_to_mmdd(value: str) -> str:
    """ISO 时间戳 → 本地 MM/DD；无法解析返回空串。"""
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    return dt.astimezone().strftime("%m/%d")


def _memory_entry_metadata(node: dict) -> str:
    """记忆文件条目的 `[MM/DD, N lines, #tag...]`；无可用字段返回空串。"""
    parts: list[str] = []
    updated = str(node.get("updated_at") or "").strip()
    if updated:
        mmdd = _iso_to_mmdd(updated)
        if mmdd:
            parts.append(mmdd)
    line_count = node.get("line_count")
    if isinstance(line_count, int):
        parts.append(f"{line_count} lines")
    tags = [str(t).strip() for t in (node.get("tags") or []) if str(t).strip()]
    parts += [f"#{t}" for t in tags[:3]]
    return ", ".join(parts)


def _format_tree(tree: dict, indent: int = 0, *, with_metadata: bool = False) -> str:
    """Format a memory tree node into a text listing.

    只列出当前层的直接子项（类似 ``ls``），不再递归展开整个子树：
    - 目录显示为 ``name/``
    - 文件显示为 ``name``，with_metadata 时下一行补充 ``[MM/DD, N lines, #tag]``
    """
    result = []
    name = tree.get("name", "/")
    prefix = "  " * indent
    result.append(f"{prefix}{name}/")
    for child in tree.get("children", []):
        if not isinstance(child, dict):
            continue
        ctype = child.get("type", "")
        cname = child.get("name", "?")
        if ctype == "dir":
            result.append(f"{prefix}  {cname}/")
        else:
            result.append(f"{prefix}  {cname}")
            if with_metadata:
                meta = _memory_entry_metadata(child)
                if meta:
                    result.append(f"{prefix}    [{meta}]")
    return "\n".join(result)


def _read_image(path: Path, *, force_plain_text: bool = False) -> str:
    """Read an image file and return a multimodal JSON string or plain text."""
    raw = path.read_bytes()
    if force_plain_text:
        return f"[图片文件: {path.name}]\n大小: {len(raw)} bytes\n类型: {path.suffix}"
    import base64, json

    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".svg": "image/svg+xml",
    }
    mime = mime_map.get(path.suffix.lower(), "image/png")
    b64 = base64.b64encode(raw).decode("ascii")
    payload = {
        "kind": "multimodal_tool_result",
        "text": f"图片文件: {path.name} ({len(raw)} bytes)",
        "images": [{"url": f"data:{mime};base64,{b64}"}],
    }
    return json.dumps(payload, ensure_ascii=False)
