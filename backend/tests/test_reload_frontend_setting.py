from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.frontend.bridge import FrontendBridge


def test_bridge_reload_settings_pushes_command():
    bridge = FrontendBridge()

    async def _collect():
        bridge.reload_settings()
        return await asyncio.wait_for(bridge.queue.get(), timeout=1)

    assert asyncio.run(_collect()) == "RELOAD_FRONTEND_SETTING"


def test_push_reload_if_ui_changed_on_ui_key(monkeypatch):
    import faust_backend.admin_runtime as admin_runtime
    import faust_backend.backend2front as b2f

    calls = []
    monkeypatch.setattr(b2f, "FrontEndReloadSettings", lambda: calls.append(1))
    changed = admin_runtime.push_reload_if_ui_changed(
        {"TTS_CHUNK_IDEAL_TOKENS": 30}, {"TTS_CHUNK_IDEAL_TOKENS": 50}
    )
    assert changed is True
    assert calls == [1]


def test_push_reload_if_ui_changed_ignores_non_ui_keys(monkeypatch):
    import faust_backend.admin_runtime as admin_runtime
    import faust_backend.backend2front as b2f

    calls = []
    monkeypatch.setattr(b2f, "FrontEndReloadSettings", lambda: calls.append(1))
    changed = admin_runtime.push_reload_if_ui_changed(
        {"REASONING_CONFIG": "off"}, {"REASONING_CONFIG": "high"}
    )
    assert changed is False
    assert calls == []


def test_push_reload_if_ui_changed_normalization(monkeypatch):
    """None 与 '' 视为相等；int 与等值字符串视为相等（save_config 可能改类型）"""
    import faust_backend.admin_runtime as admin_runtime
    import faust_backend.backend2front as b2f

    calls = []
    monkeypatch.setattr(b2f, "FrontEndReloadSettings", lambda: calls.append(1))
    assert admin_runtime.push_reload_if_ui_changed(
        {"LIVE2D_MODEL_X": None, "LIVE2D_MODEL_Y": 0.5},
        {"LIVE2D_MODEL_X": "", "LIVE2D_MODEL_Y": 0.5},
    ) is False
    assert admin_runtime.push_reload_if_ui_changed(
        {"TTS_CHUNK_IDEAL_TOKENS": 30}, {"TTS_CHUNK_IDEAL_TOKENS": "30"}
    ) is False
    assert calls == []


def test_ptt_mode_default_is_true():
    import faust_backend.admin_runtime as admin_runtime

    assert admin_runtime.PUBLIC_CONFIG_DEFAULTS["PTT_MODE"] is True


def test_push_reload_if_ui_changed_on_ptt_mode(monkeypatch):
    import faust_backend.admin_runtime as admin_runtime
    import faust_backend.backend2front as b2f

    calls = []
    monkeypatch.setattr(b2f, "FrontEndReloadSettings", lambda: calls.append(1))
    changed = admin_runtime.push_reload_if_ui_changed(
        {"PTT_MODE": True}, {"PTT_MODE": False}
    )
    assert changed is True
    assert calls == [1]
