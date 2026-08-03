import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure the backend is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from faust_backend.provider import (
    ModelProvider, ModelProviders,
    new_provider, remove_provider, remove_model_from_provider,
    loads, dumps, build_ReasoningChatOpenAI_from_spec,
)


def make_providers() -> ModelProviders:
    p = ModelProviders()
    new_provider(p, "deepseek", "https://api.deepseek.com/v1", "sk-test")
    new_provider(p, "qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "sk-qwen")
    p.main_model = "deepseek::deepseek-v4-pro"
    p.subagent_models = ["deepseek::deepseek-v4", "qwen::qwen-7b-chat"]
    return p


def test_new_and_remove_provider():
    p = ModelProviders()
    new_provider(p, "a", "http://a/v1", "k")
    assert len(p.providers) == 1
    assert p.providers[0].name == "a"
    assert p.providers[0].base_url == "http://a/v1"
    assert p.providers[0].key == "k"
    assert remove_provider(p, "a") is True
    assert len(p.providers) == 0
    assert remove_provider(p, "missing") is False


def test_remove_model_from_provider():
    p = make_providers()
    p.providers[0].models = ["deepseek-v4-pro", "deepseek-v4"]
    assert remove_model_from_provider(p, "deepseek", "deepseek-v4") is True
    assert p.providers[0].models == ["deepseek-v4-pro"]
    assert remove_model_from_provider(p, "deepseek", "nope") is False
    assert remove_model_from_provider(p, "missing", "x") is False


def test_loads_dumps_roundtrip(tmp_path):
    p = make_providers()
    path = tmp_path / "provider.private.json"
    dumps(p, str(path))
    loaded = loads(str(path))
    assert loaded.main_model == "deepseek::deepseek-v4-pro"
    assert loaded.subagent_models == ["deepseek::deepseek-v4", "qwen::qwen-7b-chat"]
    assert loaded.providers[0].key == "sk-test"
    assert loaded.providers[1].name == "qwen"


def test_loads_empty_file(tmp_path):
    path = tmp_path / "provider.private.json"
    path.write_text("{}", encoding="utf-8")
    loaded = loads(str(path))
    assert loaded.providers == []
    assert loaded.main_model is None


def test_build_model_from_spec(monkeypatch):
    p = make_providers()
    p.providers[0].models = ["deepseek-v4-pro", "deepseek-v4"]
    model = asyncio.run(build_ReasoningChatOpenAI_from_spec(p, "deepseek::deepseek-v4", intensity=None))
    assert model.model_name == "deepseek-v4"
    assert model.openai_api_key.get_secret_value() == "sk-test"  # SecretStr
    assert "api.deepseek.com" in model.openai_api_base


def test_build_model_unknown_provider_raises():
    p = make_providers()
    try:
        asyncio.run(build_ReasoningChatOpenAI_from_spec(p, "nope::x", intensity=None))
        assert False, "should raise"
    except ValueError as e:
        assert "not found" in str(e)


def test_build_model_unknown_model_raises():
    p = make_providers()
    p.providers[0].models = ["deepseek-v4-pro"]
    try:
        asyncio.run(build_ReasoningChatOpenAI_from_spec(p, "deepseek::missing", intensity=None))
        assert False, "should raise"
    except ValueError as e:
        assert "not found" in str(e)


def test_build_model_tolerates_empty_models_list():
    """[R3] models 为空（加载失败/离线）时，显式指定的模型名仍可用，不崩溃。"""
    import faust_backend.provider as prov
    p = make_providers()
    p.providers[0].models = []  # 模拟自动加载失败

    async def _no_load(provider):
        return provider.models  # 无网络

    prov.auto_load_model_for_provider = _no_load
    model = asyncio.run(build_ReasoningChatOpenAI_from_spec(p, "deepseek::deepseek-v4", intensity=None))
    assert model.model_name == "deepseek-v4"


def test_build_model_thinking_disabled_when_type_none():
    """[R5] thinking_type == 'none' 时即使传 intensity 也不启用思考（返回 ChatOpenAI 而非 ReasoningChatOpenAI）。"""
    from faust_backend.thinking import ReasoningChatOpenAI as RChat
    from langchain_openai import ChatOpenAI
    import faust_backend.provider as prov
    p = make_providers()
    p.providers[0].thinking_type = "none"

    async def _no_load(provider):
        return provider.models  # 无网络

    prov.auto_load_model_for_provider = _no_load
    model = asyncio.run(build_ReasoningChatOpenAI_from_spec(p, "deepseek::deepseek-v4", intensity="medium"))
    assert isinstance(model, ChatOpenAI)
    assert not isinstance(model, RChat)


def test_get_provider_models_parses_openai_shape(monkeypatch):
    """[R2] 解析 OpenAI 兼容 {data:[{id}]} 结构。"""
    import httpx
    from faust_backend.provider import get_provider_models_by_api

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "m1"}, {"id": "m2"}]}

    class FakeClient:
        def __init__(self, *a, **kw):
            self.timeout = kw.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            assert self.timeout is not None  # 必须有超时（R2）
            return FakeResp()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    p = make_providers().providers[0]
    res = asyncio.run(get_provider_models_by_api(p))
    assert res == ["m1", "m2"]


