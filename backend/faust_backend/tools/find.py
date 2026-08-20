"""
Find tool — glob-based file matching across filesystem and memory store.

The `patterns` parameter accepts globs that may include memory:// prefix:
  - src/**/*.py        → filesystem glob
  - memory://notes/*   → memory file tree match
"""

from __future__ import annotations

import glob as globmod
import re
from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.logger import get_logger

log = get_logger("faust.tools.find")


@register
@tool
def find(patterns: list[str]) -> str:
    """Find files matching glob patterns — the universal file locator.

    FILESYSTEM GLOBBING:
    - `find(["src/**/*.py"])` → all Python files under src/.
    - `find(["tests/**/*.py", "docs/**/*.md"])` → multiple patterns.
    - `find(["*.json"])` → all JSON files in the project root.
    - Results are sorted by modification time (newest first).
    - Use this when: locating all files of a certain type, listing a directory
      tree, or finding recently modified files.

    MEMORY GLOBBING:
    - `find(["memory://notes/*"])` → list documents under memory://notes/.
    - `find(["memory://*"])` → list all memory documents.
    - Use this when: browsing your memory store structure.

    FAUSTBOT VFS GLOBBING:
    - `find(["faustbot://plugins/"])` → list all nodes under faustbot://plugins/.
    - `find(["faustbot://agile/*"])` → list all agile mirror nodes.
    - `find(["faustbot://**/*.md"])` → list all .md nodes across the VFS.
    - Use this when: locating virtual nodes (plugin data, agile mirrors, resources).

    COMBINED:
    - `find(["src/**/*.py", "memory://notes/*", "faustbot://plugins/"])` → all backends.

    TIP: The output may be long.  Use read() to inspect specific files found.

    Args:
        patterns: List of glob patterns. Supports ** for recursive matching
                  and memory:// / faustbot:// prefixes for virtual stores.

    Returns:
        Sorted list of matching paths, newest first.

    """
    log.info("find INPUT patterns=%s", patterns)
    fs_globs: list[str] = []
    mem_globs: list[str] = []
    vfs_globs: list[str] = []

    from faust_backend.runtime.uri import (
        detect_unsupported_protocol,
        SCHEME_FILE,
        SCHEME_MEMORY,
        SCHEME_FAUSTBOT,
    )

    for p in patterns:
        p = str(p).strip()
        err = detect_unsupported_protocol(
            p, {SCHEME_FILE, SCHEME_MEMORY, SCHEME_FAUSTBOT}
        )
        if err:
            result = f"find: {err}"
            log.info("find OUTPUT len=%d", len(result))
            return result
        if p.startswith("memory://"):
            mem_globs.append(p[len("memory://"):].strip("/"))
        elif p.startswith("faustbot://"):
            vfs_globs.append(p[len("faustbot://"):])  # 保留尾斜杠以区分目录
        else:
            fs_globs.append(p)

    results: list[str] = []

    if fs_globs:
        results.append(_find_filesystem(fs_globs))

    if mem_globs:
        results.append(_find_memory(mem_globs))

    if vfs_globs:
        results.append(_find_faustbot(vfs_globs))

    if not results:
        result = "没有匹配任何文件"
        log.info("find OUTPUT len=%d", len(result))
        return result

    result = "\n\n".join(r for r in results if r)
    log.info("find OUTPUT len=%d", len(result))
    return result


def _find_filesystem(globs: list[str]) -> str:
    from faust_backend.config_loader import WORKDIR_ROOT
    project_root = Path(WORKDIR_ROOT)

    all_matches: list[Path] = []
    seen: set[str] = set()

    for g in globs:
        full_pattern = str(project_root / g) if not Path(g).is_absolute() else g
        try:
            matches = sorted(globmod.glob(full_pattern, recursive=True))
        except Exception:
            continue
        for m in matches:
            mp = Path(m)
            if mp.is_file() and str(mp) not in seen:
                seen.add(str(mp))
                all_matches.append(mp)

    # Sort by mtime (newest first)
    try:
        all_matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        all_matches.sort()

    if not all_matches:
        return f"[文件系统] 未匹配到文件: {', '.join(globs)}"

    lines = [f"[文件系统] {len(all_matches)} 个文件:"]
    for p in all_matches[:50]:
        try:
            rel = p.relative_to(project_root)
        except ValueError:
            rel = p
        lines.append(f"  {rel}")
    if len(all_matches) > 50:
        lines.append(f"  ... 还有 {len(all_matches) - 50} 个文件")
    return "\n".join(lines)


