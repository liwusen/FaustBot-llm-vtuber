from __future__ import annotations

from openai import AsyncOpenAI

import faust_backend.config_loader as conf
from faust_backend.memory.config import EMBED_MODEL


def build_openai_client() -> AsyncOpenAI:
    api_key = conf.EMBED_API_KEY or conf.CHAT_API_KEY
    base_url = conf.EMBED_API_BASE or "https://api.openai.com/v1"
    if not api_key:
        raise RuntimeError("缺少可用的 Embedding API Key，无法构建 embedding")
    return AsyncOpenAI(api_key=api_key, base_url=base_url)
