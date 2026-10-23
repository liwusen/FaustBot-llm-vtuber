import json
import uuid
from pydantic import BaseModel
from typing import Dict, List, Optional
from langchain_openai import ChatOpenAI
from faust_backend.llm_formats import API_FORMATS
from faust_backend.thinking import ReasoningChatOpenAI

# 对外统一标识本客户端（FaustBot v3，langchain 栈）。不设置时 openai-python /
# httpx 会以自身 UA 发请求（AsyncOpenAI/Python x.y.z、python-httpx/x.y），部分
# 网关据此做来源识别或拦截，因此所有 LLM 与 /models 请求都显式携带该 UA。
FAUSTBOT_USER_AGENT = "faustbot/3 (langchain)"

# 模型未配置上下文长度时使用的默认值（token）。DeepSeek / Qwen 等主流模型
# 均为 128k 量级，作为默认值可让未显式配置的模型直接获得可用的 auto compact。
DEFAULT_CONTEXT_LENGTH = 128_000

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
    # 模型名 → 上下文长度（token）。未配置的模型不出现在此 dict 中，
    # 默认值（128000）由调用方在使用时决定，provider 层不填默认值。
    model_context_lengths: Dict[str, int] = {}
    thinking_type: str = "qwen"  # Default thinking type (qwen/deepseek/openai/none/mimo/glm/minimax)
    opencode_go: bool = False  # OpenCode Go 订阅适配: 自动附带 x-opencode-session 会话头

    # ── models.dev 目录（持久化缓存，构建模型时无需联网） ──
    #: 匹配到的 models.dev provider id；None = 由程序自动匹配，用户可 pin
    catalog_provider_id: Optional[str] = None
    #: model → {format, base_url, host_matched, npm, shape}；刷新目录时整表重算
    model_catalog_routes: Dict[str, dict] = {}
    #: 上次用目录刷新本 Provider 的时间（epoch 秒）
    catalog_synced_at: Optional[float] = None

    # ── 用户覆盖与能力 ──
    #: Provider 级默认格式：auto / openai_chat / openai_responses /
    #: anthropic_messages / google_gemini
    api_format: str = "auto"
    #: 单模型显式覆盖（只存用户显式设置，自动推断结果不落盘到这里）
    model_api_formats: Dict[str, str] = {}
    #: 格式 → SDK 根地址；覆盖目录与推导结果
    format_base_urls: Dict[str, str] = {}
    #: 模型 → 最大输出 token（用户 > /models > models.dev，只填空缺）
    model_max_output_tokens: Dict[str, int] = {}
    #: 模型 → /models 上报的能力声明（规范化后，如 anthropic_messages）
    model_api_capabilities: Dict[str, dict] = {}
    #: 模型 → 用户显式启用的服务端工具名（web_search）
    model_server_tools: Dict[str, List[str]] = {}
    #: 流式是否请求用量统计；None = 按格式默认
    stream_usage: Optional[bool] = None

ModelProvider.model_rebuild()  # Rebuild the model to resolve forward references
ModelProviders.model_rebuild()  # Rebuild the model to resolve forward references

