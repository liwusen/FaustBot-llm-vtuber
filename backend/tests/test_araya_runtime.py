import asyncio
import json
import sys
import time
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.argv = [sys.argv[0]]

import faust_backend.araya_runtime as araya_runtime


def _prepare_araya_prompt(root: Path) -> None:
    agent_root = root / "agents" / "araya"
    agent_root.mkdir(parents=True, exist_ok=True)
    for name in ("AGENT.md", "ROLE.md", "COREMEMORY.md", "TASK.md"):
        (agent_root / name).write_text(f"# {name}\n", encoding="utf-8")


def test_araya_should_trigger_after_idle(monkeypatch, tmp_path):
    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")
    monkeypatch.setattr(araya_runtime.conf, "ARAYA_ENABLED", True)
    monkeypatch.setattr(araya_runtime.conf, "ARAYA_IDLE_MINUTES", 1)

    runtime = araya_runtime.ArayaRuntime()
    state = runtime._load_state()
    state["last_main_activity_ts"] = time.time() - 120
    state["last_trigger_ts"] = 0.0
    state["idle_minutes"] = 1
    runtime._save_state(state)

    assert runtime.should_trigger() is True


def test_araya_run_once_updates_state_and_log(monkeypatch, tmp_path):
    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")
    monkeypatch.setattr(araya_runtime.conf, "ARAYA_ENABLED", True)
    monkeypatch.setattr(araya_runtime.conf, "ARAYA_IDLE_MINUTES", 30)
    monkeypatch.setattr(araya_runtime.conf, "CHAT_MODEL", "fake-model")
    monkeypatch.setattr(araya_runtime.conf, "CHAT_API_KEY", "fake-key")
    monkeypatch.setattr(araya_runtime.conf, "CHAT_API_BASE", "http://example.test/v1")
    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class FakeAgent:
        async def ainvoke(self, payload):
            from types import SimpleNamespace
            return {"messages": [SimpleNamespace(content="maintained")], "payload": payload}

    monkeypatch.setattr(araya_runtime, "ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(araya_runtime, "create_agent", lambda **kwargs: FakeAgent())
    monkeypatch.setattr(araya_runtime.ArayaRuntime, "_build_tools", lambda self: [])

    runtime = araya_runtime.ArayaRuntime()
    result = asyncio.run(runtime.run_once(reason="manual-test"))
    assert result["status"] == "ok"
    assert result["target_agent"] == "faust"

    status = runtime.get_status()
    assert status["last_run_status"] == "ok"
    assert status["last_log"] is not None
    assert status["last_log"]["reason"] == "manual-test"

    last_log = json.loads(runtime.paths.last_log_file.read_text(encoding="utf-8"))
    assert last_log["status"] == "ok"
    assert runtime.paths.history_log_file.exists()


def test_araya_mark_main_agent_activity_updates_timestamp(monkeypatch, tmp_path):
    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")

    runtime = araya_runtime.ArayaRuntime()
    before = time.time()
    stamped = runtime.mark_main_agent_activity()
    after = time.time()

    assert before <= stamped <= after
    state = runtime._load_state()
    assert float(state["last_main_activity_ts"]) == stamped


def test_araya_trigger_run_is_non_blocking(monkeypatch, tmp_path):
    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")

    runtime = araya_runtime.ArayaRuntime()

    async def fake_run_once(reason: str = "manual"):
        await asyncio.sleep(0)
        return {"status": "ok", "reason": reason}

    monkeypatch.setattr(runtime, "run_once", fake_run_once)

    async def main():
        result = runtime.trigger_run(reason="manual-test")
        assert result["accepted"] is True
        assert result["status"] == "queued"
        await asyncio.sleep(0)

    asyncio.run(main())