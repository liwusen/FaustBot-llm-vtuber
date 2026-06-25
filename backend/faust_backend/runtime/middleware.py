"""
Tool output interception middleware — wraps tool functions so their return
values go through OutputStore and LLMs receive only a truncated summary.

Because LangChain/LangGraph middleware APIs vary across versions, we
implement this as a tool-wrapper approach (compatible with all versions).
"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import BaseTool

from faust_backend.runtime.output_store import get_output_store


def wrap_tool_output(tool: BaseTool) -> BaseTool:
    """Wrap a LangChain tool so its output goes through OutputStore.

    The LLM receives only the truncated summary; full output is available
    via artifact://<id> reference in the summary footer.
    """
    store = get_output_store()
    original_func = tool.func if hasattr(tool, 'func') else getattr(tool, '_run', None)
    original_coro = getattr(tool, 'coroutine', None) or getattr(tool, '_arun', None)
    tool_name = tool.name

    # --- async path ---
    if original_coro:

        async def _wrapped_async_run(*args, **kwargs):
            try:
                result = await original_coro(*args, **kwargs)
            except Exception as e:
                output_id = store.put(
                    str(e), tool_name=tool_name,
                    metadata={"status": "error"}
                )
                return f"工具执行出错\n[完整输出: artifact://{output_id}]"
            return _store_and_summarize(store, tool_name, result)

        setattr(tool, '_arun', _wrapped_async_run)
    elif original_func:

        def _wrapped_run(*args, **kwargs):
            try:
                result = original_func(*args, **kwargs)
            except Exception as e:
                output_id = store.put(
                    str(e), tool_name=tool_name,
                    metadata={"status": "error"}
                )
                return f"工具执行出错\n[完整输出: artifact://{output_id}]"
            return _store_and_summarize(store, tool_name, result)

        tool._run = _wrapped_run

    return tool


def wrap_tools(tools: list[BaseTool]) -> list[BaseTool]:
    """Apply output wrapping to a list of tools."""
    return [wrap_tool_output(t) for t in tools]


def _store_and_summarize(store, tool_name: str, result: Any) -> str:
    """Store tool output and return truncated summary."""
    output = str(result) if not isinstance(result, str) else result

    # Skip trivial results — no need for an artifact
    if len(output) <= 120 and "\n" not in output:
        return output

    output_id = store.put(output, tool_name=tool_name, metadata={})
    summary = store.summary(output_id)
    return summary
