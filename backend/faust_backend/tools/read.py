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
import sys
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
)
from faust_backend.runtime.output_store import get_output_store
from faust_backend.memory.store import _path_id
from faust_backend.logger import get_logger

log = get_logger("faust.tools.read")

IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"})


@register
@tool
def read(uri: str, *, force_plain_text: bool = False) -> str:
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

    **Reading system resources (faustbot://):**
    - `read("faustbot://")` → list all available faustbot resources (index.md, tool_use.md, mc.md, pc_info, source/).
    - `read("faustbot://index.md")` → read the faustbot index.
    - `read("faustbot://pc_info")` → read system information.
    - `read("faustbot://source/{PATH}")` → read project source files.
    - Use this when: you need system info, tool usage guides, or project source code.

    **Reading skills (skill://):**
    - `read("skill://")` → list all available skill names.
    - `read("skill://{name}/")` → list files in a skill directory.
    - `read("skill://{name}/SKILL.md")` → read a skill's main file.
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

    Args:
        uri: Path or URI with optional :line-selector suffix.
        force_plain_text: If True, images and multimodal artifacts return only
                          text description (no base64 data). Defaults to False.

    Returns:
        For files: structural summary (code) or first 300 lines; or specified range.
        For images: multimodal JSON with base64 (unless force_plain_text=True).
        For directories: list of entries.
        For artifacts: full or ranged tool output.
        For memory: document content or file tree.
        For faustbot://: system resources and project source code.
        For skill://: skill files and directory listings.
        For img_source://: screenshot or camera images (multimodal).
    """
    log.info("read INPUT uri=%s force_plain_text=%s", uri, force_plain_text)
    parsed = parse(uri)
    log.debug("read parsed: scheme=%s path=%r selector=%r force_plain_text=%r",
              parsed.scheme, parsed.path, parsed.selector, force_plain_text)

    if parsed.scheme == SCHEME_ARTIFACT:
        result = _read_artifact(parsed, force_plain_text=force_plain_text)
        log.info("read OUTPUT len=%d", len(result))
        return result
    elif parsed.scheme == SCHEME_MEMORY:
        result = _read_memory(parsed, force_plain_text=force_plain_text)
        log.info("read OUTPUT len=%d", len(result))
        return result
    elif parsed.scheme == SCHEME_SKILL:
        result = _read_skill(parsed, force_plain_text=force_plain_text)
        log.info("read OUTPUT len=%d", len(result))
        return result
    elif parsed.scheme == SCHEME_FAUSTBOT:
        result = _read_faustbot(parsed, force_plain_text=force_plain_text)
        log.info("read OUTPUT len=%d", len(result))
        return result
    elif parsed.scheme == SCHEME_IMG_SOURCE:
        result = _read_img_source(parsed, force_plain_text=force_plain_text)
        log.info("read OUTPUT len=%d", len(result))
        return result
    else:
        result = _read_file(parsed, force_plain_text=force_plain_text)
        log.info("read OUTPUT len=%d", len(result))
        return result


def _read_artifact(parsed, *, force_plain_text: bool = False) -> str:
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

    # Image/multimodal artifacts: return plain text if requested
    if force_plain_text and art.content_type in ("image", "multimodal"):
        return art.content or f"[图片 artifact: {output_id}]"

    return art.get()


def _read_memory(parsed, *, force_plain_text: bool = False) -> str:
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
            desc = result.get("description") or f"记忆图片: {path}"
            if force_plain_text:
                return f"[图片附件: {path}]\n描述: {desc}\n类型: {result['content_type']}"
            import json as _json
            payload = {
                "kind": "multimodal_tool_result",
                "text": desc,
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


def _read_skill(parsed, *, force_plain_text: bool = False) -> str:
    del force_plain_text
    from faust_backend.runtime import state

    raw_path = str(parsed.path or "").strip("/")
    if not raw_path:
        # 列出所有 skill
        skill_root = Path(state.AGENT_ROOT) / "skill.d"
        if not skill_root.exists():
            return "(没有可用的 skill)"
        names = sorted(d.name for d in skill_root.iterdir() if d.is_dir())
        if not names:
            return "(没有可用的 skill)"
        lines = ["skill:// 可用 skill:"]
        lines += [f"  skill://{name}/" for name in names]
        return "\n".join(lines)

    parts = [part for part in raw_path.split("/") if part]
    skill_name = parts[0] if parts else ""
    if not skill_name:
        return "[skill 名称不能为空]"

    relative_parts = parts[1:] or ["SKILL.md"]
    # 如果路径以 / 结尾（显式目录请求），或只有 skill_name 后空格默认为目录
    if raw_path.endswith("/") or (len(parts) == 1 and parsed.is_dir):
        skill_root_dir = Path(state.AGENT_ROOT) / "skill.d" / skill_name
        if not skill_root_dir.is_dir():
            return f"[skill 不存在: {skill_name}]"
        items = sorted(skill_root_dir.iterdir())
        files = []
        dirs = []
        for item in items:
            if item.name.startswith("."): continue
            if item.is_dir():
                dirs.append(item.name + "/")
            else:
                files.append(item.name)
        lines = [f"skill://{skill_name}/ 内容:"]
        lines += [f"  skill://{skill_name}/{d}" for d in dirs]
        lines += [f"  skill://{skill_name}/{f}" for f in files]
        return "\n".join(lines)

    if any(part in (".", "..") for part in relative_parts):
        return "[不允许越界访问 skill 目录]"

    skill_root = Path(state.AGENT_ROOT) / "skill.d" / skill_name
    target_path = (skill_root / Path(*relative_parts)).resolve()
    try:
        skill_root_resolved = skill_root.resolve()
    except FileNotFoundError:
        skill_root_resolved = skill_root
    if not str(target_path).startswith(str(skill_root_resolved)):
        return "[不允许访问 skill 目录外的文件]"
    if not target_path.exists():
        return f"[skill 文件不存在: {skill_name}/{'/'.join(relative_parts)}]"

    file_uri = str(target_path)
    if parsed.selector:
        file_uri += parsed.selector
    return _read_file(parse(file_uri))


def _read_faustbot(parsed, *, force_plain_text: bool = False) -> str:
    del force_plain_text
    path = str(parsed.path or "").strip("/")

    if not path or parsed.is_dir:
        # 列出所有 faustbot 可用资源
        items = [
            "index.md",
            "tool_use.md",
            "mc.md",
            "pc_info",
            "source/",
        ]
        lines = ["faustbot:// 可用资源:"]
        lines += [f"  faustbot://{item}" for item in items]
        return "\n".join(lines)

    if path == "index.md":
        content = "\n".join([
            "# faustbot:// 只读索引",
            "",
            "可读取资源：",
            "- faustbot://index.md",
            "- faustbot://tool_use.md",
            "- faustbot://mc.md",
            "- faustbot://pc_info",
            "- faustbot://source/{PATH}",
            "",
            "使用说明：",
            "- 先读 faustbot://index.md 了解可用内容。",
            "- 想看工具使用规范，读取 faustbot://tool_use.md。",
            "- 想看 Minecraft 指南，读取 faustbot://mc.md。",
            "- 想只读源码，读取 faustbot://source/{PATH}。",
        ])
        return _apply_selector_to_text(content, parsed.selector_lines)

    if path == "tool_use.md":
        content = _read_task_section("## 核心工具速查")
        return _apply_selector_to_text(content, parsed.selector_lines)

    if path == "mc.md":
        content = _read_task_section("## Minecraft 操作系统说明")
        return _apply_selector_to_text(content, parsed.selector_lines)

    if path == "pc_info":
        info = "\n".join([
            "# pc_info",
            f"username: {getpass.getuser()}",
            f"platform: {platform.platform()}",
            f"python: {sys.version.split()[0]}",
            f"cwd: {os.getcwd()}",
        ])
        return _apply_selector_to_text(info, parsed.selector_lines)

    if path.startswith("source/"):
        return _read_faustbot_source(parsed)

    return f"[未知 faustbot 资源: {path}]"


def _read_faustbot_source(parsed) -> str:
    source_rel = str(parsed.path or "").strip("/")[len("source/"):]
    if not source_rel:
        return _list_directory(_get_faustbot_source_root())
    rel_path = Path(source_rel)
    if any(part in (".", "..") for part in rel_path.parts):
        return "[不允许访问 source 根目录外的路径]"
    source_root = _get_faustbot_source_root().resolve()
    target_path = (source_root / rel_path).resolve()
    if not str(target_path).startswith(str(source_root)):
        return "[不允许访问 source 根目录外的路径]"
    if not target_path.exists():
        return f"[source 文件不存在: {source_rel}]"
    file_uri = str(target_path)
    if parsed.selector:
        file_uri += parsed.selector
    return _read_file(parse(file_uri))


def _read_img_source(parsed, *, force_plain_text: bool = False) -> str:
    path = str(parsed.path or "").strip("/")

    if not path or parsed.is_dir:
        return "img_source:// 可用资源:\n  img_source://screenshot\n  img_source://camera_0\n使用 read(\"img_source://screenshot?grid=true&scale=0.5\") 截图，使用 read(\"img_source://camera_0\") 访问摄像头。"

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


def _get_faustbot_source_root() -> Path:
    from faust_backend.config_loader import PROJECT_ROOT

    backend_root = Path(PROJECT_ROOT)
    repo_root = backend_root.parent
    if getattr(sys, "frozen", False):#FIXME:使用正确的方式查询是否打包
        mirror_root = backend_root / "data" / "source"
        if mirror_root.exists():
            return mirror_root
        raise FileNotFoundError("源码镜像未生成: backend/data/source")
    return repo_root


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


def _apply_img_source_transforms(image: Image.Image, query: dict[str, list[str]]) -> Image.Image:
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
    scale = float(values[-1])
    if not (0 < scale < 1):
        raise ValueError("scale 必须满足 0 < scale < 1")
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
        "images": [{"url": f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"}],
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


def _apply_selector_to_text(content: str, selector_lines: tuple[int, int] | None) -> str:
    if not selector_lines:
        return content
    start, end = selector_lines
    lines = content.split("\n")
    return "\n".join(lines[start - 1:end])


def _read_file(parsed, *, force_plain_text: bool = False) -> str:
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
        return _read_image(file_path, force_plain_text=force_plain_text)

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


def _read_image(path: Path, *, force_plain_text: bool = False) -> str:
    """Read an image file and return a multimodal JSON string or plain text."""
    raw = path.read_bytes()
    if force_plain_text:
        return f"[图片文件: {path.name}]\n大小: {len(raw)} bytes\n类型: {path.suffix}"
    import base64, json
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
                ".svg": "image/svg+xml"}
    mime = mime_map.get(path.suffix.lower(), "image/png")
    b64 = base64.b64encode(raw).decode("ascii")
    payload = {
        "kind": "multimodal_tool_result",
        "text": f"图片文件: {path.name} ({len(raw)} bytes)",
        "images": [{"url": f"data:{mime};base64,{b64}"}],
    }
    return json.dumps(payload, ensure_ascii=False)
