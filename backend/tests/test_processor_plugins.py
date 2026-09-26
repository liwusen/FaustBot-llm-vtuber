from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import psutil
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 仓库的插件加载器只实例化模块级 `Plugin`（或 `get_plugin()`，见
# plugin_system/manager.py `_create_plugin_instance`），所以下面两个场景
# 各自把对应实现暴露为 `Plugin`；坏插件用独立源，加载失败才会真实发生。
_PLUGIN_COMMON = '''
from __future__ import annotations

from faust_backend.plugin_system import PluginManifest, hookimpl
from faust_backend.plugin_system.plugin_base import FaustPlugin
from faust_backend.processors.base import Processor


class TmpPluginProcessor(Processor):
    NAME = "TMP_PLUGIN_ECHO"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx):
        pass

    def start(self, ctx):
        ctx.log("tmp plugin processor started")

    def invoke(self, ctx, data):
        return {"echo": data, "config": dict(ctx.config)}


class BrokenProcessor:
    """不是 Processor 子类，注册必须失败。"""
'''

PLUGIN_MAIN = _PLUGIN_COMMON + '''

class Plugin(FaustPlugin):
    manifest = PluginManifest(plugin_id="tmp_processor_plugin", name="Tmp", enabled=True)

    def register_processors(self, ctx):
        return [TmpPluginProcessor]
'''

BROKEN_PLUGIN_MAIN = _PLUGIN_COMMON + '''

class Plugin(FaustPlugin):
    manifest = PluginManifest(plugin_id="tmp_broken_plugin", name="Broken", enabled=True)

    def register_processors(self, ctx):
        return [BrokenProcessor]
'''

# 旧式插件形态（非 FaustPlugin 子类），避免 pluggy 注册的额外影响：
# register_processors 成功登记后再让 register_middlewares 抛错，模拟“注册之后才失败”。
BAD_MIDDLEWARE_PLUGIN_MAIN = _PLUGIN_COMMON + '''

class BadMiddlewareProcessor(Processor):
    NAME = "TMP_BADMW_ECHO"
    SETUP_TIMEOUT = 20.0
    START_TIMEOUT = 20.0

    def setup(self, ctx):
        pass

    def start(self, ctx):
        pass

    def invoke(self, ctx, data):
        return data


class Plugin:
    def register_processors(self, ctx):
        return [BadMiddlewareProcessor]

    def register_middlewares(self, ctx):
        raise RuntimeError("middleware 注册失败")
'''

PLUGIN_JSON = {"id": "tmp_processor_plugin", "name": "Tmp", "entry": "main.py", "enabled": True}


def _write_plugin(root: Path, plugin_id: str, source: str) -> Path:
    plugin_dir = root / plugin_id
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "main.py").write_text(source, encoding="utf-8")
    (plugin_dir / "plugin.json").write_text(
        json.dumps({**PLUGIN_JSON, "id": plugin_id}), encoding="utf-8"
    )
    return plugin_dir


@pytest.mark.asyncio
async def test_plugin_processor_can_be_required_and_invoked(tmp_path):
    from faust_backend.plugin_system import PluginManager
    from faust_backend.processors import get_processor_manager
    from faust_backend.processors.errors import ProcessorNotFoundError
    from faust_backend.processors.registry import get_processor

    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "tmp_processor_plugin", PLUGIN_MAIN)

    manager = PluginManager(plugins_dir=plugins_dir, state_file=str(tmp_path / "state.json"))
    summary = await manager.reload(force=True)
    assert summary["errors"] == []

    processor_manager = get_processor_manager()
    lease = await processor_manager.startRequire("TMP_PLUGIN_ECHO", requirer="t1")
    await lease.wait_until_ready(timeout=30)
    assert await lease.invoke({"n": 1}) == {"echo": {"n": 1}, "config": {}}
    assert processor_manager.handle("TMP_PLUGIN_ECHO").entry.owner == "tmp_processor_plugin"
    pid = processor_manager.handle("TMP_PLUGIN_ECHO").pid
    lease.release()

    # 卸载：停止子进程并摘除注册，同名随后可再次注册
    removed = await processor_manager.unregister_owner("tmp_processor_plugin")
    assert removed == ["TMP_PLUGIN_ECHO"]
    await asyncio.sleep(0.2)
    assert not psutil.pid_exists(int(pid))
    with pytest.raises(ProcessorNotFoundError):
        get_processor("TMP_PLUGIN_ECHO")

    await manager.reload(force=True)
    lease = await processor_manager.startRequire("TMP_PLUGIN_ECHO", requirer="t1")
    await lease.wait_until_ready(timeout=30)
    lease.release()
    await processor_manager.stop("TMP_PLUGIN_ECHO")


