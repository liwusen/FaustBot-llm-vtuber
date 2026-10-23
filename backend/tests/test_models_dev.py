"""models.dev 目录：下载/缓存、Provider 匹配、通用模型路由。

本文件刻意**不包含任何真实 Provider 的模型表**：所有断言都建立在通用规则上
（host 匹配、模型名重合、npm/shape/URL 路径），因此新增 Provider 时不需要改测试。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import faust_backend.config_loader as conf
import faust_backend.models_dev as models_dev
from faust_backend.provider import ModelProvider


# ── 通用 fixture：一个「多格式聚合网关」的目录结构（虚构名字，无 Provider 特判） ──


def _catalog() -> dict:
    return {
        "gateway": {
            "id": "gateway",
            "name": "Gateway",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://gateway.example.com/v1",
            "models": {
                "chat-model": {"limit": {"context": 128000, "output": 16384}},
                "claude-model": {
                    "provider": {
                        "npm": "@ai-sdk/anthropic",
                        "api": "https://gateway.example.com/anthropic",
                    },
                    "limit": {"context": 200000, "output": 32000},
                    "reasoning": True,
                },
                "gemini-model": {
                    "provider": {
                        "npm": "@ai-sdk/google",
                        "api": "https://gateway.example.com/google",
                    },
                    "limit": {"context": 1000000, "output": 65536},
                },
                "gpt-model": {
                    "provider": {
                        "npm": "@ai-sdk/openai",
                        "api": "https://gateway.example.com/v1",
                    },
                    "limit": {"context": 400000, "output": 128000},
                    "reasoning": True,
                },
            },
        },
        # 同 host 的另一个目录条目，只有 1 个重合模型：靠重合度区分
        "gateway-legacy": {
            "id": "gateway-legacy",
            "name": "Gateway Legacy",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://gateway.example.com/v1",
            "models": {"chat-model": {}},
        },
        "unrelated": {
            "id": "unrelated",
            "name": "Unrelated",
            "npm": "@ai-sdk/openai-compatible",
            "api": "https://unrelated.example.com/v1",
            "models": {"other-model": {}},
        },
    }


def _gateway_provider(**overrides) -> ModelProvider:
    defaults = dict(
        name="Gateway",
        base_url="https://gateway.example.com/v1",
        key="sk-test",
        models=["chat-model", "claude-model", "gemini-model", "gpt-model"],
    )
    defaults.update(overrides)
    return ModelProvider(**defaults)


@pytest.fixture(autouse=True)
def _isolated_catalog(tmp_path, monkeypatch):
    """每个用例独立的内存态 + 缓存目录。"""
    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(models_dev, "_catalog", None)
    monkeypatch.setattr(models_dev, "_catalog_loaded_at", 0.0)
    monkeypatch.setattr(models_dev, "_catalog_source", "")
    monkeypatch.setattr(models_dev, "_last_error", "")
    yield


# ── 下载与缓存 ──


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.content = payload

    def raise_for_status(self) -> None:
        return None


class _FakeClient:
    def __init__(self, payload: bytes | None = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[str] = []

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *args) -> None:
        return None

    async def get(self, url, headers=None, **kwargs):  # noqa: ANN001
        self.calls.append(url)
        if self.error is not None:
            raise self.error
        assert self.payload is not None
        return _FakeResponse(self.payload)


def _patch_client(monkeypatch, payload=None, error=None) -> _FakeClient:
    client = _FakeClient(payload=payload, error=error)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: client)
    return client


def _payload(catalog: dict | None = None) -> bytes:
    return json.dumps(catalog or _catalog()).encode("utf-8")


def test_refresh_writes_cache_atomically(monkeypatch, tmp_path):
    _patch_client(monkeypatch, payload=_payload())

    catalog = asyncio.run(models_dev.refresh_catalog())

    assert "gateway" in catalog
    cache_file = models_dev.cache_path()
    assert cache_file.startswith(str(tmp_path))
    assert json.loads(open(cache_file, "rb").read()) == _catalog()
    # 临时文件不得残留（原子写）
    leftovers = list(tmp_path.rglob("*.tmp"))
    assert leftovers == []
    status = models_dev.catalog_status()
    assert status["loaded"] is True
    assert status["providers"] == 3
    assert status["source"] == "network"


def test_refresh_rejects_non_json_and_keeps_memory(monkeypatch):
    _patch_client(monkeypatch, payload=b"not-json")

    with pytest.raises(ValueError):
        asyncio.run(models_dev.refresh_catalog())

    assert models_dev._catalog is None
    assert "解析 models.dev 目录失败" in models_dev.catalog_status()["last_error"]


def test_refresh_rejects_oversized_payload(monkeypatch):
    monkeypatch.setattr(models_dev, "MAX_BYTES", 16)
    _patch_client(monkeypatch, payload=_payload())

    with pytest.raises(ValueError) as excinfo:
        asyncio.run(models_dev.refresh_catalog())

    assert "过大" in str(excinfo.value)


def test_refresh_failure_falls_back_to_stale_cache(monkeypatch):
    _patch_client(monkeypatch, payload=_payload())
    asyncio.run(models_dev.refresh_catalog())

    # 清空内存态，模拟重启后联网失败
    monkeypatch.setattr(models_dev, "_catalog", None)
    monkeypatch.setattr(models_dev, "_catalog_loaded_at", 0.0)
    _patch_client(monkeypatch, error=httpx.ConnectError("boom"))

    catalog = asyncio.run(models_dev.ensure_catalog(force=True))

    assert "gateway" in catalog
    assert models_dev.catalog_status()["source"] == "cache"


def test_ensure_catalog_without_cache_or_network_returns_empty(monkeypatch):
    catalog = asyncio.run(models_dev.ensure_catalog(allow_network=False))
    assert catalog == {}
    assert models_dev.catalog_status()["loaded"] is False


def test_ensure_catalog_prefers_fresh_cache_over_network(monkeypatch):
    _patch_client(monkeypatch, payload=_payload())
    asyncio.run(models_dev.refresh_catalog())

    monkeypatch.setattr(models_dev, "_catalog", None)
    monkeypatch.setattr(models_dev, "_catalog_loaded_at", 0.0)
    client = _patch_client(monkeypatch, payload=_payload())

    catalog = asyncio.run(models_dev.ensure_catalog(allow_network=False))

    assert "gateway" in catalog
    assert client.calls == []  # 未联网


def test_corrupt_cache_is_ignored(monkeypatch):
    cache_file = models_dev.cache_path()
    import os

    os.makedirs(os.path.dirname(cache_file), exist_ok=True)
    with open(cache_file, "wb") as handle:
        handle.write(b"{broken")

    catalog = asyncio.run(models_dev.ensure_catalog(allow_network=False))
    assert catalog == {}


def test_validate_catalog_rejects_bad_shapes():
    with pytest.raises(ValueError):
        models_dev.validate_catalog([])
    with pytest.raises(ValueError):
        models_dev.validate_catalog({})
    with pytest.raises(ValueError):
        models_dev.validate_catalog({"p": "not-dict"})
    with pytest.raises(ValueError):
        models_dev.validate_catalog({"p": {"models": []}})
    assert models_dev.validate_catalog({"p": {"models": {}}}) == {"p": {"models": {}}}


# ── Provider 匹配 ──


def test_match_provider_prefers_higher_model_overlap_on_same_host():
    match = models_dev.match_provider(_gateway_provider(), _catalog())

    assert match is not None
    assert match.provider_id == "gateway"
    assert match.host_matched is True
    assert match.overlap == pytest.approx(1.0)
    # 候选里应同时出现 legacy（同 host、只有 1 个重合模型）
    candidate_ids = [item["provider_id"] for item in match.candidates]
    assert candidate_ids[0] == "gateway"
    assert "gateway-legacy" in candidate_ids


def test_match_provider_overlap_only_when_host_differs():
    provider = _gateway_provider(base_url="https://proxy.example.com/v1")

    match = models_dev.match_provider(provider, _catalog())

    assert match is not None
    assert match.provider_id == "gateway"
    assert match.host_matched is False
    assert match.overlap == pytest.approx(1.0)


def test_match_provider_overlap_only_requires_threshold():
    provider = _gateway_provider(
        name="Tiny", base_url="https://proxy.example.com/v1", models=["chat-model"]
    )

    # 1/4 重合 < 0.8 → 不能靠模型名硬匹配
    assert models_dev.match_provider(provider, _catalog()) is None


def test_match_provider_pinned_id_wins():
    provider = _gateway_provider(catalog_provider_id="gateway-legacy")

    match = models_dev.match_provider(provider, _catalog())

    assert match is not None
    assert match.provider_id == "gateway-legacy"


def test_match_provider_without_catalog_returns_none():
    assert models_dev.match_provider(_gateway_provider(), {}) is None


# ── 通用路由（无模型表） ──


@pytest.mark.parametrize(
    ("model", "expected_format", "expected_base"),
    [
        ("chat-model", "openai_chat", "https://gateway.example.com/v1"),
        ("claude-model", "anthropic_messages", "https://gateway.example.com/anthropic"),
        ("gemini-model", "google_gemini", "https://gateway.example.com/google"),
        ("gpt-model", "openai_responses", "https://gateway.example.com/v1"),
    ],
)
def test_route_model_infers_four_formats(model, expected_format, expected_base):
    route = models_dev.route_model(
        _catalog(), "gateway", model, host_matched=True
    )

    assert route is not None
    assert route.api_format == expected_format
    assert route.base_url == expected_base


def test_route_model_host_mismatch_keeps_user_base_url():
    provider_base = "https://proxy.example.com/v1"

    route = models_dev.route_model(
        _catalog(),
        "gateway",
        "claude-model",
        host_matched=False,
        provider_base_url=provider_base,
    )

    assert route is not None
    assert route.api_format == "anthropic_messages"
    # Anthropic SDK 自己追加 /v1/messages，因此根地址去掉 /v1
    assert route.base_url == "https://proxy.example.com"


def test_route_model_unknown_model_returns_none():
    assert (
        models_dev.route_model(_catalog(), "gateway", "nope", host_matched=True) is None
    )


def test_route_model_shape_completions_forces_chat():
    catalog = {
        "p": {
            "npm": "@ai-sdk/openai",
            "api": "https://x.example.com/v1",
            "models": {"m": {"provider": {"shape": "completions"}}},
        }
    }
    route = models_dev.route_model(catalog, "p", "m", host_matched=True)
    assert route is not None
    assert route.api_format == "openai_chat"


# ── enrich_provider ──


def test_enrich_provider_writes_routes_and_fills_missing_limits():
    provider = _gateway_provider()
    # 用户手填的值不能被目录覆盖
    provider.model_context_lengths = {"chat-model": 4096}

    result = models_dev.enrich_provider(provider, _catalog())

    assert result["provider_id"] == "gateway"
    assert result["routed"] == 4
    assert result["missing"] == []
    assert set(provider.model_catalog_routes) == set(provider.models)
    assert provider.model_catalog_routes["claude-model"]["format"] == "anthropic_messages"
    assert provider.model_catalog_routes["chat-model"]["reasoning"] is None
    assert provider.model_catalog_routes["claude-model"]["reasoning"] is True
    # 用户值保留，其余从目录补齐
    assert provider.model_context_lengths["chat-model"] == 4096
    assert provider.model_context_lengths["gemini-model"] == 1000000
    assert provider.model_max_output_tokens["claude-model"] == 32000
    assert provider.catalog_synced_at is not None


def test_enrich_provider_recomputes_routes_and_reports_missing():
    provider = _gateway_provider()
    provider.model_catalog_routes = {"stale-model": {"format": "openai_chat"}}

    result = models_dev.enrich_provider(provider, _catalog())

    assert "stale-model" not in provider.model_catalog_routes
    assert result["routed"] == 4


def test_enrich_provider_records_missing_models():
    provider = _gateway_provider(models=["chat-model", "not-in-catalog"])

    result = models_dev.enrich_provider(provider, _catalog())

    assert result["missing"] == ["not-in-catalog"]


def test_enrich_provider_without_catalog_keeps_config():
    provider = _gateway_provider()
    before = provider.model_dump()

    result = models_dev.enrich_provider(provider, {})

    assert result["provider_id"] is None
    after = provider.model_dump()
    assert after == {**before, "model_catalog_routes": {}, "catalog_synced_at": None}


def test_enrich_provider_never_uses_foreign_host_endpoint():
    provider = _gateway_provider(base_url="https://proxy.example.com/v1")

    models_dev.enrich_provider(provider, _catalog())

    for route in provider.model_catalog_routes.values():
        assert route["host_matched"] is False
        assert "gateway.example.com" not in (route["base_url"] or "")


def test_enrich_all_covers_every_provider():
    providers = type("P", (), {})()
    providers.providers = [_gateway_provider(), _gateway_provider(name="B", base_url="https://b.example.com/v1", models=["zzz"])]

    results = models_dev.enrich_all(providers, _catalog())

    assert [item["provider"] for item in results] == ["Gateway", "B"]
    assert results[1]["provider_id"] is None


def test_summarize_formats_counts():
    provider = _gateway_provider()
    models_dev.enrich_provider(provider, _catalog())

    counts = models_dev.summarize_formats(provider.model_catalog_routes)

    assert counts == {
        "openai_chat": 1,
        "openai_responses": 1,
        "anthropic_messages": 1,
        "google_gemini": 1,
    }