# ── Task 2: config_loader 集成 ──


def test_config_loader_creates_and_migrates(tmp_path, monkeypatch):
    import faust_backend.config_loader as conf

    # 模拟旧配置：有 CHAT_* 无 provider.private.json
    public = {"CHAT_MODEL": "gpt-4o", "CHAT_API_BASE": "https://www.dmxapi.cn/v1"}
    private = {"CHAT_API_KEY": "sk-old"}
    (tmp_path / "faust.config.json").write_text(json.dumps(public), encoding="utf-8")
    (tmp_path / "faust.config.private.json").write_text(json.dumps(private), encoding="utf-8")

    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "CONFIG_FILE_PATH", str(tmp_path / "faust.config.json"))
    monkeypatch.setattr(conf, "CONFIG_FILE_P_PATH", str(tmp_path / "faust.config.private.json"))
    monkeypatch.setattr(conf, "PROVIDER_CONFIG_PATH", str(tmp_path / "provider.private.json"))
    monkeypatch.setattr(conf, "MODEL_PROVIDERS", None)  # 清除前序测试的缓存实例
    # 迁移逻辑读模块级 config/private_config dict——指向临时配置
    monkeypatch.setattr(conf, "config", dict(public))
    monkeypatch.setattr(conf, "private_config", dict(private))

    conf.ensure_model_providers_loaded()
    mp = conf.MODEL_PROVIDERS
    assert mp is not None
    # 自动创建 ORIGIONAL provider
    assert any(p.name == "ORIGIONAL" for p in mp.providers)
    original = next(p for p in mp.providers if p.name == "ORIGIONAL")
    assert original.base_url == "https://www.dmxapi.cn/v1"
    assert original.key == "sk-old"
    assert mp.main_model == "ORIGIONAL::gpt-4o"
    # [R3] 旧模型必须已进入 provider.models，避免 build 时"模型不在列表"启动即崩
    assert "gpt-4o" in original.models
    # provider.private.json 已自动创建
    assert (tmp_path / "provider.private.json").exists()
    # 旧字段已从配置文件清除（写回）
    saved_public = json.loads((tmp_path / "faust.config.json").read_text(encoding="utf-8"))
    assert "CHAT_MODEL" not in saved_public
    saved_private = json.loads((tmp_path / "faust.config.private.json").read_text(encoding="utf-8"))
    assert "CHAT_API_KEY" not in saved_private


def test_config_loader_loads_existing(tmp_path, monkeypatch):
    import faust_backend.config_loader as conf

    data = {
        "providers": [{"name": "x", "base_url": "http://x/v1", "key": "k", "models": ["m1"]}],
        "main_model": "x::m1",
        "subagent_models": ["x::m1"],
    }
    (tmp_path / "provider.private.json").write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "PROVIDER_CONFIG_PATH", str(tmp_path / "provider.private.json"))
    monkeypatch.setattr(conf, "MODEL_PROVIDERS", None)  # 清除前序测试的缓存实例

    conf.ensure_model_providers_loaded()
    mp = conf.MODEL_PROVIDERS
    assert len(mp.providers) == 1
    assert mp.main_model == "x::m1"
    assert mp.subagent_models == ["x::m1"]


