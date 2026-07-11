from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from faust_backend.mcp_manager import (  # noqa: E402
    McpManager,
    McpServerHandle,
    _find_nodejs,
    _replace_bundled_command,
    _strip_none_values,
)


def test_replace_bundled_command_maps_node_and_npx(tmp_path):
    node_path = str(tmp_path / "node.exe")
    assert _replace_bundled_command("node", node_path) == node_path
    assert _replace_bundled_command("node.exe", node_path) == node_path
    assert _replace_bundled_command("npx", node_path) == str(tmp_path / "npx.cmd")
    assert _replace_bundled_command("custom.cmd", node_path) == "custom.cmd"


def test_find_nodejs_prefers_env(monkeypatch, tmp_path):
    node = tmp_path / "node.exe"
    node.write_text("stub", encoding="utf-8")
    monkeypatch.setenv("FAUST_NODEJS", str(node))
    assert _find_nodejs() == str(node)


def test_manager_list_status_and_tool_cache():
    mgr = McpManager()
    mgr.load_config({
        "playwright": {
            "enabled": True,
            "description": "pw",
            "custom": False,
            "transport": "stdio",
            "args": ["--browser-channel=msedge"],
        }
    })
    handle = McpServerHandle(server_id="playwright", config={}, transport="stdio")
    handle.initialized = True
    handle.session = object()
    handle.tool_specs = [{
        "name": "navigate",
        "title": "Navigate",
        "description": "Open a URL",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string", "description": "target url"}},
            "required": ["url"],
        },
    }]
    mgr._servers["playwright"] = handle
    mgr._rebuild_tool_cache()

    statuses = mgr.list_server_statuses(include_log=False)
    assert len(statuses) == 1
    assert statuses[0]["server_id"] == "playwright"
    assert statuses[0]["status"] == "running"
    assert statuses[0]["tool_count"] == 1

    tools = mgr.get_langchain_tools()
    assert len(tools) == 1
    assert getattr(tools[0], "name", "") == "playwright_navigate"
    assert "Args:" in getattr(tools[0], "description", "")
    assert "url: target url" in getattr(tools[0], "description", "")


def test_build_tool_description_with_args():
    args_schema = type("FakeArgs", (), {
        "model_fields": {
            "url": type("FieldInfo", (), {"description": "target url"})(),
            "waitUntil": type("FieldInfo", (), {"description": "load event mode"})(),
        }
    })
    desc = McpManager.build_tool_description("Open a URL", args_schema)
    assert desc == "Open a URL\nArgs:\nurl: target url\nwaitUntil: load event mode"


def test_manager_status_keeps_headers():
    mgr = McpManager()
    mgr.load_config({
        "remote": {
            "enabled": True,
            "transport": "streamable-http",
            "url": "https://example.com/mcp",
            "headers": {"Authorization": "Bearer token"},
        }
    })
    status = mgr.get_server_status("remote")
    assert status["transport"] == "streamable-http"
    assert status["headers"] == {"Authorization": "Bearer token"}


def test_strip_none_values_removes_only_none():
    payload = {
        "element": None,
        "target": None,
        "filename": None,
        "fullPage": None,
        "type": "png",
        "scale": "css",
        "flags": {"keep": False, "drop": None},
    }
    assert _strip_none_values(payload) == {
        "type": "png",
        "scale": "css",
        "flags": {"keep": False},
    }


@pytest.mark.asyncio
async def test_call_tool_omits_none_arguments():
    mgr = McpManager()
    session = AsyncMock()
    session.call_tool.return_value = type("FakeResult", (), {"content": [], "structuredContent": None, "isError": False})()
    handle = McpServerHandle(server_id="playwright", config={}, transport="stdio")
    handle.initialized = True
    handle.session = session
    mgr._servers["playwright"] = handle

    await mgr.call_tool("playwright", "browser_take_screenshot", {
        "type": "png",
        "scale": "css",
        "element": None,
        "target": None,
        "filename": None,
        "fullPage": None,
    })

    session.call_tool.assert_awaited_once_with("browser_take_screenshot", arguments={
        "type": "png",
        "scale": "css",
    })


@pytest.mark.asyncio
async def test_stop_server_clears_handle_tools():
    mgr = McpManager()
    mgr.load_config({"demo": {"enabled": False}})
    handle = McpServerHandle(server_id="demo", config={}, transport="stdio")
    handle.initialized = True
    handle.session = None
    handle.transport_ctx = None
    handle.tool_specs = [{"name": "x", "description": "y", "inputSchema": None}]
    mgr._servers["demo"] = handle

    result = await mgr.stop_server("demo")
    assert result["status"] == "stopped"
    assert handle.tool_specs == []