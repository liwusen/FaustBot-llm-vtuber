"""四种 LLM API 格式的解析与模型构建（不包含任何具体 Provider 的模型表）。

支持的格式：

- ``openai_chat``：OpenAI Chat Completions（含所有 OpenAI 兼容网关）
- ``openai_responses``：OpenAI Responses API（无状态，`store=False`）
- ``anthropic_messages``：Anthropic Messages API
- ``google_gemini``：Google Gemini generateContent

格式来源的优先级（高 → 低）：

1. `provider.model_api_formats[model]`（用户显式覆盖）
2. `provider.api_format`（Provider 级声明，非 auto）
3. `provider.model_catalog_routes[model]`（models.dev 目录推断）
4. base_url 的路径启发式（`/anthropic`、`/messages`、`/google`、`/v1beta`、`/responses`）
5. 多格式 Provider 的模型族回退（仅当目录里确实出现 ≥2 种格式时）
6. `openai_chat`（最终兜底，附 warning）

端点来源：`provider.format_base_urls[format]` > 目录路由（仅 host 相同时）>
`provider.base_url`。显式指定了格式却没有可用端点时**报错**，绝不静默把请求
发到别的主机。

思考参数的形状（Gemini 的 `thinking_budget` vs `thinking_level`、Anthropic 的
`budget_tokens`、Responses 的 `reasoning.effort`）由 `thinking_caps` 解析：
手动覆盖 > Provider `/models` 自报 > models.dev 目录 > 模型名启发式。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

from faust_backend.core.log import get_logger
from faust_backend.llm.thinking import get_format_thinking_params
from faust_backend.llm.thinking_caps import (
    ThinkingOption,
    describe_thinking,
    resolve_thinking_capability,
)

log = get_logger("faust.llm_formats")

API_FORMATS = (
    "openai_chat",
    "openai_responses",
    "anthropic_messages",
    "google_gemini",
)

FORMAT_LABELS = {
    "openai_chat": "OpenAI Chat Completions",
    "openai_responses": "OpenAI Responses",
    "anthropic_messages": "Anthropic Messages",
    "google_gemini": "Google Gemini",
}

# 默认走 Responses API 的 SDK 包（只有当 shape 不是 completions 时成立）
_RESPONSES_NPM = {"@ai-sdk/openai", "@ai-sdk/azure", "@ai-sdk/xai"}

# 模型族回退：仅在「目录里该 Provider 出现 ≥2 种格式」时启用，
# 属于通用命名规则，不是某个 Provider 的模型表。
_FAMILY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^claude"), "anthropic_messages"),
    (re.compile(r"^gemini"), "google_gemini"),
    (re.compile(r"^(gpt-5|gpt-6|o3|o4|codex)"), "openai_responses"),
)

_ANTHROPIC_SUFFIXES = ("/v1/messages", "/messages", "/v1")
_GOOGLE_SUFFIXES = (
    "/v1beta/models",
    "/v1/models",
    "/v1beta",
    "/v1",
)
_OPENAI_SUFFIXES = ("/chat/completions", "/responses")


@dataclass(frozen=True)
class FormatDecision:
    """一个模型最终的格式决策（含来源与端点）。"""

    api_format: str
    source: str  # manual | provider_default | catalog | url_path | family | fallback
    reason: str
    base_url: str | None


# ── 通用协议推断 ──


def detect_api_format(*, npm: str = "", shape: str = "", api: str = "") -> str:
    """按 npm 包名 / shape / 端点路径推断 API 格式（通用规则，无 Provider 特判）。"""
    npm_l = str(npm or "").lower()
    shape_l = str(shape or "").lower()
    path = _url_path(api)

    if "anthropic" in npm_l or "/anthropic" in path or "/messages" in path:
        return "anthropic_messages"
    if "google" in npm_l or "vertex" in npm_l or "/google" in path or "/v1beta" in path:
        return "google_gemini"
    if shape_l == "responses":
        return "openai_responses"
    if shape_l == "completions":
        return "openai_chat"
    if npm_l in _RESPONSES_NPM:
        return "openai_responses"
    if path.rstrip("/").endswith("/responses"):
        return "openai_responses"
    return "openai_chat"


def detect_url_format(base_url: str) -> str | None:
    """只看端点路径的格式启发式；无明确信号时返回 None。"""
    path = _url_path(base_url)
    if not path:
        return None
    if "/anthropic" in path or path.rstrip("/").endswith("/messages"):
        return "anthropic_messages"
    if "/google" in path or "/v1beta" in path:
        return "google_gemini"
    if path.rstrip("/").endswith("/responses"):
        return "openai_responses"
    return None


def _url_path(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    if "//" not in text:
        text = "//" + text
    try:
        return (urlparse(text).path or "").lower()
    except ValueError:
        return ""


def normalize_format_base_url(api_format: str, url: str) -> str | None:
    """把端点归一化成对应 SDK 期望的「根地址」。

    - Anthropic：去掉尾部 `/v1/messages`、`/messages`、`/v1`（SDK 自己追加）；
    - Google：去掉尾部 `/v1beta/models/...`、`/v1beta`、`/v1`（SDK 自己追加）；
    - OpenAI：去掉尾部 `/chat/completions`、`/responses`，**保留** `/v1`。

    非法/非 http(s) 地址返回 None（调用方据此报错或回退），不静默使用。
    """
    text = str(url or "").strip()
    if not text:
        return None
    if "//" not in text:
        text = "//" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    path = (parsed.path or "").rstrip("/")
    if api_format == "anthropic_messages":
        suffixes = _ANTHROPIC_SUFFIXES
    elif api_format == "google_gemini":
        suffixes = _GOOGLE_SUFFIXES
        # `/v1beta/models/<model>:generateContent` 这类完整资源路径要截到 /models 之前
        lowered = path.lower()
        for marker in ("/v1beta/models/", "/v1/models/"):
            index = lowered.find(marker)
            if index >= 0:
                path = path[:index]
                break
    else:
        suffixes = _OPENAI_SUFFIXES
    changed = True
    while changed and path:
        changed = False
        lowered = path.lower()
        for suffix in suffixes:
            if lowered.endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                changed = True
                break
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def family_format(model: str) -> str | None:
    """按模型名前缀给出通用族回退格式（claude / gemini / gpt-5 等）。"""
    name = str(model or "").strip().lower()
    for pattern, api_format in _FAMILY_PATTERNS:
        if pattern.search(name):
            return api_format
    return None


def _catalog_format(route: Any) -> str | None:
    if not isinstance(route, dict):
        return None
    fmt = str(route.get("format") or "")
    return fmt if fmt in API_FORMATS else None


def is_multi_format(provider: Any) -> bool:
    """目录里是否出现 ≥2 种格式（多格式聚合 Provider 的判定依据）。"""
    routes = getattr(provider, "model_catalog_routes", None) or {}
    formats = {_catalog_format(route) for route in routes.values()}
    formats.discard(None)
    return len(formats) >= 2


def resolve_route(provider: Any, model: str) -> FormatDecision:
    """决定 `model` 使用哪种 API 格式与端点（见模块 docstring 的优先级）。"""
    catalog_route = (getattr(provider, "model_catalog_routes", None) or {}).get(model)
    manual = (getattr(provider, "model_api_formats", None) or {}).get(model)
    provider_default = str(getattr(provider, "api_format", "auto") or "auto")
    base_url = str(getattr(provider, "base_url", "") or "")

    if manual in API_FORMATS:
        return _decision(
            provider,
            model,
            manual,
            source="manual",
            reason="用户为此模型显式指定格式",
            catalog_route=catalog_route,
        )

    if provider_default in API_FORMATS:
        return _decision(
            provider,
            model,
            provider_default,
            source="provider_default",
            reason="Provider 级 api_format 声明",
            catalog_route=catalog_route,
        )

    catalog_format = _catalog_format(catalog_route)
    if catalog_format:
        return _decision(
            provider,
            model,
            catalog_format,
            source="catalog",
            reason="models.dev 目录推断",
            catalog_route=catalog_route,
        )

    url_format = detect_url_format(base_url)
    if url_format:
        return _decision(
            provider,
            model,
            url_format,
            source="url_path",
            reason="base_url 路径表明协议",
            catalog_route=None,
        )

    if is_multi_format(provider):
        guessed = family_format(model)
        if guessed:
            return _decision(
                provider,
                model,
                guessed,
                source="family",
                reason="目录显示该 Provider 支持多种格式，按模型族回退",
                catalog_route=None,
            )

    log.warning(
        "模型 '%s'（provider=%s）无法确定 API 格式，回退到 openai_chat",
        model,
        getattr(provider, "name", ""),
    )
    return _decision(
        provider,
        model,
        "openai_chat",
        source="fallback",
        reason="无任何格式线索，回退 OpenAI Chat Completions",
        catalog_route=None,
    )


def _decision(
    provider: Any,
    model: str,
    api_format: str,
    *,
    source: str,
    reason: str,
    catalog_route: Any,
) -> FormatDecision:
    base_url = _resolve_base_url(provider, api_format, catalog_route=catalog_route)
    if base_url is None:
        if source == "fallback":
            raise ValueError(
                f"Provider '{getattr(provider, 'name', '')}' 没有可用的 base_url，"
                f"无法为模型 '{model}' 构建 LLM"
            )
        # auto 路径：目标格式没有可用端点 → 退化为 openai_chat（显式格式仍会报错）
        if source != "manual" and source != "provider_default":
            log.warning(
                "模型 '%s' 的目标格式 %s 没有可用端点，回退 openai_chat",
                model,
                api_format,
            )
            return FormatDecision(
                api_format="openai_chat",
                source="fallback",
                reason=f"{reason}（端点不可用，回退 chat）",
                base_url=_resolve_base_url(provider, "openai_chat", catalog_route=None),
            )
        raise ValueError(
            f"Provider '{getattr(provider, 'name', '')}' 的格式 {api_format} 没有可用端点："
            "请设置 format_base_urls 或 provider.base_url"
        )
    return FormatDecision(
        api_format=api_format, source=source, reason=reason, base_url=base_url
    )


def _resolve_base_url(
    provider: Any, api_format: str, *, catalog_route: Any
) -> str | None:
    explicit = (getattr(provider, "format_base_urls", None) or {}).get(api_format)
    if explicit:
        normalized = normalize_format_base_url(api_format, explicit)
        if normalized is None:
            raise ValueError(
                f"Provider '{getattr(provider, 'name', '')}' 的 format_base_urls"
                f"[{api_format}] 不是合法的 http(s) 地址: {explicit}"
            )
        return normalized
    # 目录端点只对「同一种格式 + host 匹配」有效：
    # - 用户手动把格式改成别的时，目录端点是另一种协议的路径，用了必然 404；
    # - host 不匹配时（只按模型名匹配到目录），请求不能发到用户没配置密钥的主机。
    if (
        isinstance(catalog_route, dict)
        and catalog_route.get("host_matched")
        and _catalog_format(catalog_route) == api_format
    ):
        catalog_base = catalog_route.get("base_url")
        if catalog_base:
            normalized = normalize_format_base_url(api_format, str(catalog_base))
            if normalized:
                return normalized
    return normalize_format_base_url(
        api_format, str(getattr(provider, "base_url", "") or "")
    )


def describe_provider_formats(provider: Any) -> dict[str, dict[str, Any]]:
    """返回该 Provider 每个模型的格式决策（管理端/前端展示用）。

    同时给出思考参数形状的解析结果（`thinking`），前端据此显示「等级/预算 ·
    来源」，也便于用户判断自动推断是否可靠。
    """
    result: dict[str, dict[str, Any]] = {}
    for model in getattr(provider, "models", None) or []:
        try:
            decision = resolve_route(provider, model)
        except ValueError as exc:
            result[model] = {
                "api_format": None,
                "source": "error",
                "reason": str(exc),
                "base_url": None,
                "thinking": None,
            }
            continue
        thinking = None
        if str(getattr(provider, "thinking_type", "none")) != "none":
            thinking = describe_thinking(
                resolve_thinking_capability(provider, model),
                decision.api_format,
                model=model,
            )
        result[model] = {
            "api_format": decision.api_format,
            "source": decision.source,
            "reason": decision.reason,
            "base_url": decision.base_url,
            "thinking": thinking,
        }
    return result


def describe_all_formats(providers: Any) -> dict[str, dict[str, dict[str, Any]]]:
    """全部 Provider 的格式决策：`{provider_name: {model: decision}}`。"""
    return {
        provider.name: describe_provider_formats(provider)
        for provider in (getattr(providers, "providers", None) or [])
    }


# ── 服务端（Provider 托管）工具规格 ──

SERVER_TOOL_FORMATS = {
    "web_search": {
        "openai_responses": {"type": "web_search"},
        "anthropic_messages": {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        },
        "google_gemini": {"google_search": {}},
    },
}


def build_server_tool_specs(api_format: str, names: Iterable[str]) -> list[dict[str, Any]]:
    """把工具名翻译成对应格式的 provider-hosted 工具规格。

    `openai_chat` 没有服务端工具概念：显式要求时直接报错，不静默忽略。
    """
    specs: list[dict[str, Any]] = []
    for name in names or []:
        target = str(name or "").strip()
        if not target:
            continue
        mapping = SERVER_TOOL_FORMATS.get(target)
        if mapping is None:
            raise ValueError(f"未知的服务端工具: {target}")
        spec = mapping.get(api_format)
        if spec is None:
            raise ValueError(
                f"API 格式 {api_format} 不支持服务端工具 '{target}'"
                f"（支持: {', '.join(sorted(mapping))}）"
            )
        specs.append(dict(spec))
    return specs


def supported_server_tools(api_format: str) -> list[str]:
    """该格式支持的服务端工具名（UI 勾选依据）。"""
    return [
        name
        for name, mapping in SERVER_TOOL_FORMATS.items()
        if api_format in mapping
    ]


def forced_no_tool_choice(api_format: str) -> str | dict[str, str]:
    """「禁止调用工具」的 `tool_choice` 值（各格式写法不同）。

    Anthropic SDK 的字符串 `tool_choice` 只接受 `auto` / `any` / 工具名，
    传 `"none"` 会被解释成「调用名为 none 的工具」，所以必须用
    `{"type": "none"}`；OpenAI 与 Gemini 用字符串 `"none"`。
    """
    if api_format == "anthropic_messages":
        return {"type": "none"}
    return "none"


# ── 模型构建 ──


async def build_chat_model_from_spec(
    providers: Any,
    spec: str = "",
    intensity: str | None = None,
    *,
    purpose: str = "chat",
) -> Any:
    """按 `provider::model` 构建对应格式的 LangChain Chat 模型。

    Args:
        providers: `ModelProviders` 实例。
        spec: `provider::model`；空则用 `providers.main_model`。
        intensity: thinking 强度（low/medium/high）；None 表示不启用。
        purpose: `chat` / `aux`（辅助调用，无思考）/ `compact`（摘要专用上限）。
    """
    from faust_backend.llm.provider import (
        auto_load_model_for_provider,
        parse_spec,
        resolve_provider,
    )

    text = str(spec or "").strip() or str(getattr(providers, "main_model", "") or "")
    if not text:
        raise RuntimeError("main_model is not configured (provider.private.json)")
    provider_name, model_name = parse_spec(text)
    provider = resolve_provider(providers, provider_name)
    await auto_load_model_for_provider(provider)
    # [R3] 容忍 main_model 不在已加载 models 列表（迁移/离线场景）
    if provider.models and model_name not in provider.models:
        raise ValueError(
            f"Model '{model_name}' not found for provider '{provider_name}'."
        )

    decision = resolve_route(provider, model_name)
    thinking = intensity if purpose == "chat" else None
    if str(getattr(provider, "thinking_type", "none")) == "none":
        thinking = None
    capability = None
    option = None
    if thinking is not None:
        # 思考参数形状来自手动覆盖 > /models > models.dev > 模型名启发式
        # （例如 Gemini 3/4 只接受 thinking level，2.5 只接受 budget）。
        capability = resolve_thinking_capability(provider, model_name)
        if capability.reasoning is False:
            log.info(
                "模型 '%s' 标注为不支持推理（来源: %s），本次不启用思考参数",
                model_name,
                capability.source or "unknown",
            )
            thinking = None
        else:
            option = capability.option_for(decision.api_format, model=model_name)
            log.debug(
                "模型 '%s' 思考参数: format=%s mode=%s source=%s",
                model_name,
                decision.api_format,
                option.mode if option is not None else "preset",
                capability.source or "none",
            )

    if decision.api_format == "openai_responses":
        return _build_openai_responses(
            provider, model_name, decision, thinking, option
        )
    if decision.api_format == "anthropic_messages":
        return _build_anthropic(
            provider, model_name, decision, thinking, purpose, option
        )
    if decision.api_format == "google_gemini":
        return _build_google(provider, model_name, decision, thinking, option)
    return _build_openai_chat(provider, model_name, decision, thinking)


def _common_headers(provider: Any) -> dict[str, str]:
    from faust_backend.llm.provider import FAUSTBOT_USER_AGENT, opencode_headers

    headers = {"User-Agent": FAUSTBOT_USER_AGENT}
    if getattr(provider, "opencode_go", False):
        headers = opencode_headers(headers)
    return headers


def _stream_usage(provider: Any, *, default: bool) -> bool:
    configured = getattr(provider, "stream_usage", None)
    if configured is None:
        return default
    return bool(configured)


def _build_openai_chat(provider, model, decision, intensity):
    from langchain_openai import ChatOpenAI

    from faust_backend.llm.thinking import ReasoningChatOpenAI, get_thinking_params

    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": provider.key,
        "base_url": decision.base_url,
        "request_timeout": 60,
        "max_retries": 1,
        "default_headers": _common_headers(provider),
        "stream_usage": _stream_usage(provider, default=True),
    }
    if intensity is not None:
        thinking_params = get_format_thinking_params(
            "openai_chat", provider.thinking_type, intensity
        )
        if "reasoning_effort" in thinking_params:
            kwargs["reasoning_effort"] = thinking_params.pop("reasoning_effort")
        model_kw = thinking_params.pop("model_kwargs", {})
        extra = {**thinking_params.pop("extra_body", {}), **kwargs.get("extra_body", {})}
        if extra:
            kwargs["extra_body"] = extra
        kwargs["model_kwargs"] = {**kwargs.get("model_kwargs", {}), **model_kw}
        return ReasoningChatOpenAI(**kwargs)
    return ChatOpenAI(**kwargs)


def _build_openai_responses(
    provider, model, decision, intensity, option: ThinkingOption | None = None
):
    from langchain_openai import ChatOpenAI

    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": provider.key,
        "base_url": decision.base_url,
        "request_timeout": 60,
        "max_retries": 1,
        "default_headers": _common_headers(provider),
        "stream_usage": _stream_usage(provider, default=True),
        "use_responses_api": True,
        "output_version": "responses/v1",
        # 无状态：不依赖 previous_response_id，也不把对话存到 provider 侧。
        # 会话历史由本项目的 checkpoint 负责，压缩/清空才可控。
        "store": False,
        "use_previous_response_id": False,
    }
    if intensity is not None:
        kwargs.update(
            get_format_thinking_params(
                "openai_responses", provider.thinking_type, intensity, option=option
            )
        )
    return ChatOpenAI(**kwargs)


def _anthropic_max_tokens(provider, model, *, purpose: str, budget: int) -> int:
    """Anthropic 必须显式给 `max_tokens`（SDK 默认 1024 会截断长回答/摘要）。"""
    cap = int((getattr(provider, "model_max_output_tokens", None) or {}).get(model) or 8192)
    if purpose == "compact":
        return max(4096, min(cap, 8192))
    if budget > 0:
        return max(1024, min(cap, budget + 1024))
    return cap


def _build_anthropic(
    provider,
    model,
    decision,
    intensity,
    purpose,
    option: ThinkingOption | None = None,
):
    from langchain_anthropic import ChatAnthropic

    budget = 0
    thinking_params: dict[str, Any] = {}
    if intensity is not None:
        thinking_params = get_format_thinking_params(
            "anthropic_messages", provider.thinking_type, intensity, option=option
        )
        thinking = thinking_params.get("thinking")
        if isinstance(thinking, dict):
            budget = int(thinking.get("budget_tokens") or 0)
    kwargs: dict[str, Any] = {
        "model": model,
        "api_key": provider.key,
        "base_url": decision.base_url,
        "max_tokens": _anthropic_max_tokens(
            provider, model, purpose=purpose, budget=budget
        ),
        "timeout": 60,
        "max_retries": 1,
        "default_headers": _common_headers(provider),
        "stream_usage": _stream_usage(provider, default=True),
    }
    kwargs.update(thinking_params)
    return ChatAnthropic(**kwargs)


def _build_google(
    provider, model, decision, intensity, option: ThinkingOption | None = None
):
    from langchain_google_genai import ChatGoogleGenerativeAI

    cap = int((getattr(provider, "model_max_output_tokens", None) or {}).get(model) or 8192)
    kwargs: dict[str, Any] = {
        "model": model,
        "google_api_key": provider.key,
        "max_output_tokens": cap,
        "timeout": 60,
        "max_retries": 1,
        "additional_headers": _common_headers(provider),
    }
    if decision.base_url:
        kwargs["base_url"] = decision.base_url
    if intensity is not None:
        kwargs.update(
            get_format_thinking_params(
                "google_gemini", provider.thinking_type, intensity, option=option
            )
        )
    return ChatGoogleGenerativeAI(**kwargs)


# ── 结构化 JSON 调用（内部功能共用） ──


def parse_json_object(text: str) -> dict[str, Any]:
    """从模型输出里取出第一个 JSON 对象（容忍 ```json 围栏与前后废话）。

    解析失败抛 ValueError：调用方（记忆抽取等）需要明确知道「模型没按格式
    输出」，而不是拿到空 dict 后静默写坏数据。
    """
    import json

    raw = str(text or "").strip()
    if not raw:
        raise ValueError("模型返回为空，无法解析 JSON")
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型返回不是合法 JSON: {exc}") from exc
    raise ValueError("模型返回里找不到 JSON 对象")


async def ainvoke_json(model: Any, messages: list[Any], api_format: str) -> dict[str, Any]:
    """按格式发起一次「要求 JSON 输出」的调用并解析结果。

    `response_format={"type": "json_object"}` 只有 OpenAI Chat Completions 支持；
    直接把它 bind 到 Anthropic/Gemini/Responses 上会被上游拒绝（400 或
    unsupported parameter）。其余格式改为「只在 prompt 里要求 JSON + 本地解析」，
    仍然严格校验，解析失败就抛错。
    """
    runnable = model
    if api_format == "openai_chat":
        try:
            runnable = model.bind(
                temperature=0.1, response_format={"type": "json_object"}
            )
        except Exception as exc:  # noqa: BLE001 - 网关不支持就退回纯 prompt 约束
            log.debug("模型不支持 response_format=json_object，改用 prompt 约束: %s", exc)
    response = await runnable.ainvoke(messages)
    from faust_backend.runtime.state import message_content_to_text

    return parse_json_object(message_content_to_text(getattr(response, "content", "")))


# ── 关闭客户端 ──

async def close_chat_model(model: Any) -> None:
    """关闭模型持有的 HTTP 客户端（不同格式的模型类实现不同）。

    不关闭会在 GC 时触发 openai SDK 的 `__del__` → `create_task(aclose())`，
    事件循环关闭后表现为 "Event loop is closed" 报错。
    """
    if model is None:
        return
    try:
        from langchain_openai import ChatOpenAI

        if isinstance(model, ChatOpenAI):
            async_client = model.root_async_client
            sync_client = model.root_client
            if async_client is not None and not async_client.is_closed():
                await async_client.close()
            if sync_client is not None and not sync_client.is_closed():
                sync_client.close()
            return
    except Exception as exc:  # noqa: BLE001 - 关闭失败不应影响调用方
        log.debug("关闭 ChatOpenAI 客户端失败: %s", exc)

    try:
        from langchain_anthropic import ChatAnthropic

        if isinstance(model, ChatAnthropic):
            client = model._async_client
            if client is not None:
                await client.close()
            return
    except Exception as exc:  # noqa: BLE001
        log.debug("关闭 ChatAnthropic 客户端失败: %s", exc)


def summarize_route_sources(
    routes: dict[str, dict[str, Any]] | None,
) -> dict[str, int]:
    """统计目录路由里各格式数量（UI 概览用）。"""
    counts: dict[str, int] = {fmt: 0 for fmt in API_FORMATS}
    for route in (routes or {}).values():
        fmt = _catalog_format(route)
        if fmt:
            counts[fmt] += 1
    return counts


def decision_payloads(providers: Any) -> list[dict[str, Any]]:
    """管理端 `/providers` 用的扁平列表（provider 级概览）。"""
    payloads: list[dict[str, Any]] = []
    for provider in getattr(providers, "providers", None) or []:
        decisions = describe_provider_formats(provider)
        counts: dict[str, int] = {fmt: 0 for fmt in API_FORMATS}
        for item in decisions.values():
            fmt = item.get("api_format")
            if fmt in counts:
                counts[fmt] += 1
        payloads.append(
            {
                "provider": provider.name,
                "catalog_provider_id": getattr(provider, "catalog_provider_id", None),
                "formats": counts,
                "models": decisions,
            }
        )
    return payloads
