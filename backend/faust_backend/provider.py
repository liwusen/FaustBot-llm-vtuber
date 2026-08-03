import json
from pydantic import BaseModel
from typing import List, Optional
from urllib.parse import urljoin
from langchain.chat_models import ChatOpenAI
from faust_backend.thinking import (
            ReasoningChatOpenAI,
            get_thinking_params,
            THINKING_PRESET
        )

class ModelProviders(BaseModel):
    providers: List['ModelProvider'] = []
    main_model: Optional[str] = None  # ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4", "qwen/qwen-7b-chat", "qwen/qwen-7b-chat-int4", "qwen/qwen-7b-chat-int8"]
    subagent_models: Optional[List[str]] = None  # ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4", "qwen/qwen-7b-chat", "qwen/qwen-7b-chat-int4", "qwen/qwen-7b-chat-int8"]

class ModelProvider(BaseModel):
    name: str
    base_url: str
    key: Optional[str] = None
    models: List[str] = []
    thinking_type: str = "qwen"  # Default thinking type

ModelProvider.model_rebuild()  # Rebuild the model to resolve forward references
ModelProviders.model_rebuild()  # Rebuild the model to resolve forward references

async def get_provider_models_by_api(provider: ModelProvider) -> List[str]:
    import httpx
    async with httpx.AsyncClient() as client:
        response = await client.get(urljoin(provider.base_url, "/models"), headers={"Authorization": f"Bearer {provider.key}"})
        response.raise_for_status()
        models_data = response.json()
        res = []
        for model_data in models_data['data']:
            res.append(model_data['id'])
    return res

async def auto_load_model_for_provider(provider: ModelProvider) -> List[str]:
    if not provider.models:
        provider.models = await get_provider_models_by_api(provider)
    return provider.models

async def build_ReasoningChatOpenAI_from_spec(providers: ModelProviders, spec:str="deepseek::deepseek-v4-pro",intensity:str|None = "medium")-> ReasoningChatOpenAI:
    """Build a ReasoningChatOpenAI instance from a model specification string.

    Args:
        providers (ModelProviders): The model providers instance.
        spec (str, optional): The model specification string. Defaults to "deepseek::deepseek-v4-pro".

    Raises:
        ValueError: If the provider is not found.
        ValueError: If the model is not found for the provider.

    Returns:
        ReasoningChatOpenAI: The ReasoningChatOpenAI instance built from the specification.
    """
    provider_name, model_name = spec.split("::")
    provider = next((p for p in providers.providers if p.name == provider_name), None)
    if not provider:
        raise ValueError(f"Provider '{provider_name}' not found.")
    await auto_load_model_for_provider(provider)
    if model_name not in provider.models:
        raise ValueError(f"Model '{model_name}' not found for provider '{provider_name}'.")

    kwargs = dict(
            model=model_name,
            api_key=provider.key,
            base_url=provider.base_url,
            request_timeout=60,
            max_retries=1,
    )
    if intensity is not None:
        if intensity not in THINKING_PRESET:
            raise ValueError(f"Intensity '{intensity}' not found in THINKING_PRESET.")
        
        thinking_params = get_thinking_params(provider.thinking_type, intensity)
        if "reasoning_effort" in thinking_params:
            kwargs["reasoning_effort"] = thinking_params.pop("reasoning_effort")
        model_kw = thinking_params.pop("model_kwargs", {})
        extra = {
            **thinking_params.pop("extra_body", {}),
            **kwargs.get("extra_body", {}),
        }
        if extra:
            kwargs["extra_body"] = extra
        kwargs["model_kwargs"] = {**kwargs.get("model_kwargs", {}), **model_kw}
        return ReasoningChatOpenAI(**kwargs)
    else:
        return ChatOpenAI(**kwargs)

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

async def loads(path:str) -> ModelProviders:
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return ModelProviders.model_validate(data)

async def dumps(providers: ModelProviders, path:str):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(providers.model_dump(), f, ensure_ascii=False, indent=4)
