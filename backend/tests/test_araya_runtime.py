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
    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.model_name = kwargs.get("model")

    class FakeAgent:
        async def ainvoke(self, payload):
            from types import SimpleNamespace
            return {"messages": [SimpleNamespace(content="maintained")], "payload": payload}

        async def astream_events(self, payload, config=None, version=None):
            class FakeAIMessageChunk:
                type = "ai"
                content = "maintained"
            yield {"event": "on_chat_model_stream", "data": {"chunk": FakeAIMessageChunk()}}

    import faust_backend.provider as provider_mod

    async def _fake_build_main_chat_model(providers, intensity=None):
        # Araya 必须经 provider.build_main_chat_model 获取统一配置的 LLM
        return FakeChatOpenAI(model="fake-model")

    monkeypatch.setattr(provider_mod, "build_main_chat_model", _fake_build_main_chat_model)
    monkeypatch.setattr(araya_runtime, "create_agent", lambda **kwargs: FakeAgent())
    monkeypatch.setattr(araya_runtime.ArayaRuntime, "_build_tools", lambda self: [])

    runtime = araya_runtime.ArayaRuntime()
    result = runtime.run_once(reason="manual-test")
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

    async def fake_run_once_async(reason: str = "manual", since_ts: float | None = None):
        return {"status": "ok", "reason": reason}

    monkeypatch.setattr(runtime, "run_once_async", fake_run_once_async)
    # trigger_run is async; run it in an event loop for the test
    import asyncio as _asyncio
    result = _asyncio.run(runtime.trigger_run(reason="manual-test"))
    assert result["accepted"] is True
    assert result["status"] == "queued"


