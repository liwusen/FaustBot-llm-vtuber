from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.argv = [sys.argv[0]]

PLUGIN_DIR = BACKEND_ROOT / "default_plugins" / "agent_dream"


def _load_module(name: str, file: Path):
    spec = importlib.util.spec_from_file_location(f"agent_dream_test_{name}", file)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


impl = _load_module("impl", PLUGIN_DIR / "impl.py")


class _FakeCtx:
    def __init__(self, data_dir: Path, config: dict):
        self.plugin_id = "agent_dream"
        self.plugin_dir = data_dir
        self.plugin_data_dir = data_dir / "plugin_data" / "agent_dream"
        self._config = dict(config)
        self._vfs: dict[str, str] = {}
        self._symbolic: dict[str, object] = {}
        self._registered: list = []

    def register_config(self, schema):
        self._registered = list(schema)
        return None

    def get_config(self, key, default=None):
        return self._config.get(key, default)

    def vfs_write(self, path, content):
        self._vfs[path] = content

    def vfs_write_symbolic(self, path, func, should_be_included_in_search=True):
        self._symbolic[path] = func


def _default_config(**overrides):
    cfg = {
        "sleep_window_start": 0,
        "sleep_window_end": 5,
        "dream_frequency_hours": 24,
        "dream_probability": 1.0,
        "dream_memory_days": 7,
        "dream_memory_limit": 6,
        "dream_temperature": 1.1,
        "dream_system_prompt": "",
    }
    cfg.update(overrides)
    return cfg


def _make_plugin(tmp_path, **config_overrides):
    ctx = _FakeCtx(tmp_path / "dream", _default_config(**config_overrides))
    p = impl.Plugin()
    p.startup(ctx)
    return p


@pytest.fixture()
def plugin(tmp_path):
    return _make_plugin(tmp_path)


# ── 时间窗判断 ──


def test_time_window_judgment():
    assert impl._in_window(2, 0, 5) is True
    assert impl._in_window(0, 0, 5) is True
    assert impl._in_window(5, 0, 5) is False
    assert impl._in_window(10, 0, 5) is False
    # 跨天窗口 22:00-05:00
    assert impl._in_window(23, 22, 5) is True
    assert impl._in_window(1, 22, 5) is True
    assert impl._in_window(22, 22, 5) is True
    assert impl._in_window(10, 22, 5) is False


# ── 调度：窗口 + 冷却 + 概率 + 状态 ──


def test_should_dream_in_window_out_of_window(plugin):
    now = datetime(2026, 8, 10, 2, 0, 0)
    plugin._last_dream_ts = 0.0
    assert plugin._should_dream(now) is True
    assert plugin._should_dream(datetime(2026, 8, 10, 10, 0, 0)) is False


def test_should_dream_cooldown(plugin):
    now = datetime(2026, 8, 10, 2, 0, 0)
    # 1 小时前刚做过梦 < 24h 冷却
    plugin._last_dream_ts = now.timestamp() - 3600
    assert plugin._should_dream(now) is False
    # 超过冷却后放行
    plugin._last_dream_ts = now.timestamp() - 25 * 3600
    assert plugin._should_dream(now) is True


def test_should_dream_probability(plugin, monkeypatch):
    now = datetime(2026, 8, 10, 2, 0, 0)
    plugin._last_dream_ts = 0.0
    plugin.ctx._config["dream_probability"] = 0.5
    monkeypatch.setattr(impl.random, "random", lambda: 0.9)
    assert plugin._should_dream(now) is False
    monkeypatch.setattr(impl.random, "random", lambda: 0.1)
    assert plugin._should_dream(now) is True


def test_should_dream_blocked_while_dreaming(plugin):
    now = datetime(2026, 8, 10, 2, 0, 0)
    plugin._last_dream_ts = 0.0
    plugin._dreaming = True
    assert plugin._should_dream(now) is False


