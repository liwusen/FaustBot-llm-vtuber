import json
import asyncio

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.memory.tools import (
    memoryListTool as _memoryListTool,
    memoryReadTool as _memoryReadTool,
    memoryWriteTool as _memoryWriteTool,
    memorySearchTool as _memorySearchTool,
)
from faust_backend.logger import get_logger

log = get_logger("faust.tools.memory")





# @register
# @tool
# def kbSearchTool(query: str, scope: str = "", top_k: int = 8, return_mode: str = "snippets", tags_json: str = "[]", ignore_score_patch: bool = False) -> str:
#     """在知识库指定范围内做向量检索。"""
#     from faust_backend.memory.tools import memorySearchTool as _impl
#     return _impl(query, scope, top_k, return_mode, tags_json, True)


@register
@tool
async def kbTagSetTool(path: str, tags_json: str, managed_by: str = "agent") -> str:
    """为知识库文档设置标签。"""
    try:
        tags = json.loads(tags_json) if str(tags_json or "").strip() else []
        from faust_backend.memory import get_memory
        m = get_memory()
        result = await m.set_tags(path, tags)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
async def kbScorePatchTool(path: str, score_patch: float, managed_by: str = "agent") -> str:
    """为知识库文档设置 score patch，范围为 -0.15 到 +0.15。"""
    try:
        from faust_backend.memory import get_memory
        result = await get_memory().set_score_patch(path, score_patch)
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
async def kbChangedNodesTool(since_ts: float, scope: str = "", tags_json: str = "[]") -> str:
    """获取某个时间戳之后发生变更的知识库节点。"""
    try:
        from faust_backend.memory import get_memory
        items = await get_memory().get_changed_nodes(since_ts, scope=scope)
        return json.dumps(items, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
async def memorySearchTool(query: str, scope: str = "", top_k: int = 5,
                           return_mode: str = "compact", tags_json: str = "[]",
                           use_graph: bool = True) -> str:
    """
    Description:
        增强记忆搜索。默认返回 compact JSON（path, line_count, description），
        并自动扩展相邻节点。你可以根据 path 自行读取文件内容。
        设置 return_mode='snippets' 可切回传统带片段的结果。
        但我们建议你使用 compact 模式并自行读取，能获得更好的上下文和更快的响应。
    Args:
        query (str): 搜索查询,支持自然语言。
        scope (str): 限定目录范围,如果不确定可留空。
        top_k (int): 返回数量，默认 5。
        return_mode (str): compact/snippets/paths/full。
        tags_json (str): JSON 标签数组，按标签过滤。
        use_graph (bool): 是否启用图谱增强搜索,建议始终启用。
    Returns:
        str(json): 搜索结果列表。compact 模式下每项含 path, line_count, description, score。
    """
    return await _memorySearchTool(query, scope, top_k, return_mode, tags_json, use_graph)


@register
@tool
async def memoryDeleteTool(path: str, recursive_dangerous: bool = False) -> str:
    """删除记忆库中的文档或目录。

    - ``path``: 要删除的记忆库路径，例如 ``/notes/todo.md`` 或 ``/notes``。
    - ``recursive_dangerous``: 当目标是**目录**时必须为 True 才会递归删除整个目录树；
      为 False 且目标是目录时拒绝删除（防止误删）。删除单个文件时无需该参数。

    Args:
        path (str): 记忆库路径（memory:// 前缀或裸路径均可）。
        recursive_dangerous (bool): 是否允许递归删除目录树。

    Returns:
        str(json): 操作结果。
    """
    try:
        from faust_backend.memory import get_memory
        m = get_memory()
        p = str(path or "").strip()
        if p.startswith("memory://"):
            p = p[len("memory://"):]
        if not p:
            return json.dumps({"status": "error", "error": "path 不能为空"}, ensure_ascii=False)
        p = "/" + p.strip("/")
        nid = None
        try:
            from faust_backend.memory.store import _path_id
            nid = _path_id(p)
        except Exception:
            nid = None
        if nid is not None and m._has_node(nid):
            ntype = m._get_node_attr(nid, "type", "file")
            if ntype == "dir" and not recursive_dangerous:
                return json.dumps(
                    {"status": "error", "error": f"{p} 是目录，删除目录需 recursive_dangerous=True"},
                    ensure_ascii=False,
                )
        result = await m.file_delete_tree(p)
        return json.dumps({"status": "ok", **result}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)
