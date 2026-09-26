from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pyautogui
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.plugin_system import PluginManager

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "default_plugins"


class _FakeLease:
    def __init__(self, calls: list) -> None:
        self.name = "OCR"
        self.requirer = "ui_operator"
        self.handle = SimpleNamespace(state="ACTIVE")
        self._calls = calls

    async def __aenter__(self):
        self._calls.append("enter")
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._calls.append("exit")

    async def wait_until_ready(self, timeout=None):
        self._calls.append("ready")

    async def invoke(self, data, *, timeout=None):
        self._calls.append("invoke")
        return [
            {"text": "Hello", "confidence": 0.93, "box": [[10, 10], [30, 10], [30, 30], [10, 30]]},
            {"text": "noise", "confidence": 0.11, "box": [[0, 0], [5, 0], [5, 5], [0, 5]]},
        ]


class _FakeManager:
    def __init__(self, calls: list) -> None:
        self.requests: list[tuple] = []
        self._calls = calls

    def require(self, name: str, requirer: str, *, config=None):
        self.requests.append((name, requirer, config))
        return _FakeLease(self._calls)


async def _load_ui_operator(tmp_path: Path, monkeypatch, calls: list):
    monkeypatch.setattr(pyautogui, "screenshot", lambda: Image.new("RGB", (100, 50), "white"))
    monkeypatch.setattr(pyautogui, "size", lambda: (100, 50))

    manager = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(tmp_path / "state.json"))
    manager.set_plugin_enabled("ui_operator", True)
    manager.set_plugin_config_values("ui_operator", {"OCR_MIN_CONF": 0.5})
    await manager.reload(force=True)
    plugin = manager._plugins["ui_operator"]["plugin"]

    fake_manager = _FakeManager(calls)
    plugin_module = sys.modules[type(plugin).__module__]
    monkeypatch.setattr(plugin_module, "get_processor_manager", lambda: fake_manager)
    return plugin, fake_manager


@pytest.mark.asyncio
async def test_screen_ocr_tool_calls_ocr_processor(tmp_path, monkeypatch):
    calls: list = []
    plugin, fake_manager = await _load_ui_operator(tmp_path, monkeypatch, calls)
    tools = {spec.name: spec for spec in plugin.register_tools(plugin.ctx)}

    payload = await tools["screenOCRTool"].tool.ainvoke({"lang_list_json": ""})

    assert fake_manager.requests == [
        ("OCR", "ui_operator", {"langs": ["ch_sim", "en"], "gpu": False})
    ]
    assert calls == ["enter", "ready", "invoke", "exit"]
    result = json.loads(payload)
    assert result == {"res": [{"id": 1, "text": "Hello", "pos": [0.2, 0.4]}]}   # min_conf=0.5 过滤掉 noise