def test_should_dream_respects_failure_backoff(plugin):
    now = datetime(2026, 8, 10, 2, 0, 0)
    plugin._last_dream_ts = 0.0
    # 失败退避期内不放行
    plugin._failure_backoff_until = now.timestamp() + 1800
    assert plugin._should_dream(now) is False
    # 退避结束后放行
    plugin._failure_backoff_until = now.timestamp() - 1
    assert plugin._should_dream(now) is True


def test_heartbeat_starts_dream_when_due(plugin, monkeypatch):
    calls = []
    monkeypatch.setattr(plugin, "_should_dream", lambda now=None: True)
    monkeypatch.setattr(plugin, "_start_dream", lambda: calls.append(1))
    plugin.heartbeat(plugin.ctx)
    assert calls == [1]

    monkeypatch.setattr(plugin, "_should_dream", lambda now=None: False)
    plugin.heartbeat(plugin.ctx)
    assert calls == [1]


# ── 提示词模板渲染 ──


def test_render_prompt_with_memories():
    now = datetime(2026, 8, 10, 2, 0, 0)
    memories = [
        {
            "path": "/diary/2026-08-09/000001.md",
            "content": "今天写了一个深夜做梦的插件，字符像萤火虫。",
            "updated_at": "2026-08-09T22:00:00Z",
        }
    ]
    prompt = impl.Plugin()._render_prompt(now, memories)
    assert "{{Month}}" not in prompt and "{{Day}}" not in prompt
    assert "{{TimeOfDay}}" not in prompt and "{{DreamTreeBlock}}" not in prompt
    assert "8月10日" in prompt
    assert "凌晨" in prompt
    assert "萤火虫" in prompt
    assert "/diary/2026-08-09/000001.md" in prompt


def test_render_prompt_fallback_without_memories():
    now = datetime(2026, 8, 10, 2, 0, 0)
    prompt = impl.Plugin()._render_prompt(now, [])
    assert "{{DreamTreeBlock}}" not in prompt
    assert "记忆之海一片空白" in prompt


def test_render_prompt_truncates_long_memory():
    now = datetime(2026, 8, 10, 2, 0, 0)
    long_content = "长" * 500
    prompt = impl.Plugin()._render_prompt(now, [{"path": "/a.md", "content": long_content, "updated_at": ""}])
    assert "…" in prompt
    assert ("长" * 301) not in prompt


# ── 梦境保存到 fake VFS ──


def test_save_dream_writes_vfs_and_state(plugin):
    now = datetime(2026, 8, 10, 2, 0, 0)
    narrative = "我梦见自己在一片数据海洋里漂浮，光标变成水母。"
    memories = [
        {"path": "/diary/2026-08-09/000001.md", "content": "内容", "updated_at": "2026-08-09T22:00:00Z"}
    ]
    path = plugin._save_dream(narrative, memories, now)
    assert path == "/plugins/agent_dream/dreams/2026-08-10_dream.md"
    assert path in plugin.ctx._vfs
    assert narrative in plugin.ctx._vfs[path]
    assert "记忆涟漪: 1 条" in plugin.ctx._vfs[path]
    assert plugin._last_dream_path == path
    assert plugin._last_dream_ts == now.timestamp()

    # latest.md 为 symbolic 节点，读取时返回最新梦境
    latest_fn = plugin.ctx._symbolic["/plugins/agent_dream/latest.md"]
    assert narrative in latest_fn("/plugins/agent_dream/latest.md")

    # 冷却状态落盘
    state = json.loads((plugin.ctx.plugin_data_dir / "dream_state.json").read_text(encoding="utf-8"))
    assert state["last_dream_ts"] == now.timestamp()
    assert state["last_dream_path"] == path


