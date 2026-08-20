from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
AGILE_DIR = Path(__file__).resolve().parents[1] / "default_plugins" / "agile-engine"
sys.path.insert(0, str(AGILE_DIR))

from faust_backend.plugin_system import PluginContext  # noqa: E402
from faust_backend.tools.vfs import get_faustbot_vfs, run_coro_sync  # noqa: E402
import runner  # noqa: E402


def _make_ctx(vfs, events):
    return PluginContext(plugin_id="agile-engine", plugin_dir=AGILE_DIR, config={
        "trigger_create": lambda payload: (events.append(payload), payload)[1],
        "vfs_read_text": lambda path, default="": run_coro_sync(vfs.read_text(path, default=default)),
        "vfs_write": lambda path, content: run_coro_sync(vfs.write(path, content)),
        "vfs_write_symbolic": lambda path, func, should_be_included_in_search=True, writable=False: run_coro_sync(
            vfs.write_symbolic(path, func, should_be_included_in_search=should_be_included_in_search, writable=writable)),
        "vfs_set_write_handler": lambda path, func: run_coro_sync(vfs.set_write_handler(path, func)),
        "vfs_set_edit_handler": lambda path, func: run_coro_sync(vfs.set_edit_handler(path, func)),
        "vfs_delete": lambda path: run_coro_sync(vfs.delete(path)),
        "vfs_list": lambda path="/": run_coro_sync(vfs.list_dir(path)),
    })


@pytest.fixture(autouse=True)
def agile_env(tmp_path):
    vfs = get_faustbot_vfs()
    events: list[dict] = []
    ctx = _make_ctx(vfs, events)
    mods_dir = tmp_path / "agile-modules"
    runner.configure(ctx, modules_dir=mods_dir)
    runner.register_overview_node()
    yield {"ctx": ctx, "vfs": vfs, "mods_dir": mods_dir, "events": events}
    for name in list(runner.AGILE_INSTANCES.keys()):
        try:
            runner.unload_module(name)
        except Exception:
            pass
    run_coro_sync(vfs.delete("/agile"))


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


