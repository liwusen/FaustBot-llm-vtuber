"""插件配置「LLM 选择」类型（type: "llm"）测试。

覆盖：类型归一化（llm / model → llm）、取值即 "provider::model" 字符串、
缺省值 = 主对话模型标识符（主模型变化时跟随）、显式选择优先、清空回落，
以及 quick-screen-view 的 screen-model 已改用该类型。

Run: python -m pytest backend/tests/test_plugin_config_llm.py -v
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REPO_ROOT = Path(__file__).resolve().parents[2]
REPO_PLUGIN_DIR = REPO_ROOT / "backend" / "default_plugins"
MAIN_MODEL = "deepseek::deepseek-v4-pro"


def _manager(tmp_path, monkeypatch):
    import faust_backend.config_loader as conf
    from faust_backend.plugin_system.manager import PluginManager
    from faust_backend.provider import ModelProviders

    monkeypatch.setattr(conf, "MODEL_PROVIDERS", ModelProviders(main_model=MAIN_MODEL))
    return PluginManager(
        plugins_dir=tmp_path / "plugins",
        state_file=str(tmp_path / "plugin-state.json"),
    )


def test_llm_type_normalization(tmp_path, monkeypatch):
    pm = _manager(tmp_path, monkeypatch)

    schema = pm._normalize_config_schema(
        [
            {"key": "A", "type": "llm"},
            {"key": "B", "type": "model"},
            {"key": "C", "type": "str"},
            {"key": "D", "type": "unknown-type"},
        ]
    )
    types = {item["key"]: item["type"] for item in schema}
    assert types == {"A": "llm", "B": "llm", "C": "str", "D": "str"}
    # 取值按字符串处理
    assert pm._coerce_config_value("llm", "deepseek::x") == "deepseek::x"
    assert pm._coerce_config_value("llm", 123) == "123"


def test_llm_default_falls_back_to_main_model(tmp_path, monkeypatch):
    import faust_backend.config_loader as conf
    from faust_backend.provider import ModelProviders

    pm = _manager(tmp_path, monkeypatch)
    pm._register_plugin_config_schema(
        "demo", [{"key": "SCREEN_MODEL", "type": "llm", "label": "屏幕分析模型"}]
    )

    snapshot = pm.get_plugin_config_snapshot("demo")
    assert snapshot["schema"][0]["type"] == "llm"
    # 未选择 → 缺省值就是主对话模型标识符（值本身是字符串）
    assert snapshot["values"]["SCREEN_MODEL"] == MAIN_MODEL
    assert pm._plugin_config_get("demo", "SCREEN_MODEL") == MAIN_MODEL
    assert pm._plugin_config_list("demo")["SCREEN_MODEL"] == MAIN_MODEL

    # 显式选择优先
    pm.set_plugin_config_values("demo", {"SCREEN_MODEL": "qwen::qwen-max"})
    assert pm._plugin_config_get("demo", "SCREEN_MODEL") == "qwen::qwen-max"
    assert pm.get_plugin_config_snapshot("demo")["values"]["SCREEN_MODEL"] == "qwen::qwen-max"

    # 清空 → 回落到主对话模型
    pm.set_plugin_config_values("demo", {"SCREEN_MODEL": ""})
    assert pm._plugin_config_get("demo", "SCREEN_MODEL") == MAIN_MODEL

    # 主对话模型变化 → 缺省值跟随（未被显式设置时）
    monkeypatch.setattr(conf, "MODEL_PROVIDERS", ModelProviders(main_model="qwen::qwen3"))
    assert pm.get_plugin_config_snapshot("demo")["values"]["SCREEN_MODEL"] == "qwen::qwen3"

    # 主对话模型也没有配置 → 保持空（调用方自行报错）
    monkeypatch.setattr(conf, "MODEL_PROVIDERS", ModelProviders(main_model=None))
    assert pm._plugin_config_get("demo", "SCREEN_MODEL", "") == ""


def test_non_llm_key_keeps_plain_defaults(tmp_path, monkeypatch):
    """非 llm 类型不受影响：缺省值仍取 schema default。"""
    pm = _manager(tmp_path, monkeypatch)
    pm._register_plugin_config_schema(
        "demo",
        [
            {"key": "MODE", "type": "str", "default": "tool"},
            {"key": "SCALE", "type": "float", "default": 0.5},
        ],
    )
    assert pm._plugin_config_get("demo", "MODE") == "tool"
    assert pm._plugin_config_get("demo", "SCALE") == 0.5
    assert pm._plugin_config_get("demo", "MISSING", "fallback") == "fallback"


@pytest.mark.asyncio
async def test_quick_screen_view_uses_llm_type(tmp_path, monkeypatch):
    """内置插件 Quick Screen View 的 screen-model 使用 llm 类型（缺省 = 主对话模型）。"""
    import faust_backend.config_loader as conf
    from faust_backend.plugin_system.manager import PluginManager
    from faust_backend.provider import ModelProviders

    monkeypatch.setattr(conf, "MODEL_PROVIDERS", ModelProviders(main_model=MAIN_MODEL))
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(tmp_path / "qs-state.json"))
    pm.set_plugin_enabled("quick-screen-view", True)
    await pm.reload(force=True)

    snapshot = pm.get_plugin_config_snapshot("quick-screen-view")
    field = next(item for item in snapshot["schema"] if item["key"] == "screen-model")
    assert field["type"] == "llm"
    assert field["default"] is None
    # 未选择 → 插件读到的就是主对话模型标识符
    assert snapshot["values"]["screen-model"] == MAIN_MODEL
    assert await pm._faust_plugins["quick-screen-view"].ctx.get_config("screen-model", "") == MAIN_MODEL
