from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

IMPL_PATH = (
    Path(__file__).resolve().parents[1]
    / "default_plugins"
    / "emotion-engine"
    / "impl.py"
)


def _load_impl():
    spec = importlib.util.spec_from_file_location("emotion_engine_impl", IMPL_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


impl = _load_impl()


class Clock:
    """可控时钟，monkeypatch impl._now。"""

    def __init__(self, start: float = 1_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(impl, "_now", c)
    return c


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    # 隔离 corememory 读写，避免触碰真实配置
    monkeypatch.setattr(impl, "_write_corememory_state", lambda payload: None)
    monkeypatch.setattr(impl, "_read_corememory_state", lambda: None)
    return tmp_path / "data"


# ── tier() 档位映射 ──


def test_tier_default_is_normal(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    assert store.tier() == "normal"


def test_tier_chatty_when_happy_dominant(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.set_emotion("joy", 6.0)
    assert store.tier() == "chatty"


def test_tier_curiosity_high_is_chatty(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.set_emotion("curiosity", 5.5)
    assert store.tier() == "chatty"


def test_tier_quiet_when_negative_dominant(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.set_emotion("irritation", 5.0)
    assert store.tier() == "quiet"


def test_tier_quiet_when_total_low(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    for key in impl.EMOTION_KEYS:
        store.set_emotion(key, 1.0)
    assert store.tier() == "quiet"


def test_tier_silent_when_very_bored(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.set_emotion("boredom", 8.0)
    assert store.tier() == "silent"


def test_tier_joy_below_threshold_stays_normal(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.set_emotion("joy", 4.8)
    assert store.tier() == "normal"


# ── 态度模板渲染（趋势 / 变化链） ──


def test_mood_trend_up(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.apply_signed_emotion_tag_list(["+JOY"])       # deltas 和 +0.8
    store.apply_signed_emotion_tag_list(["-BOREDOM"])   # deltas 和 -1.4
    store.apply_signed_emotion_tag_list(["+CURIOSITY"])  # deltas 和 +1.8
    # 近 3 条和 = 0.8 - 1.4 + 1.8 = +1.2 → 上升中
    assert store._mood_trend() == "上升中"


def test_mood_trend_down(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.apply_signed_emotion_tag_list(["+JOY"])       # +0.8
    store.apply_signed_emotion_tag_list(["-BOREDOM"])   # -1.4
    store.apply_signed_emotion_tag_list(["--JOY"])      # joy -3.6, boredom +2.0 → -1.6
    # 0.8 - 1.4 - 1.6 = -2.2 → 下降中
    assert store._mood_trend() == "下降中"


def test_mood_trend_flat(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store._record_history("manual", {"joy": 0.0})
    store._record_history("manual", {"boredom": 0.0})
    store._record_history("manual", {"curiosity": 0.0})
    assert store._mood_trend() == "平稳"


def test_recent_change_chain(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.apply_signed_emotion_tag_list(["+JOY"])
    store.apply_signed_emotion_tag_list(["-BOREDOM"])
    store.apply_signed_emotion_tag_list(["+SHARPNESS"])
    assert (
        store._recent_change_chain()
        == "message_sent:joy → message_sent:boredom → message_sent:sharpness"
    )


def test_recent_change_chain_empty(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    assert store._recent_change_chain() == "（暂无）"


def test_mood_trend_ignores_heartbeat_entries(data_dir, clock):
    store = impl.EmotionEngineStore(data_dir)
    store.apply_signed_emotion_tag_list(["+JOY"])      # 过滤后近 3 条和 +0.8
    store.apply_signed_emotion_tag_list(["-BOREDOM"])  # 过滤后近 3 条和 -1.4
    for _ in range(5):
        clock.advance(10)
        store.heartbeat(0.1)  # reason=heartbeat，deltas 恒 0.0
    # 不过滤时近 3 条全是 heartbeat → 平稳；过滤后 = 0.8 - 1.4 = -0.6 → 下降中
    assert store._mood_trend() == "下降中"


def test_recent_change_chain_ignores_heartbeat(data_dir, clock):
    store = impl.EmotionEngineStore(data_dir)
    store.apply_signed_emotion_tag_list(["+JOY"])
    store.apply_signed_emotion_tag_list(["-BOREDOM"])
    store.apply_signed_emotion_tag_list(["+SHARPNESS"])
    for _ in range(3):
        clock.advance(10)
        store.heartbeat(0.1)
    assert (
        store._recent_change_chain()
        == "message_sent:joy → message_sent:boredom → message_sent:sharpness"
    )


def test_recent_change_chain_only_heartbeat_returns_none(data_dir, clock):
    store = impl.EmotionEngineStore(data_dir)
    for _ in range(3):
        clock.advance(10)
        store.heartbeat(0.1)
    assert store._recent_change_chain() == "（暂无）"


def test_build_prompt_suffix_includes_attitude_block(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.apply_signed_emotion_tag_list(["+JOY"])
    suffix = store.build_prompt_suffix({})
    # 既有说明保留
    assert "[Emotion Engine]" in suffix
    assert "EmotionInvoke" in suffix
    # 新增：档位 / 趋势 / 变化链 / 态度模板
    assert "当前档位" in suffix
    assert "情绪趋势" in suffix
    assert "最近变化" in suffix
    assert "message_sent:joy" in suffix
    assert "态度" in suffix


def test_build_prompt_suffix_silent_template(data_dir):
    store = impl.EmotionEngineStore(data_dir)
    store.set_emotion("boredom", 8.0)
    suffix = store.build_prompt_suffix({})
    assert "沉默" in suffix
    assert "（silent）" in suffix


# ── 无回应惩罚（触发与冷却） ──


def test_no_response_penalty_trigger_and_cooldown(data_dir, clock):
    store = impl.EmotionEngineStore(data_dir)
    store.note_user_message("你好")
    clock.advance(10)
    store.mark_reply_done()
    boredom_before = store._state.vector["boredom"]

    # 未到超时
    clock.advance(100)
    assert store.check_no_response_penalty(300) is False

    # 到达超时 → 触发，boredom +0.5
    clock.advance(200)  # 距 done 310s
    assert store.check_no_response_penalty(300) is True
    assert store._state.vector["boredom"] == pytest.approx(boredom_before + 0.5)

    # 冷却中
    clock.advance(100)
    assert store.check_no_response_penalty(300) is False

    # 冷却结束 → 再次触发
    clock.advance(300)
    assert store.check_no_response_penalty(300) is True
    assert store._state.vector["boredom"] == pytest.approx(boredom_before + 1.0)


def test_no_response_penalty_blocked_by_new_message(data_dir, clock):
    store = impl.EmotionEngineStore(data_dir)
    store.note_user_message("你好")
    clock.advance(10)
    store.mark_reply_done()
    clock.advance(400)
    store.note_user_message("新消息")  # 期间有新消息 → 不惩罚
    assert store.check_no_response_penalty(300) is False


def test_no_response_penalty_requires_done(data_dir, clock):
    store = impl.EmotionEngineStore(data_dir)
    clock.advance(400)
    assert store.check_no_response_penalty(300) is False


def test_no_response_penalty_wired_in_heartbeat(monkeypatch):
    seen = {}

    class FakeStore:
        def heartbeat(self, decay):
            return {"boredom_bump": False}

        def should_write_diary(self, reason, significant):
            return False

        def snapshot(self, config=None):
            return {"pending_corememory_sync": False}

        def sync_corememory(self):
            pass

        def check_no_response_penalty(self, timeout):
            seen["timeout"] = timeout
            return True

        def tier(self):
            return "normal"

        def should_proactive(self, interval):
            return False

        def mark_proactive_fired(self):
            pass

    plugin = impl.Plugin()
    plugin.ctx = _FakeCtx({})
    plugin.store = FakeStore()
    plugin.heartbeat(None)
    assert seen.get("timeout") == pytest.approx(300.0)


# ── 离线回归补算 ──


def _write_last_decay(store_dir: Path, last_decay_ts: float) -> None:
    path = store_dir / impl.STATE_FILE_NAME
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["last_decay_ts"] = last_decay_ts
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_offline_regression_catchup(data_dir, clock):
    d1 = data_dir / "case1"
    store = impl.EmotionEngineStore(d1, decay_per_minute=0.2)
    store.set_emotion("joy", 8.0)
    _write_last_decay(d1, clock.now - 600)  # 离线 10 分钟

    reloaded = impl.EmotionEngineStore(d1, decay_per_minute=0.2)
    # 0.2 * 10 分钟 = 2.0 → joy 8 → 6
    assert reloaded._state.vector["joy"] == pytest.approx(6.0)
    # boredom 不参与衰减
    assert reloaded._state.vector["boredom"] == pytest.approx(2.0)
    # 补算后 last_decay_ts 对齐到 now
    assert reloaded._state.last_decay_ts == pytest.approx(clock.now)


def test_offline_regression_skipped_under_60s(data_dir, clock):
    d1 = data_dir / "case1"
    store = impl.EmotionEngineStore(d1, decay_per_minute=0.2)
    store.set_emotion("joy", 8.0)
    _write_last_decay(d1, clock.now - 30)  # 离线 30 秒，低于 60s 阈值

    reloaded = impl.EmotionEngineStore(d1, decay_per_minute=0.2)
    assert reloaded._state.vector["joy"] == pytest.approx(8.0)


def test_offline_regression_respects_configured_rate(data_dir, clock):
    d1 = data_dir / "case1"
    store = impl.EmotionEngineStore(d1, decay_per_minute=0.5)
    store.set_emotion("joy", 8.0)
    _write_last_decay(d1, clock.now - 600)  # 离线 10 分钟

    reloaded = impl.EmotionEngineStore(d1, decay_per_minute=0.5)
    # 0.5 * 10 = 5.0 → joy 8 → 3
    assert reloaded._state.vector["joy"] == pytest.approx(3.0)


# ── 主动对话调度（间隔判断） ──


def test_proactive_interval_check_real_store(data_dir, clock):
    store = impl.EmotionEngineStore(data_dir)
    # 初始化时 last_proactive_ts = now → 未到间隔
    assert store.should_proactive(900) is False
    clock.advance(899)
    assert store.should_proactive(900) is False
    clock.advance(1)
    assert store.should_proactive(900) is True
    store.mark_proactive_fired()
    assert store.should_proactive(900) is False


class _FakeCtx:
    def __init__(self, configs=None, data_dir=None):
        self.configs = dict(configs or {})
        self.plugin_data_dir = data_dir
        self.plugin_dir = data_dir or Path(".")
        self.registered = []

    def register_config(self, schema):
        self.registered.append(schema)

    def get_config(self, key, default=None):
        return self.configs.get(key, default)

    def list_configs(self):
        return dict(self.configs)

    def vfs_write(self, *args, **kwargs):
        return None

    def vfs_write_symbolic(self, *args, **kwargs):
        return None


class _FakeStore:
    def __init__(self, tier="normal"):
        self.tier_value = tier
        self.checked_intervals = []
        self.proactive_fired = 0

    def heartbeat(self, decay):
        return {"boredom_bump": False}

    def should_write_diary(self, reason, significant):
        return False

    def snapshot(self, config=None):
        return {"pending_corememory_sync": False}

    def sync_corememory(self):
        pass

    def check_no_response_penalty(self, timeout):
        return False

    def tier(self):
        return self.tier_value

    def should_proactive(self, interval):
        self.checked_intervals.append(interval)
        return True

    def mark_proactive_fired(self):
        self.proactive_fired += 1


def _make_plugin(store, configs=None):
    plugin = impl.Plugin()
    plugin.ctx = _FakeCtx(configs=configs)
    plugin.store = store
    return plugin


def test_proactive_scheduling_normal_tier(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        impl,
        "_schedule_proactive_chat",
        lambda content: scheduled.append(content) or True,
    )
    store = _FakeStore(tier="normal")
    plugin = _make_plugin(store, {"PROACTIVE_ENABLED": True})
    plugin.heartbeat(None)
    assert store.checked_intervals == [3600]
    assert len(scheduled) == 1
    assert scheduled[0].startswith("（情绪系统主动寒暄）")
    assert store.proactive_fired == 1


def test_proactive_scheduling_chatty_and_quiet_intervals(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        impl,
        "_schedule_proactive_chat",
        lambda content: scheduled.append(content) or True,
    )
    store = _FakeStore(tier="chatty")
    plugin = _make_plugin(store, {})
    plugin.heartbeat(None)
    assert store.checked_intervals == [900]

    store2 = _FakeStore(tier="quiet")
    plugin2 = _make_plugin(store2, {})
    plugin2.heartbeat(None)
    assert store2.checked_intervals == [7200]
    assert len(scheduled) == 2


def test_proactive_scheduling_silent_paused(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        impl,
        "_schedule_proactive_chat",
        lambda content: scheduled.append(content) or True,
    )
    store = _FakeStore(tier="silent")
    plugin = _make_plugin(store, {})
    plugin.heartbeat(None)
    assert store.checked_intervals == []
    assert scheduled == []


def test_proactive_scheduling_disabled(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        impl,
        "_schedule_proactive_chat",
        lambda content: scheduled.append(content) or True,
    )
    store = _FakeStore(tier="normal")
    plugin = _make_plugin(store, {"PROACTIVE_ENABLED": False})
    plugin.heartbeat(None)
    assert store.checked_intervals == []
    assert scheduled == []


def test_proactive_skips_when_interval_not_reached(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        impl,
        "_schedule_proactive_chat",
        lambda content: scheduled.append(content) or True,
    )

    class NotDueStore(_FakeStore):
        def should_proactive(self, interval):
            self.checked_intervals.append(interval)
            return False

    store = NotDueStore(tier="normal")
    plugin = _make_plugin(store, {})
    plugin.heartbeat(None)
    assert store.checked_intervals == [3600]
    assert scheduled == []
    assert store.proactive_fired == 0


# ── done 事件记录 last_reply_ts ──


def test_agent_event_done_records_last_reply_ts(data_dir, clock):
    plugin = impl.Plugin()
    plugin.ctx = _FakeCtx(data_dir=data_dir)
    plugin.store = impl.EmotionEngineStore(data_dir)
    assert plugin.store._state.last_reply_ts == 0.0
    result = plugin.agent_event_sent({"type": "done"}, [], None)
    assert result is None
    assert plugin.store._state.last_reply_ts == pytest.approx(clock.now)


def test_agent_event_emotion_invoke_still_ignored(data_dir):
    plugin = impl.Plugin()
    plugin.ctx = _FakeCtx(data_dir=data_dir)
    plugin.store = impl.EmotionEngineStore(data_dir)
    assert (
        plugin.agent_event_sent(
            {"type": "tool_start", "tool_name": "EmotionInvokeSigned"}, [], None
        )
        == "__IGNORED__"
    )
    assert (
        plugin.agent_event_sent({"type": "delta", "content": "hi"}, [], None) is None
    )


def test_covered_hooks_marked_as_hookimpl():
    # 覆盖 hook 必须带 @hookimpl（pluggy 按函数标记注册，不继承父类装饰器）
    for name in (
        "register_tools",
        "register_frontend",
        "register_prompt_suffix",
        "communicate_handler",
        "health_check",
    ):
        fn = getattr(impl.Plugin, name)
        assert getattr(fn, "faustbot_impl", None) is not None


# ── startup：新配置注册与衰减速率透传 ──


def test_startup_registers_new_configs(data_dir):
    plugin = impl.Plugin()
    ctx = _FakeCtx(configs={}, data_dir=data_dir)
    plugin.startup(ctx)
    keys = [item["key"] for item in ctx.registered[0]]
    for expected in (
        "PROACTIVE_ENABLED",
        "PROACTIVE_INTERVAL_CHATTY",
        "PROACTIVE_INTERVAL_NORMAL",
        "PROACTIVE_INTERVAL_QUIET",
        "NO_RESPONSE_TIMEOUT",
    ):
        assert expected in keys
    assert plugin.store is not None


def test_startup_uses_configured_decay(data_dir):
    plugin = impl.Plugin()
    ctx = _FakeCtx(configs={"DECAY_PER_MINUTE": 0.3}, data_dir=data_dir)
    plugin.startup(ctx)
    assert plugin.store._decay_per_minute == pytest.approx(0.3)
