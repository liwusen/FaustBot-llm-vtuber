"""agent-communicate 插件测试（规格 §17.3）。

覆盖：信封解析、队列（上限/串行/并行）、状态机、ACK、权限四路与超时/迟到、
持久化与恢复、探测、reload 幂等、卸载清理、读不产生外部 I/O（不变量）。
外部 Agent 用 fixtures/fake_acp_agent.py（SDK 自带的 Agent API 写成）。

Run: .runtime/python.exe -m pytest backend/tests/test_agent_communicate.py -v
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
REPO_PLUGIN_DIR = BACKEND_ROOT / "default_plugins" / "agent-communicate"
FAKE_AGENT = BACKEND_ROOT / "tests" / "fixtures" / "fake_acp_agent.py"
PYTHON = REPO_ROOT / ".runtime" / "python.exe"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_PLUGIN_DIR))

import faust_backend.config_loader as conf  # noqa: E402
import faust_backend.trigger_manager as trigger_manager  # noqa: E402
from faust_backend.plugin_system import PluginManager  # noqa: E402
from faust_backend.tools.vfs import get_faustbot_vfs  # noqa: E402

import agents as agents_module  # noqa: E402
import tasks as tasks_module  # noqa: E402
from tasks import (  # noqa: E402
    Envelope,
    EnvelopeError,
    Task,
    TaskStore,
    parse_envelope,
    parse_permission_decision,
    select_permission_option,
)

TERMINAL = ("completed", "failed", "timeout", "cancelled", "rejected", "interrupted")


def fake_agent_def(name: str = "fake", mode: str = "normal", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "command": [str(PYTHON), str(FAKE_AGENT), mode],
        "cwd": str(REPO_ROOT),
        "enabled": True,
        "description": "fake ACP agent",
        "handshake_timeout_sec": 20,
        "session_timeout_sec": 10,
        "task_timeout_sec": 60,
        "permission_timeout_sec": 5,
        "permission_default": "deny",
    }
    payload.update(overrides)
    return payload


class Harness:
    def __init__(self, pm: PluginManager, plugin: Any, ctx: Any, tmp_path: Path) -> None:
        self.pm = pm
        self.plugin = plugin
        self.ctx = ctx
        self.tmp_path = tmp_path

    @property
    def store(self) -> TaskStore:
        return self.plugin.store

    @property
    def registry(self) -> Any:
        return self.plugin.registry

    @property
    def surface(self) -> Any:
        return self.plugin.surface

    async def configure(self, *defs: dict[str, Any]) -> dict[str, Any]:
        return await self.plugin._handle_action("agents_save", {"agents": list(defs)})

    async def set_config_value(self, key: str, value: Any) -> None:
        await self.ctx.set_config(key, value)
        await self.plugin.config_changed(key, None, value, self.ctx)

    async def submit(self, agent: str, envelope: Any) -> str:
        """通过 VFS submit 节点提交（走真实 write handler 路径）。"""
        return await self.surface.submit(agent, envelope)

    async def wait(self, task_id: str, timeout: float = 40.0) -> Task:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            task = self.store.get(task_id)
            if task is not None and task.is_terminal:
                return task
            await asyncio.sleep(0.05)
        raise AssertionError(f"任务 {task_id} 在 {timeout}s 内没有进入终态：{self.store.get(task_id)}")

    async def read(self, path: str) -> str:
        return await self.ctx.vfs_read_text(path, default="")


@pytest_asyncio.fixture
async def harness(tmp_path, monkeypatch):
    """真实 PluginManager + 真实插件加载（插件目录复制到 tmp），探测结果固定。"""
    monkeypatch.setattr(conf, "PLUGIN_DATA_ROOT", str(tmp_path / "plugin_data"))

    async def _no_discovery() -> list[dict[str, Any]]:
        return [
            {"name": item.name, "available": False, "reason": "测试环境禁用探测", "command": [], "exe": ""}
            for item in agents_module.CANDIDATES
        ]

    monkeypatch.setattr(agents_module, "run_discovery", _no_discovery)

    triggers: list[dict[str, Any]] = []

    def _capture(payload: dict[str, Any]) -> None:
        triggers.append(payload)

    monkeypatch.setattr(trigger_manager, "append_trigger", _capture)

    plugins_dir = tmp_path / "plugins"
    shutil.copytree(REPO_PLUGIN_DIR, plugins_dir / "agent-communicate", ignore=shutil.ignore_patterns("__pycache__", "data"))
    pm = PluginManager(plugins_dir=plugins_dir, state_file=str(tmp_path / "state.json"))
    await pm.reload()
    record = pm._plugins["agent-communicate"]  # noqa: SLF001
    assert record["plugin"] is not None, pm.list_plugins()
    instance = Harness(pm, record["plugin"], record["ctx"], tmp_path)
    instance.triggers = triggers  # type: ignore[attr-defined]
    try:
        yield instance
    finally:
        await record["plugin"].plugin_unloaded(record["ctx"])


# ============================================================
# 1. 信封解析（规格 §11）
# ============================================================

class TestEnvelope:
    def _parse(self, raw: Any, **overrides: Any):
        kwargs = {"default_cwd": str(REPO_ROOT), "default_notify": "batched", "default_timeout": 1800.0}
        kwargs.update(overrides)
        return parse_envelope(raw, **kwargs)

    def test_json_object(self):
        env = self._parse(json.dumps({"prompt": "改代码", "notify": "normal", "config": {"model": "x"}}))
        assert env.prompt == "改代码"
        assert env.notify == "normal"
        assert env.config == {"model": "x"}
        assert env.cwd == str(REPO_ROOT)
        assert env.timeout_sec == 1800.0
        assert env.warnings == []

    def test_plain_text_shorthand(self):
        env = self._parse("解释一下 backend/main.py 的启动流程")
        assert env.prompt == "解释一下 backend/main.py 的启动流程"
        assert env.session == "reuse" and env.notify == "batched"

    def test_invalid_json_treated_as_prompt(self):
        env = self._parse("{ not json at all")
        assert env.prompt == "{ not json at all"

    @pytest.mark.parametrize("raw", ["123", '"x"', "[1, 2]"])
    def test_non_object_json_treated_as_prompt(self, raw: str):
        env = self._parse(raw)
        assert env.prompt == raw

    @pytest.mark.parametrize("raw", ["", "   ", json.dumps({"prompt": ""}), json.dumps({"other": 1})])
    def test_missing_prompt_rejected(self, raw: str):
        with pytest.raises(EnvelopeError):
            self._parse(raw)

    def test_unknown_fields_ignored_with_warning(self):
        env = self._parse(json.dumps({"prompt": "x", "bogus": 1, "another": 2}))
        assert env.warnings and "bogus" in env.warnings[0] and "another" in env.warnings[0]

    def test_bad_notify_rejected_lists_legal(self):
        with pytest.raises(EnvelopeError) as excinfo:
            self._parse(json.dumps({"prompt": "x", "notify": "sometimes"}))
        assert "batched" in str(excinfo.value) and "normal" in str(excinfo.value)

    def test_config_must_be_object(self):
        with pytest.raises(EnvelopeError):
            self._parse(json.dumps({"prompt": "x", "config": ["model"]}))

    def test_cwd_must_exist(self):
        with pytest.raises(EnvelopeError) as excinfo:
            self._parse(json.dumps({"prompt": "x", "cwd": str(REPO_ROOT / "no-such-dir")}))
        assert "cwd" in str(excinfo.value)

    @pytest.mark.parametrize("value", [0, -1, "abc"])
    def test_timeout_must_be_positive(self, value: Any):
        with pytest.raises(EnvelopeError):
            self._parse(json.dumps({"prompt": "x", "timeout_sec": value}))


# ============================================================
# 2. 状态机（规格 §9.1；终态不可逆）
# ============================================================

class _StubRegistry:
    """TaskStore 单测用的最小注册表替身。

    关键：`enqueue_trigger` 必须显式注入，否则 `_finish` 会走生产路径去写
    **真实的** trigger_manager（污染用户 ~/.faustbot 下的触发器文件）。
    """

    def __init__(self) -> None:
        self.settings = agents_module.RegistrySettings()

    def runtimes(self) -> list[Any]:
        return []

    def get(self, name: str) -> Any:
        return None

    def resolve_agents(self) -> list[Any]:
        return []

    def agent_names(self) -> list[str]:
        return []


class TestStateMachine:
    def _store(self, tmp_path: Path, triggers: list[dict[str, Any]] | None = None) -> TaskStore:
        return TaskStore(
            data_dir=tmp_path / "data",
            registry=_StubRegistry(),  # type: ignore[arg-type]
            enqueue_trigger=(triggers.append if triggers is not None else (lambda _payload: None)),
        )

    def test_all_declared_transitions_are_legal(self, tmp_path):
        store = self._store(tmp_path)
        for source, targets in tasks_module.VALID_TRANSITIONS.items():
            for target in targets:
                task = Task(id="task_1", agent="a", prompt="p", cwd=".", status=source)
                assert store._transition(task, target) is True, (source, target)  # noqa: SLF001
                assert task.status == target

    def test_illegal_transition_ignored(self, tmp_path):
        store = self._store(tmp_path)
        task = Task(id="task_1", agent="a", prompt="p", cwd=".", status="queued")
        assert store._transition(task, "running") is False  # noqa: SLF001
        assert task.status == "queued"

    @pytest.mark.asyncio
    async def test_terminal_is_irreversible(self, tmp_path):
        triggers: list[dict[str, Any]] = []
        store = self._store(tmp_path, triggers)
        task = Task(id="task_1", agent="a", prompt="p", cwd=".", status="running")
        await store._finish(task, "completed", None)  # noqa: SLF001
        assert task.status == "completed"
        await store._finish(task, "failed", "不应覆盖")  # noqa: SLF001
        assert task.status == "completed" and task.error is None
        assert len(triggers) == 1, "终态只应通知一次"

    def test_terminal_set_matches_spec(self):
        assert tasks_module.TERMINAL_STATUSES == {
            "completed",
            "cancelled",
            "timeout",
            "failed",
            "rejected",
            "interrupted",
        }

    @pytest.mark.asyncio
    async def test_late_events_do_not_pollute_result(self, tmp_path):
        """终态后迟到的事件不得污染 result.md（规格 §9.1 终态不可逆）。"""
        from acp.schema import AgentMessageChunk, TextContentBlock

        store = self._store(tmp_path)
        task = Task(            id="task_1",
            agent="a",
            prompt="p",
            cwd=".",
            status="running",
            session_id="ses_1",
            result_text="最终答案",
        )
        store._tasks[task.id] = task  # noqa: SLF001
        store._session_tasks["ses_1"] = task.id  # noqa: SLF001
        await store._finish(task, "completed", None)  # noqa: SLF001

        # 终态之后又来了一段正文（真实场景：进程收尾期间的最后一条通知）
        await store.on_session_update(
            "ses_1",
            AgentMessageChunk(session_update="agent_message_chunk", content=TextContentBlock(type="text", text="迟到的尾巴")),
        )
        assert task.status == "completed"
        assert task.result_text == "最终答案", "迟到事件改写了终态结论"


# ============================================================
# 3-4. 队列、ACK、读不产生 I/O（真实提交）
# ============================================================

class TestSubmitAndQueue:
    @pytest.mark.asyncio
    async def test_submit_ack_contains_task_id_session_queue_and_guidance(self, harness):
        await harness.configure(fake_agent_def())
        ack = await harness.submit("fake", "hello fake")
        assert "task_1" in ack
        assert "session=reuse" in ack
        assert "队列 1/5" in ack
        assert "faustbot://agents/fake/tasks/task_1/result.md" in ack
        assert "faustbot://agents/fake/tasks/task_1/events.md" in ack
        task = await harness.wait("task_1")
        assert task.status == "completed"
        assert "Hello " in task.result_text and "world" in task.result_text

    @pytest.mark.asyncio
    async def test_queue_full_rejected_with_explicit_count(self, harness):
        """max_queue 约束的是**待执行队列**（状态 queued 的任务）；满即 rejected。

        用同步的 store.submit 先占住队列槽位，保证第二次提交时队列确实是满的
        （store.submit 不 await，无法让 worker 插进来取走任务）。
        """
        await harness.configure(fake_agent_def(mode="slow_prompt=1.0"))
        await harness.set_config_value("max_queue", 1)
        first = harness.store.submit(
            "fake",
            Envelope(
                prompt="第一个",
                cwd=str(REPO_ROOT),
                notify="none",
                timeout_sec=30,
            ),
        )
        assert first.status == "queued"
        assert harness.store.queue_depth("fake") == 1

        ack = await harness.submit("fake", json.dumps({"prompt": "第二个", "notify": "none"}))
        assert "队列已满 (1/1)" in ack
        second = harness.store.get("task_2")
        assert second is not None and second.status == "rejected"
        assert "队列已满 (1/1)" in (second.error or "")
        assert await harness.wait("task_1", timeout=30) is not None

    @pytest.mark.asyncio
    async def test_running_task_does_not_consume_queue_slots(self, harness):
        """语义固化：running 的任务不占 max_queue 槽位（队列 = 待执行任务）。"""
        await harness.configure(fake_agent_def(mode="slow_prompt=1.0"))
        await harness.set_config_value("max_queue", 1)
        await harness.submit("fake", json.dumps({"prompt": "跑起来的", "notify": "none"}))
        for _ in range(60):
            if harness.store.current_task("fake") is not None:
                break
            await asyncio.sleep(0.05)
        assert harness.store.queue_depth("fake") == 0
        ack = await harness.submit("fake", json.dumps({"prompt": "排队", "notify": "none"}))
        assert "队列 1/1" in ack
        assert "队列已满" not in ack

    @pytest.mark.asyncio
    async def test_same_agent_runs_serially(self, harness):
        await harness.configure(fake_agent_def(mode="slow_prompt=0.3"))
        await harness.submit("fake", json.dumps({"prompt": "one", "notify": "none"}))
        await harness.submit("fake", json.dumps({"prompt": "two", "notify": "none"}))
        first = await harness.wait("task_1", timeout=30)
        second = await harness.wait("task_2", timeout=30)
        assert second.started_at >= first.finished_at - 0.01, "同 Agent 的任务必须串行"

    @pytest.mark.asyncio
    async def test_different_agents_run_in_parallel(self, harness):
        """不同 Agent 完全并行：断言两个任务在同一时刻都处于 running。"""
        await harness.configure(
            fake_agent_def("slow-a", mode="slow_prompt=1.5"),
            fake_agent_def("slow-b", mode="slow_prompt=1.5"),
        )
        await harness.submit("slow-a", json.dumps({"prompt": "a", "notify": "none"}))
        await harness.submit("slow-b", json.dumps({"prompt": "b", "notify": "none"}))
        overlapped = False
        for _ in range(400):
            first = harness.store.current_task("slow-a")
            second = harness.store.current_task("slow-b")
            if first is not None and second is not None:
                overlapped = True
                break
            await asyncio.sleep(0.02)
        assert overlapped, "两个 Agent 没有同时在跑（被串行化了）"
        await harness.wait("task_1", timeout=40)
        await harness.wait("task_2", timeout=40)

    @pytest.mark.asyncio
    async def test_reads_never_spawn_agents(self, harness):
        """不变量 1/2：读任何节点都不产生外部 I/O，也不拉起进程。"""
        await harness.configure(fake_agent_def())
        runtime = harness.registry.get("fake")
        assert runtime is not None and not runtime.running
        for path in (
            "/agents/index.md",
            "/agents/discover.md",
            "/agents/fake/status.md",
            "/agents/fake/submit",
            "/agents/fake/config.json",
            "/agents/fake/sessions.md",
            "/agents/fake/tasks.md",
            "/agents/fake/permissions.md",
            "/agents/fake/cancel",
            "/agents/fake/close",
            "/plugins/agent-communicate.md",
        ):
            text = await harness.read(path)
            assert text, f"{path} 渲染为空"
        assert not runtime.running, "读操作拉起/启动了外部 Agent 进程（违反不变量）"
        assert runtime.bridge.pid is None

    @pytest.mark.asyncio
    async def test_search_only_touches_whitelist(self, harness):
        await harness.configure(fake_agent_def())
        await harness.submit("fake", json.dumps({"prompt": "搜索用关键词-xyz", "notify": "none"}))
        await harness.wait("task_1")
        vfs = await get_faustbot_vfs()
        hits = await vfs.search("/agents", "task_1")
        assert set(hits) <= {"/agents/index.md", "/agents/fake/tasks.md", "/agents/fake/tasks/task_1/result.md"}
        assert "/agents/fake/status.md" not in hits
        assert "/agents/fake/submit" not in hits


# ============================================================
# 5. 权限升级（规格 §12）
# ============================================================

class TestPermissions:
    @pytest.mark.asyncio
    async def test_allow_through_vfs_node(self, harness):
        """全链路：fake agent 请求权限 → 挂节点 → 触发器唤醒 → 写节点裁决 → 应答回传。"""
        await harness.configure(fake_agent_def(mode="permission", permission_timeout_sec=20))
        await harness.submit("fake", json.dumps({"prompt": "改文件", "notify": "none"}))
        pending: list[Any] = []
        for _ in range(600):  # 子进程启动 + 握手可能占用数秒，等权限请求真正到达
            pending = harness.store.pending_permissions("fake")
            if pending:
                break
            await asyncio.sleep(0.05)
        else:
            task = harness.store.get("task_1")
            raise AssertionError(
                f"没有观察到权限请求；任务状态={task.status if task else None}，"
                f"事件={task.events_text if task else ''}"
            )
        request = pending[0]
        assert request.tool_title
        assert await harness.read(f"/agents/fake/permissions/{request.request_id}.md")
        assert "req_1" in await harness.read("/agents/fake/permissions.md")

        triggers = harness.triggers  # type: ignore[attr-defined]
        assert any("req_1" in (item.get("recall_description") or "") for item in triggers)
        assert all(item.get("priority") == "normal" for item in triggers if "perm" in item.get("id", ""))

        ack = await harness.surface.answer_permission(
            "fake", request.request_id, json.dumps({"outcome": "allow", "scope": "once", "reason": "仓库内文件"})
        )
        assert "option=approve" in ack
        task = await harness.wait("task_1", timeout=30)
        assert task.status == "completed"
        assert "permission outcome: approve" in task.result_text
        events = await harness.read("/agents/fake/tasks/task_1/events.md")
        assert "reason=仓库内文件" in events, "reason 必须本地留痕"
        assert not harness.store.pending_permissions("fake")

    @pytest.mark.asyncio
    async def test_deny_maps_to_reject_option(self):
        from acp.schema import PermissionOption
        from tasks import build_permission_response

        options = [
            PermissionOption(option_id="ok", name="Allow", kind="allow_once"),
            PermissionOption(option_id="no", name="Reject", kind="reject_once"),
        ]
        response, note, option_id = build_permission_response(options, "deny", "once")
        assert option_id == "no" and not note
        assert response.outcome.outcome == "selected"

    @pytest.mark.asyncio
    async def test_always_scope_selects_always_kind(self):
        from acp.schema import PermissionOption
        from tasks import build_permission_response

        options = [
            PermissionOption(option_id="a1", name="Once", kind="allow_once"),
            PermissionOption(option_id="a2", name="Always", kind="allow_always"),
        ]
        _response, _note, option_id = build_permission_response(options, "allow", "always")
        assert option_id == "a2"

    def test_kind_missing_falls_back_to_first_option_with_note(self):
        from types import SimpleNamespace

        options = [SimpleNamespace(option_id="only", kind=""), SimpleNamespace(option_id="second", kind=None)]
        option, note = select_permission_option(options, "allow", "once")
        assert option.option_id == "only"
        assert "未提供 kind" in note

    def test_kind_mismatch_degrades_with_note(self):
        from acp.schema import PermissionOption

        options = [PermissionOption(option_id="r", name="R", kind="reject_once")]
        option, note = select_permission_option(options, "allow", "once")
        assert option.option_id == "r"
        assert "已取第一个选项" in note

    @pytest.mark.asyncio
    async def test_timeout_applies_default_action_and_records_it(self, harness):
        await harness.configure(
            fake_agent_def(mode="permission", permission_timeout_sec=1, permission_default="deny")
        )
        await harness.submit("fake", json.dumps({"prompt": "改文件", "notify": "none"}))
        task = await harness.wait("task_1", timeout=30)
        assert task.status == "completed"
        # 默认动作 deny → 按 kind 映射到 reject_once，外部 Agent 收到的就是 reject
        assert "permission outcome: reject" in task.result_text
        events = await harness.read("/agents/fake/tasks/task_1/events.md")
        assert "超时默认动作" in events
        request = harness.store.get_permission("req_1")
        assert request is not None and request.status == "defaulted"
        assert request.decision is not None and request.decision["outcome"] == "deny"

    @pytest.mark.asyncio
    async def test_late_decision_does_not_change_sent_response(self, harness):
        await harness.configure(
            fake_agent_def(mode="permission", permission_timeout_sec=1, permission_default="deny")
        )
        await harness.submit("fake", json.dumps({"prompt": "改文件", "notify": "none"}))
        await harness.wait("task_1", timeout=30)
        ack = await harness.surface.answer_permission(
            "fake", "req_1", json.dumps({"outcome": "allow", "scope": "always"})
        )
        assert "未改变已发出的应答" in ack
        request = harness.store.get_permission("req_1")
        assert request is not None and request.decision["outcome"] == "deny"

    def test_decision_parsing(self):
        assert parse_permission_decision('{"outcome":"allow","scope":"always","reason":"r"}') == (
            "allow",
            "always",
            "r",
        )
        assert parse_permission_decision("deny") == ("deny", "once", "")
        with pytest.raises(Exception):
            parse_permission_decision('{"outcome":"maybe"}')
        with pytest.raises(Exception):
            parse_permission_decision("nonsense")


# ============================================================
# 6. 持久化与恢复（规格 §9.4）
# ============================================================

class TestPersistence:
    @pytest.mark.asyncio
    async def test_task_log_written_and_restored(self, harness):
        await harness.configure(fake_agent_def())
        await harness.submit("fake", json.dumps({"prompt": "persist me", "notify": "none"}))
        await harness.wait("task_1")
        log = harness.tmp_path / "plugin_data" / "agent-communicate" / "tasks.jsonl"
        assert log.exists()
        lines = [json.loads(item) for item in log.read_text(encoding="utf-8").splitlines() if item.strip()]
        assert any(item["id"] == "task_1" and item["status"] == "completed" for item in lines)
        transcript = harness.tmp_path / "plugin_data" / "agent-communicate" / "transcripts" / "task_1.jsonl"
        assert transcript.exists() and transcript.stat().st_size > 0

        store = TaskStore(data_dir=log.parent, registry=harness.registry)
        restored = store.restore(20)
        assert [item.id for item in restored] == ["task_1"]
        assert restored[0].status == "completed"
        assert "Hello " in restored[0].result_text

    @pytest.mark.asyncio
    async def test_non_terminal_restored_as_interrupted(self, harness):
        data_dir = harness.tmp_path / "plugin_data" / "agent-communicate"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "tasks.jsonl").write_text(
            json.dumps(
                {
                    "id": "task_9",
                    "agent": "fake",
                    "prompt": "半路中断",
                    "cwd": str(REPO_ROOT),
                    "status": "running",
                    "created_at": time.time(),
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        store = TaskStore(data_dir=data_dir, registry=harness.registry)
        restored = store.restore(20)
        assert restored[0].status == "interrupted"
        assert "中断" in (restored[0].error or "")
        assert "不伪造完成" in restored[0].events_text
        # 恢复时补写的终态行落盘
        lines = [json.loads(item) for item in (data_dir / "tasks.jsonl").read_text(encoding="utf-8").splitlines()]
        assert lines[-1]["status"] == "interrupted"


# ============================================================
# 7. 探测（规格 §8）
# ============================================================

class TestDiscovery:
    @pytest.mark.asyncio
    async def test_probe_outcomes_and_reasons(self, monkeypatch, tmp_path):
        fake_cmd = tmp_path / "opencode.exe"
        fake_cmd.write_text("x", encoding="utf-8")

        monkeypatch.setattr(
            agents_module,
            "_resolve_executable",
            lambda command: str(fake_cmd) if command == "opencode" else "",
        )
        candidate = agents_module.CANDIDATES[0]
        candidate_omp = agents_module.CANDIDATES[1]

        async def _help_with_acp(_exe: str):
            return "Usage: opencode <command>\n  acp   start ACP server", False

        monkeypatch.setattr(agents_module, "_run_help", _help_with_acp)
        row = await agents_module.probe_candidate(candidate)
        assert row["available"] is True and row["command"] == [str(fake_cmd), "acp"]

        row = await agents_module.probe_candidate(candidate_omp)
        assert row["available"] is False and "找不到" in row["reason"]

        async def _help_no_acp(_exe: str):
            return "Usage: opencode", False

        monkeypatch.setattr(agents_module, "_run_help", _help_no_acp)
        row = await agents_module.probe_candidate(candidate)
        assert row["available"] is False and "没有 ACP 入口" in row["reason"]

        async def _help_timeout(_exe: str):
            return "", True

        monkeypatch.setattr(agents_module, "_run_help", _help_timeout)
        row = await agents_module.probe_candidate(candidate)
        assert row["available"] is False and "无响应" in row["reason"]

    @pytest.mark.asyncio
    async def test_discovery_cache_ttl(self, harness, monkeypatch):
        calls = {"n": 0}

        async def _counting() -> list[dict[str, Any]]:
            calls["n"] += 1
            return [{"name": "opencode", "available": True, "reason": "假探测", "command": ["x", "acp"]}]

        monkeypatch.setattr(agents_module, "run_discovery", _counting)
        cache = harness.ctx.storage
        await harness.registry.refresh_discovery(force=True, cache=cache)
        await harness.registry.refresh_discovery(cache=cache)
        assert calls["n"] == 1, "第二次应当命中缓存"

    def test_discover_md_lists_not_probed_candidates(self):
        text = agents_module.render_discover_md(
            [{"name": "opencode", "available": False, "reason": "未安装", "command": []}]
        )
        assert "opencode" in text and "未安装" in text
        assert "claude" in text and "codex" in text and "gemini" in text


# ============================================================
# 8-9. reload 幂等与卸载清理（规格 §6.3 / §16.2）
# ============================================================

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_startup_is_idempotent(self, harness):
        await harness.configure(fake_agent_def())
        runtime = harness.registry.get("fake")
        vfs = await get_faustbot_vfs()

        # 没跑过任何任务时，重复 startup 不得拉起进程/产生重复节点
        before = sorted(await vfs.walk("/agents"))
        await harness.plugin.startup(harness.ctx)
        await harness.plugin.startup(harness.ctx)
        assert sorted(await vfs.walk("/agents")) == before, "重复 startup 产生了重复/新增节点"
        assert not runtime.running, "重复 startup 不应 spawn 进程"

        # 跑完一个任务后再 startup：节点集合与任务条目都不应重复，进程不被重建
        await harness.submit("fake", json.dumps({"prompt": "idem", "notify": "none"}))
        await harness.wait("task_1")
        pid = runtime.bridge.pid
        before = sorted(await vfs.walk("/agents"))
        await harness.plugin.startup(harness.ctx)
        assert sorted(await vfs.walk("/agents")) == before, "重复 startup 后节点集合发生变化"
        assert runtime.bridge.pid == pid, "重复 startup 重建了进程"
        ids = [task.id for task in harness.store.tasks_of("fake")]
        assert ids == sorted(set(ids)), f"恢复产生了重复任务条目: {ids}"

    @pytest.mark.asyncio
    async def test_unload_cleans_processes_and_nodes(self, harness):
        import psutil

        await harness.configure(fake_agent_def())
        await harness.submit("fake", "bye")
        await harness.wait("task_1")
        runtime = harness.registry.get("fake")
        pid = runtime.bridge.pid
        assert pid

        await harness.plugin.plugin_unloaded(harness.ctx)
        vfs = await get_faustbot_vfs()
        left = [path for path in await vfs.walk("/") if path.startswith("/agents")]
        assert left == [], f"卸载后残留节点：{left}"
        assert not await vfs.exists("/plugins/agent-communicate.md"), "卸载后残留插件说明节点"
        for _ in range(50):
            if not psutil.pid_exists(pid):
                break
            await asyncio.sleep(0.1)
        assert not psutil.pid_exists(pid), "卸载后残留子进程"


# ============================================================
# 终态通知（规格 §9 / T9）
# ============================================================

class TestTerminalNotify:
    @pytest.mark.asyncio
    async def test_notify_modes(self, harness):
        await harness.configure(fake_agent_def())
        cases = [("batched", "batched"), ("normal", "normal"), ("none", None)]
        expected_ids = []
        for index, (notify, expected_priority) in enumerate(cases, start=1):
            triggers = harness.triggers  # type: ignore[attr-defined]
            triggers.clear()
            await harness.submit("fake", json.dumps({"prompt": f"case {index}", "notify": notify}))
            task = await harness.wait(f"task_{index}")
            assert task.status == "completed"
            await asyncio.sleep(0.05)
            if expected_priority is None:
                assert not triggers, "notify=none 不应入队触发器"
                continue
            assert triggers and triggers[0]["priority"] == expected_priority
            assert triggers[0]["type"] == "datetime"
            assert f"task_{index}" in triggers[0]["recall_description"]
            assert f"faustbot://agents/fake/tasks/task_{index}/result.md" in triggers[0]["recall_description"]
            expected_ids.append(triggers[0]["id"])
        assert len(set(expected_ids)) == len(expected_ids), "触发器 id 必须唯一"

    @pytest.mark.asyncio
    async def test_failure_also_notifies(self, harness):
        await harness.configure(fake_agent_def(mode="prompt_error"))
        triggers = harness.triggers  # type: ignore[attr-defined]
        triggers.clear()
        await harness.submit("fake", json.dumps({"prompt": "boom", "notify": "normal"}))
        task = await harness.wait("task_1")
        assert task.status == "failed"
        await asyncio.sleep(0.05)
        assert triggers and "失败" in triggers[0]["recall_description"]


# ============================================================
# 配置（规格 §13 / T10）
# ============================================================

class TestConfig:
    @pytest.mark.asyncio
    async def test_schema_registered_and_readable(self, harness):
        snapshot = harness.pm.get_plugin_config_snapshot("agent-communicate")
        keys = {item["key"] for item in snapshot["schema"]}
        assert {
            "agents",
            "auto_start",
            "inherit_env",
            "env_extra",
            "idle_ttl_sec",
            "max_queue",
            "task_timeout_sec",
            "permission_timeout_sec",
            "permission_default",
            "record_thoughts",
            "notify_default",
            "restore_tasks",
            "discover_enabled",
            "discover_cache_ttl_sec",
        } <= keys
        assert snapshot["values"]["auto_start"] is False
        assert snapshot["values"]["inherit_env"] is True
        assert snapshot["values"]["permission_default"] == "deny"

    @pytest.mark.asyncio
    async def test_agents_change_closes_process_and_drops_nodes(self, harness):
        await harness.configure(fake_agent_def())
        await harness.submit("fake", "hi")
        await harness.wait("task_1")
        runtime = harness.registry.get("fake")
        assert runtime.running

        await harness.configure(fake_agent_def("other", mode="normal"))
        assert harness.registry.existing("fake") is None, "被移除的 Agent 运行时应当被丢弃"
        assert not runtime.running
        vfs = await get_faustbot_vfs()
        assert not await vfs.exists("/agents/fake")
        assert await vfs.exists("/agents/other")

    @pytest.mark.asyncio
    async def test_disabled_agent_keeps_config_without_nodes(self, harness):
        result = await harness.configure(fake_agent_def("off", enabled=False))
        assert result["status"] == "ok"
        assert "off" not in harness.registry.agent_names()
        vfs = await get_faustbot_vfs()
        assert not await vfs.exists("/agents/off")
        snapshot = harness.pm.get_plugin_config_snapshot("agent-communicate")
        stored = snapshot["values"]["agents"]
        if isinstance(stored, str):
            stored = json.loads(stored)
        assert any(item["name"] == "off" for item in stored)

    @pytest.mark.asyncio
    async def test_prompt_suffix_and_frontend_assets(self, harness):
        suffixes = harness.plugin.register_prompt_suffix()
        assert suffixes and "skill://agent-communicate/SKILL.md" in suffixes[0]
        assert "turn" in suffixes[0]
        # 细节不进常驻上下文：常驻后缀里不应出现字段表/示例
        assert "timeout_sec" not in suffixes[0] and "outcome" not in suffixes[0]
        assets = harness.plugin.register_frontend()
        assert {item["type"] for item in assets} == {"css", "js"}
        assert all(item["path"].startswith("/faust/plugins/agent-communicate/frontend/") for item in assets)


class TestSkill:
    @pytest.mark.asyncio
    async def test_skill_installed_listed_and_readable(self, tmp_path, monkeypatch):
        """内置模板会装进 skill.d，listSkills 可见，skill:// 可读（规格 §15.1 验收）。"""
        from faust_backend import skill_manager
        from faust_backend.runtime import state
        from faust_backend.tools.read import read as read_tool

        config_root = tmp_path / "config"
        monkeypatch.setattr(conf, "CONFIG_ROOT", str(config_root))
        monkeypatch.setattr(conf, "AGENT_NAME", "faust")
        monkeypatch.setattr(state, "AGENT_ROOT", str(config_root / "agents" / "faust"))

        skill_manager._ensure_builtin_skills("faust")  # noqa: SLF001
        slugs = {item["slug"] for item in skill_manager.list_skills(agent_name="faust")}
        assert "agent-communicate" in slugs
        assert "agent-communicate" in skill_manager.list_skills_yaml("faust")

        text = await read_tool.ainvoke({"uri": "skill://agent-communicate/SKILL.md"})
        assert "faustbot://agents/{name}/submit" in text
        assert "权限裁决" in text and "超时" in text
        assert "不是沙箱" in text
