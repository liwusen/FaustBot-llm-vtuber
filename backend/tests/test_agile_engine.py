from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
AGILE_DIR = Path(__file__).resolve().parents[1] / "default_plugins" / "agile-engine"
sys.path.insert(0, str(AGILE_DIR))

import faust_backend.config_loader as conf  # noqa: E402
from faust_backend.plugin_system import PluginContext  # noqa: E402
from faust_backend.tools.vfs import get_faustbot_vfs  # noqa: E402
import pytest_asyncio
import runner  # noqa: E402
import asyncio


def _make_ctx(vfs, events):
    async def _vfs_read_text(path, default=""):
        return await vfs.read_text(path, default=default)

    async def _vfs_write(path, content):
        return await vfs.write(path, content)

    async def _vfs_write_symbolic(path, func, should_be_included_in_search=True, writable=False):
        return await vfs.write_symbolic(path, func, should_be_included_in_search=should_be_included_in_search, writable=writable)

    async def _vfs_set_write_handler(path, func):
        return await vfs.set_write_handler(path, func)

    async def _vfs_set_edit_handler(path, func):
        return await vfs.set_edit_handler(path, func)

    async def _vfs_delete(path):
        return await vfs.delete(path)

    async def _vfs_list(path="/"):
        return await vfs.list_dir(path)

    return PluginContext(plugin_id="agile-engine", plugin_dir=AGILE_DIR, config={
        "trigger_create": lambda payload: (events.append(payload), payload)[1],
        "vfs_read_text": _vfs_read_text,
        "vfs_write": _vfs_write,
        "vfs_write_symbolic": _vfs_write_symbolic,
        "vfs_set_write_handler": _vfs_set_write_handler,
        "vfs_set_edit_handler": _vfs_set_edit_handler,
        "vfs_delete": _vfs_delete,
        "vfs_list": _vfs_list,
    })


@pytest_asyncio.fixture(autouse=True)
async def agile_env(tmp_path, monkeypatch):
    vfs = await get_faustbot_vfs()
    events: list[dict] = []
    ctx = _make_ctx(vfs, events)
    mods_dir = tmp_path / "agile-modules"
    monkeypatch.setattr(conf, "PLUGIN_DATA_ROOT", str(tmp_path / "plugin_data"))
    runner.configure(ctx, modules_dir=mods_dir)
    await runner.register_overview_node()
    yield {"ctx": ctx, "vfs": vfs, "mods_dir": mods_dir, "events": events}
    for name in list(runner.AGILE_INSTANCES.keys()):
        try:
            await runner.unload_module(name)
        except Exception:
            pass
    await vfs.delete("/agile")


def _write_module(mods_dir, name, source):
    (mods_dir / f"{name}.py").write_text(source, encoding="utf-8")


SIMPLE_MODULE = '''
from agile_base import AgileModule

module = AgileModule("demo", "demo module")

@module.vfsContentFunc("/demo/value", cacheStrategy="nocache")
def value(_path):
    return "hello-" + _path

def get_agile_module():
    return module
'''


