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


@register
@tool
def kbListTool(scope: str = "") -> str:
    """[已弃用] 列出知识库树 → 请使用 read("memory://") 或 find(["memory://**"]).
    直接转发给 read 工具。
    """
    from faust_backend.tools.read import read
    return read(f"memory://{scope}" if scope else "memory://")


@register
@tool
def kbReadTool(path: str) -> str:
    """[已弃用] 读取知识库节点 → 请使用 read("memory://path")。
    直接转发给 read 工具。
    """
    from faust_backend.tools.read import read
    return read(f"memory://{path}")


@register
@tool
def kbWriteTool(path: str, content: str, declared_by: str = "agent", index: bool = True, tags_json: str = "[]") -> str:
    """[已弃用] 写入知识库 → 请使用 write("memory://path", content)。
    直接转发给 write 工具。
    """
    from faust_backend.tools.write import write
    return write(f"memory://{path}", content)


# @register
# @tool
# def kbSearchTool(query: str, scope: str = "", top_k: int = 8, return_mode: str = "snippets", tags_json: str = "[]", ignore_score_patch: bool = False) -> str:
#     """在知识库指定范围内做向量检索。"""
#     from faust_backend.memory.tools import memorySearchTool as _impl
#     return _impl(query, scope, top_k, return_mode, tags_json, True)


@register
@tool
def kbTagSetTool(path: str, tags_json: str, managed_by: str = "agent") -> str:
    """为知识库文档设置标签。"""
    try:
        tags = json.loads(tags_json) if str(tags_json or "").strip() else []
        from faust_backend.memory import get_memory
        m = get_memory()
        result = asyncio.run(m.set_tags(path, tags))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
def kbScorePatchTool(path: str, score_patch: float, managed_by: str = "agent") -> str:
    """为知识库文档设置 score patch，范围为 -0.15 到 +0.15。"""
    try:
        from faust_backend.memory import get_memory
        result = asyncio.run(get_memory().set_score_patch(path, score_patch))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
def kbChangedNodesTool(since_ts: float, scope: str = "", tags_json: str = "[]") -> str:
    """获取某个时间戳之后发生变更的知识库节点。"""
    try:
        from faust_backend.memory import get_memory
        items = asyncio.run(get_memory().get_changed_nodes(since_ts, scope=scope))
        return json.dumps(items, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)}, ensure_ascii=False)


@register
@tool
def memorySearchTool(query: str, scope: str = "", top_k: int = 5,
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
    return _memorySearchTool(query, scope, top_k, return_mode, tags_json, use_graph)
