"""VFS write/edit handler 返回值（ACK）契约测试。

覆盖 C-1（VFS 透传 handler 返回值）、C-2（write 工具追加 `↳` ACK）、
C-3（edit 工具改走 vfs.edit，edit_handler 不再是死代码）。

契约：handler 返回**非空 str** → 作为 ACK 返回；None / 空串 / 其它类型 → 归一为 None。

Run: .runtime/python.exe -m pytest backend/tests/test_vfs_handler_ack.py -v
"""

from __future__ import annotations

import os
import sys

import pytest
import pytest_asyncio

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

WRITE_NODE = "/ack-test-write"
EDIT_NODE = "/ack-test-edit"
TOOL_NODE = "/ack-test-tool"


@pytest_asyncio.fixture
async def vfs():
    from faust_backend.tools.vfs import get_faustbot_vfs

    v = await get_faustbot_vfs()
    yield v
    for path in (WRITE_NODE, EDIT_NODE, TOOL_NODE):
        await v.delete(path)


# ============================================================
# C-1：vfs.write / vfs.edit 返回值契约
# ============================================================

class TestHandlerReturnContract:
    @pytest.mark.asyncio
    async def test_write_returns_handler_str(self, vfs):
        await vfs.write_symbolic(WRITE_NODE, lambda _p: "", writable=True)
        await vfs.set_write_handler(WRITE_NODE, lambda _node, _content: "已提交 task_1")

        assert await vfs.write(WRITE_NODE, "payload") == "已提交 task_1"

    @pytest.mark.asyncio
    async def test_write_async_handler_str(self, vfs):
        await vfs.write_symbolic(WRITE_NODE, lambda _p: "", writable=True)

        async def handler(_node, _content):
            return "async-ack"

        await vfs.set_write_handler(WRITE_NODE, handler)
        assert await vfs.write(WRITE_NODE, "payload") == "async-ack"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("returned", [None, "", {"ok": True}, ["a"], 3])
    async def test_write_non_str_or_empty_returns_none(self, vfs, returned):
        await vfs.write_symbolic(WRITE_NODE, lambda _p: "", writable=True)
        await vfs.set_write_handler(WRITE_NODE, lambda _node, _content, r=returned: r)

        assert await vfs.write(WRITE_NODE, "payload") is None

    @pytest.mark.asyncio
    async def test_write_without_handler_returns_none(self, vfs):
        await vfs.write("/ack-test-plain", "content")
        try:
            assert await vfs.write("/ack-test-plain", "content2") is None
        finally:
            await vfs.delete("/ack-test-plain")

    @pytest.mark.asyncio
    async def test_edit_returns_handler_str(self, vfs):
        await vfs.write_symbolic(EDIT_NODE, lambda _p: "origin", writable=True)
        await vfs.set_edit_handler(EDIT_NODE, lambda _node, content: f"已采纳: {content}")

        assert await vfs.edit(EDIT_NODE, "new") == "已采纳: new"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("returned", [None, "", {"ok": True}])
    async def test_edit_non_str_or_empty_returns_none(self, vfs, returned):
        await vfs.write_symbolic(EDIT_NODE, lambda _p: "origin", writable=True)
        await vfs.set_edit_handler(EDIT_NODE, lambda _node, _content, r=returned: r)

        assert await vfs.edit(EDIT_NODE, "new") is None

    @pytest.mark.asyncio
    async def test_edit_without_handler_returns_none(self, vfs):
        await vfs.write("/ack-test-plain-edit", "content")
        try:
            assert await vfs.edit("/ack-test-plain-edit", "content2") is None
        finally:
            await vfs.delete("/ack-test-plain-edit")


# ============================================================
# C-2：write 工具呈现 `↳ {msg}`
# ============================================================