def test_load_and_content_node(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    result = runner.load_module("demo")
    assert result["ok"], result
    assert run_coro_sync(vfs.read_text("/demo/value")) == "hello-/demo/value"
    # 框架镜像节点
    status = run_coro_sync(vfs.read_text("/agile/demo/status"))
    assert "loaded" in status
    overview = run_coro_sync(vfs.read_text("/agile/status"))
    assert "demo" in overview


def test_load_twice_requires_reload(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert runner.load_module("demo")["ok"]
    result = runner.load_module("demo")
    assert not result["ok"]
    assert "reload" in result["message"]


def test_write_handler_and_edit_handler(agile_env):
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
    assert runner.load_module("w")["ok"]
    run_coro_sync(vfs.write("/w/state", "new-value"))
    assert run_coro_sync(vfs.read_text("/w/last")) == "last=new-value"
    run_coro_sync(vfs.edit("/w/state", "edited-value"))
    assert run_coro_sync(vfs.read_text("/w/last")) == "last=edited-value"


def test_event_fire_single_dict(agile_env):
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
    assert runner.load_module("ev")["ok"]
    assert len(events) == 1
    payload = events[0]
    assert payload["id"] == "agileEngine::booted"
    assert payload["type"] == "event"
    assert payload["payload"] == {"x": 1}
    assert payload["recall_description"] == "模块已加载"


def test_unload_reversible(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert runner.load_module("demo")["ok"]
    assert run_coro_sync(vfs.get_node("/demo/value")) is not None
    assert run_coro_sync(vfs.get_node("/agile/demo/status")) is not None
    assert "agile_module_demo" in sys.modules
    assert runner.unload_module("demo")["ok"]
    # 模块节点与镜像节点全部删除
    assert run_coro_sync(vfs.get_node("/demo/value")) is None
    assert run_coro_sync(vfs.get_node("/agile/demo/status")) is None
    assert run_coro_sync(vfs.get_node("/agile/modules/demo.py")) is None
    assert "agile_module_demo" not in sys.modules
    # 未加载时卸载返回提示
    result = runner.unload_module("demo")
    assert not result["ok"]


def test_interval_task_runs_and_stops(agile_env):
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
    assert runner.load_module("tick")["ok"]
    handle = runner.AGILE_INSTANCES["tick"]["interval_handles"][0]
    time.sleep(1.5)
    logs = run_coro_sync(runner.LM.getLog(agile_from="tick"))
    assert any("interval-fired" in str(l.message) for l in logs)
    assert runner.unload_module("tick")["ok"]
    assert not handle.thread.is_alive()


def test_error_module_isolated(agile_env):
    mods_dir = agile_env["mods_dir"]
    (mods_dir / "bad.py").write_text("this is not python {{{", encoding="utf-8")
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    result = runner.load_module("bad")
    assert not result["ok"]
    assert runner.AGILE_INSTANCES["bad"]["status"] == "error"
    # 好模块不受影响
    assert runner.load_module("demo")["ok"]
    assert run_coro_sync(agile_env["vfs"].read_text("/demo/value")) == "hello-/demo/value"


def test_disable_enable_roundtrip(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert runner.load_module("demo")["ok"]
    result = runner.disable_module("demo")
    assert result["ok"]
    assert (mods_dir / "demo.py.disabled").exists()
    assert not (mods_dir / "demo.py").exists()
    assert "demo" not in runner.AGILE_INSTANCES
    # 禁用后不再自动加载（startup 只加载 .py）
    result = runner.enable_module("demo")
    assert result["ok"]
    assert (mods_dir / "demo.py").exists()
    assert "demo" in runner.AGILE_INSTANCES


def test_agile_operate_tool(agile_env):
    ctx, mods_dir = agile_env["ctx"], agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    # 按文件路径加载插件 main（避免与 backend/main.py 的 sys.modules["main"] 冲突）
    import importlib.util
    spec = importlib.util.spec_from_file_location("agile_engine_main_under_test", str(AGILE_DIR / "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plugin = mod.get_plugin()
    plugin.startup(ctx)
    tools = plugin.register_tools(ctx)
    names = [t.name for t in tools]
    assert "agileOperate" in names
    op = tools[0].tool
    assert "demo" in op.invoke({"action": "list", "name": ""})
    # startup 已自动加载 demo，load 提示已加载（含 reload 指引）
    assert "reload" in op.invoke({"action": "load", "name": "demo"})
    assert "loaded" in op.invoke({"action": "status", "name": "demo"})
    assert "已卸载" in op.invoke({"action": "unload", "name": "demo"})
    assert "未知操作" in op.invoke({"action": "bogus", "name": "demo"})
    plugin.plugin_unloaded(ctx)


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


def test_cache_strategy(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "cache", CACHE_MODULE)
    assert runner.load_module("cache")["ok"]
    # cache@0.5: 连续读命中缓存
    assert run_coro_sync(vfs.read_text("/cache/c")) == "call-1"
    assert run_coro_sync(vfs.read_text("/cache/c")) == "call-1"
    time.sleep(0.6)
    assert run_coro_sync(vfs.read_text("/cache/c")) == "call-2"
    # nocache: 每次执行
    assert run_coro_sync(vfs.read_text("/cache/n")) == "call-3"
    assert run_coro_sync(vfs.read_text("/cache/n")) == "call-4"
    # wait@0.5: 连续读第二次等待后返回新值
    t0 = time.monotonic()
    run_coro_sync(vfs.read_text("/cache/w"))
    run_coro_sync(vfs.read_text("/cache/w"))
    elapsed = time.monotonic() - t0
    assert elapsed >= 0.4, elapsed
    # error@0.5: 连续读第二次返回错误文本
    run_coro_sync(vfs.read_text("/cache/e"))
    err = run_coro_sync(vfs.read_text("/cache/e"))
    assert "读取过于频繁" in err
    time.sleep(0.6)
    assert run_coro_sync(vfs.read_text("/cache/e")) == "call-8"


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


def test_async_hook_and_di(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "di", DI_MODULE)
    assert runner.load_module("di")["ok"]
    assert run_coro_sync(vfs.read_text("/di/plain")) == "p:/di/plain"
    assert run_coro_sync(vfs.read_text("/di/with_agile")) == "w:/di/with_agile:di"
    assert run_coro_sync(vfs.read_text("/di/only_agile")) == "o:di"


def test_reload_after_edit(agile_env):
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert runner.load_module("demo")["ok"]
    assert run_coro_sync(vfs.read_text("/demo/value")) == "hello-/demo/value"
    _write_module(mods_dir, "demo", SIMPLE_MODULE.replace("hello-", "v2-"))
    assert runner.reload_module("demo")["ok"]
    assert run_coro_sync(vfs.read_text("/demo/value")) == "v2-/demo/value"


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


def test_tpm_blocks_excess_triggers(agile_env):
    """默认 60/min 不限；调低到 2 后第 3 次 event_fire 报错，只有前 2 次进入 trigger 系统。"""
    events, mods_dir = agile_env["events"], agile_env["mods_dir"]
    _write_module(mods_dir, "tpm", TPM_MODULE)
    assert runner.load_module("tpm")["ok"]
    assert len(events) == 3
    # 调低限制并重载（重载会重新触发 onload 的 3 次 event_fire）
    assert runner.set_tpm_limit("tpm", 2)["ok"]
    runner.reload_module("tpm")
    # 窗口内：第 1 次 ok（窗口 1/2），第 2 次 ok（2/2），第 3 次超限被隔离捕获
    assert len(events) == 3 + 2
    errors = run_coro_sync(runner.LM.getLog(agile_from="tpm", level="ERROR"))
    assert any("超过每分钟上限 2 次" in str(e.message) for e in errors)
    status = run_coro_sync(agile_env["vfs"].read_text("/agile/tpm/status"))
    assert "触发限制: 2/min" in status


def test_tpm_limit_settable_and_unlimited(agile_env):
    events, mods_dir = agile_env["events"], agile_env["mods_dir"]
    _write_module(mods_dir, "tpm", TPM_MODULE)
    assert runner.load_module("tpm")["ok"]
    # 不限制（0）→ 3 次全放行
    assert runner.set_tpm_limit("tpm", 0)["ok"]
    runner.reload_module("tpm")
    assert len(events) == 6
    # 负数也视为不限制
    assert runner.set_tpm_limit("tpm", -5)["ok"]
    runner.reload_module("tpm")
    assert len(events) == 9


def test_tpm_window_slides(agile_env, monkeypatch):
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
    assert runner.load_module("tpm")["ok"]
    assert runner.set_tpm_limit("tpm", 1)["ok"]
    assert len(events) == 0
    agile = runner.AGILE_INSTANCES["tpm"]["agile"]
    # 触发一次（窗口 1/1）→ 再触发报错
    run_coro_sync(agile.event_fire("x", {}, "x"))
    assert len(events) == 1
    try:
        run_coro_sync(agile.event_fire("y", {}, "y"))
        raise AssertionError("应超限报错")
    except RuntimeError as exc:
        assert "超过每分钟上限 1 次" in str(exc)
    # 时钟推进 61 秒 → 窗口清空 → 可再次触发
    clock.t += 61.0
    run_coro_sync(agile.event_fire("z", {}, "z"))
    assert len(events) == 2


def test_tpm_limit_persists(agile_env):
    """limit 持久化：set 写盘，unload→load / disable→enable / reload 均保留。"""
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "tpm", TPM_MODULE)
    assert runner.load_module("tpm")["ok"]
    runner.set_tpm_limit("tpm", 7)
    assert (mods_dir / "tpm.limit").read_text(encoding="utf-8").strip() == "7"
    # unload → load 读回
    runner.unload_module("tpm")
    assert runner.load_module("tpm")["ok"]
    assert runner.AGILE_INSTANCES["tpm"]["tpm_limit"] == 7
    # disable → enable 读回
    runner.disable_module("tpm")
    assert runner.enable_module("tpm")["ok"]
    assert runner.AGILE_INSTANCES["tpm"]["tpm_limit"] == 7
    # reload 保留（preset 继承并写盘一致）
    runner.set_tpm_limit("tpm", 3)
    runner.reload_module("tpm")
    assert runner.AGILE_INSTANCES["tpm"]["tpm_limit"] == 3
    assert (mods_dir / "tpm.limit").read_text(encoding="utf-8").strip() == "3"
    # 不限制（0）也持久化
    runner.set_tpm_limit("tpm", 0)
    assert (mods_dir / "tpm.limit").read_text(encoding="utf-8").strip() == "0"
    runner.reload_module("tpm")
    assert runner.AGILE_INSTANCES["tpm"]["tpm_limit"] == 0


def test_tpm_limit_via_tool(agile_env):
    ctx, mods_dir = agile_env["ctx"], agile_env["mods_dir"]
    _write_module(mods_dir, "tpm", TPM_MODULE)
    import importlib.util
    spec = importlib.util.spec_from_file_location("agile_engine_main_tpm", str(AGILE_DIR / "main.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    plugin = mod.get_plugin()
    plugin.startup(ctx)
    tools = plugin.register_tools(ctx)
    op = tools[0].tool
    assert "已设为 10" in op.invoke({"action": "limit", "name": "tpm", "value": "10"})
    assert "上限" in op.invoke({"action": "limit", "name": "tpm", "value": "abc"})
    assert "触发限制: 10/min" in op.invoke({"action": "status", "name": "tpm"})
    plugin.plugin_unloaded(ctx)


# ── last_seen 活动打点 ──


def test_last_seen_initialized_on_load(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert runner.load_module("demo")["ok"]
    assert runner.AGILE_INSTANCES["demo"]["last_seen"] > 0
    status = run_coro_sync(agile_env["vfs"].read_text("/agile/demo/status"))
    assert "上次活动" in status


def test_last_seen_updated_on_vfs_content_read_and_cache_hit(agile_env):
    """vfsContent 读取更新 last_seen，且缓存命中也算活动（用户触达模块即活动）。"""
    vfs, mods_dir = agile_env["vfs"], agile_env["mods_dir"]
    _write_module(mods_dir, "cache", CACHE_MODULE)
    assert runner.load_module("cache")["ok"]
    # 首次读取（执行内容函数）→ 打点
    assert run_coro_sync(vfs.read_text("/cache/c")) == "call-1"
    runner.AGILE_INSTANCES["cache"]["last_seen"] = 0.0
    # cache@0.5 内第二次读取命中缓存，仍算活动
    assert run_coro_sync(vfs.read_text("/cache/c")) == "call-1"
    assert runner.AGILE_INSTANCES["cache"]["last_seen"] > 0.0


def test_last_seen_updated_on_write_and_edit_handler(agile_env):
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
    assert runner.load_module("w")["ok"]
    runner.AGILE_INSTANCES["w"]["last_seen"] = 0.0
    run_coro_sync(vfs.write("/w/h", "x"))
    assert runner.AGILE_INSTANCES["w"]["last_seen"] > 0.0
    runner.AGILE_INSTANCES["w"]["last_seen"] = 0.0
    run_coro_sync(vfs.edit("/w/h", "y"))
    assert runner.AGILE_INSTANCES["w"]["last_seen"] > 0.0


def test_last_seen_updated_on_event_fire(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "ev", '''
from agile_base import AgileModule

module = AgileModule("ev", "event test")

def get_agile_module():
    return module
''')
    assert runner.load_module("ev")["ok"]
    runner.AGILE_INSTANCES["ev"]["last_seen"] = 0.0
    agile = runner.AGILE_INSTANCES["ev"]["agile"]
    run_coro_sync(agile.event_fire("probe", {}, "probe"))
    assert runner.AGILE_INSTANCES["ev"]["last_seen"] > 0.0


def test_last_seen_not_updated_by_interval(agile_env):
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
    assert runner.load_module("tick")["ok"]
    runner.AGILE_INSTANCES["tick"]["last_seen"] = 0.0
    time.sleep(1.5)
    logs = run_coro_sync(runner.LM.getLog(agile_from="tick"))
    assert any("interval-fired" in str(l.message) for l in logs)  # interval 确实跑了
    assert runner.AGILE_INSTANCES["tick"]["last_seen"] == 0.0  # 但没打点


def test_status_shows_idle_time(agile_env):
    mods_dir = agile_env["mods_dir"]
    _write_module(mods_dir, "demo", SIMPLE_MODULE)
    assert runner.load_module("demo")["ok"]
    runner.AGILE_INSTANCES["demo"]["last_seen"] = time.time() - 7200  # 2 小时前
    status = run_coro_sync(agile_env["vfs"].read_text("/agile/demo/status"))
    assert "上次活动" in status
    assert "小时前" in status
