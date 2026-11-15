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
from faust_backend.core.log import get_logger
from faust_backend.tools.vfs import get_faustbot_vfs, refresh_runtime_nodes

log = get_logger("faust.tools.write")


@register
@tool
async def write(path: str, content: str) -> str:
    r"""创建或覆盖文件；路径前缀决定写到哪。

    - 普通路径 → 磁盘。相对路径基于 agent 工作目录，父目录自动创建；
      只允许写项目/配置根目录内，越界会被拒绝。
    - memory:// → 记忆库（自动分块 + 嵌入 + 索引，之后可被 search 语义检索）。
    - faustbot:// → 触发节点 write_handler；handler 返回非空字符串时会以换行 + `↳ ` 追加到
      工具输出，这是插件向你回执的唯一通道（例如提交任务后拿到 task id）。

    怎么选：要被别的程序读/执行（代码、配置）→ 磁盘；只是给自己以后检索的知识 → memory://。

    Args:
        path (str): 目标路径；memory:// 或 faustbot:// 前缀切换后端。
        content (str): 完整内容（纯文本，覆盖式写入）。
    """
    path_str = str(path or "").strip()
    if not path_str:
        return "错误: path 不能为空"

    content = str(content or "")
    raw = path_str
    log.info("write INPUT path=%s content_len=%d", path_str, len(content))

    from faust_backend.core.uri import (
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
    from faust_backend.core.config import PROJECT_ROOT, CONFIG_ROOT, WORKDIR_ROOT

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
    """写入 faustbot:// 节点；节点 write_handler 的非空字符串返回值以 `↳ ` 追加为 ACK。

    契约见 AsyncVirtualFileSystem.write()：只有非空 str 会作为 ACK 返回，
    其余类型（None / dict / …）一律视为"无 ACK"。
    """
    if not path:
        return "错误: faustbot:// 路径不能为空"
    vfs = await get_faustbot_vfs(refresh=True)
    await refresh_runtime_nodes(vfs)
    target_path = "/" + path.strip("/")
    try:
        ack = await vfs.write(target_path, content)
    except Exception as e:
        return f"写入 faustbot 资源出错: {e}"
    size = len(content.encode("utf-8"))
    result = f"已写入 faustbot://{path} ({size} bytes)"
    if ack:
        result += f"\n↳ {ack}"
    return result


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
