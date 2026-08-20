"""
Find tool — glob-based file matching across filesystem and memory store.

The `patterns` parameter accepts globs that may include memory:// prefix:
  - src/**/*.py        → filesystem glob
  - memory://notes/*   → memory file tree match
"""

from __future__ import annotations

import glob as globmod
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

    COMBINED:
    - `find(["src/**/*.py", "memory://notes/*"])` → both filesystem and memory.

    TIP: The output may be long.  Use read() to inspect specific files found.

    Args:
        patterns: List of glob patterns. Supports ** for recursive matching
                  and memory:// prefix for memory store.

    Returns:
        Sorted list of matching paths, newest first.

    """
    log.info("find INPUT patterns=%s", patterns)
    fs_globs: list[str] = []
    mem_globs: list[str] = []

    for p in patterns:
        p = str(p).strip()
        if p.startswith("memory://"):
            mem_globs.append(p[len("memory://"):].strip("/"))
        else:
            fs_globs.append(p)

    results: list[str] = []

    if fs_globs:
        results.append(_find_filesystem(fs_globs))

    if mem_globs:
        results.append(_find_memory(mem_globs))

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


def _extract_paths_from_tree(tree: dict, prefix: str) -> list[str]:
    paths: list[str] = []
    name = tree.get("name", "").strip("/")
    full = f"{prefix}/{name}" if name else prefix
    for child in tree.get("children", []):
        if isinstance(child, dict):
            ctype = child.get("type", "")
            cname = child.get("name", "?")
            cpath = f"{full}/{cname}".strip("/")
            if ctype == "dir":
                paths.extend(_extract_paths_from_tree(child, cpath))
            else:
                paths.append(cpath)
    return paths
