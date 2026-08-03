from __future__ import annotations

from openai import AsyncOpenAI

import faust_backend.config_loader as conf
from faust_backend.memory.config import EMBED_MODEL


def _provider_fallback_key() -> str:
    from faust_backend.runtime import state as runtime_state
    from faust_backend.provider import get_main_credentials
    _, _fallback_key, _ = get_main_credentials(runtime_state.get_model_providers())
    return _fallback_key


def build_openai_client() -> AsyncOpenAI:
    api_key = conf.EMBED_API_KEY or _provider_fallback_key()
    base_url = conf.EMBED_API_BASE or "https://api.openai.com/v1"
    if not api_key:
        raise RuntimeError("缺少可用的 Embedding API Key，无法构建 embedding")
    return AsyncOpenAI(api_key=api_key, base_url=base_url)
