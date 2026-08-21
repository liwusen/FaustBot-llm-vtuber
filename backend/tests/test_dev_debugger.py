from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BACKEND = Path(__file__).resolve().parents[1]
IMPL = BACKEND / "default_plugins" / "dev-debugger" / "impl.py"


def _load_plugin_cls():
    spec = importlib.util.spec_from_file_location("devdbg_impl_test", str(IMPL))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.Plugin


@pytest.fixture
def plugin():
    cls = _load_plugin_cls()
    return cls()


class _Ctx:
    pass


def test_register_frontend_serves_app_hook(plugin):
    assets = plugin.register_frontend()
    assert any(
        a.get("type") == "js"
        and a.get("path", "").endswith("/faust/plugins/dev-debugger/frontend/app-hook.js")
        for a in assets
    )


def test_schema_parses_bool_and_types(plugin, monkeypatch):
    """用户例：testTool(uri: str, test: bool) → 前端应得到两个字段及正确类型。"""
    from langchain.tools import tool
    from faust_backend.tools import _registry as registry

    @tool
    def testTool(uri: str, test: bool = False) -> str:
        """测试工具：返回 uri 与 test 的序列化。"""
        return f"{uri}:{test}"

    registered_name = getattr(testTool, "name") or "testTool"
    # 临时注册以便 list_tools 能看到
    monkeypatch.setattr(registry, "toollist", list(registry.toollist) + [testTool])
    monkeypatch.setattr(
        registry,
        "ORIGINAL_TOOL_FUNCS",
        {**registry.ORIGINAL_TOOL_FUNCS, registered_name: testTool},
    )
    r = asyncio.run(plugin.communicate_handler({"action": "list_tools"}, _Ctx()))
    assert r["status"] == "ok"
    tools = {t["name"]: t for t in r["tools"]}
    assert registered_name in tools
    fields = {f["name"]: f for f in tools[registered_name]["schema"]}
    assert set(fields) == {"uri", "test"}
    # uri: str 必填
    assert fields["uri"]["type"] == "str"
    assert fields["uri"]["required"] is True
    # test: bool 可选、默认 False
    assert fields["test"]["type"] == "bool"
    assert fields["test"]["required"] is False
    assert fields["test"]["default"] is False


def test_invoke_tool_returns_result(plugin):
    r = asyncio.run(plugin.communicate_handler(
        {"action": "invoke_tool", "name": "read", "args": {"uri": "surely_nonexistent_zz"}},
        _Ctx(),
    ))
    assert r["status"] == "ok"
    assert isinstance(r["result"], str)


def test_invoke_unknown_tool_errors(plugin):
    r = asyncio.run(plugin.communicate_handler(
        {"action": "invoke_tool", "name": "no_such_tool", "args": {}}, _Ctx()
    ))
    assert r["status"] == "error"


def test_unknown_action_errors(plugin):
    r = asyncio.run(plugin.communicate_handler({"action": "bogus"}, _Ctx()))
    assert r["status"] == "error"


def test_invoke_inside_running_loop(plugin, monkeypatch):
    """回归：在运行中的事件循环里 invoke 一个内部用 asyncio.run 的工具不应报错。

    触发场景：invoke_tool 在 running loop（如路由上下文）里调用，
    同步工具内部 asyncio.run 会冲突。修复后走 LangChain ainvoke 的
    线程池路径，与 LLM 路径一致。
    """
    from langchain.tools import tool
    from faust_backend.tools import _registry as registry

    @tool
    def loopSensitiveTool(content: str) -> str:
        """内部用 asyncio.run 的同步工具（模拟 writeDiaryFileTool）。"""
        import asyncio as _a
        async def _f():
            return "inner: " + content
        return _a.run(_f())

    registered_name = getattr(loopSensitiveTool, "name") or "loopSensitiveTool"
    monkeypatch.setattr(registry, "toollist", list(registry.toollist) + [loopSensitiveTool])
    monkeypatch.setattr(
        registry,
        "ORIGINAL_TOOL_FUNCS",
        {**registry.ORIGINAL_TOOL_FUNCS, registered_name: loopSensitiveTool},
    )

    async def _async_wrapper():
        # 在 running loop 中 await communicate_handler（模拟路由上下文）
        return await plugin.communicate_handler(
            {"action": "invoke_tool", "name": registered_name, "args": {"content": "hi"}},
            _Ctx(),
        )

    r = asyncio.run(_async_wrapper())
    assert r["status"] == "ok"
    assert r["result"] == "inner: hi"
    assert "running event loop" not in r.get("result", "")