class TestWriteToolAck:
    @pytest.mark.asyncio
    async def test_write_tool_appends_ack(self, vfs):
        from faust_backend.tools.write import write

        await vfs.write_symbolic(TOOL_NODE, lambda _p: "", writable=True)
        await vfs.set_write_handler(TOOL_NODE, lambda _node, _c: "已提交 task_7（队列 1/5）")

        result = await write.ainvoke({"path": "faustbot://" + TOOL_NODE.lstrip("/"), "content": "hi"})

        assert "已写入 faustbot://" in result
        assert "↳ 已提交 task_7（队列 1/5）" in result

    @pytest.mark.asyncio
    async def test_write_tool_multiline_ack_keeps_lines(self, vfs):
        from faust_backend.tools.write import write

        await vfs.write_symbolic(TOOL_NODE, lambda _p: "", writable=True)
        await vfs.set_write_handler(TOOL_NODE, lambda _node, _c: "line1\nline2")

        result = await write.ainvoke({"path": "faustbot://" + TOOL_NODE.lstrip("/"), "content": "hi"})

        assert "↳ line1\nline2" in result

    @pytest.mark.asyncio
    async def test_write_tool_without_ack(self, vfs):
        from faust_backend.tools.write import write

        await vfs.write_symbolic(TOOL_NODE, lambda _p: "", writable=True)
        await vfs.set_write_handler(TOOL_NODE, lambda _node, _c: {"ignored": True})

        result = await write.ainvoke({"path": "faustbot://" + TOOL_NODE.lstrip("/"), "content": "hi"})

        assert "已写入 faustbot://" in result
        assert "↳" not in result

    @pytest.mark.asyncio
    async def test_write_tool_handler_exception_reports_error(self, vfs):
        from faust_backend.tools.write import write

        await vfs.write_symbolic(TOOL_NODE, lambda _p: "", writable=True)

        def boom(_node, _c):
            raise ValueError("信封不是合法 JSON")

        await vfs.set_write_handler(TOOL_NODE, boom)
        result = await write.ainvoke({"path": "faustbot://" + TOOL_NODE.lstrip("/"), "content": "hi"})

        assert "写入 faustbot 资源出错" in result
        assert "信封不是合法 JSON" in result


# ============================================================
# C-3：edit 工具走 edit_handler（回归）
# ============================================================

class TestEditToolAck:
    @pytest.mark.asyncio
    async def test_edit_tool_calls_edit_handler_only(self, vfs):
        """注册仅 edit_handler 的节点：edit 工具必须触发它，且不触发 write_handler。"""
        from faust_backend.tools.edit import edit

        await vfs.write_symbolic(TOOL_NODE, lambda _p: "alpha\nbeta\n", writable=True)
        calls: dict[str, list] = {"edit": [], "write": []}

        def write_handler(_node, content):
            calls["write"].append(content)
            return "write-ack"

        def edit_handler(_node, content):
            calls["edit"].append(content)
            return "已改为 gamma"

        await vfs.set_write_handler(TOOL_NODE, write_handler)
        await vfs.set_edit_handler(TOOL_NODE, edit_handler)

        result = await edit.ainvoke({
            "path": "faustbot://" + TOOL_NODE.lstrip("/"),
            "old_str": "alpha",
            "new_str": "gamma",
        })

        assert calls["edit"] == ["gamma\nbeta\n"], calls
        assert calls["write"] == []
        assert "已编辑 faustbot://" in result
        assert "↳ 已改为 gamma" in result

    @pytest.mark.asyncio
    async def test_edit_tool_edit_handler_without_ack(self, vfs):
        from faust_backend.tools.edit import edit

        await vfs.write_symbolic(TOOL_NODE, lambda _p: "alpha\nbeta\n", writable=True)
        await vfs.set_edit_handler(TOOL_NODE, lambda _node, _c: None)

        result = await edit.ainvoke({
            "path": "faustbot://" + TOOL_NODE.lstrip("/"),
            "old_str": "alpha",
            "new_str": "gamma",
        })

        assert "已编辑 faustbot://" in result
        assert "↳" not in result

    @pytest.mark.asyncio
    async def test_content_node_edit_still_replaces_without_handler(self, vfs):
        """普通内容节点（无 handler）行为不变：直接替换内容，无 ACK。"""
        from faust_backend.tools.edit import edit

        await vfs.write(TOOL_NODE, "hello world")
        result = await edit.ainvoke({
            "path": "faustbot://" + TOOL_NODE.lstrip("/"),
            "old_str": "world",
            "new_str": "faust",
        })

        assert "已编辑 faustbot://" in result
        assert "↳" not in result
        assert await vfs.read_text(TOOL_NODE) == "hello faust"
