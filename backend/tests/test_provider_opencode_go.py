# backend/tests/test_provider_opencode_go.py
"""Opencode Go 适配头：opencode_go 开关 → ChatOpenAI default_headers 注入。"""
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from faust_backend import provider as pv  # noqa: E402


def test_model_provider_opencode_go_flag_default_off():
    p = pv.ModelProvider(name="Go", base_url="https://x/v1", opencode_go=True)
    assert p.opencode_go is True
    p2 = pv.ModelProvider(name="D", base_url="https://x/v1")
    assert p2.opencode_go is False


@pytest.mark.asyncio
async def test_build_injects_opencode_headers(monkeypatch):
    mp = pv.ModelProviders(
        providers=[pv.ModelProvider(name="Go", base_url="https://x/v1", key="k", models=["m1"], opencode_go=True)],
        main_model="Go::m1",
    )

    async def _noop_load(provider):
        return []

    monkeypatch.setattr(pv, "auto_load_model_for_provider", _noop_load)
    llm = await pv.build_ReasoningChatOpenAI_from_spec(mp, "Go::m1", intensity=None)
    dh = llm.default_headers or {}
    assert dh.get("x-opencode-session") == pv._OPENCODE_SESSION_ID  # 进程内稳定
    assert dh.get("User-Agent") == pv.FAUSTBOT_USER_AGENT

    llm2 = await pv.build_ReasoningChatOpenAI_from_spec(mp, "Go::m1", intensity="medium")
    dh2 = llm2.default_headers or {}
    assert dh2.get("x-opencode-session") == pv._OPENCODE_SESSION_ID


@pytest.mark.asyncio
async def test_build_without_flag_has_no_session_header(monkeypatch):
    mp = pv.ModelProviders(
        providers=[pv.ModelProvider(name="D", base_url="https://x/v1", key="k", models=["m1"])],
        main_model="D::m1",
    )

    async def _noop_load(provider):
        return []

    monkeypatch.setattr(pv, "auto_load_model_for_provider", _noop_load)
    llm = await pv.build_ReasoningChatOpenAI_from_spec(mp, "D::m1", intensity=None)
    dh = llm.default_headers or {}
    assert "x-opencode-session" not in dh
    # UA 是全局契约：非 opencode_go 的 provider 也必须携带
    assert dh.get("User-Agent") == pv.FAUSTBOT_USER_AGENT


@pytest.mark.asyncio
async def test_session_id_stable_within_process():
    assert pv._OPENCODE_SESSION_ID == pv.OPENCODE_DEFAULT_HEADERS["x-opencode-session"]
