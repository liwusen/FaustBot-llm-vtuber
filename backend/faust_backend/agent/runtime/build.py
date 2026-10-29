"""主 Agent 的装配：工具收集、中间件栈、模型构建、create_agent。

自 ``runtime/lifecycle.py`` 整块挖出（P4 布局拆分）。
"""

from __future__ import annotations

import os

from langchain.agents import create_agent

import faust_backend.agent.runtime.state as state
import faust_backend.tools as tools_mod
from faust_backend.agent.runtime import middleware
from faust_backend.agent.runtime.turn.output_store import get_output_store
from faust_backend.agent.subagent.manager import SubagentManager
from faust_backend.core.log import get_logger

log = get_logger("faust.lifecycle")


def _collect_runtime_tools() -> list:
    """收集主 Agent 的完整工具列表（内置 + MCP + 插件注入）。"""
    from faust_backend.integrations.mcp import get_mcp_manager

    base_tools = list(tools_mod.get_tools_for_agent(state.AGENT_NAME))
    mcp_tools = get_mcp_manager().get_langchain_tools()
    if mcp_tools:
        base_tools.extend(mcp_tools)
    pm = state.plugin_manager
    if pm:
        return pm.compose_tools(base_tools=base_tools, agent_name=state.AGENT_NAME)
    return base_tools


def _compose_runtime_middlewares(*, compact_middleware=None, server_tool_middleware=None) -> list:
    """组装中间件栈。

    compact_middleware 会插在 ModelRetryMiddleware 之前（重试始终是最内层，
    只重放模型请求本身），并且始终排在最外层之后，保证判定基于未修复/未桥接
    的原始消息。server_tool_middleware 只负责往请求里追加 provider 托管工具规格。
    """
    from faust_backend.agent.runtime.middleware.mm_bridge import MultimodalBridgeMiddleware
    from faust_backend.agent.runtime.middleware.model_retry import with_model_retry
    from faust_backend.agent.runtime.middleware.server_tools import ServerToolMiddleware
    from faust_backend.agent.runtime.middleware.tool_call_repair import ToolCallRepairMiddleware

    pm = state.plugin_manager
    middlewares = pm.compose_middlewares(agent_name=state.AGENT_NAME) if pm else []
    # Filter out any stale mm_bridge instances from old plugin state
    middlewares = [
        m for m in middlewares if not isinstance(m, MultimodalBridgeMiddleware)
    ]
    middlewares.append(MultimodalBridgeMiddleware())
    # Always-on repair: never allow dangling assistant tool_calls to reach the model
    middlewares = [
        m for m in middlewares if not isinstance(m, ToolCallRepairMiddleware)
    ]
    middlewares.append(ToolCallRepairMiddleware())
    # 服务端（provider 托管）工具：仅在模型显式启用时存在
    middlewares = [
        m for m in middlewares if not isinstance(m, ServerToolMiddleware)
    ]
    if server_tool_middleware is not None:
        middlewares.append(server_tool_middleware)
    # Always-on 会话压缩：保命机制，不依赖插件开关
    if compact_middleware is not None:
        from faust_backend.agent.runtime.compact import CompactMiddleware

        middlewares = [
            m for m in middlewares if not isinstance(m, CompactMiddleware)
        ]
        middlewares.append(compact_middleware)
    # Always-on: 瞬时模型故障统一退避重试（最内层，只重放模型请求本身）
    return with_model_retry(middlewares)


def _compose_runtime_extensions(*, compact_middleware=None, server_tool_middleware=None):
    return (
        _collect_runtime_tools(),
        _compose_runtime_middlewares(
            compact_middleware=compact_middleware,
            server_tool_middleware=server_tool_middleware,
        ),
    )


def _find_tool_by_name(name: str):
    target = str(name or "").strip()
    for tool_item in tools_mod.get_tools_for_agent(state.AGENT_NAME):
        tool_name = getattr(tool_item, "name", None) or getattr(
            tool_item, "__name__", ""
        )
        if str(tool_name).strip() == target:
            return tool_item
    raise KeyError(f"Tool not found: {target}")