async def fetch_provider_model_entries(provider: ModelProvider) -> List[dict]:
    """从 provider 的 GET {base_url}/models 拉取模型条目（含元数据）。

    带 10s 超时；对非 OpenAI 兼容的响应结构做容错解析，
    任何异常都会抛出明确的 ValueError（前端向导据此提示用户）。

    返回 `[{"id": str, "context_length": int | None, "max_output_tokens": int | None,
    "api_capabilities": dict}]`。

    各 Provider 的元数据丰富程度差异很大（实测）：
      - deepseek : context_window / max_output_tokens / input_modalities /
                   output_modalities / effort / api_capabilities
      - OpenRouter: context_length / architecture / pricing / top_provider /
                   supported_parameters / knowledge_cutoff / reasoning / benchmarks
      - aliyun / OpenCode Go: 只有 id / object / created / owned_by
    只解析本项目真正消费的字段（上下文长度、最大输出、能力声明）；其余字段
    暂无使用方，解析它们只会变成死代码。
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

    entries: List[dict] = []
    # OpenAI 兼容: {"data": [{"id": "..."}]}
    data = models_data.get("data") if isinstance(models_data, dict) else None
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            mid = item.get("id")
            if not mid:
                continue
            entries.append(_model_entry(str(mid), item))
    # 兜底: 直接是字符串数组（无元数据）
    elif isinstance(models_data, list):
        entries = [
            _model_entry(str(m), None) for m in models_data if str(m).strip()
        ]
    if not entries:
        raise ValueError(f"无法从 {provider.base_url}/models 解析模型列表")
    return entries


def _model_entry(model_id: str, item: dict | None) -> dict:
    """把 /models 的一条记录规范成本项目消费的模型条目。"""
    source = item or {}
    return {
        "id": model_id,
        "context_length": _extract_context_length(source),
        "max_output_tokens": _extract_max_output_tokens(source),
        "api_capabilities": _extract_api_capabilities(source),
    }


def _extract_api_capabilities(item: dict) -> dict:
    """从 /models 条目里提取「该模型支持哪些 API 格式」的能力声明。

    只保留已知格式键（未知键没有消费方，留着只会让配置持续膨胀）；值保持
    原始 dict，这样上游新增的约束字段（如 anthropic_messages 的 beta 列表）
    不需要改代码就能被前端展示。
    """
    caps = item.get("api_capabilities")
    if not isinstance(caps, dict):
        return {}
    return {
        str(key): value
        for key, value in caps.items()
        if str(key) in API_FORMATS and isinstance(value, dict)
    }


def _extract_max_output_tokens(item: dict) -> Optional[int]:
    """从 /models 条目里提取最大输出 token（字段名随 Provider 而异）。"""
    for key in ("max_output_tokens", "max_completion_tokens"):
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    top_provider = item.get("top_provider")
    if isinstance(top_provider, dict):
        value = top_provider.get("max_completion_tokens")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return None


def _extract_context_length(item: dict) -> Optional[int]:
    """从 /models 条目里提取上下文长度（token）。

    字段名随 Provider 而异：OpenRouter 用 `context_length`，DeepSeek 用
    `context_window`。取不到或值非法时返回 None（调用方保留「未配置」状态，
    由 DEFAULT_CONTEXT_LENGTH 兜底）。
    """
    for key in ("context_length", "context_window"):
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
    # OpenRouter 部分条目把 context_length 也放在 top_provider 下
    top_provider = item.get("top_provider")
    if isinstance(top_provider, dict):
        value = top_provider.get("context_length")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
            return int(value)
    return None


async def get_provider_models_by_api(provider: ModelProvider) -> List[str]:
    """从 provider 的 GET {base_url}/models 拉取模型名列表（只要 id）。"""
    return [entry["id"] for entry in await fetch_provider_model_entries(provider)]


async def auto_load_model_for_provider(provider: ModelProvider, force: bool = False) -> List[str]:
    """自动从 provider API 拉取模型列表并写入 provider.models。

    前端"交互式模型添加向导"在用户填完 name/base_url/key 后调用本函数，
    通过 provider 的 GET {base_url}/models 接口获取可用模型列表。

    force=False（默认，构建 LLM 的热路径）：已有 models 时跳过网络请求（幂等）。
    force=True（前端「自动加载模型」按钮）：丢弃已有 models 后重新拉取，保证
    列表与 Provider 端一致；拉取失败时抛错，旧列表不被清空。

    同时用 API 返回的元数据**自动填充**：`model_context_lengths`、
    `model_max_output_tokens`、`model_api_capabilities`：
      - 只填空缺项，**不覆盖用户手工配置的值**（用户配置优先）；
      - force=True 时顺带清理已不在模型列表中的陈旧条目。
    """
    if not (force or not provider.models):
        return provider.models

    entries = await fetch_provider_model_entries(provider)
    provider.models = [entry["id"] for entry in entries]

    lengths = dict(provider.model_context_lengths or {})
    outputs = dict(provider.model_max_output_tokens or {})
    capabilities = dict(provider.model_api_capabilities or {})
    if force:
        # 显式刷新：丢弃已不在列表中的模型（其元数据也就无从生效）
        keep = set(provider.models)
        lengths = {k: v for k, v in lengths.items() if k in keep}
        outputs = {k: v for k, v in outputs.items() if k in keep}
        capabilities = {k: v for k, v in capabilities.items() if k in keep}
    filled = 0
    for entry in entries:
        model_id = entry["id"]
        reported = entry.get("context_length")
        if reported is not None and not lengths.get(model_id):
            lengths[model_id] = reported
            filled += 1
        reported_output = entry.get("max_output_tokens")
        if reported_output is not None and not outputs.get(model_id):
            outputs[model_id] = reported_output
        reported_caps = entry.get("api_capabilities")
        if reported_caps and not capabilities.get(model_id):
            capabilities[model_id] = reported_caps
    provider.model_context_lengths = lengths
    provider.model_max_output_tokens = outputs
    provider.model_api_capabilities = capabilities
    if filled:
        print(
            f"[provider] 已从 /models 元数据自动填充 {filled} 个模型的上下文长度"
            f"（provider={provider.name}）"
        )
    return provider.models

async def build_ReasoningChatOpenAI_from_spec(
    providers: ModelProviders,
    spec: str = "deepseek::deepseek-v4-pro",
    intensity: str | None = "medium",
) -> ReasoningChatOpenAI | ChatOpenAI:
    """按模型 spec 构建对应 API 格式的 LangChain Chat 模型（兼容入口）。

    真正的格式解析与构建在 `faust_backend.llm_formats.build_chat_model_from_spec`：
    一个 Provider 可能同时提供 OpenAI Chat / OpenAI Responses / Anthropic
    Messages / Gemini 四种格式的模型（如聚合网关），必须按模型分别构建。
    本函数保留旧名字，避免大量既有调用点与测试被迫改动。

    Args:
        providers: 模型 provider 集合。
        spec: `provider::model`。
        intensity: thinking 强度；None 关闭思考。

    Raises:
        ValueError: provider/model 不存在，或无法为该模型确定可用端点。
    """
    from faust_backend.llm_formats import build_chat_model_from_spec

    return await build_chat_model_from_spec(
        providers, spec=spec, intensity=intensity, purpose="chat"
    )


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


def get_context_length(providers: ModelProviders, spec: str) -> int:
    """返回 spec 对应模型的上下文长度（token）。

    未配置该模型、或 spec 无法解析、或 provider 不存在时，回退到
    DEFAULT_CONTEXT_LENGTH（128000）。provider 层不写回默认值，
    保证「未配置」这一状态在持久化数据里始终可辨。
    """
    try:
        provider_name, model_name = parse_spec(spec)
    except ValueError:
        return DEFAULT_CONTEXT_LENGTH
    provider = next((p for p in providers.providers if p.name == provider_name), None)
    if provider is None:
        return DEFAULT_CONTEXT_LENGTH
    configured = (provider.model_context_lengths or {}).get(model_name)
    if isinstance(configured, int) and configured > 0:
        return configured
    return DEFAULT_CONTEXT_LENGTH


def try_main_openai_credentials(providers: ModelProviders) -> tuple[str, str, str] | None:
    """返回 main_model 的 OpenAI **Chat 兼容**端点；不是该格式时返回 None。

    很多内部功能（GUI 操作模型、QA eval、rerank、embedding、语音）仍假定
    OpenAI Chat Completions 协议。主模型若是 Anthropic/Gemini/Responses
    格式，这些功能**不能**拿 base_url 直接拼 `/chat/completions`——必须让
    调用方明确知道不可用，而不是发一个必然 404/400 的请求。
    """
    from faust_backend.llm_formats import resolve_route

    if not providers or not providers.main_model:
        return None
    try:
        provider_name, model_name = parse_spec(providers.main_model)
    except ValueError:
        return None
    provider = next(
        (p for p in (getattr(providers, "providers", None) or []) if p.name == provider_name),
        None,
    )
    if provider is None:
        return None
    try:
        decision = resolve_route(provider, model_name)
    except ValueError:
        return None
    if decision.api_format not in ("openai_chat", "openai_responses"):
        return None
    # 显式配置的 openai_chat 端点优先：主模型走 Responses/Anthropic 时，
    # 内部功能仍可指向同一 Provider 的 Chat Completions 路径。
    explicit_chat = (provider.format_base_urls or {}).get("openai_chat")
    if explicit_chat:
        from faust_backend.llm_formats import normalize_format_base_url

        normalized = normalize_format_base_url("openai_chat", explicit_chat)
        if normalized:
            return model_name, provider.key or "", normalized
    return model_name, provider.key or "", decision.base_url or provider.base_url


def get_main_openai_credentials(providers: ModelProviders) -> tuple[str, str, str]:
    """同 `try_main_openai_credentials`，但不可用时抛明确的 RuntimeError。

    错误信息直接告诉用户该怎么修：给该 Provider 配置一个 OpenAI 兼容端点，
    或把对应的内部功能指向一个独立的 OpenAI 兼容 Provider。
    """
    credentials = try_main_openai_credentials(providers)
    if credentials is None:
        spec = getattr(providers, "main_model", "") or "(未配置)"
        raise RuntimeError(
            f"主模型 {spec} 不是 OpenAI Chat 兼容格式，当前功能需要 OpenAI 兼容端点。"
            "请在配置器中为该 Provider 设置 format_base_urls['openai_chat']，"
            "或把主模型指向一个 OpenAI 兼容的 Provider。"
        )
    return credentials


def get_provider_decisions(providers: ModelProviders) -> dict[str, dict[str, dict]]:
    """返回 `{provider: {model: 格式决策}}`，供管理接口/前端展示。"""
    from faust_backend.llm_formats import describe_all_formats

    return describe_all_formats(providers)


def get_model_api_format(providers: ModelProviders, spec: str) -> str:
    """返回 spec 对应的 API 格式；无法解析时按 openai_chat 处理。"""
    from faust_backend.llm_formats import resolve_route

    try:
        provider_name, model_name = parse_spec(spec)
    except ValueError:
        return "openai_chat"
    provider = next(
        (p for p in (getattr(providers, "providers", None) or []) if p.name == provider_name),
        None,
    )
    if provider is None:
        return "openai_chat"
    try:
        return resolve_route(provider, model_name).api_format
    except ValueError:
        return "openai_chat"



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
