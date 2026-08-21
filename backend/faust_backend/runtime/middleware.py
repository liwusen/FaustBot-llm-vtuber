"""
Tool output interception middleware — wraps tool functions so their return
values go through OutputStore and LLMs receive only a truncated summary.

Because LangChain/LangGraph middleware APIs vary across versions, we
implement this as a tool-wrapper approach (compatible with all versions).
"""

from __future__ import annotations

import asyncio
import json
from functools import wraps
from typing import Any, Callable

from langchain_core.tools import BaseTool

from faust_backend.logger import get_logger
from faust_backend.runtime.output_store import get_output_store
from faust_backend.runtime import state

log = get_logger("faust.tool_middleware")

def _resolve_config(config: Any) -> Any:
    """langchain 1.4+ 的 _run/_arun 要求 config 参数；直接调用时兜底为空 dict。"""
    return config if config is not None else {}

def wrap_tool_output(tool: BaseTool) -> BaseTool:
    """Wrap a LangChain tool so its output goes through OutputStore.

    The LLM receives only the truncated summary; full output is available
    via artifact://<id> reference in the summary footer.
    """
    store = get_output_store()
    tool_name = tool.name
    import inspect as _inspect

    # async @tool：tool.coroutine 是原生 async 函数；包装 _arun 委托它。
    # sync @tool：tool.coroutine 为 None，tool._run 是 StructuredTool._run
    # （接受 config/run_manager 并委托 func）；包装 _run 和 _arun。
    original_coro = getattr(tool, 'coroutine', None)

    if original_coro and _inspect.iscoroutinefunction(original_coro):
        # ── async @tool：只包装 _arun，委托 coroutine ──
        @wraps(original_coro)
        async def _wrapped_arun(*args, **kwargs):
            pm = getattr(state, 'plugin_manager', None)
            if pm:
                try:
                    modified = await pm._call_pluggy_hook('tool_call_pre', name=tool_name, args=kwargs, ctx=None)
                    if modified and isinstance(modified, list) and modified[0] is not None:
                        kwargs = modified[0] if isinstance(modified[0], dict) else kwargs
                except Exception:
                    pass
            try:
                result = await original_coro(*args, **kwargs)
                if pm:
                    try:
                        post_results = await pm._call_pluggy_hook('tool_call_post', name=tool_name, args=kwargs, result=result, ctx=None)
                        if post_results and isinstance(post_results, list):
                            for r in post_results:
                                if r is not None:
                                    result = r
                    except Exception:
                        pass
            except Exception as e:
                output_id = store.put(
                    str(e), tool_name=tool_name,
                    metadata={"status": "error"}
                )
                return f"工具执行出错\n[完整输出: artifact://{output_id}]"
            return _store_and_summarize(store, tool_name, result, args, kwargs)
        tool._arun = _wrapped_arun
        return tool

    # ── sync @tool：包装 _run（接受 config/run_manager），并让 _arun
    # 委托 _wrapped_run 到线程池执行，避免阻塞事件循环 ──
    original_run = getattr(tool, '_run', None)
    if original_run and not _inspect.iscoroutinefunction(original_run):
        log.warning("Wrapping sync tool %s._run for OutputStore", tool_name)
        log.warning("SYNC TOOL WILL BE NO LONGER SUPPORTED IN FUTURE VERSIONS.")
        # @wraps 保留原 _run 签名（含 config 注解），使 langchain 的
        # invoke/ainvoke 能探测到 config 参数并注入。
        @wraps(original_run)
        def _wrapped_run(*args, config: Any = None, run_manager: Any = None, **kwargs):
            pm = getattr(state, 'plugin_manager', None)
            if pm:
                try:
                    modified = pm._call_pluggy_hook_sync('tool_call_pre', name=tool_name, args=kwargs, ctx=None)
                    if modified and isinstance(modified, list) and modified[0] is not None:
                        kwargs = modified[0] if isinstance(modified[0], dict) else kwargs
                except Exception:
                    pass
            try:
                result = original_run(*args, config=_resolve_config(config), run_manager=run_manager, **kwargs)
                if pm:
                    try:
                        post_results = pm._call_pluggy_hook_sync('tool_call_post', name=tool_name, args=kwargs, result=result, ctx=None)
                        if post_results and isinstance(post_results, list):
                            for r in post_results:
                                if r is not None:
                                    result = r
                    except Exception:
                        pass
            except Exception as e:
                output_id = store.put(
                    str(e), tool_name=tool_name,
                    metadata={"status": "error"}
                )
                return f"工具执行出错\n[完整输出: artifact://{output_id}]"
            return _store_and_summarize(store, tool_name, result, args, kwargs)

        tool._run = _wrapped_run

        @wraps(original_run)
        async def _wrapped_arun_sync(*args, config: Any = None, run_manager: Any = None, **kwargs):
            return await asyncio.to_thread(
                _wrapped_run, *args, config=config, run_manager=run_manager, **kwargs
            )

        tool._arun = _wrapped_arun_sync

    return tool


def wrap_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Apply output wrapping to a list of tools.

    Non-BaseTool entries (bare callables leaked by plugins) are skipped with a
    warning instead of crashing on `tool.name` access downstream.
    """
    out: list[BaseTool] = []
    for t in tools:
        if not isinstance(t, BaseTool):
            log.warning(
                "跳过非 LangChain 工具（插件需用 @tool 包装）: %s",
                getattr(t, "__name__", repr(t)),
            )
            continue
        out.append(wrap_tool_output(t))
    return out


def _store_and_summarize(store, tool_name: str, result: Any,
                         args: tuple | None = None,
                         kwargs: dict | None = None) -> str:
    """Store tool output and return truncated summary.
    Multimodal JSON: store a copy in OutputStore, return full content to
    the LLM with the artifact ID in the text. Skipped when `read` is
    reading from an existing artifact:// URI (avoids circular storage)."""
    # Detect multimodal JSON
    data = None
    if isinstance(result, str):
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            data = None
    elif isinstance(result, dict):
        data = result
    if isinstance(data, dict) and data.get("kind") == "multimodal_tool_result":
        # Read from artifact → don't store a second copy
        if tool_name == "read" and _is_read_from_artifact(args, kwargs):
            if isinstance(result, str):
                return result
            return json.dumps(result, ensure_ascii=False)
        # Store a copy in OutputStore, inject artifact ID into text
        output_id = store.put_multimodal(data, tool_name=tool_name)
        text = str(data.get("text") or "")
        data["text"] = f"{text}\n[图片副本已保存: artifact://{output_id}]"
        return json.dumps(data, ensure_ascii=False)

    if isinstance(result, str):
        output = result
    elif isinstance(result, (dict, list)):
        output = json.dumps(result, ensure_ascii=False)
    else:
        output = str(result)

    # Skip trivial results — no need for an artifact
    if len(output) <= 120 and "\n" not in output:
        return output

    output_id = store.put(output, tool_name=tool_name, metadata={})
    summary = store.summary(output_id)
    return summary


def _is_read_from_artifact(args: tuple | None, kwargs: dict | None) -> bool:
    """Check if the tool call is read() with an artifact:// URI."""
    if kwargs:
        uri_val = str(kwargs.get("uri", ""))
        if uri_val.startswith("artifact://"):
            return True
    if args:
        uri_val = str(args[0])
        if uri_val.startswith("artifact://"):
            return True
    return False
