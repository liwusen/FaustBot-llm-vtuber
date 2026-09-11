import json
import uuid
from pydantic import BaseModel
from typing import List, Optional
from langchain_openai import ChatOpenAI
from faust_backend.thinking import (
            ReasoningChatOpenAI,
            get_thinking_params,
            THINKING_PRESETS
        )

# 对外统一标识本客户端（FaustBot v3，langchain 栈）。不设置时 openai-python /
# httpx 会以自身 UA 发请求（AsyncOpenAI/Python x.y.z、python-httpx/x.y），部分
# 网关据此做来源识别或拦截，因此所有 LLM 与 /models 请求都显式携带该 UA。
FAUSTBOT_USER_AGENT = "faustbot/3 (langchain)"

# OpenCode Go 要求每个会话携带稳定 session ID（用于路由优化与 prompt 缓存）。
# 进程生命周期内保持稳定，重启后更换。
_OPENCODE_SESSION_ID = uuid.uuid4().hex
OPENCODE_DEFAULT_HEADERS = {
    "x-opencode-session": _OPENCODE_SESSION_ID,
    "User-Agent": FAUSTBOT_USER_AGENT,
}


def opencode_headers(extra: dict | None = None) -> dict:
    headers = dict(OPENCODE_DEFAULT_HEADERS)
    if extra:
        headers.update(extra)
    return headers

class ModelProviders(BaseModel):
    providers: List['ModelProvider'] = []
    main_model: Optional[str] = None  # ["deepseek::deepseek-v4-pro", "deepseek::deepseek-v4", "qwen::qwen-7b-chat"]
    subagent_models: Optional[List[str]] = None  # ["deepseek::deepseek-v4-pro", "deepseek::deepseek-v4"]

class ModelProvider(BaseModel):
    name: str
    base_url: str
    key: Optional[str] = None
    models: List[str] = []
    thinking_type: str = "qwen"  # Default thinking type (qwen/deepseek/openai/none/mimo/glm/minimax)
    opencode_go: bool = False  # OpenCode Go 订阅适配: 自动附带 x-opencode-session 会话头

ModelProvider.model_rebuild()  # Rebuild the model to resolve forward references
ModelProviders.model_rebuild()  # Rebuild the model to resolve forward references

async def get_provider_models_by_api(provider: ModelProvider) -> List[str]:
    """从 provider 的 GET {base_url}/models 拉取模型列表。

    带 10s 超时；对非 OpenAI 兼容的响应结构做容错解析，
    任何异常都会抛出明确的 ValueError（前端向导据此提示用户）。
    """
    import httpx
    headers = {"User-Agent": FAUSTBOT_USER_AGENT}
    if provider.key:
        headers["Authorization"] = f"Bearer {provider.key}"
    if provider.opencode_go:
        headers.update(opencode_headers())
    try:
        # 注意：不能用 urljoin(base_url, "/models")——"/models" 是绝对路径会
        # 丢弃 base_url 的路径前缀（如 /v1、/compatible-mode/v1），导致 404/502。
        models_url = str(provider.base_url or "").rstrip("/") + "/models"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(models_url, headers=headers)
            response.raise_for_status()
            models_data = response.json()
    except httpx.HTTPError as exc:
        raise ValueError(f"请求 {provider.base_url}/models 失败: {exc}") from exc

    res = []
    # OpenAI 兼容: {"data": [{"id": "..."}]}
    data = models_data.get("data") if isinstance(models_data, dict) else None
    if isinstance(data, list):
        for item in data:
            mid = item.get("id") if isinstance(item, dict) else None
            if mid:
                res.append(str(mid))
    # 兜底: 直接是字符串数组
    elif isinstance(models_data, list):
        res = [str(m) for m in models_data if str(m).strip()]
    if not res:
        raise ValueError(f"无法从 {provider.base_url}/models 解析模型列表")
    return res