def test_startup_exposes_state_symbolic(plugin):
    plugin._dreaming = False
    state_fn = plugin.ctx._symbolic["/plugins/agent_dream/state.json"]
    data = json.loads(state_fn("/plugins/agent_dream/state.json"))
    assert data["status"] == "sleeping"

    plugin._dreaming = True
    data = json.loads(state_fn("/plugins/agent_dream/state.json"))
    assert data["status"] == "dreaming"


def test_startup_registers_config_and_readme(plugin):
    keys = {item["key"] for item in plugin.ctx._registered}
    assert {"sleep_window_start", "sleep_window_end", "dream_frequency_hours", "dream_probability"}.issubset(keys)
    assert "/plugins/agent_dream/README.md" in plugin.ctx._vfs


# ── 完整入梦流程（假记忆 + 假 LLM，不碰网络）──


def test_dream_once_end_to_end(plugin, monkeypatch):
    async def fake_memories():
        return [
            {"path": "/diary/2026-08-09/000001.md", "content": "今天写了 agent-dream 插件。", "updated_at": "2026-08-09T22:00:00Z"}
        ]

    async def fake_generate(prompt):
        assert "agent-dream 插件" in prompt
        return "我在梦里敲代码，字符化作萤火虫。"

    monkeypatch.setattr(plugin, "_collect_memory_ripples", fake_memories)
    monkeypatch.setattr(plugin, "_generate_dream", fake_generate)
    plugin._dreaming = True

    asyncio.run(plugin._dream_once())

    assert plugin._dreaming is False
    # 成功后清除失败退避
    assert plugin._failure_backoff_until == 0.0
    dreams = [p for p in plugin.ctx._vfs if p.startswith("/plugins/agent_dream/dreams/")]
    assert len(dreams) == 1
    assert "萤火虫" in plugin.ctx._vfs[dreams[0]]


def test_dream_once_empty_memory_fallback(plugin, monkeypatch):
    async def no_memories():
        return []

    async def fake_generate(prompt):
        assert "记忆之海一片空白" in prompt
        return "空白之梦，什么都没有发生。"

    monkeypatch.setattr(plugin, "_collect_memory_ripples", no_memories)
    monkeypatch.setattr(plugin, "_generate_dream", fake_generate)

    asyncio.run(plugin._dream_once())

    dreams = [p for p in plugin.ctx._vfs if p.startswith("/plugins/agent_dream/dreams/")]
    assert len(dreams) == 1
    assert "空白之梦" in plugin.ctx._vfs[dreams[0]]
    assert plugin._dreaming is False


def test_dream_once_failure_swallowed(plugin, monkeypatch):
    async def boom():
        raise RuntimeError("memory unavailable")

    monkeypatch.setattr(plugin, "_collect_memory_ripples", boom)

    asyncio.run(plugin._dream_once())

    assert plugin._dreaming is False
    assert not [p for p in plugin.ctx._vfs if p.startswith("/plugins/agent_dream/dreams/")]
    # 失败后进入退避：至少 dream_failure_backoff_sec（默认 1800s）内不重试，
    # 避免 heartbeat 每 10s 触发一次梦境生成风暴
    assert plugin._failure_backoff_until > time.time()
    assert plugin._failure_backoff_until - time.time() >= 1790


# ── 手动入梦工具 ──


def test_trigger_dream_tool(plugin, monkeypatch):
    async def noop():
        pass

    monkeypatch.setattr(plugin, "_dream_once", noop)
    tools = plugin.register_tools(plugin.ctx)
    spec = next(t for t in tools if t.name == "trigger_dream")
    assert spec.tool is not None

    result = json.loads(spec.tool.invoke({"reason": "想看看今晚的梦"}))
    assert result["status"] == "dreaming"
    assert result["reason"] == "想看看今晚的梦"


def test_trigger_dream_blocked_while_busy(plugin):
    plugin._dreaming = True
    spec = next(t for t in plugin.register_tools(plugin.ctx) if t.name == "trigger_dream")
    result = json.loads(spec.tool.invoke({"reason": "再来一次"}))
    assert result["status"] == "error"
