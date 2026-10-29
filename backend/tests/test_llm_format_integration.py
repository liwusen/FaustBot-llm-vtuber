"""真实环境集成测试（默认跳过）。

两个开关互相独立：

- `FAUST_MODELS_DEV_IT=1`：真实拉取 models.dev 目录，断言目录结构可用，
  并对当前配置的 Provider 打印匹配/路由结果（不做硬编码 Provider 断言）。
- `FAUST_LLM_FORMAT_IT=1`：对当前 `provider.private.json` 里每个解析出的格式
  各发一次最小请求（"say hi"，16 token），断言拿到非空文本与 usage。

这些测试会真的花钱/走网络，因此 CI 与日常 `pytest` 必须保持跳过。
"""

from __future__ import annotations

import asyncio
import os

import pytest

MODELS_DEV_ENABLED = os.environ.get("FAUST_MODELS_DEV_IT") == "1"
LLM_FORMAT_ENABLED = os.environ.get("FAUST_LLM_FORMAT_IT") == "1"


@pytest.mark.skipif(not MODELS_DEV_ENABLED, reason="set FAUST_MODELS_DEV_IT=1 to run")
def test_models_dev_catalog_reachable():
    import faust_backend.llm.models_dev as models_dev

    catalog = asyncio.run(models_dev.refresh_catalog())

    assert isinstance(catalog, dict) and catalog
    # 目录里每个条目都必须是 {id, name?, models{}} 形状
    for provider_id, entry in catalog.items():
        assert isinstance(entry, dict), provider_id
        assert isinstance(entry.get("models", {}), dict), provider_id

    status = models_dev.catalog_status()
    assert status["loaded"] is True
    assert status["providers"] == len(catalog)


@pytest.mark.skipif(not MODELS_DEV_ENABLED, reason="set FAUST_MODELS_DEV_IT=1 to run")
def test_models_dev_matches_current_providers():
    import faust_backend.llm.models_dev as models_dev
    from faust_backend.runtime import state

    catalog = asyncio.run(models_dev.refresh_catalog())
    providers = state.get_model_providers()

    for provider in providers.providers:
        result = models_dev.enrich_provider(provider, catalog)
        print(
            f"{provider.name}: catalog={result['provider_id']} "
            f"host_matched={result['host_matched']} routed={result['routed']} "
            f"missing={len(result['missing'])}"
        )
        # 有匹配就必须至少路由出 1 个模型（否则说明匹配/解析有问题）
        if result["provider_id"]:
            assert result["routed"] > 0


@pytest.mark.skipif(not LLM_FORMAT_ENABLED, reason="set FAUST_LLM_FORMAT_IT=1 to run")
def test_each_resolved_format_answers():
    from langchain_core.messages import HumanMessage

    from faust_backend.llm.formats import resolve_route
    from faust_backend.runtime import state

    providers = state.get_model_providers()
    seen_formats: dict[str, str] = {}
    for provider in providers.providers:
        for model in provider.models:
            decision = resolve_route(provider, model)
            seen_formats.setdefault(decision.api_format, f"{provider.name}::{model}")

    assert seen_formats, "当前配置里没有任何可解析格式的模型"

    from faust_backend.llm.formats import build_chat_model_from_spec

    for api_format, spec in seen_formats.items():
        model = asyncio.run(
            build_chat_model_from_spec(providers, spec=spec, intensity=None)
        )
        message = model.invoke([HumanMessage(content="say hi")], max_tokens=16)
        text = message.content if isinstance(message.content, str) else str(message.content)
        print(f"{api_format} ({spec}) -> {text!r} usage={message.usage_metadata}")
        assert text.strip(), f"{api_format} 没有返回文本"
        # 用量是自动压缩的前提：这里必须能看到 API 上报值
        assert message.usage_metadata, f"{api_format} 没有上报 usage"