async def auto_load_model_for_provider(provider: ModelProvider, force: bool = False) -> List[str]:
    """自动从 provider API 拉取模型列表并写入 provider.models。

    前端"交互式模型添加向导"在用户填完 name/base_url/key 后调用本函数，
    通过 provider 的 GET {base_url}/models 接口获取可用模型列表。

    force=False（默认，构建 LLM 的热路径）：已有 models 时跳过网络请求（幂等）。
    force=True（前端「自动加载模型」按钮）：丢弃已有 models 后重新拉取，保证
    列表与 Provider 端一致；拉取失败时抛错，旧列表不被清空。
    """
    if force or not provider.models:
        provider.models = await get_provider_models_by_api(provider)
    return provider.models

async def build_ReasoningChatOpenAI_from_spec(providers: ModelProviders, spec:str="deepseek::deepseek-v4-pro",intensity:str|None = "medium")-> ReasoningChatOpenAI|ChatOpenAI:
    """Build a ReasoningChatOpenAI instance from a model specification string.

    Args:
        providers (ModelProviders): The model providers instance.
        spec (str, optional): The model specification string, format 'provider::model'.
        intensity (str | None, optional): Thinking intensity preset; None disables thinking.

    Raises:
        ValueError: If the provider or model is not found, or spec is malformed.

    Returns:
        ReasoningChatOpenAI: The model instance built from the specification.
    """
    provider_name, model_name = parse_spec(spec)
    provider = resolve_provider(providers, provider_name)
    await auto_load_model_for_provider(provider)
    # [R3] 容忍 main_model 不在已加载 models 列表（迁移/离线场景）：
    # 仅当 models 列表非空且模型不在其中时才报错；models 为空（加载失败）
    # 时直接使用用户显式指定的模型名，避免启动即崩。
    if provider.models and model_name not in provider.models:
        raise ValueError(f"Model '{model_name}' not found for provider '{provider_name}'.")

    kwargs = dict(
            model=model_name,
            api_key=provider.key,
            base_url=provider.base_url,
            request_timeout=60,
            max_retries=1,
    )
    default_headers = {"User-Agent": FAUSTBOT_USER_AGENT}
    if provider.opencode_go:
        default_headers = opencode_headers(default_headers)
    kwargs["default_headers"] = default_headers
    # [R5] thinking 开关语义：provider.thinking_type == "none" 时强制关闭思考
    # （无论 intensity 传什么），与旧 THINKING_ENABLED=False 默认行为保持一致，
    # 避免重构后所有对话意外开启推理。
    if intensity is not None and provider.thinking_type != "none":
        # provider.thinking_type 是 thinking 预设名（qwen/deepseek/openai/none/mimo/glm/minimax），
        # 与 thinking.THINKING_PRESETS 的 key 对应；intensity 是低/中/高。
        thinking_params = get_thinking_params(provider.thinking_type, intensity)
        if "reasoning_effort" in thinking_params:
            kwargs["reasoning_effort"] = thinking_params.pop("reasoning_effort")
        model_kw = thinking_params.pop("model_kwargs", {})
        extra = {
            **thinking_params.pop("extra_body", {}),
            **kwargs.get("extra_body", {}),#type: ignore
        }
        if extra:
            kwargs["extra_body"] = extra#type: ignore
        kwargs["model_kwargs"] = {**kwargs.get("model_kwargs", {}), **model_kw}#type: ignore
        return ReasoningChatOpenAI(**kwargs)#type: ignore
    else:
        return ChatOpenAI(**kwargs)#type: ignore

async def build_main_chat_model(
    providers: ModelProviders, intensity: str | None = None
) -> ReasoningChatOpenAI | ChatOpenAI:
    """按 main_model 构建统一配置的 LLM（含 opencode_go 头、thinking 参数）。

    供主 Agent 之外的内部功能（Araya、安全审核等）复用，确保所有基于
    main_model 的功能都走同一套 provider 配置逻辑，不各自拼装 ChatOpenAI。

    Args:
        providers: 模型 provider 集合。
        intensity: thinking 强度；None 表示不启用思考。

    Raises:
        RuntimeError: main_model 未配置。
    """
    if not providers or not providers.main_model:
        raise RuntimeError("main_model is not configured (provider.private.json)")
    return await build_ReasoningChatOpenAI_from_spec(
        providers, spec=providers.main_model, intensity=intensity
    )


