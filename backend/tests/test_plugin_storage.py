# backend/tests/test_plugin_storage.py
"""PluginStorage 生命周期测试：GLOBAL 常驻、SESSION per-agent、clear/compact 重置。"""
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from faust_backend.plugin_system.plugin_storage import PluginStorage  # noqa: E402


def test_global_scope_persists_and_survives_reset(tmp_path):
    st = PluginStorage("p1", tmp_path, agent_name="faust")
    st.set("global", "counter", 42)
    assert st.get("global", "counter") == 42
    # reset_session 不影响 global
    st.reset_session()
    assert st.get("global", "counter") == 42
    # 落盘验证
    data = json.loads((tmp_path / "storage" / "global.json").read_text(encoding="utf-8"))
    assert data["counter"] == 42


def test_session_scope_per_agent(tmp_path):
    st_a = PluginStorage("p1", tmp_path, agent_name="faust")
    st_b = PluginStorage("p1", tmp_path, agent_name="nova")
    st_a.set("session", "recent_turns", [["/a"]])
    # 每个 Agent 各一份
    assert st_a.get("session", "recent_turns") == [["/a"]]
    assert st_b.get("session", "recent_turns") is None
    assert (tmp_path / "storage" / "session_faust.json").exists()
    st_b.set("session", "recent_turns", [["/b"]])
    assert st_b.get("session", "recent_turns") == [["/b"]]
    assert st_a.get("session", "recent_turns") == [["/a"]]
    assert (tmp_path / "storage" / "session_nova.json").exists()


def test_session_reset_to_registered_defaults(tmp_path):
    st = PluginStorage("p1", tmp_path, agent_name="faust")
    st.register_defaults({"recent_turns": [], "mode": "full"})
    st.set("session", "recent_turns", [["/a"], ["/b"]])
    st.set("session", "mode", "lite")
    st.set("session", "unregistered_key", "x")
    st.reset_session()
    assert st.get("session", "recent_turns") == []
    assert st.get("session", "mode") == "full"
    # 未注册默认值的键在重置后消失
    assert st.get("session", "unregistered_key") is None


def test_session_reset_persists_and_reloads(tmp_path):
    st = PluginStorage("p1", tmp_path, agent_name="faust")
    st.register_defaults({"recent_turns": []})
    st.set("session", "recent_turns", [["/a"]])
    st.reset_session()
    # 新实例（模拟插件重载/后端重启）读到重置后的值
    st2 = PluginStorage("p1", tmp_path, agent_name="faust")
    assert st2.get("session", "recent_turns") == []


def test_unknown_scope_raises(tmp_path):
    st = PluginStorage("p1", tmp_path, agent_name="faust")
    try:
        st.get("bogus", "k")
        assert False, "应当抛出 ValueError"
    except ValueError:
        pass