@pytest.mark.asyncio
async def test_broken_plugin_processor_marks_plugin_load_failed(tmp_path):
    from faust_backend.plugin_system import PluginManager

    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "tmp_broken_plugin", BROKEN_PLUGIN_MAIN)

    manager = PluginManager(plugins_dir=plugins_dir, state_file=str(tmp_path / "state.json"))
    summary = await manager.reload(force=True)

    assert [item["plugin"] for item in summary["errors"]] == ["tmp_broken_plugin"]
    assert "Processor" in summary["errors"][0]["error"]


@pytest.mark.asyncio
async def test_plugin_load_failure_rolls_back_registered_processors(tmp_path):
    """Processor 注册后插件才加载失败：必须回滚，否则名字被永久占住。"""
    from faust_backend.plugin_system import PluginManager
    from faust_backend.processors.errors import ProcessorNotFoundError
    from faust_backend.processors.registry import get_processor

    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "tmp_badmw_plugin", BAD_MIDDLEWARE_PLUGIN_MAIN)

    manager = PluginManager(plugins_dir=plugins_dir, state_file=str(tmp_path / "state.json"))
    summary = await manager.reload(force=True)

    assert [item["plugin"] for item in summary["errors"]] == ["tmp_badmw_plugin"]
    assert "middleware 注册失败" in summary["errors"][0]["error"]
    with pytest.raises(ProcessorNotFoundError):
        get_processor("TMP_BADMW_ECHO")          # 加载失败 ⇒ 注册已回滚

    # 再 reload：注册没回滚的话这里会因名字冲突而提前失败
    second = await manager.reload(force=True)
    assert [item["plugin"] for item in second["errors"]] == ["tmp_badmw_plugin"]
    assert "名字冲突" not in second["errors"][0]["error"]
    assert "middleware 注册失败" in second["errors"][0]["error"]


@pytest.mark.asyncio
async def test_register_plugin_processors_rolls_back_on_conflict(tmp_path):
    from faust_backend.processors import get_processor_manager
    from faust_backend.processors.base import Processor
    from faust_backend.processors.errors import ProcessorError
    from faust_backend.processors.registry import get_processor

    class Left(Processor):
        NAME = "TMP_ROLLBACK_LEFT"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    class Right(Processor):
        NAME = "TMP_ROLLBACK_RIGHT"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    processor_manager = get_processor_manager()
    assert processor_manager.register_plugin_processors("tmp_owner", [Left, Right]) == [
        "TMP_ROLLBACK_LEFT",
        "TMP_ROLLBACK_RIGHT",
    ]

    class Clash(Processor):
        NAME = "TMP_ROLLBACK_LEFT"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    class New(Processor):
        NAME = "TMP_ROLLBACK_NEW"

        def start(self, ctx):
            pass

        def invoke(self, ctx, data):
            return data

    with pytest.raises(ProcessorError):
        processor_manager.register_plugin_processors("tmp_owner_2", [New, Clash])
    from faust_backend.processors.errors import ProcessorNotFoundError

    with pytest.raises(ProcessorNotFoundError):
        get_processor("TMP_ROLLBACK_NEW")          # 回滚：本次注册的都没留下
    assert get_processor("TMP_ROLLBACK_LEFT").name == "TMP_ROLLBACK_LEFT"

    await processor_manager.unregister_owner("tmp_owner")
