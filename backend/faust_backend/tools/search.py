"""
Unified Search tool — searches filesystem (regex) and memory store (semantic/BM25).

The `paths` parameter determines the backend:
  - memory://           → GraphStore.hybrid_search
  - memory://notes      → memory store scoped to /notes
  - src/ tests/         → regex search on filesystem
  - Mixed: both backends searched, results merged
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.logger import get_logger

log = get_logger("faust.tools.search")

@register
@tool
def search(pattern: str, *, paths: list[str] | None = None) -> str:
    """Search content across filesystem and memory store in one call.

    This unifies the old memorySearchTool and filesystem grep into a single
    tool.  The `paths` argument determines which backend to use.

    SEARCHING MEMORY (memory://):
    - `search("勾股定理", paths=["memory://"])` → semantic + BM25 hybrid search
      across all memory documents.  Returns snippets with relevance scores.
    - `search("physics", paths=["memory://notes"])` → search only within
      the /notes/ scope in memory.
    - Use this when: looking up facts, notes, or knowledge you previously stored
      with write("memory://...").

    SEARCHING FILESYSTEM (bare paths):
    - `search("def main", paths=["src/"])` → regex search across all files
      under src/.  Returns matching lines with file paths and line numbers.
    - `search("class Agent", paths=["src/", "tests/"])` → search multiple dirs.
    - Use this when: finding where a function/class is defined, locating
      specific code patterns, or searching documentation files.

    COMBINED SEARCH:
    - `search("setup instructions", paths=["memory://notes", "README.md"])` →
      searches both memory and filesystem, merging results.

    TIP: search() returns summaries.  After finding something interesting,
    use read() with the specific path:line to see the full context.

    Args:
        pattern: Regex (filesystem) or natural language query (memory).
        paths: List of paths/URIs. memory:// for memory store, bare paths for filesystem.

    Returns:
        Summarized results grouped by backend with paths, snippets, and scores.
    """
    pattern = str(pattern or "").strip()
    if not pattern:
        return "错误: 搜索模式不能为空"

    if paths is None:
        paths = ["."]

    mem_scopes: list[str] = []
    fs_paths: list[str] = []

    for p in paths:
        p = str(p).strip()
        if p.startswith("memory://"):
            scope = p[len("memory://"):].strip("/") or "/"
            mem_scopes.append(f"/{scope}" if scope != "/" else "/")
        else:
            fs_paths.append(p)

    results: list[str] = []

    if mem_scopes:
        results.append(_search_memory(pattern, mem_scopes))

    if fs_paths:
        results.append(_search_filesystem(pattern, fs_paths))

    if not mem_scopes and not fs_paths:
        return "错误: 没有有效的搜索路径"

    return "\n\n".join(r for r in results if r)


def _search_memory(query: str, scopes: list[str]) -> str:
    try:
        from faust_backend.memory import get_memory
    except ImportError:
        return "(记忆模块不可用)"

    store = get_memory()
    all_results: list[dict] = []

    for scope in scopes:
        scope_arg = scope.strip("/") if scope != "/" else ""
        try:
            import asyncio
            hits = asyncio.run(store.search(query, scope=scope_arg, top_k=5,
                                            return_mode="snippets", use_graph=False))
            all_results.extend(hits)
        except Exception as e:
            log.warning("记忆搜索出错 (scope=%s): %s", scope, e)
            continue

    if not all_results:
        return f"[memory://] 未找到匹配: {query}"

    lines = [f"[memory://] {len(all_results)} 条结果:"]
    for hit in all_results[:10]:
        path = hit.get("path", "?")
        snippet = str(hit.get("snippet", hit.get("text", "")))[:120]
        score = hit.get("score", hit.get("_score", 0))
        lines.append(f"  memory://{path}  (score={score:.2f})")
        if snippet:
            lines.append(f"    {snippet}")
    return "\n".join(lines)


def _search_filesystem(pattern: str, paths: list[str]) -> str:
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return f"正则表达式错误: {e}"

    from faust_backend.config_loader import PROJECT_ROOT
    project_root = Path(PROJECT_ROOT)

    results: list[dict] = []
    max_results = 20

    for p in paths:
        search_dir = project_root / p if not Path(p).is_absolute() else Path(p)
        if not search_dir.exists():
            continue
        for root, dirs, files in os.walk(str(search_dir)):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.startswith("."):
                    continue
                fpath = Path(root) / fname
                if fpath.suffix in (".pyc", ".pyd", ".dll", ".so", ".exe", ".bin", ".png", ".jpg", ".mp3", ".wav"):
                    continue
                try:
                    content = fpath.read_text(encoding="utf-8", errors="replace")
                except Exception:
                    continue
                for i, line in enumerate(content.split("\n"), 1):
                    if regex.search(line):
                        rel_path = fpath.relative_to(project_root)
                        results.append({
                            "file": str(rel_path),
                            "line": i,
                            "text": line.strip()[:150],
                        })
                        if len(results) >= max_results:
                            break
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

    if not results:
        return f"[文件系统] 未找到匹配: {pattern}"

    lines = [f"[文件系统] {len(results)} 条结果:"]
    for r in results[:max_results]:
        lines.append(f"  {r['file']}:{r['line']}: {r['text']}")
    return "\n".join(lines)
