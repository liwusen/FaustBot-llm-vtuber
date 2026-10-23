"""ServerToolMiddleware — 注入 provider 托管的「服务端工具」。

与本地工具（ToolNode 执行的 BaseTool）不同，服务端工具由**服务商自己执行**：

- OpenAI Responses：`{"type": "web_search"}`（模型在服务端检索后直接作答）
- Anthropic Messages：`{"type": "web_search_20250305", "name": "web_search", ...}`
- Google Gemini：`{"google_search": {}}`
- OpenAI Chat Completions：**不支持**（没有对应概念，显式要求时抛错）

它们在请求里是 `tools` 数组的成员，但不会产生普通的 `tool_calls`，也不会进入
本地 ToolNode；结果以 content block（`server_tool_use` / `web_search_call` /
`google_search_result` 等）的形式回到消息里。因此：

1. 只需要把 spec 追加到 `ModelRequest.tools`，Agent 不需要注册任何本地工具；
2. `repair_dangling_tool_calls` 不处理这些 content block（它们没有 tool_call_id）；
3. 压缩调用必须用 `tool_choice="none"` 阻止摘要过程真的去联网检索。
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from typing_extensions import override

from faust_backend.logger import get_logger

log = get_logger("faust.server_tools")


class ServerToolMiddleware(AgentMiddleware):
    """把 provider-hosted 工具规格追加到每次模型请求的 tools 列表。"""

    def __init__(self, specs: list[dict[str, Any]] | None = None) -> None:
        super().__init__()
        #: 复制一份，避免不同 Agent 之间共享同一个 dict 被就地修改
        self.specs: list[dict[str, Any]] = [dict(spec) for spec in (specs or [])]

    def _merged(self, request: ModelRequest[Any]) -> list[Any]:
        existing = list(request.tools or [])
        return [*existing, *self.specs]

    @override
    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        if not self.specs:
            return handler(request)
        return handler(request.override(tools=self._merged(request)))

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        if not self.specs:
            return await handler(request)
        return await handler(request.override(tools=self._merged(request)))


def resolve_server_tool_specs(providers: Any, spec: str) -> list[dict[str, Any]]:
    """按模型 spec 解析用户启用了哪些服务端工具（未启用则返回空列表）。

    端点/格式解析失败时**抛错**：用户显式勾选了服务端工具却因为配置问题静默
    不生效，会让 Agent 表现出「明明开了联网搜索却不会搜」的假故障。
    """
    from faust_backend.llm_formats import build_server_tool_specs, resolve_route
    from faust_backend.provider import parse_spec

    if not spec:
        return []
    provider_name, model_name = parse_spec(spec)
    provider = next(
        (p for p in (getattr(providers, "providers", None) or []) if p.name == provider_name),
        None,
    )
    if provider is None:
        return []
    names = (getattr(provider, "model_server_tools", None) or {}).get(model_name) or []
    if not names:
        return []
    decision = resolve_route(provider, model_name)
    return build_server_tool_specs(decision.api_format, names)


def build_server_tool_middleware(
    providers: Any, spec: str
) -> ServerToolMiddleware | None:
    """按 spec 构建中间件；未启用任何服务端工具时返回 None。"""
    try:
        specs = resolve_server_tool_specs(providers, spec)
    except ValueError as exc:
        log.error("服务端工具配置无效（spec=%s）: %s", spec, exc)
        raise
    if not specs:
        return None
    log.info(
        "模型 %s 启用服务端工具: %s",
        spec,
        ", ".join(str(spec_item.get("name") or spec_item.get("type") or spec_item) for spec_item in specs),
    )
    return ServerToolMiddleware(specs)