def test_get_main_credentials():
    from faust_backend.provider import get_main_credentials
    p = make_providers()
    assert get_main_credentials(p) == ("deepseek-v4-pro", "sk-test", "https://api.deepseek.com/v1")
    p.main_model = None
    assert get_main_credentials(p) == ("", "", "")  # R7: 空值不崩
    p.main_model = "bad-spec"
    assert get_main_credentials(p) == ("", "", "")  # R7: 非法 spec 不崩


def test_subagent_model_validation():
    from faust_backend.provider import is_subagent_model_allowed, get_default_subagent_model
    p = make_providers()
    assert is_subagent_model_allowed(p, "deepseek::deepseek-v4") is True
    assert is_subagent_model_allowed(p, "qwen::qwen-7b-chat") is True
    assert is_subagent_model_allowed(p, "deepseek::deepseek-v4-pro") is True  # 等于 main_model
    assert is_subagent_model_allowed(p, "nope::x") is False
    assert get_default_subagent_model(p) == "deepseek::deepseek-v4"
    p.subagent_models = []
    assert get_default_subagent_model(p) == "deepseek::deepseek-v4-pro"  # 回退 main_model


def test_no_chat_global_references():
    """全后端不应再引用 CHAT_MODEL/CHAT_API_BASE/CHAT_API_KEY 全局。"""
    root = Path(__file__).resolve().parents[2] / "backend" / "faust_backend"
    offenders = []
    for py in root.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for pat in ("conf.CHAT_MODEL", "conf.CHAT_API_BASE", "conf.CHAT_API_KEY",
                    "CHAT_MODEL", "CHAT_API_BASE", "CHAT_API_KEY"):
            if pat in text:
                offenders.append((py.relative_to(root).as_posix(), pat))
    # [R10] 豁免：admin_runtime 的 OBSOLETE 列表、config_loader 迁移逻辑、
    # provider.py 注释、memory/eval 的 CLI 帮助文本
    allowed = {
        "admin_runtime.py",
        "config_loader.py",
        "provider.py",
        "memory/eval/cli.py",
    }
    real = [o for o in offenders if o[0] not in allowed]
    assert not real, f"CHAT_* still referenced: {real}"


# ── Task 5: admin_providers 路由 ──


def _make_provider_app():
    from fastapi import FastAPI
    from faust_backend.routes.admin_providers import router
    app = FastAPI()
    app.include_router(router)
    return app


def test_providers_crud_roundtrip(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import faust_backend.config_loader as conf
    import faust_backend.runtime.state as state_mod

    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "PROVIDER_CONFIG_PATH", str(tmp_path / "provider.private.json"))
    monkeypatch.setattr(conf, "MODEL_PROVIDERS", None)
    monkeypatch.setattr(state_mod, "get_model_providers", conf.ensure_model_providers_loaded)

    client = TestClient(_make_provider_app())
    # 添加 provider
    r = client.post("/faust/admin/providers", json={
        "name": "test", "base_url": "http://test/v1", "key": "k1"
    })
    assert r.status_code == 200
    # 列表
    r = client.get("/faust/admin/providers")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["providers"]]
    assert "test" in names
    # 删除
    r = client.delete("/faust/admin/providers/test")
    assert r.status_code == 200
    r = client.delete("/faust/admin/providers/test")
    assert r.status_code == 404


def test_select_model_switches(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import faust_backend.config_loader as conf
    import faust_backend.runtime.state as state_mod
    from faust_backend.provider import new_provider

    async def _noop_rebuild(**kw):
        return {"status": "ok"}

    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "PROVIDER_CONFIG_PATH", str(tmp_path / "provider.private.json"))
    monkeypatch.setattr(conf, "MODEL_PROVIDERS", None)
    mp = conf.ensure_model_providers_loaded()
    new_provider(mp, "a", "http://a/v1", "k")
    mp.providers[0].models = ["m1", "m2"]
    conf.save_model_providers()
    monkeypatch.setattr(
        "faust_backend.routes.admin_providers._rebuild_after_change",
        _noop_rebuild,
    )

    client = TestClient(_make_provider_app())
    r = client.post("/faust/admin/model/select", json={
        "main_model": "a::m1",
        "subagent_models": ["a::m1", "a::m2"],
    })
    assert r.status_code == 200
    # 持久化
    assert conf.MODEL_PROVIDERS.main_model == "a::m1"
    assert conf.MODEL_PROVIDERS.subagent_models == ["a::m1", "a::m2"]