def new_provider(ModelProviders: ModelProviders, name: str, base_url: str, key: Optional[str] = None) -> ModelProvider:
    """Create a new model provider and add it to the ModelProviders instance.

    Args:
        ModelProviders (ModelProviders): The model providers instance.
        name (str): The name of the new provider.
        base_url (str): The base URL of the new provider.
        key (Optional[str], optional): The API key for the new provider. Defaults to None.

    Returns:
        ModelProvider: The newly created model provider.
    """
    provider = ModelProvider(name=name, base_url=base_url, key=key)
    ModelProviders.providers.append(provider)
    return provider

def remove_provider(ModelProviders: ModelProviders, name: str) -> bool:
    """Remove a model provider from the ModelProviders instance by name.

    Args:
        ModelProviders (ModelProviders): The model providers instance.
        name (str): The name of the provider to remove.

    Returns:
        bool: True if the provider was found and removed, False otherwise.
    """
    for i, provider in enumerate(ModelProviders.providers):
        if provider.name == name:
            del ModelProviders.providers[i]
            return True
    return False

def remove_model_from_provider(ModelProviders: ModelProviders, provider_name: str, model_name: str) -> bool:
    """Remove a model from a specific provider in the ModelProviders instance.

    Args:
        ModelProviders (ModelProviders): The model providers instance.
        provider_name (str): The name of the provider.
        model_name (str): The name of the model to remove.

    Returns:
        bool: True if the model was found and removed, False otherwise.
    """
    provider = next((p for p in ModelProviders.providers if p.name == provider_name), None)
    if not provider:
        return False
    if model_name in provider.models:
        provider.models.remove(model_name)
        return True
    return False

def loads(path:str) -> ModelProviders:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return ModelProviders.model_validate(data)

def dumps(providers: ModelProviders, path:str):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(providers.model_dump(), f, ensure_ascii=False, indent=4)

# ── 补全：模型管理辅助 ──


def resolve_provider(providers: ModelProviders, provider_name: str) -> ModelProvider:
    """按名称查找 provider，找不到抛 ValueError。"""
    provider = next((p for p in providers.providers if p.name == provider_name), None)
    if not provider:
        raise ValueError(f"Provider '{provider_name}' not found.")
    return provider


def parse_spec(spec: str) -> tuple[str, str]:
    """解析 'provider::model' 字符串为 (provider_name, model_name)。"""
    text = str(spec or "").strip()
    if "::" not in text:
        raise ValueError(f"Invalid model spec '{spec}', expected 'provider::model'.")
    provider_name, model_name = text.split("::", 1)
    if not provider_name or not model_name:
        raise ValueError(f"Invalid model spec '{spec}', expected 'provider::model'.")
    return provider_name, model_name


def get_main_provider(providers: ModelProviders) -> ModelProvider:
    """返回 main_model 对应的 provider；无配置时抛 ValueError。"""
    if not providers.main_model:
        raise ValueError("main_model is not configured.")
    provider_name, _ = parse_spec(providers.main_model)
    return resolve_provider(providers, provider_name)


def get_main_credentials(providers: ModelProviders) -> tuple[str, str, str]:
    """返回 (model_name, api_key, base_url) 基于 main_model 对应的 provider。

    main_model 未配置时返回 ("", "", "")，不抛异常（调用方决定降级行为）。
    """
    if not providers.main_model:
        return "", "", ""
    try:
        provider_name, model_name = parse_spec(providers.main_model)
    except ValueError:
        return "", "", ""
    provider = next((p for p in providers.providers if p.name == provider_name), None)
    if not provider:
        return "", "", ""
    return model_name, provider.key or "", provider.base_url


def get_default_subagent_model(providers: ModelProviders) -> str:
    """返回默认 Subagent 模型 spec：subagent_models[0]，空则回退 main_model。"""
    if providers.subagent_models:
        return providers.subagent_models[0]
    if providers.main_model:
        return providers.main_model
    raise ValueError("No subagent model configured: set subagent_models or main_model.")


def is_subagent_model_allowed(providers: ModelProviders, spec: str) -> bool:
    """校验 spec 是否在 subagent_models 白名单内（或等于 main_model）。"""
    if spec in (providers.subagent_models or []):
        return True
    return spec == providers.main_model