@pytest.mark.asyncio
async def test_load_and_content_node(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    result = await runner.load_module("demo")
    assert result["ok"], result
    assert await vfs.read_text("/demo/value") == "hello-/demo/value"
    # 框架镜像节点
    status = await vfs.read_text("/agile/demo/status")
    assert "loaded" in status
    overview = await vfs.read_text("/agile/status")
    assert "demo" in overview


@pytest.mark.asyncio
async def test_load_twice_requires_reload(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert (await runner.load_module("demo"))["ok"]
    result = await runner.load_module("demo")
    assert not result["ok"]
    assert "reload" in result["message"]


@pytest.mark.asyncio
async def test_write_handler_and_edit_handler(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "w", '''
from agile_base import AgileModule, AgileContext

module = AgileModule("w", "write test")

@module.vfsContentFunc("/w/state")
def state(_path):
    return "init"

@module.vfsWriteHook("/w/state")
async def on_write(node, content, agile: AgileContext):
    await agile.vfs_write("/w/last", f"last={content}")

@module.vfsEditHook("/w/state")
async def on_edit(node, content, agile: AgileContext):
    await agile.vfs_write("/w/last", f"last={content}")

def get_agile_module():
    return module
''')
    assert (await runner.load_module("w"))["ok"]
    await vfs.write("/w/state", "new-value")
    assert await vfs.read_text("/w/last") == "last=new-value"
    await vfs.edit("/w/state", "edited-value")
    assert await vfs.read_text("/w/last") == "last=edited-value"


@pytest.mark.asyncio
async def test_event_fire_single_dict(agile_env):
    events, mods_dir = agile_env["events"], agile_env["mods_dir"]
    _write_module(mods_dir, "ev", '''
from agile_base import AgileModule, AgileContext

module = AgileModule("ev", "event test")

@module.onloadHook()
async def boot(agile: AgileContext):
    await agile.event_fire("booted", {"x": 1}, "模块已加载")

def get_agile_module():
    return module
''')
    assert (await runner.load_module("ev"))["ok"]
    assert len(events) == 1
    payload = events[0]
    assert payload["id"] == "agileEngine::booted"
    assert payload["type"] == "event"
    assert payload["payload"] == {"x": 1}
    assert payload["recall_description"] == "模块已加载"


@pytest.mark.asyncio
async def test_unload_reversible(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert (await runner.load_module("demo"))["ok"]
    assert await vfs.get_node("/demo/value") is not None
    assert await vfs.get_node("/agile/demo/status") is not None
    assert "agile_module_demo" in sys.modules
    assert (await runner.unload_module("demo"))["ok"]
    # 模块节点与镜像节点全部删除
    assert await vfs.get_node("/demo/value") is None
    assert await vfs.get_node("/agile/demo/status") is None
    assert await vfs.get_node("/agile/modules/demo.py") is None
    assert "agile_module_demo" not in sys.modules
    # 未加载时卸载返回提示
    result = await runner.unload_module("demo")
    assert not result["ok"]


@pytest.mark.asyncio
async def test_interval_task_runs_and_stops(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "tick", '''
from agile_base import AgileModule, AgileContext

module = AgileModule("tick", "interval test")

@module.registerInterval(1)
async def tick(agile: AgileContext):
    await agile.linfo("interval-fired")

def get_agile_module():
    return module
''')
    assert (await runner.load_module("tick"))["ok"]
    handle = runner.AGILE_INSTANCES["tick"]["interval_handles"][0]
    time.sleep(1.5)
    logs = await runner.LM.getLog(agile_from="tick")
    assert any("interval-fired" in str(l.message) for l in logs)
    assert (await runner.unload_module("tick"))["ok"]
    assert not handle.thread.is_alive()


@pytest.mark.asyncio
async def test_error_module_isolated(agile_env):
    mods_dir = agile_env["mods_dir"]
    (mods_dir / "bad.py").write_text("this is not python {{{", encoding="utf-8")
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    result = await runner.load_module("bad")
    assert not result["ok"]
    assert runner.AGILE_INSTANCES["bad"]["status"] == "error"
    # 好模块不受影响
    assert (await runner.load_module("demo"))["ok"]
    assert await agile_env["vfs"].read_text("/demo/value") == "hello-/demo/value"


@pytest.mark.asyncio
async def test_disable_enable_roundtrip(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert (await runner.load_module("demo"))["ok"]
    result = await runner.disable_module("demo")
    assert result["ok"]
    assert (mods_dir / "demo.py.disabled").exists()
    assert not (mods_dir / "demo.py").exists()
    assert "demo" not in runner.AGILE_INSTANCES
    # 禁用后不再自动加载（startup 只加载 .py）
    result = await runner.enable_module("demo")
    assert result["ok"]
    assert (mods_dir / "demo.py").exists()
    assert "demo" in runner.AGILE_INSTANCES


@pytest.mark.asyncio
async def test_agile_operate_tool(agile_env):
    ctx, mods_dir = agile_env["ctx"], agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    # 按文件路径加载插件 main（避免与 backend/main.py 的 sys.modules["main"] 冲突）
    import importlib.util
    spec = importlib.util.spec_from_file_location("agile_engine_main_under_test", str(AGILE_DIR / "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plugin = mod.get_plugin()
    await plugin.startup(ctx)
    tools = plugin.register_tools(ctx)
    names = [t.name for t in tools]
    assert "agileOperate" in names
    op = tools[0].tool
    assert "demo" in await op.ainvoke({"action": "list", "name": ""})
    # startup 已自动加载 demo，load 提示已加载（含 reload 指引）
    assert "reload" in await op.ainvoke({"action": "load", "name": "demo"})
    assert "loaded" in await op.ainvoke({"action": "status", "name": "demo"})
    assert "已卸载" in await op.ainvoke({"action": "unload", "name": "demo"})
    assert "未知操作" in await op.ainvoke({"action": "bogus", "name": "demo"})
    await plugin.plugin_unloaded(ctx)


CACHE_MODULE = '''
from agile_base import AgileModule

module = AgileModule("cache", "cache test")
_calls = {"n": 0}

@module.vfsContentFunc("/cache/c", cacheStrategy="cache@0.5")
def cached(_path):
    _calls["n"] += 1
    return f"call-{_calls['n']}"

@module.vfsContentFunc("/cache/w", cacheStrategy="wait@0.5")
def waited(_path):
    _calls["n"] += 1
    return f"call-{_calls['n']}"

@module.vfsContentFunc("/cache/e", cacheStrategy="error@0.5")
def errored(_path):
    _calls["n"] += 1
    return f"call-{_calls['n']}"

@module.vfsContentFunc("/cache/n", cacheStrategy="nocache")
def nocached(_path):
    _calls["n"] += 1
    return f"call-{_calls['n']}"

def get_agile_module():
    return module
'''


@pytest.mark.asyncio
async def test_cache_strategy(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "cache", CACHE_MODULE)
    assert (await runner.load_module("cache"))["ok"]
    # cache@0.5: 连续读命中缓存
    assert await vfs.read_text("/cache/c") == "call-1"
    assert await vfs.read_text("/cache/c") == "call-1"
    time.sleep(0.6)
    assert await vfs.read_text("/cache/c") == "call-2"
    # nocache: 每次执行
    assert await vfs.read_text("/cache/n") == "call-3"
    assert await vfs.read_text("/cache/n") == "call-4"
    # wait@0.5: 连续读第二次等待后返回新值
    t0 = time.monotonic()
    await vfs.read_text("/cache/w")
    await vfs.read_text("/cache/w")
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.4, elapsed
    # error@0.5: 连续读第二次返回错误文本
    await vfs.read_text("/cache/e")
    err = await vfs.read_text("/cache/e")
    assert "读取过于频繁" in err
    time.sleep(0.6)
    assert await vfs.read_text("/cache/e") == "call-8"


DI_MODULE = '''
from agile_base import AgileModule, AgileContext

module = AgileModule("di", "di test")

@module.vfsContentFunc("/di/plain")
def plain(_path):
    return "p:" + _path

@module.vfsContentFunc("/di/with_agile")
def with_agile(_path, agile: AgileContext):
    return f"w:{_path}:{agile.agile_name}"

@module.vfsContentFunc("/di/only_agile")
async def only_agile(agile: AgileContext):
    return f"o:{agile.agile_name}"

def get_agile_module():
    return module
'''


@pytest.mark.asyncio
async def test_async_hook_and_di(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "di", DI_MODULE)
    assert (await runner.load_module("di"))["ok"]
    assert await vfs.read_text("/di/plain") == "p:/di/plain"
    assert await vfs.read_text("/di/with_agile") == "w:/di/with_agile:di"
    assert await vfs.read_text("/di/only_agile") == "o:di"


@pytest.mark.asyncio
async def test_reload_after_edit(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert (await runner.load_module("demo"))["ok"]
    assert await vfs.read_text("/demo/value") == "hello-/demo/value"
    _write_module(mods_dir, "demo", SIMPLE_MODULE.replace("hello-", "v2-"))
    assert (await runner.reload_module("demo"))["ok"]
    assert await vfs.read_text("/demo/value") == "v2-/demo/value"


TPM_MODULE = '''
from agile_base import AgileModule, AgileContext

module = AgileModule("tpm", "tpm test")

@module.onloadHook()
async def boot(agile: AgileContext):
    for i in range(3):
        await agile.event_fire(f"booted-{i}", {"i": i}, "boot event")

def get_agile_module():
    return module
'''


@pytest.mark.asyncio
async def test_tpm_blocks_excess_triggers(agile_env):
    """默认 60/min 不限；调低到 2 后第 3 次 event_fire 报错，只有前 2 次进入 trigger 系统。"""
    events, mods_dir = agile_env["events"], agile_env["mods_dir"]
    _write_module(mods_dir, "tpm", TPM_MODULE)
    assert (await runner.load_module("tpm"))["ok"]
    assert len(events) == 3
    # 调低限制并重载（重载会重新触发 onload 的 3 次 event_fire）
    assert runner.set_tpm_limit("tpm", 2)["ok"]
    await runner.reload_module("tpm")
    # 窗口内：第 1 次 ok（窗口 1/2），第 2 次 ok（2/2），第 3 次超限被隔离捕获
    assert len(events) == 3 + 2
    errors = await runner.LM.getLog(agile_from="tpm", level="ERROR")
    assert any("超过每分钟上限 2 次" in str(e.message) for e in errors)
    status = await agile_env["vfs"].read_text("/agile/tpm/status")
    assert "触发限制: 2/min" in status


@pytest.mark.asyncio
async def test_tpm_limit_settable_and_unlimited(agile_env):
    events, mods_dir = agile_env["events"], agile_env["mods_dir"]
    _write_module(mods_dir, "tpm", TPM_MODULE)
    assert (await runner.load_module("tpm"))["ok"]
    # 不限制（0）→ 3 次全放行
    assert runner.set_tpm_limit("tpm", 0)["ok"]
    await runner.reload_module("tpm")
    assert len(events) == 6
    # 负数也视为不限制
    assert runner.set_tpm_limit("tpm", -5)["ok"]
    await runner.reload_module("tpm")
    assert len(events) == 9


@pytest.mark.asyncio
async def test_tpm_window_slides(agile_env, monkeypatch):
    """60 秒窗口滑动后恢复触发能力。"""
    events, mods_dir = agile_env["events"], agile_env["mods_dir"]
    _write_module(mods_dir, "tpm", '''
from agile_base import AgileModule
module = AgileModule("tpm", "tpm test")
def get_agile_module():
    return module
''')

    class FakeClock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

    clock = FakeClock()
    monkeypatch.setattr(runner, "_monotonic", clock)
    assert (await runner.load_module("tpm"))["ok"]
    assert runner.set_tpm_limit("tpm", 1)["ok"]
    assert len(events) == 0
    agile = runner.AGILE_INSTANCES["tpm"]["agile"]
    # 触发一次（窗口 1/1）→ 再触发报错
    await agile.event_fire("x", {}, "x")
    assert len(events) == 1
    try:
        await agile.event_fire("y", {}, "y")
        raise AssertionError("应超限报错")
    except RuntimeError as exc:
        assert "超过每分钟上限 1 次" in str(exc)
    # 时钟推进 61 秒 → 窗口清空 → 可再次触发
    clock.t += 61.0
    await agile.event_fire("z", {}, "z")
    assert len(events) == 2


@pytest.mark.asyncio
async def test_tpm_limit_persists(agile_env):
    """limit 持久化：set 写盘，unload→load / disable→enable / reload 均保留。"""
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "tpm", TPM_MODULE)
    assert (await runner.load_module("tpm"))["ok"]
    runner.set_tpm_limit("tpm", 7)
    assert (mods_dir / "tpm.limit").read_text(encoding="utf-8").strip() == "7"
    # unload → load 读回
    await runner.unload_module("tpm")
    assert (await runner.load_module("tpm"))["ok"]
    assert runner.AGILE_INSTANCES["tpm"]["tpm_limit"] == 7
    # disable → enable 读回
    await runner.disable_module("tpm")
    assert (await runner.enable_module("tpm"))["ok"]
    assert runner.AGILE_INSTANCES["tpm"]["tpm_limit"] == 7
    # reload 保留（preset 继承并写盘一致）
    runner.set_tpm_limit("tpm", 3)
    await runner.reload_module("tpm")
    assert runner.AGILE_INSTANCES["tpm"]["tpm_limit"] == 3
    assert (mods_dir / "tpm.limit").read_text(encoding="utf-8").strip() == "3"
    # 不限制（0）也持久化
    runner.set_tpm_limit("tpm", 0)
    assert (mods_dir / "tpm.limit").read_text(encoding="utf-8").strip() == "0"
    await runner.reload_module("tpm")
    assert runner.AGILE_INSTANCES["tpm"]["tpm_limit"] == 0


@pytest.mark.asyncio
async def test_tpm_limit_via_tool(agile_env):
    ctx, mods_dir = agile_env["ctx"], agile_env["mods_dir"]
    _write_module(mods_dir, "tpm", TPM_MODULE)
    import importlib.util
    spec = importlib.util.spec_from_file_location("agile_engine_main_tpm", str(AGILE_DIR / "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plugin = mod.get_plugin()
    await plugin.startup(ctx)
    tools = plugin.register_tools(ctx)
    op = tools[0].tool
    assert "已设为 10" in await op.ainvoke({"action": "limit", "name": "tpm", "value": "10"})
    assert "上限" in await op.ainvoke({"action": "limit", "name": "tpm", "value": "abc"})
    assert "触发限制: 10/min" in await op.ainvoke({"action": "status", "name": "tpm"})
    await plugin.plugin_unloaded(ctx)


# ── last_seen 活动打点 ──


@pytest.mark.asyncio
async def test_last_seen_initialized_on_load(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert (await runner.load_module("demo"))["ok"]
    assert runner.AGILE_INSTANCES["demo"]["last_seen"] > 0
    status = await agile_env["vfs"].read_text("/agile/demo/status")
    assert "上次活动" in status


@pytest.mark.asyncio
async def test_last_seen_updated_on_vfs_content_read_and_cache_hit(agile_env):
    """vfsContent 读取更新 last_seen，且缓存命中也算活动（用户触达模块即活动）。"""
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "cache", CACHE_MODULE)
    assert (await runner.load_module("cache"))["ok"]
    # 首次读取（执行内容函数）→ 打点
    assert await vfs.read_text("/cache/c") == "call-1"
    runner.AGILE_INSTANCES["cache"]["last_seen"] = 0.0
    # cache@0.5 内第二次读取命中缓存，仍算活动
    assert await vfs.read_text("/cache/c") == "call-1"
    assert runner.AGILE_INSTANCES["cache"]["last_seen"] > 0.0


@pytest.mark.asyncio
async def test_last_seen_updated_on_write_and_edit_handler(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "w", '''
from agile_base import AgileModule

module = AgileModule("w", "write test")

@module.vfsContentFunc("/w/h")
def state(_path):
    return "init"

@module.vfsWriteHook("/w/h")
def on_write(node, content):
    pass

@module.vfsEditHook("/w/h")
def on_edit(node, content):
    pass

def get_agile_module():
    return module
''')
    assert (await runner.load_module("w"))["ok"]
    runner.AGILE_INSTANCES["w"]["last_seen"] = 0.0
    await vfs.write("/w/h", "x")
    assert runner.AGILE_INSTANCES["w"]["last_seen"] > 0.0
    runner.AGILE_INSTANCES["w"]["last_seen"] = 0.0
    await vfs.edit("/w/h", "y")
    assert runner.AGILE_INSTANCES["w"]["last_seen"] > 0.0


@pytest.mark.asyncio
async def test_last_seen_updated_on_event_fire(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "ev", '''
from agile_base import AgileModule

module = AgileModule("ev", "event test")

def get_agile_module():
    return module
''')
    assert (await runner.load_module("ev"))["ok"]
    runner.AGILE_INSTANCES["ev"]["last_seen"] = 0.0
    agile = runner.AGILE_INSTANCES["ev"]["agile"]
    await agile.event_fire("probe", {}, "probe")
    assert runner.AGILE_INSTANCES["ev"]["last_seen"] > 0.0


@pytest.mark.asyncio
async def test_last_seen_not_updated_by_interval(agile_env):
    """interval 轮询不算活动——后台自转不能让模块永远新鲜。"""
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "tick", '''
from agile_base import AgileModule, AgileContext

module = AgileModule("tick", "interval test")

@module.registerInterval(1)
async def tick(agile: AgileContext):
    await agile.linfo("interval-fired")

def get_agile_module():
    return module
''')
    assert (await runner.load_module("tick"))["ok"]
    runner.AGILE_INSTANCES["tick"]["last_seen"] = 0.0
    time.sleep(1.5)
    logs = await runner.LM.getLog(agile_from="tick")
    assert any("interval-fired" in str(l.message) for l in logs)  # interval 确实跑了
    assert runner.AGILE_INSTANCES["tick"]["last_seen"] == 0.0  # 但没打点


@pytest.mark.asyncio
async def test_status_shows_idle_time(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert (await runner.load_module("demo"))["ok"]
    runner.AGILE_INSTANCES["demo"]["last_seen"] = time.time() - 7200  # 2 小时前
    status = await agile_env["vfs"].read_text("/agile/demo/status")
    assert "上次活动" in status
    assert "小时前" in status


# ── AgileStorage(模块级 KV 持久存储)──


STORAGE_MODULE = '''
from agile_base import AgileModule, AgileContext

module = AgileModule("sto", "storage test")

@module.vfsContentFunc("/sto/count", cacheStrategy="nocache")
def count(_path, agile: AgileContext):
    n = agile.storage.get("count", 0)
    agile.storage.set("count", n + 1)
    return f"count={n + 1}"

def get_agile_module():
    return module
'''


@pytest.mark.asyncio
async def test_storage_set_get_roundtrip_and_file(agile_env):
    """set/get 同步可用(异步 hook 内直接调用),落盘到 plugin_data/agile-engine/<name>.json。"""
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "sto", STORAGE_MODULE)
    assert (await runner.load_module("sto"))["ok"]
    assert await agile_env["vfs"].read_text("/sto/count") == "count=1"
    assert await agile_env["vfs"].read_text("/sto/count") == "count=2"
    f = mods_dir.parent / "plugin_data" / "agile-engine" / "sto.json"
    assert f.exists()
    import json
    assert json.loads(f.read_text(encoding="utf-8"))["count"] == 2
    # 默认值:键不存在返回 default
    agile = runner.AGILE_INSTANCES["sto"]["agile"]
    assert agile.storage.get("missing", "dft") == "dft"


@pytest.mark.asyncio
async def test_storage_persists_across_reload_and_unload(agile_env):
    """reload / unload→load 后数据仍在(文件持久,卸载不删除)。"""
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "sto", STORAGE_MODULE)
    assert (await runner.load_module("sto"))["ok"]
    assert await agile_env["vfs"].read_text("/sto/count") == "count=1"
    assert (await runner.reload_module("sto"))["ok"]
    assert await agile_env["vfs"].read_text("/sto/count") == "count=2"
    assert (await runner.unload_module("sto"))["ok"]
    f = mods_dir.parent / "plugin_data" / "agile-engine" / "sto.json"
    assert f.exists()
    assert (await runner.load_module("sto"))["ok"]
    assert await agile_env["vfs"].read_text("/sto/count") == "count=3"


@pytest.mark.asyncio
async def test_storage_isolated_between_modules(agile_env):
    """不同模块同名键互不干扰。"""
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "a", STORAGE_MODULE.replace('"sto"', '"a"').replace("/sto/count", "/a/count"))
    _write_module(mods_dir, "b", STORAGE_MODULE.replace('"sto"', '"b"').replace("/sto/count", "/b/count"))
    assert (await runner.load_module("a"))["ok"]
    assert (await runner.load_module("b"))["ok"]
    assert await agile_env["vfs"].read_text("/a/count") == "count=1"
    assert await agile_env["vfs"].read_text("/b/count") == "count=1"
    assert await agile_env["vfs"].read_text("/a/count") == "count=2"
    assert await agile_env["vfs"].read_text("/b/count") == "count=2"  # b 未被 a 影响


@pytest.mark.asyncio
async def test_storage_thread_safe_under_interval(agile_env):
    """interval 线程与主循环并发读写不丢数据(锁保护)。"""
    import threading
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "sto", STORAGE_MODULE)
    assert (await runner.load_module("sto"))["ok"]
    agile = runner.AGILE_INSTANCES["sto"]["agile"]
    errors: list[Exception] = []

    def hammer():
        try:
            for _ in range(200):
                with agile.storage:  # 跨 get/set 持锁,读改写原子化
                    n = agile.storage.get("count", 0)
                    agile.storage.set("count", n + 1)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert agile.storage.get("count") == 800  # 4 线程 × 200 次,无丢失


@pytest.mark.asyncio
async def test_communicate_handler_modules_and_logs(agile_env):
    """面板 communicate_handler：get_modules / get_module_logs 返回只读状态。"""
    import importlib.util

    main_path = AGILE_DIR / "main.py"
    spec = importlib.util.spec_from_file_location("agile_main_test", str(main_path))
    assert spec is not None and spec.loader is not None
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)

    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "sto", STORAGE_MODULE)
    assert (await runner.load_module("sto"))["ok"]
    # 写入一个存储键供面板读取
    agile = runner.AGILE_INSTANCES["sto"]["agile"]
    with agile.storage:
        agile.storage.set("greeting", "hi")

    plugin = main.Plugin()
    ctx = agile_env["ctx"]

    data = await plugin.communicate_handler({"action": "get_modules"}, ctx)
    assert data["status"] == "ok"
    item = next((m for m in data["items"] if m["name"] == "sto"), None)
    assert item is not None
    assert item["loaded"] is True
    assert item["status"] == "loaded"
    assert item["vfs_count"] == 1
    assert item["storage_keys"] == ["greeting"]
    assert item["log_count"] >= 1

    logs = await plugin.communicate_handler({"action": "get_module_logs", "name": "sto"}, ctx)
    assert logs["status"] == "ok"
    assert any("模块加载成功" in line for line in logs["logs"])

    # 未知 action → None（不拦截其它插件的消息）
    assert await plugin.communicate_handler({"action": "unknown_action"}, ctx) is None


PRIORITY_MODULE = '''
from agile_base import AgileModule, AgileContext

module = AgileModule("prio", "priority test")

@module.onloadHook()
async def boot(agile: AgileContext):
    await agile.event_fire("prio-alarm", {"x": 1}, priority="interrupt")
    await agile.event_fire("prio-soft", {"x": 2})

def get_agile_module():
    return module
'''


@pytest.mark.asyncio
async def test_event_fire_priority_passthrough(agile_env):
    """event_fire 的 priority 参数透传进 trigger payload；缺省为 normal。"""
    events, mods_dir = agile_env["events"], agile_env["mods_dir"]
    _write_module(mods_dir, "prio", PRIORITY_MODULE)
    assert (await runner.load_module("prio"))["ok"]
    prios = [e["priority"] for e in events if e["id"].startswith("agileEngine::prio")]
    assert prios == ["interrupt", "normal"]
