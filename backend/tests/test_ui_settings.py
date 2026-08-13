from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faust_backend.ui_settings as ui_settings
from faust_backend.routes.ui_settings import get_ui_setting, post_ui_setting


def test_ui_settings_load_defaults_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_settings, "UI_SETTINGS_PATH", tmp_path / "ui-settings.json")
    assert ui_settings.load_ui_settings() == {"widgets": {}}


def test_ui_settings_save_and_load_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "ui-settings.json"
    monkeypatch.setattr(ui_settings, "UI_SETTINGS_PATH", path)
    payload = {
        "widgets": {
            "text-chat-bar": {
                "bindingType": "model",
                "coord": {"x": 0.5, "y": 0.53},
                "scale": 1.2,
                "hidden": False,
            }
        }
    }
    saved = ui_settings.save_ui_settings(payload)
    assert path.exists()
    assert saved == payload
    assert ui_settings.load_ui_settings() == payload


def test_ui_setting_routes_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "ui-settings.json"
    monkeypatch.setattr(ui_settings, "UI_SETTINGS_PATH", path)
    payload = {
        "widgets": {
            "emotion-badge": {
                "bindingType": "model",
                "coord": {"x": 0.08, "y": 0.1},
                "scale": 1,
                "hidden": True,
                "props": {"dynamicBackground": False},
            }
        }
    }
    post_result = asyncio.run(post_ui_setting(payload))
    assert post_result["status"] == "ok"
    get_result = asyncio.run(get_ui_setting())
    assert get_result["status"] == "ok"
    assert get_result["settings"] == payload


def test_ui_settings_onboarding_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_settings, "UI_SETTINGS_PATH", tmp_path / "ui-settings.json")
    saved = ui_settings.save_ui_settings({"widgets": {}, "onboarding": {"done": True}})
    assert saved["onboarding"] == {"done": True}
    assert ui_settings.load_ui_settings()["onboarding"] == {"done": True}


def test_ui_settings_onboarding_preserved_when_payload_lacks_key(tmp_path, monkeypatch):
    """config 窗口只 POST widgets 时，onboarding 必须从磁盘合并保留"""
    monkeypatch.setattr(ui_settings, "UI_SETTINGS_PATH", tmp_path / "ui-settings.json")
    ui_settings.save_ui_settings({"widgets": {}, "onboarding": {"done": True}})
    saved = ui_settings.save_ui_settings({"widgets": {"text-chat-bar": {"hidden": False}}})
    assert saved["onboarding"] == {"done": True}
    assert ui_settings.load_ui_settings()["onboarding"] == {"done": True}


def test_ui_settings_onboarding_explicitly_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(ui_settings, "UI_SETTINGS_PATH", tmp_path / "ui-settings.json")
    ui_settings.save_ui_settings({"widgets": {}, "onboarding": {"done": True}})
    saved = ui_settings.save_ui_settings({"widgets": {}, "onboarding": {}})
    assert saved["onboarding"] == {}