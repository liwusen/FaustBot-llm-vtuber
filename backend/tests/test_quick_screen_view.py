from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pyautogui
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.plugin_system import PluginManager
from faust_backend.tools.vfs import get_faustbot_vfs

REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "default_plugins"

FOCUS_PATH = "/plugins/quick-screen-view/focus"
TEXT_PATH = "/plugins/quick-screen-view/text"


class _FakeChat:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, messages: list) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(content="# 屏幕概览\n- 模拟屏幕内容\n\n## 与 focus 相关\n- 模拟 focus 细节")


class _FakeModelBuilder:
    def __init__(self) -> None:
        self.chat = _FakeChat()

    async def build(self, providers: Any, spec: str, intensity: str | None = None) -> _FakeChat:
        return self.chat


async def _build_manager_async(tmp_path: Path) -> PluginManager:
    state_file = tmp_path / "plugin-test-state.json"
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(state_file))
    pm.set_plugin_enabled("quick-screen-view", True)
    await pm.reload(force=True)
    return pm


async def _clean_vfs_async() -> None:
    vfs = await get_faustbot_vfs()
    await vfs.delete("/plugins/quick-screen-view")


async def _set_mode_async(pm: PluginManager, mode: str, screen_model: str = "test::fake") -> None:
    pm.set_plugin_config_values(
        "quick-screen-view", {"mode": mode, "screen-model": screen_model}
    )
    await pm.reload(force=True)


def _patch_screenshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pyautogui, "screenshot", lambda: Image.new("RGB", (100, 50), "white"))


def _patch_model(monkeypatch: pytest.MonkeyPatch) -> _FakeModelBuilder:
    fake = _FakeModelBuilder()
    import faust_backend.provider as provider_mod
    import faust_backend.runtime.state as state_mod

    monkeypatch.setattr(provider_mod, "build_ReasoningChatOpenAI_from_spec", fake.build)
    monkeypatch.setattr(state_mod, "get_model_providers", lambda: None)
    return fake


# ── Tool 模式 ──


@pytest.mark.asyncio
async def test_tool_mode_registers_tool_and_no_vfs(tmp_path: Path) -> None:
    await _clean_vfs_async()
    pm = await _build_manager_async(tmp_path)
    plugin = pm._faust_plugins["quick-screen-view"]
    tools = await plugin.register_tools(plugin.ctx)
    names = [spec.name for spec in tools]
    assert "quickScreenView" in names
    vfs = await get_faustbot_vfs()
    assert not await vfs.exists(FOCUS_PATH)
    assert not await vfs.exists(TEXT_PATH)


@pytest.mark.asyncio
async def test_tool_call_with_focus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _clean_vfs_async()
    fake = _patch_model(monkeypatch)
    _patch_screenshot(monkeypatch)
    pm = await _build_manager_async(tmp_path)
    pm.set_plugin_config_values("quick-screen-view", {"screen-model": "test::fake"})
    await pm.reload(force=True)
    plugin = pm._faust_plugins["quick-screen-view"]
    tool = (await plugin.register_tools(plugin.ctx))[0].tool
    result = await tool.ainvoke({"focus": "看看屏幕上有什么"})
    assert "屏幕概览" in result
    assert fake.chat.calls == 1
    # 同 focus 二次调用命中缓存
    result2 = await tool.ainvoke({"focus": "看看屏幕上有什么"})
    assert result2 == result
    assert fake.chat.calls == 1
    # 不同 focus 重新计算
    await tool.ainvoke({"focus": "只看数字"})
    assert fake.chat.calls == 2


# ── VFS 模式 ──


@pytest.mark.asyncio
async def test_vfs_mode_nodes_and_async_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _clean_vfs_async()
    fake = _patch_model(monkeypatch)
    _patch_screenshot(monkeypatch)
    pm = await _build_manager_async(tmp_path)
    await _set_mode_async(pm, "vfs")
    plugin = pm._faust_plugins["quick-screen-view"]
    assert await plugin.register_tools(plugin.ctx) == []

    vfs = await get_faustbot_vfs()
    assert await vfs.exists(FOCUS_PATH)
    assert await vfs.exists(TEXT_PATH)

    # focus 可写可读
    await vfs.write(FOCUS_PATH, "关注屏幕上的数字")
    assert await vfs.read_text(FOCUS_PATH) == "关注屏幕上的数字"

    # text 为异步内容函数，读取时实时分析
    text = await vfs.read_text(TEXT_PATH)
    assert "屏幕概览" in text
    assert fake.chat.calls == 1


@pytest.mark.asyncio
async def test_text_cache_and_focus_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _clean_vfs_async()
    fake = _patch_model(monkeypatch)
    _patch_screenshot(monkeypatch)
    pm = await _build_manager_async(tmp_path)
    await _set_mode_async(pm, "vfs")
    vfs = await get_faustbot_vfs()

    await vfs.write(FOCUS_PATH, "focus-a")
    await vfs.read_text(TEXT_PATH)
    await vfs.read_text(TEXT_PATH)
    assert fake.chat.calls == 1  # 缓存命中

    # 写 focus 清缓存 → 重算
    await vfs.write(FOCUS_PATH, "focus-b")
    await vfs.read_text(TEXT_PATH)
    assert fake.chat.calls == 2

    # 相同 focus 再次命中缓存
    await vfs.read_text(TEXT_PATH)
    assert fake.chat.calls == 2


# ── 错误处理 ──


@pytest.mark.asyncio
async def test_missing_screen_model(tmp_path: Path) -> None:
    await _clean_vfs_async()
    pm = await _build_manager_async(tmp_path)
    await _set_mode_async(pm, "vfs")
    vfs = await get_faustbot_vfs()
    text = await vfs.read_text(TEXT_PATH)
    assert text.startswith("[quick-screen-view]")
    assert "screen-model" in text


@pytest.mark.asyncio
async def test_malformed_screen_model(tmp_path: Path) -> None:
    await _clean_vfs_async()
    pm = await _build_manager_async(tmp_path)
    pm.set_plugin_config_values(
        "quick-screen-view", {"mode": "vfs", "screen-model": "bad-spec"}
    )
    await pm.reload(force=True)
    vfs = await get_faustbot_vfs()
    text = await vfs.read_text(TEXT_PATH)
    assert text.startswith("[quick-screen-view]")


@pytest.mark.asyncio
async def test_plugin_unload_cleans_vfs_nodes(tmp_path: Path) -> None:
    await _clean_vfs_async()
    pm = await _build_manager_async(tmp_path)
    await _set_mode_async(pm, "vfs")
    vfs = await get_faustbot_vfs()
    assert await vfs.exists(FOCUS_PATH)
    pm.set_plugin_enabled("quick-screen-view", False)
    await pm.reload(force=True)
    assert not await vfs.exists(FOCUS_PATH)
    assert not await vfs.exists(TEXT_PATH)