def _build_subagent_toolsets() -> dict[str, list]:
    from faust_backend.integrations.mcp import get_mcp_manager

    toolsets = {
        "BASESET": [
            _find_tool_by_name("read"),
            _find_tool_by_name("search"),
            _find_tool_by_name("find"),
        ],
        "WRITESET": [
            _find_tool_by_name("write"),
            _find_tool_by_name("edit"),
        ],
        "EXECUTESET": [
            _find_tool_by_name("execute"),
        ],
        "SKILLSET": [
            _find_tool_by_name("listSkills"),
        ],
    }
    mcp_manager = get_mcp_manager()
    for item in mcp_manager.list_server_statuses(include_log=False):
        server_id = str(item.get("server_id") or "").strip()
        if not server_id or not item.get("running"):
            continue
        prefix = f"{server_id}_"
        matching = []
        for tool_item in mcp_manager.get_langchain_tools():
            tool_name = getattr(tool_item, "name", "")
            if str(tool_name).startswith(prefix):
                matching.append(tool_item)
        if matching:
            toolsets[f"MCP_{server_id.upper()}_SET"] = matching
    return toolsets


async def _rebuild_subagent_manager(*, model_name: str) -> SubagentManager:
    from faust_backend.agent.runtime.middleware.server_tools import build_server_tool_middleware

    subagent_db_path = os.path.join(state.AGENT_ROOT, "subagents.db")
    if state.subagent_manager is not None:
        try:
            await state.subagent_manager.aclose()
        except Exception as exc:
            log.warning("关闭旧 SubagentManager 失败: %s", exc)
    manager = SubagentManager(checkpointerPath=subagent_db_path)
    manager.setChatModel(await _build_chat_model(model_name=model_name), spec=model_name)
    _tools, middlewares = _compose_runtime_extensions(
        server_tool_middleware=build_server_tool_middleware(
            state.get_model_providers(), model_name
        )
    )
    manager.setMiddlewares(middlewares)
    for name, toolset in _build_subagent_toolsets().items():
        # Subagent 与主 Agent 共用 _registry 里的工具对象；不包装的话
        # 工具输出（含图片 base64）会原样进入消息文本
        manager.newToolset(name, middleware.wrap_tools(toolset))
    manager.restore_persisted_state()
    state.subagent_manager = manager
    get_output_store()
    log.info("SubagentManager 已重建: %s", subagent_db_path)
    return manager


async def _build_chat_model(*, model_name: str):
    """Build the main agent's chat model from the ModelProviders.

    model_name 是 'provider::model' spec。返回的模型格式由该 spec 的路由决定
    （OpenAI Chat / OpenAI Responses / Anthropic / Gemini），所以只能依赖
    BaseChatModel 的公共接口——不要假设 ChatOpenAI 专有属性（如 `model_name`）。
    thinking 参数由全局 REASONING_CONFIG 驱动（off/low/medium/high）：
    REASONING_CONFIG == "off" 或 provider 的 thinking_type == "none" 时不传思考参数。
    """
    from faust_backend.llm.provider import build_ReasoningChatOpenAI_from_spec
    from faust_backend.agent.runtime import state as runtime_state
    import faust_backend.core.config as conf
    providers = runtime_state.get_model_providers()
    spec = model_name or providers.main_model
    if not spec:
        raise RuntimeError("main_model is not configured (provider.private.json)")
    # 找到 spec 对应的 provider，取其 thinking_type 决定是否启用思考
    provider_name, _ = spec.split("::", 1)
    provider = next((p for p in providers.providers if p.name == provider_name), None)
    reasoning = getattr(conf, "REASONING_CONFIG", "medium")
    thinking = (
        reasoning
        if reasoning != "off" and provider and provider.thinking_type != "none"
        else None
    )
    return await build_ReasoningChatOpenAI_from_spec(
        providers, spec=spec, intensity=thinking
    )


async def _create_agent_with_extensions(*, model_name: str, checkpointer):
    from faust_backend.agent.runtime.compact import build_compact_middleware
    from faust_backend.agent.runtime.middleware.server_tools import build_server_tool_middleware

    tools = _collect_runtime_tools()
    chat_model = await _build_chat_model(model_name=model_name)
    # 压缩 middleware 需要「与主请求逐字节相同的工具 schema」来命中前缀缓存，
    # 而 wrap_tools 是就地包装（不改变 schema），因此在包装前构建即可。
    compact_middleware = await build_compact_middleware(
        model=chat_model, tools=tools, model_spec=model_name
    )
    server_tool_middleware = build_server_tool_middleware(
        state.get_model_providers(), model_name
    )
    tools = middleware.wrap_tools(tools)
    mgmt_middlewares = _compose_runtime_middlewares(
        compact_middleware=compact_middleware,
        server_tool_middleware=server_tool_middleware,
    )
    state.compact_middleware = compact_middleware
    kwargs = {
        "model": chat_model,
        "checkpointer": checkpointer,
        "tools": tools,
    }
    if mgmt_middlewares:
        kwargs["middleware"] = mgmt_middlewares
    return create_agent(**kwargs)