def _find_memory(globs: list[str]) -> str:
    try:
        from faust_backend.memory import get_memory
    except ImportError:
        return "(记忆模块不可用)"

    store = get_memory()
    results: list[str] = []

    for g in globs:
        # Strip memory:// prefix (defensive: caller may not have stripped it)
        if g.startswith("memory://"):
            g = g[len("memory://"):]
        scope = g.rstrip("*").rstrip("/") or ""
        try:
            import asyncio
            tree = asyncio.run(store.tree_list(scope if scope else "/"))
        except Exception as e:
            log.warning("find memory tree error (scope=%s): %s", scope, e)
            continue

        # Collect paths from tree
        results.extend(_extract_paths_from_tree(tree, scope))

    if not results:
        return f"[memory://] 未匹配: {', '.join(globs)}"

    lines = [f"[memory://] {len(results)} 个文档:"]
    for r in results[:50]:
        lines.append(f"  memory://{r}")
    if len(results) > 50:
        lines.append(f"  ... 还有 {len(results) - 50} 个")
    return "\n".join(lines)


def _find_faustbot(globs: list[str]) -> str:
    """在 faustbot:// 虚拟文件系统里做 glob 匹配（VFS 自带 glob，fnmatch 语义）。

    - ``faustbot://plugins/`` → 该目录下所有节点（含子目录递归）
    - ``faustbot://agile/*`` → 所有 agile 节点
    - ``faustbot://**/*.md`` → 全 VFS 的 .md 节点
    目录节点以 ``/`` 结尾标注。
    """
    try:
        from faust_backend.tools.vfs import get_faustbot_vfs, run_coro_sync
    except ImportError:
        return "(faustbot VFS 不可用)"

    vfs = get_faustbot_vfs(refresh=True)
    found: list[str] = []
    all_nodes = run_coro_sync(vfs.walk("/")) or []

    def _glob_regex(pat: str) -> re.Pattern:
        """把 glob 转成正则：``**/`` 跨任意层（含零层），``**`` 跨任意层，``*`` 不跨层。"""
        out = []
        i = 0
        n = len(pat)
        while i < n:
            ch = pat[i]
            if ch == "*":
                if i + 1 < n and pat[i + 1] == "*":
                    if i + 2 < n and pat[i + 2] == "/":
                        # **/ → 零或多个目录层
                        out.append("(?:.*/)?")
                        i += 3
                    else:
                        out.append(".*")
                        i += 2
                else:
                    out.append("[^/]*")
                    i += 1
            elif ch == "?":
                out.append("[^/]")
                i += 1
            else:
                out.append(re.escape(ch))
                i += 1
        return re.compile("^" + "".join(out) + "$")

    for g in globs:
        # faustbot://plugins/ → /plugins/**（列目录含子级）；其余按原样 glob
        norm = "/" + g.rstrip("/") if g else "/"
        if g.endswith("/") or not g:
            norm = norm.rstrip("/") + "/**" if norm != "/" else "/**"
        rx = _glob_regex(norm)
        for p in all_nodes:
            if p == "/" or not rx.match(p):
                continue
            is_dir = False
            try:
                is_dir = run_coro_sync(vfs.is_dir(p))
            except Exception:
                pass
            disp = f"faustbot://{p.strip('/')}"
            if is_dir:
                disp += "/"
            if disp not in found:
                found.append(disp)

    if not found:
        return f"[faustbot://] 未匹配: {', '.join(globs)}"

    lines = [f"[faustbot://] {len(found)} 个节点:"]
    for r in sorted(found)[:50]:
        lines.append(f"  {r}")
    if len(found) > 50:
        lines.append(f"  ... 还有 {len(found) - 50} 个")
    return "\n".join(lines)


def _extract_paths_from_tree(tree: dict, prefix: str) -> list[str]:
    """从 tree_list 返回的树里收集所有文件路径。

    tree 的每个文件节点都带准确的 ``path``（绝对路径，如 /user/facts），
    直接使用它，避免用 prefix 再拼 root 的 name 导致路径重复
    （如 user/user/facts）。
    prefix 仅用于兼容调用方，不再参与路径拼接。
    """
    del prefix
    paths: list[str] = []

    def walk(node: dict) -> None:
        if not isinstance(node, dict):
            return
        ntype = node.get("type", "")
        node_path = str(node.get("path") or "").strip("/")
        if ntype == "file" and node_path:
            paths.append(node_path)
        for child in node.get("children", []):
            walk(child)

    walk(tree)
    return paths
