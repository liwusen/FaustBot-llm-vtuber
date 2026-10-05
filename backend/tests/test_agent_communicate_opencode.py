"""真实 opencode 集成测试（**默认 skip**）。

规格 docs/agent-communicate-spec.md §17.4：覆盖真实 `initialize → session/new → session/prompt`
（只读提问）并断言 `result.md` 非空；顺带记录是否观察到 `request_permission`
（这是规格 §18 未验证项 1/2/10 的收敛点）。

开启条件：环境变量 `FAUST_ACP_IT=1` 且 `opencode` 可执行。

Run: $env:FAUST_ACP_IT=1; .runtime/python.exe -m pytest backend/tests/test_agent_communicate_opencode.py -v -s
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path

import pytest
import pytest_asyncio

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_ROOT.parent
REPO_PLUGIN_DIR = BACKEND_ROOT / "default_plugins" / "agent-communicate"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(REPO_PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(REPO_PLUGIN_DIR))

import faust_backend.config_loader as conf  # noqa: E402
from faust_backend.plugin_system import PluginManager  # noqa: E402

OPENCODE = shutil.which("opencode")
ENABLED = os.environ.get("FAUST_ACP_IT") == "1" and bool(OPENCODE)

pytestmark = pytest.mark.skipif(
    not ENABLED,
    reason="真实链路集成测试默认跳过：需要 FAUST_ACP_IT=1 且 PATH 上有 opencode",
)

PROMPT = (
    "只回答一个问题，不要修改、创建或删除任何文件，也不要运行会改变磁盘内容的命令："
    "这个仓库的后端入口文件叫什么名字？（一句话回答）"
)


@pytest_asyncio.fixture
async def opencode_harness(tmp_path, monkeypatch):
    monkeypatch.setattr(conf, "PLUGIN_DATA_ROOT", str(tmp_path / "plugin_data"))
    plugins_dir = tmp_path / "plugins"
    shutil.copytree(
        REPO_PLUGIN_DIR,
        plugins_dir / "agent-communicate",
        ignore=shutil.ignore_patterns("__pycache__", "data"),
    )
    pm = PluginManager(plugins_dir=plugins_dir, state_file=str(tmp_path / "state.json"))
    await pm.reload()
    record = pm._plugins["agent-communicate"]  # noqa: SLF001
    assert record["plugin"] is not None
    plugin = record["plugin"]
    try:
        yield plugin
    finally:
        await plugin.plugin_unloaded(record["ctx"])


@pytest.mark.asyncio
async def test_real_opencode_read_only_prompt(opencode_harness, capsys):
    plugin = opencode_harness
    resolved = json.loads(
        json.dumps(
            [item.to_dict() for item in plugin.registry.resolve_agents()], ensure_ascii=False
        )
    )
    names = [item["name"] for item in resolved]
    assert "opencode" in names, f"探测没有发现 opencode：{resolved}"
    opencode_entry = next(item for item in resolved if item["name"] == "opencode")
    print(f"\n[IT] 探测到的 opencode 命令：{opencode_entry['command']}")

    await plugin._handle_action(  # noqa: SLF001
        "agents_save",
        {
            "agents": [
                {
                    **opencode_entry,
                    "cwd": str(REPO_ROOT),
                    "enabled": True,
                    "handshake_timeout_sec": 60,
                    "session_timeout_sec": 120,
                    "task_timeout_sec": 600,
                    "permission_timeout_sec": 300,
                }
            ]
        },
    )

    await plugin.surface.submit("opencode", json.dumps({"prompt": PROMPT, "notify": "none"}))
    task_id = plugin.surface.last_task_id("opencode")
    assert task_id, "提交后没有拿到 task id"

    deadline = time.monotonic() + 620
    saw_permission = False
    auth_methods: list[str] = []
    while time.monotonic() < deadline:
        task = plugin.store.get(task_id)
        if plugin.store.pending_permissions("opencode"):
            saw_permission = True
        runtime = plugin.registry.existing("opencode")
        if runtime is not None and not auth_methods:
            auth_methods = [str(getattr(item, "id", "")) for item in runtime.bridge.auth_methods]
        if task is not None and task.is_terminal:
            break
        await asyncio.sleep(1.0)

    task = plugin.store.get(task_id)
    assert task is not None
    result = await plugin.ctx.vfs_read_text(f"/agents/opencode/tasks/{task_id}/result.md", default="")
    events = await plugin.ctx.vfs_read_text(f"/agents/opencode/tasks/{task_id}/events.md", default="")

    # §18 收敛点：把真实链路观察到的事实打出来（无论成功失败都要留痕）
    with capsys.disabled():
        print(f"\n[IT] task={task_id} status={task.status} stopReason={task.stop_reason}")
        print(f"[IT] authMethods={auth_methods}")
        print(f"[IT] 观察到 request_permission：{saw_permission}")
        print(f"[IT] result.md 长度={len(result)}，events.md 行数={len(events.splitlines())}")

    assert task.status == "completed", f"任务未成功：{task.status} / {task.error}\n{events}"
    assert result.strip(), "result.md 为空（规格要求非空）"
