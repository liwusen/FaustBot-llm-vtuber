from langchain.tools import tool
import os
import asyncio
import threading
from pathlib import Path

import faust_backend.config_loader as conf
os.environ["SEARCHAPI_API_KEY"] = conf.SEARCH_API_KEY
from faust_backend.logger import get_logger

log = get_logger("faust.tools.registry")

toollist = []
ORIGINAL_TOOL_FUNCS = {}
DIARY_DIR = Path(conf.CONFIG_ROOT) / "agents" / Path(conf.AGENT_NAME) / "diary"
STARTED = False

ARAYA_ALLOWED_TOOL_NAMES = {
    "arayaGetTimeTool",
    "arayaListTreeTool",
    "arayaReadFileTool",
    "arayaWriteFileTool",
    "arayaDeleteFileTool",
    "arayaSearchMemoryTool",
    "arayaSetTagsTool",
    "arayaSetScorePatchTool",
    "arayaChangedNodesTool",
    "arayaSearchEntityTool",
    "arayaListEntitiesTool",
    "arayaGetNeighborsTool",
    "arayaAddEntityTool",
    "arayaDeleteEntityTool",
    "arayaAddRelationTool",
    "arayaRemoveRelationTool",
    "arayaListRelationsTool",
    "arayaLinkEntityToFileTool",
}

DEFAULT_EXCLUDED_TOOL_NAMES = {
    "kbScorePatchTool",
    "kbChangedNodesTool",
}

VRM_ONLY_TOOL_NAMES = {
    "listVRMGesturesTool",
    "triggerVRMGestureTool",
    "setVRMLookAtTool",
}

MD_BLOCK_TOOL_NAMES = {
    "RenderMarkdownBlock",
}


def register(func):
    toollist.append(func)
    name = getattr(func, 'name', None) or getattr(func, '__name__', None) or func.__class__.__name__
    ORIGINAL_TOOL_FUNCS[name] = func
    log.debug("Registered tool: %s", name)
    return func


def _tool_func_name(tool_func) -> str:
    try:
        return tool_func.name
    except AttributeError:
        return tool_func.__name__


def get_tools_for_agent(agent_name: str | None = None):
    target = str(agent_name or conf.AGENT_NAME or "").strip().lower()
    if target == "araya":
        return [tool_func for tool_func in toollist if _tool_func_name(tool_func) in ARAYA_ALLOWED_TOOL_NAMES]
    model_type = str(getattr(conf, 'MODEL_TYPE', 'live2d') or 'live2d').strip().lower()
    md_block_excluded = set() if bool(getattr(conf, 'MD_BLOCK_ENABLED', True)) else set(MD_BLOCK_TOOL_NAMES)
    try:
        from faust_backend.live_mode import is_live_mode, get_excluded_tool_names
        if is_live_mode():
            excluded = DEFAULT_EXCLUDED_TOOL_NAMES | get_excluded_tool_names() | md_block_excluded
            if model_type != "vrm":
                excluded |= VRM_ONLY_TOOL_NAMES
            return [tool_func for tool_func in toollist if _tool_func_name(tool_func) not in excluded]
    except ImportError:
        pass
    base_excluded = set(DEFAULT_EXCLUDED_TOOL_NAMES) | md_block_excluded
    if model_type != "vrm":
        base_excluded |= VRM_ONLY_TOOL_NAMES
    return [tool_func for tool_func in toollist if _tool_func_name(tool_func) not in base_excluded]


def refresh_runtime_paths() -> None:
    global DIARY_DIR
    DIARY_DIR = Path(conf.CONFIG_ROOT) / "agents" / Path(conf.AGENT_NAME) / "diary"
    from faust_backend.memory import get_memory
    get_memory(refresh=True)


def _run_async_in_thread(coro) -> None:
    def runner():
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(coro)
        finally:
            try:
                loop.close()
            except Exception:
                pass
    threading.Thread(target=runner, daemon=True).start()
