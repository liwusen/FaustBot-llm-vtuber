"""avatar-performance 插件：工具校验矩阵、交互契约、ring buffer、消息备注、能力上报。

用 importlib 直接加载 impl.py（与 test_emotion_engine_upgrade 同款），模块级缓存可在
fixture 里逐测试重置；另有一个 PluginManager 集成用例覆盖"插件加载 → 工具进 agent 工具表"。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

IMPL_PATH = (
    Path(__file__).resolve().parents[1]
    / "default_plugins"
    / "avatar-performance"
    / "impl.py"
)
REPO_PLUGIN_DIR = Path(__file__).resolve().parents[1] / "default_plugins"


def _load_impl():
    spec = importlib.util.spec_from_file_location("avatar_performance_impl", IMPL_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["avatar_performance_impl"] = module
    spec.loader.exec_module(module)
    return module


impl = _load_impl()

LIVE2D_CAPS = {
    "model_type": "live2d",
    "model_path": "2D/yumi/yumi.model3.json",
    "emotions": ["neutral", "happy", "sad"],
    "expressions": ["星星眼", "f01"],
    "motions": ["Idle", "Flick", "Tap", "wave"],
    "facs_keys": ["headX", "gazeX", "blush", "breath"],
    "profile_source": "generated",
}

IMAGES_CAPS = {
    "model_type": "images",
    "model_path": "__faust_images__",
    "emotions": ["平静", "开心"],
    "expressions": [],
    "motions": [],
    "facs_keys": [],
    "profile_source": "none",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1_758_700_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeCtx:
    def __init__(self) -> None:
        self.triggers: list[dict] = []

    async def trigger_create(self, payload):
        self.triggers.append(payload)
        return {"id": payload.get("id")}


class _FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def communicate(self, plugin_id, payload):
        self.calls.append((plugin_id, payload))
        return {"status": "ok"}


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(impl, "_now", c)
    return c


@pytest.fixture(autouse=True)
def _reset_state():
    def clear():
        impl._capabilities = {}
        impl._capabilities_at = 0.0
        impl._interactions.clear()
        impl._last_note_at = 0.0

    clear()
    yield
    clear()


@pytest.fixture
def pushed(monkeypatch):
    """捕获下发给前端的 AVATAR_* 命令（工具的真实产出）。"""
    calls: list[tuple[str, dict]] = []

    def fake(command, payload=None):
        calls.append((command, payload))

    monkeypatch.setattr(impl.backend2frontend, "frontendAvatarCommand", fake)
    return calls


@pytest.fixture
def emotion_manager(monkeypatch):
    from faust_backend.runtime import state as runtime_state

    manager = _FakeManager()
    monkeypatch.setattr(runtime_state, "plugin_manager", manager)
    return manager


async def _report(payload=None, ctx=None):
    plugin = impl.Plugin()
    return await plugin.communicate_handler(
        {"action": "report_capabilities", "capabilities": dict(payload or LIVE2D_CAPS)},
        ctx or _FakeCtx(),
    )


async def _interaction(body, ctx=None):
    plugin = impl.Plugin()
    return await plugin.communicate_handler({"action": "interaction", "interaction": body}, ctx or _FakeCtx())


async def _get_state():
    plugin = impl.Plugin()
    return await plugin.communicate_handler({"action": "get_state"}, _FakeCtx())


# ── 能力上报 ──


@pytest.mark.asyncio
async def test_report_normalizes_and_echoes_unknown_facs(clock):
    caps = dict(LIVE2D_CAPS)
    caps["facs_keys"] = ["headX", "notAKey", "  ", "breath"]
    caps["motions"] = ["Idle", "", "  ", "wave"]
    caps["expressions"] = ["星星眼", ""]
    result = await _report(caps)
    assert result["status"] == "ok"
    assert result["model_type"] == "live2d"
    assert result["facs_key_count"] == 2
    assert result["unknown_facs_keys"] == ["notAKey"]
    # 空 Motions 组名会被过滤（model.motion("") 是无意义请求）
    assert impl._capabilities["motions"] == ["Idle", "wave"]
    assert impl._capabilities["expressions"] == ["星星眼"]


@pytest.mark.asyncio
async def test_report_rejects_non_object_capabilities():
    plugin = impl.Plugin()
    result = await plugin.communicate_handler({"action": "report_capabilities", "capabilities": []}, _FakeCtx())
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_get_state_exposes_capabilities_and_interactions(clock):
    await _report()
    await _interaction({"tier": "light", "kind": "tap", "at": clock.now, "summary": "用户戳了你一下"})
    state = await _get_state()
    assert state["status"] == "ok"
    assert state["capabilities"]["model_type"] == "live2d"
    assert state["capabilities_age"] == pytest.approx(0.0)
    assert len(state["interactions"]) == 1


# ── 工具校验矩阵 ──


@pytest.mark.asyncio
async def test_tools_require_capabilities_report(pushed):
    for coro in (
        impl.listAvatarCapabilities.ainvoke({}),
        impl.setAvatarEmotion.ainvoke({"emotion": "happy"}),
        impl.setAvatarExpression.ainvoke({"name": "f01"}),
        impl.playAvatarMotion.ainvoke({"name": "Idle"}),
        impl.setAvatarParameters.ainvoke({"facs": {"headX": 0.2}}),
    ):
        assert "前端尚未上报模型能力" in await coro
    assert pushed == []


@pytest.mark.asyncio
async def test_stale_capabilities_are_rejected(clock, pushed):
    await _report()
    clock.advance(137.4)
    result = await impl.setAvatarEmotion.ainvoke({"emotion": "happy"})
    assert "前端已 137 秒未上报能力" in result
    assert pushed == []


@pytest.mark.asyncio
async def test_unknown_emotion_lists_available(clock, pushed):
    await _report()
    result = await impl.setAvatarEmotion.ainvoke({"emotion": "angry"})
    assert "未知情绪: angry" in result
    assert '"available"' in result
    assert pushed == []


@pytest.mark.asyncio
async def test_emotion_intensity_clamped(clock, pushed):
    await _report()
    result = await impl.setAvatarEmotion.ainvoke({"emotion": "happy", "intensity": 3})
    assert '"clamped": ["intensity"]' in result
    assert pushed == [("AVATAR_EMOTION", {"emotion": "happy", "intensity": 1.0})]


@pytest.mark.asyncio
async def test_unknown_expression_lists_available(clock, pushed):
    await _report()
    result = await impl.setAvatarExpression.ainvoke({"name": "不存在"})
    assert "未知表情: 不存在" in result
    assert pushed == []


@pytest.mark.asyncio
async def test_unknown_motion_lists_available(clock, pushed):
    await _report()
    result = await impl.playAvatarMotion.ainvoke({"name": "tear"})
    assert "未知动作: tear" in result
    assert pushed == []


@pytest.mark.asyncio
async def test_images_expression_uses_emotion_groups(clock, pushed):
    await _report(IMAGES_CAPS)
    ok = await impl.setAvatarExpression.ainvoke({"name": "开心", "hold_seconds": 2})
    assert '"command": "AVATAR_EXPRESSION"' in ok
    assert pushed == [("AVATAR_EXPRESSION", {"name": "开心", "hold_ms": 2000})]
    rejected = await impl.setAvatarExpression.ainvoke({"name": "f01"})
    assert "未知情绪图组: f01" in rejected


@pytest.mark.asyncio
async def test_images_rejects_motion_and_facs(clock, pushed):
    await _report(IMAGES_CAPS)
    motion = await impl.playAvatarMotion.ainvoke({"name": "Idle"})
    assert "图片模型没有动作组" in motion
    facs = await impl.setAvatarParameters.ainvoke({"facs": {"headX": 0.2}})
    assert "当前模型无可用参数层" in facs
    assert pushed == []


@pytest.mark.asyncio
async def test_facs_key_not_available(clock, pushed):
    await _report()
    result = await impl.setAvatarParameters.ainvoke({"facs": {"mouthPucker": 0.3}})
    assert "键不可用: mouthPucker" in result
    assert pushed == []


@pytest.mark.asyncio
async def test_facs_out_of_range_is_clamped(clock, pushed):
    await _report()
    result = await impl.setAvatarParameters.ainvoke({"facs": {"headX": 2.5, "gazeX": 0.5}, "hold_seconds": 4})
    assert '"clamped": ["headX"]' in result
    assert pushed == [("AVATAR_FACS", {"facs": {"headX": 1.0, "gazeX": 0.5}, "hold_ms": 4000})]


@pytest.mark.asyncio
async def test_facs_non_finite_value_rejected(clock, pushed):
    await _report()
    result = await impl.setAvatarParameters.ainvoke({"facs": {"headX": float("nan")}, "hold_seconds": 1.0})
    assert "参数值必须是有穷数" in result
    assert pushed == []


@pytest.mark.asyncio
async def test_facs_empty_object_rejected(clock, pushed):
    await _report()
    result = await impl.setAvatarParameters.ainvoke({"facs": {}, "hold_seconds": 1.0})
    assert "facs 必须是非空对象" in result
    assert pushed == []


@pytest.mark.asyncio
async def test_hold_seconds_clamped_to_range(clock, pushed):
    await _report()
    await impl.playAvatarMotion.ainvoke({"name": "Idle", "hold_seconds": 999})
    await impl.playAvatarMotion.ainvoke({"name": "Idle", "hold_seconds": -5})
    assert pushed == [
        ("AVATAR_MOTION", {"name": "Idle", "hold_ms": impl.MAX_HOLD_MS}),
        ("AVATAR_MOTION", {"name": "Idle", "hold_ms": 0}),
    ]


@pytest.mark.asyncio
async def test_motion_default_hold(clock, pushed):
    await _report()
    await impl.playAvatarMotion.ainvoke({"name": "wave"})
    assert pushed == [("AVATAR_MOTION", {"name": "wave", "hold_ms": int(impl.HOLD_SECONDS_DEFAULT * 1000)})]


@pytest.mark.asyncio
async def test_clear_validates_scope(clock, pushed):
    result = await impl.clearAvatarPerformance.ainvoke({"scope": "everything"})
    assert "未知 scope" in result
    ok = await impl.clearAvatarPerformance.ainvoke({"scope": "native"})
    assert '"command": "AVATAR_CLEAR"' in ok
    # 清除不依赖能力缓存：前端未上报时也应能交还控制权
    assert pushed == [("AVATAR_CLEAR", {"scope": "native"})]


@pytest.mark.asyncio
async def test_capabilities_notes_describe_division_of_labor(clock):
    await _report()
    payload = await impl.listAvatarCapabilities.ainvoke({})
    assert "TTS 播到时生效" in payload
    assert "setAvatarParameters" in payload


@pytest.mark.asyncio
async def test_capabilities_notes_for_images(clock):
    await _report(IMAGES_CAPS)
    payload = await impl.listAvatarCapabilities.ainvoke({})
    assert "图片模型没有动作组与参数层" in payload


# ── 交互契约 ──


@pytest.mark.asyncio
async def test_interaction_rejects_bad_tier(clock):
    result = await _interaction({"tier": "medium", "kind": "tap", "at": clock.now})
    assert result["status"] == "error"
    assert "tier" in result["detail"]


@pytest.mark.asyncio
async def test_interaction_rejects_bad_kind(clock):
    result = await _interaction({"tier": "light", "kind": "poke", "at": clock.now})
    assert result["status"] == "error"
    assert "kind" in result["detail"]


@pytest.mark.asyncio
async def test_interaction_rejects_non_numeric_at(clock):
    result = await _interaction({"tier": "light", "kind": "tap", "at": "now"})
    assert result["status"] == "error"
    assert "at" in result["detail"]


@pytest.mark.asyncio
async def test_light_interaction_nudges_without_trigger(clock, emotion_manager):
    ctx = _FakeCtx()
    result = await _interaction({"tier": "light", "kind": "stroke", "at": clock.now, "summary": "用户抚摸了你"}, ctx)
    assert result == {"status": "ok", "tier": "light", "nudge": ["+JOY", "-BOREDOM"], "triggered": False}
    assert emotion_manager.calls == [("emotion-engine", {"action": "apply_tags", "tags": ["+JOY", "-BOREDOM"]})]
    assert ctx.triggers == []


@pytest.mark.asyncio
async def test_hover_interaction_has_no_nudge(clock, emotion_manager):
    result = await _interaction({"tier": "light", "kind": "hover", "at": clock.now})
    assert result["nudge"] == []
    assert emotion_manager.calls == []


@pytest.mark.asyncio
async def test_heavy_interaction_enqueues_one_trigger(clock, emotion_manager):
    ctx = _FakeCtx()
    result = await _interaction(
        {
            "tier": "heavy",
            "kind": "multi_tap",
            "count": 3,
            "duration_ms": 1240,
            "distance_px": 0,
            "area": "Head",
            "position": {"x": 0.42, "y": 0.71},
            "model_type": "live2d",
            "at": clock.now,
            "summary": "用户连续戳了你 3 下",
        },
        ctx,
    )
    assert result["tier"] == "heavy"
    assert result["triggered"] is True
    assert len(ctx.triggers) == 1
    task = ctx.triggers[0]
    assert task["type"] == "event"
    assert task["event_name"] == "avatar_interaction"
    assert task["payload"]["summary"] == "用户连续戳了你 3 下"
    assert task["recall_description"] == "用户与模型交互：用户连续戳了你 3 下"
    assert task["id"].startswith("avatar_interaction::")
    # 不传 run_background：有前端连接时走前台流式推送
    assert "run_background" not in task
    assert emotion_manager.calls == [("emotion-engine", {"action": "apply_tags", "tags": ["+IRRITATION", "+CURIOSITY"]})]


@pytest.mark.asyncio
async def test_interaction_survives_emotion_engine_failure(clock, monkeypatch):
    from faust_backend.runtime import state as runtime_state

    class _Boom:
        async def communicate(self, plugin_id, payload):
            raise RuntimeError("plugin disabled")

    monkeypatch.setattr(runtime_state, "plugin_manager", _Boom())
    result = await _interaction({"tier": "light", "kind": "tap", "at": clock.now})
    assert result["status"] == "ok"
    assert result["nudge"] == ["+CURIOSITY"]


@pytest.mark.asyncio
async def test_interaction_buffer_is_capped(clock):
    for index in range(impl.INTERACTION_BUFFER_MAX + 12):
        await _interaction({"tier": "light", "kind": "tap", "at": clock.now, "summary": f"第{index}次"})
    assert len(impl._interactions) == impl.INTERACTION_BUFFER_MAX
    assert impl._interactions[0]["summary"] == "第12次"
    assert impl._interactions[-1]["summary"] == f"第{impl.INTERACTION_BUFFER_MAX + 11}次"


@pytest.mark.asyncio
async def test_interaction_uses_server_clock(clock):
    await _interaction({"tier": "light", "kind": "tap", "at": 1_758_000_000.0, "summary": "x"})
    record = impl._interactions[0]
    assert record["reported_at"] == pytest.approx(1_758_000_000.0)
    assert record["at"] == pytest.approx(clock.now)


# ── 下一轮消息备注 ──


@pytest.mark.asyncio
async def test_message_received_injects_fresh_interaction_once(clock):
    plugin = impl.Plugin()
    await _interaction({"tier": "light", "kind": "tap", "at": clock.now, "summary": "用户戳了你一下"})
    first = plugin.message_received("你好", [], _FakeCtx())
    assert first.startswith("你好")
    assert "（刚才用户与你互动：用户戳了你一下）" in first
    # 游标去重：同一批交互不会在下一轮被反复注入
    assert plugin.message_received("你好", [], _FakeCtx()) is None


@pytest.mark.asyncio
async def test_message_received_collapses_repeated_interactions(clock):
    plugin = impl.Plugin()
    await _interaction({"tier": "light", "kind": "hover", "at": clock.now, "summary": "用户的鼠标掠过了你"})
    await _interaction({"tier": "light", "kind": "hover", "at": clock.now, "summary": "用户的鼠标掠过了你"})
    await _interaction({"tier": "light", "kind": "tap", "at": clock.now, "summary": "用户戳了你一下"})
    note = plugin.message_received("你好", [], _FakeCtx())
    assert "用户的鼠标掠过了你（×2）；用户戳了你一下" in note


@pytest.mark.asyncio
async def test_message_received_skips_stale_interactions(clock):
    plugin = impl.Plugin()
    await _interaction({"tier": "light", "kind": "tap", "at": clock.now, "summary": "一小时前"})
    clock.advance(impl.INTERACTION_NOTE_TTL_SECONDS + 1)
    assert plugin.message_received("你好", [], _FakeCtx()) is None


@pytest.mark.asyncio
async def test_message_received_returns_none_without_interactions():
    assert impl.Plugin().message_received("你好", [], _FakeCtx()) is None


@pytest.mark.asyncio
async def test_message_received_does_not_consume_buffer(clock):
    plugin = impl.Plugin()
    await _interaction({"tier": "light", "kind": "tap", "at": clock.now, "summary": "用户戳了你一下"})
    plugin.message_received("你好", [], _FakeCtx())
    state = await _get_state()
    assert [item["summary"] for item in state["interactions"]] == ["用户戳了你一下"]


# ── prompt suffix ──


def test_prompt_suffix_mentions_tools_and_conditions():
    suffix = impl.Plugin().register_prompt_suffix()[0]
    for name in (
        "listAvatarCapabilities",
        "setAvatarEmotion",
        "setAvatarExpression",
        "playAvatarMotion",
        "setAvatarParameters",
        "clearAvatarPerformance",
    ):
        assert name in suffix
    assert "faustbot://avatar/interactions.json" in suffix


@pytest.mark.asyncio
async def test_prompt_suffix_reports_missing_capabilities(clock):
    assert "尚未收到前端的能力上报" in impl.Plugin().register_prompt_suffix()[0]
    await _report()
    assert "当前模型：live2d" in impl.Plugin().register_prompt_suffix()[0]


# ── emotion-engine 的 apply_tags ──


def _load_emotion_engine():
    path = REPO_PLUGIN_DIR / "emotion-engine" / "impl.py"
    spec = importlib.util.spec_from_file_location("emotion_engine_impl_for_avatar", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["emotion_engine_impl_for_avatar"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_emotion_engine_apply_tags(tmp_path, monkeypatch):
    engine = _load_emotion_engine()
    monkeypatch.setattr(engine, "_write_corememory_state", lambda payload: None)
    monkeypatch.setattr(engine, "_read_corememory_state", lambda: None)
    plugin = engine.Plugin()
    plugin.store = engine.EmotionEngineStore(tmp_path / "data")

    result = await _call_communicate(plugin, {"action": "apply_tags", "tags": ["+JOY", "-BOREDOM"]})
    assert result["status"] == "ok"
    assert result["vector"]["joy"] > engine.DEFAULT_EMOTIONS["joy"]
    assert result["vector"]["boredom"] < engine.DEFAULT_EMOTIONS["boredom"]

    before = dict(result["vector"])
    bad = await _call_communicate(plugin, {"action": "apply_tags", "tags": "JOY"})
    assert bad == {"status": "error", "detail": "tags must be list[str]"}
    bad_item = await _call_communicate(plugin, {"action": "apply_tags", "tags": ["JOY", 3]})
    assert bad_item == {"status": "error", "detail": "tags must be list[str]"}
    assert plugin.get_state_payload()["vector"] == before


async def _call_communicate(plugin, payload):
    result = plugin.communicate_handler(payload, None)
    if hasattr(result, "__await__"):
        result = await result
    return result


# ── 插件加载集成 ──


@pytest.mark.asyncio
async def test_plugin_loads_registers_tools_and_vfs_nodes(tmp_path, monkeypatch):
    from faust_backend.config_loader import PLUGIN_DATA_ROOT  # noqa: F401  仅确认可导入
    import faust_backend.config_loader as conf
    from faust_backend.plugin_system import PluginManager
    from faust_backend.tools.vfs import get_faustbot_vfs

    monkeypatch.setattr(conf, "PLUGIN_DATA_ROOT", str(tmp_path / "plugin_data"))
    pm = PluginManager(plugins_dir=REPO_PLUGIN_DIR, state_file=str(tmp_path / "plugin-state.json"))
    pm.set_plugin_enabled("avatar-performance", True)
    await pm.reload(force=True)

    record = pm._plugins["avatar-performance"]
    assert record["plugin"] is not None
    names = [spec.name for spec in record["tools"]]
    assert names == [
        "listAvatarCapabilities",
        "setAvatarEmotion",
        "setAvatarExpression",
        "playAvatarMotion",
        "setAvatarParameters",
        "clearAvatarPerformance",
    ]
    # 插件面板/工具表展示的是首行摘要，不能是 "Description:" 这种标签
    for spec in record["tools"]:
        assert spec.description and spec.description != "Description:", spec.name
        assert len(spec.description) > 8, spec.name

    merged_names = {
        getattr(tool, "name", getattr(tool, "__name__", "")) for tool in pm.compose_tools([], "faust")
    }
    assert "listAvatarCapabilities" in merged_names
    assert "listAvailableMotionsTool" not in merged_names

    assert any("Avatar Performance" in suffix for suffix in pm.collect_prompt_suffixes())

    vfs = await get_faustbot_vfs(refresh=True)
    assert await vfs.exists("/avatar/capabilities.json")
    assert await vfs.exists("/avatar/interactions.json")

    # communicate 路由契约
    reported = await pm.communicate("avatar-performance", {"action": "report_capabilities", "capabilities": LIVE2D_CAPS})
    assert reported == {"status": "ok", "model_type": "live2d", "facs_key_count": 4, "unknown_facs_keys": []}
    listing = await pm.communicate("avatar-performance", {"action": "get_state"})
    assert listing["capabilities"]["motions"] == ["Idle", "Flick", "Tap", "wave"]

    # agent 可经 faustbot:// 直接读能力快照（VFS 符号节点内容函数）
    from faust_backend.tools.read import read

    snapshot = await read.ainvoke({"uri": "faustbot://avatar/capabilities.json"})
    assert '"model_type": "live2d"' in snapshot
    await vfs.delete("/avatar")