def test_araya_should_trigger_false_after_grouping_marker(monkeypatch, tmp_path):
    """触发后立即写入 last_trigger_ts 抢占，下次轮响应返回 False，避免重复刷屏。"""
    import faust_backend.araya_runtime as ar
    # 直接用真实 runtime 的 should_trigger + _load_state/_save_state 验证抢占效果
    monkeypatch.setattr(ar.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(ar.conf, "AGENT_NAME", "faust")
    monkeypatch.setattr(ar.conf, "ARAYA_ENABLED", True)
    monkeypatch.setattr(ar.conf, "ARAYA_IDLE_MINUTES", 1)

    runtime = ar.ArayaRuntime()
    state = runtime._load_state()
    state["last_main_activity_ts"] = time.time() - 120  # idle 已超阈值
    state["last_trigger_ts"] = 0.0
    state["idle_minutes"] = 1
    runtime._save_state(state)
    assert runtime.should_trigger() is True

    # 模拟 loop 触发后的抢占：last_trigger_ts 更新到 now
    state["last_trigger_ts"] = time.time()
    runtime._save_state(state)
    # 即便 idle 仍超阈值，抢占后 should_trigger 也应为 False（不重复触发）
    assert runtime.should_trigger() is False


def test_araya_allowlist_matches_exposed_tools(monkeypatch, tmp_path):
    """ARAYA_ALLOWED_TOOL_NAMES 必须与 _build_tools() 实际暴露的工具名一致（防白名单漂移）。"""
    from faust_backend.tools._registry import ARAYA_ALLOWED_TOOL_NAMES

    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")

    exposed = {t.name for t in araya_runtime.ArayaRuntime()._build_tools()}

    assert exposed == ARAYA_ALLOWED_TOOL_NAMES


def test_araya_add_entity_links_source_file(read_memory_store):
    """kb_refs_json 里的来源文件必须真的建出 from 边，实体才挂得上文件（图谱页可见）。"""
    import asyncio

    runtime = araya_runtime.ArayaRuntime()
    tools = {t.name: t for t in runtime._build_tools()}
    asyncio.run(read_memory_store.file_write("/records/2026-09-18.md", "今天的事"))

    eid = tools["arayaAddEntityTool"].invoke({
        "name": "阿赖耶",
        "entity_type": "concept",
        "kb_refs_json": '["/records/2026-09-18.md"]',
    })

    assert [c["id"] for c in read_memory_store.get_entity_children("/records/2026-09-18.md")] == [eid]


def test_araya_attachment_read_keeps_image_bytes_out_of_text(
    monkeypatch, tmp_path, isolated_output_store
):
    """Araya 读图必须走主 Agent 同一套输出管线：文本里只有 artifact 引用。

    否则整张图的 base64 会作为文本进入 ToolMessage，一次读图就能撑爆上下文。
    """
    import asyncio
    import base64 as _base64

    import faust_backend.memory as memory_pkg
    from faust_backend.runtime.middleware import wrap_tools

    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")

    image_b64 = _base64.b64encode(b"\x89PNG" + b"x" * 400_000).decode("ascii")

    class FakeMemory:
        async def attachment_read(self, path: str) -> dict:
            return {
                "description": "屏幕截图",
                "content_type": "image/png",
                "content_base64": image_b64,
            }

    monkeypatch.setattr(memory_pkg, "get_memory", lambda agent_name=None, **_: FakeMemory())

    tools = wrap_tools(araya_runtime.ArayaRuntime()._build_tools())
    tool = next(t for t in tools if t.name == "arayaAttachmentReadTool")

    result = asyncio.run(tool.ainvoke({"path": "/images/shot.png"}))

    assert "data:image" not in result
    assert "base64" not in result
    assert "artifact://" in result
    assert len(result) < 1000

def _araya_runtime_with_fake_llm(monkeypatch, tmp_path, stream_events):
    """构造只替换 LLM/Agent 的 ArayaRuntime（工具集留空，不触网）。"""
    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")
    monkeypatch.setattr(araya_runtime.conf, "ARAYA_ENABLED", True)

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            self.model_name = kwargs.get("model")

    class FakeAgent:
        async def astream_events(self, payload, config=None, version=None):
            for event in stream_events(payload):
                yield event

    import faust_backend.provider as provider_mod

    async def _fake_build_main_chat_model(providers, intensity=None):
        return FakeChatOpenAI(model="fake-model")

    monkeypatch.setattr(provider_mod, "build_main_chat_model", _fake_build_main_chat_model)
    monkeypatch.setattr(araya_runtime, "create_agent", lambda **kwargs: FakeAgent())
    monkeypatch.setattr(araya_runtime.ArayaRuntime, "_build_tools", lambda self: [])
    return araya_runtime.ArayaRuntime()


def _ok_events(_payload):
    class FakeAIMessageChunk:
        type = "ai"
        content = "maintained"

    yield {"event": "on_chat_model_stream", "data": {"chunk": FakeAIMessageChunk()}}


def _boom_events(_payload):
    raise RuntimeError("模型炸了")
    yield  # pragma: no cover —— 仅为让本函数成为生成器


def test_araya_idle_trigger_hands_maintenance_window(monkeypatch, tmp_path):
    """回归：idle 触发的窗口起点是上次成功维护的结束时间，不是抢占写入的“现在”。

    旧实现把防重复触发的 last_trigger_ts 直接当窗口起点，而抢占写入发生在 run 读取
    之前，于是每轮 changed-nodes 的窗口恒为 0 秒、永远返回空。
    """
    import asyncio

    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")
    monkeypatch.setattr(araya_runtime.conf, "ARAYA_ENABLED", True)
    monkeypatch.setattr(araya_runtime.conf, "ARAYA_IDLE_MINUTES", 1)

    runtime = araya_runtime.ArayaRuntime()
    state = runtime._load_state()
    state.update({
        "last_main_activity_ts": time.time() - 3600,
        "last_trigger_ts": 0.0,
        "last_maintenance_ts": 1234.5,
        "idle_minutes": 1,
    })
    runtime._save_state(state)

    runs: list[tuple[str, float | None]] = []

    async def _fake_run(reason: str = "manual", since_ts: float | None = None) -> dict:
        runs.append((reason, since_ts))
        return {}

    monkeypatch.setattr(runtime, "run_once_async", _fake_run)

    async def _drive() -> float | None:
        runtime._run_lock = asyncio.Lock()
        window = await runtime._trigger_idle_run()
        await asyncio.sleep(0)  # 让 create_task 的 run 跑起来
        return window

    assert asyncio.run(_drive()) == 1234.5
    assert runs == [("idle", 1234.5)]
    # 抢占仍然生效：last_trigger_ts 已推进，同一空闲窗口不会被重复触发
    assert runtime.should_trigger() is False


def test_araya_watermark_advances_only_on_success(monkeypatch, tmp_path):
    """回归：失败的轮次不得推进 changed-nodes 水印（否则窗口被永久跳过）。"""
    ok = _araya_runtime_with_fake_llm(monkeypatch, tmp_path, _ok_events)
    state = ok._load_state()
    state["last_maintenance_ts"] = 111.0
    ok._save_state(state)

    result = ok.run_once(reason="manual-test", since_ts=111.0)

    assert result["status"] == "ok"
    assert ok._load_state()["last_maintenance_ts"] > 111.0
    assert ok.get_status()["last_log"]["since_ts"] == 111.0


def test_araya_failed_run_keeps_watermark(monkeypatch, tmp_path):
    bad = _araya_runtime_with_fake_llm(monkeypatch, tmp_path, _boom_events)
    state = bad._load_state()
    state["last_maintenance_ts"] = 222.0
    bad._save_state(state)

    result = bad.run_once(reason="manual-test", since_ts=222.0)

    assert result["status"] == "error"
    assert bad._load_state()["last_maintenance_ts"] == 222.0


def test_araya_target_agent_snapshot_holds_during_run(monkeypatch, tmp_path):
    """回归（09-22 换库事故）：run 期间切换激活 Agent，维护目标不得跟着漂移。"""
    import asyncio

    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")

    runtime = araya_runtime.ArayaRuntime()
    assert runtime.run_target_agent == "faust"

    async def _drive() -> tuple[str, str]:
        runtime._run_lock = asyncio.Lock()
        async with runtime._run_lock:
            runtime._run_target_agent = "faust"
            monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "ishmael")  # 运行中用户切库
            return runtime.refresh_target_agent(), runtime.run_target_agent

    assert asyncio.run(_drive()) == ("faust", "faust")
    # 运行结束后才跟随新的激活 Agent
    assert runtime.refresh_target_agent() == "ishmael"


def test_araya_state_migration_seeds_watermark_from_last_trigger(monkeypatch, tmp_path):
    """老 state 没有维护水印：必须用上次触发时间兜底，否则升级后首轮把全库当增量。"""
    _prepare_araya_prompt(tmp_path)
    monkeypatch.setattr(araya_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(araya_runtime.conf, "AGENT_NAME", "faust")

    runtime = araya_runtime.ArayaRuntime()
    runtime.paths.state_file.write_text(
        json.dumps({"last_trigger_ts": 999.5, "last_main_activity_ts": 999.5}),
        encoding="utf-8",
    )

    state = runtime._load_state()
    assert state["last_maintenance_ts"] == 999.5
    assert runtime.maintenance_watermark(state) == 999.5
