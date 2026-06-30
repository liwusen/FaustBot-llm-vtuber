"""
Unit tests for the FaustBot harness core tools and infrastructure.

Run: python -m pytest backend/tests/test_harness.py -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

# Ensure the backend is on the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from faust_backend.runtime.uri import parse, SCHEME_FILE, SCHEME_ARTIFACT, SCHEME_MEMORY
from faust_backend.runtime.output_store import OutputStore, reset_output_store


# ============================================================
# URI Parser
# ============================================================

class TestURIParse:
    def test_file_path_bare(self):
        p = parse("src/main.py")
        assert p.scheme == SCHEME_FILE
        assert p.path == "src/main.py"
        assert p.selector is None

    def test_file_path_with_selector(self):
        p = parse("src/main.py:50-100")
        assert p.scheme == SCHEME_FILE
        assert p.path == "src/main.py"
        assert p.selector == ":50-100"
        assert p.selector_lines == (50, 100)

    def test_file_path_single_line(self):
        p = parse("src/main.py:42")
        assert p.selector_lines == (42, 42)

    def test_file_path_plus_offset(self):
        p = parse("src/main.py:50+20")
        assert p.selector_lines == (50, 69)

    def test_file_path_raw(self):
        p = parse("src/main.py:50-100:raw")
        assert p.selector == ":50-100:raw"
        assert p.selector_lines == (50, 100)

    def test_file_path_directory(self):
        p = parse("src/")
        assert p.scheme == SCHEME_FILE
        assert p.path == "src/"
        assert p.is_dir is True

    def test_artifact_bare(self):
        p = parse("artifact://abc123")
        assert p.scheme == SCHEME_ARTIFACT
        assert p.path == "abc123"
        assert p.selector is None

    def test_artifact_with_selector(self):
        p = parse("artifact://abc123:50-100")
        assert p.scheme == SCHEME_ARTIFACT
        assert p.path == "abc123"
        assert p.selector == ":50-100"

    def test_memory_bare(self):
        p = parse("memory://notes/math")
        assert p.scheme == SCHEME_MEMORY
        assert p.path == "notes/math"
        assert p.selector is None

    def test_memory_with_selector(self):
        p = parse("memory://notes/math:50-100")
        assert p.scheme == SCHEME_MEMORY
        assert p.path == "notes/math"
        assert p.selector == ":50-100"

    def test_memory_root(self):
        p = parse("memory://")
        assert p.scheme == SCHEME_MEMORY
        assert p.path == ""
        assert p.is_dir is True

    def test_memory_with_query(self):
        p = parse("memory://?q=勾股定理&top_k=5")
        assert p.scheme == SCHEME_MEMORY
        assert p.query == {"q": ["勾股定理"], "top_k": ["5"]}

    def test_empty(self):
        p = parse("")
        assert p.scheme == SCHEME_FILE
        assert p.path == ""

    def test_windows_path_not_selector(self):
        p = parse("D:/dev/test.py")
        assert p.scheme == SCHEME_FILE
        assert p.path == "D:/dev/test.py"
        assert p.selector is None


# ============================================================
# OutputStore
# ============================================================

class TestOutputStore:
    def setup_method(self):
        reset_output_store()

    def test_put_and_get(self):
        store = OutputStore()
        oid = store.put("hello world", tool_name="test")
        art = store.get(oid)
        assert art is not None
        assert art.content == "hello world"
        assert art.tool_name == "test"

    def test_summary_short(self):
        store = OutputStore()
        oid = store.put("short", tool_name="t")
        s = store.summary(oid)
        assert "short" in s
        assert "artifact://" not in s  # short enough, no truncation

    def test_summary_long(self):
        store = OutputStore()
        long_text = "line " * 200
        oid = store.put(long_text, tool_name="t")
        s = store.summary(oid)
        assert "artifact://" in s
        assert len(s) < len(long_text)

    def test_peek(self):
        store = OutputStore()
        oid = store.put("data", tool_name="peek_test")
        meta = store.peek(oid)
        assert meta["tool_name"] == "peek_test"
        assert meta["lines"] == 1
        assert meta["size"] == 4

    def test_get_with_pagination(self):
        store = OutputStore()
        content = "\n".join(f"line {i}" for i in range(100))
        oid = store.put(content, tool_name="t")
        art = store.get(oid)
        # get lines 10-19 (offset 10, limit 10)
        page = art.get(offset=10, limit=10)
        assert page.startswith("line 10")
        assert page.count("\n") == 9

    def test_list_ids(self):
        store = OutputStore()
        store.put("a", tool_name="x")
        store.put("b", tool_name="y")
        ids = store.list_ids()
        assert len(ids) == 2

    def test_missing_artifact(self):
        store = OutputStore()
        assert store.get("nonexistent") is None
        assert "找不到" in store.summary("nonexistent")

    def test_clear(self):
        store = OutputStore()
        store.put("data", tool_name="x")
        store.clear()
        assert len(store.list_ids()) == 0

    def test_id_format(self):
        store = OutputStore()
        oid1 = store.put("a", tool_name="sys_exec")
        oid2 = store.put("b", tool_name="python_exec")
        assert oid1.startswith("shell_")
        assert oid2.startswith("py_")


# ============================================================
# Read tool (URI dispatch)
# ============================================================

class TestReadTool:
    def setup_method(self):
        reset_output_store()

    def test_read_artifact(self):
        from faust_backend.tools.read import read
        from faust_backend.runtime.output_store import get_output_store
        store = get_output_store()
        oid = store.put("artifact content", tool_name="test")
        result = read.invoke({"uri": f"artifact://{oid}"})
        assert "artifact content" in result

    def test_read_artifact_with_range(self):
        from faust_backend.tools.read import read
        from faust_backend.runtime.output_store import get_output_store
        store = get_output_store()
        content = "\n".join(f"line {i}" for i in range(1, 21))
        oid = store.put(content, tool_name="test")
        result = read.invoke({"uri": f"artifact://{oid}:5-7"})
        assert "line 5" in result
        assert "line 6" in result
        assert "line 7" in result
        assert "line 8" not in result

    def test_read_missing_artifact(self):
        from faust_backend.tools.read import read
        result = read.invoke({"uri": "artifact://nonexistent"})
        assert "找不到" in result

    def test_read_file_with_structural_summary(self, tmp_path):
        from faust_backend.tools.read import read
        import faust_backend.config_loader as conf
        code = '''"""
Module docstring.
"""

import os

def foo():
    """do stuff"""
    return 42

def bar(x):
    return x + 1

class MyClass:
    def method(self):
        pass
'''
        f = tmp_path / "test.py"
        f.write_text(code, encoding="utf-8")

        # Override config root temporarily
        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            result = read.invoke({"uri": "test.py"})
            assert "foo" in result
            assert "bar" in result
            assert "MyClass" in result
            assert "结构摘要" in result or "[文件" in result
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_read_file_with_line_range(self, tmp_path):
        from faust_backend.tools.read import read
        import faust_backend.config_loader as conf
        content = "\n".join(f"line {i}" for i in range(1, 11))
        f = tmp_path / "data.txt"
        f.write_text(content, encoding="utf-8")

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            result = read.invoke({"uri": "data.txt:3-5"})
            assert "line 3" in result
            assert "line 5" in result
            assert "line 6" not in result
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_read_directory(self, tmp_path):
        from faust_backend.tools.read import read
        import faust_backend.config_loader as conf
        (tmp_path / "sub/").mkdir()
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.py").write_text("b")

        result = read.invoke({"uri": str(tmp_path)})
        assert "a.txt" in result
        assert "b.py" in result
        assert "sub/" in result

    def test_read_nonexistent_file(self):
        from faust_backend.tools.read import read
        result = read.invoke({"uri": "nonexistent_file_xyz.txt"})
        assert "不存在" in result


# ============================================================
# Write tool
# ============================================================

class TestWriteTool:
    def test_write_text_file(self, tmp_path):
        from faust_backend.tools.write import write
        import faust_backend.config_loader as conf

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            result = write.invoke({"path": "hello.md", "content": "# Hello\nWorld"})
            assert "已写入" in result
            assert "bytes" in result
            assert (tmp_path / "hello.md").read_text() == "# Hello\nWorld"
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_write_rejects_outside_path(self, tmp_path):
        from faust_backend.tools.write import write
        import faust_backend.config_loader as conf

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            result = write.invoke({"path": "/etc/passwd", "content": "bad"})
            assert "不允许" in result or "错误" in result
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_write_empty_path(self):
        from faust_backend.tools.write import write
        result = write.invoke({"path": "", "content": "content"})
        assert "错误" in result


# ============================================================
# Edit tool
# ============================================================

class TestEditTool:
    def test_swap_lines(self, tmp_path):
        from faust_backend.tools.edit import edit
        import faust_backend.config_loader as conf

        content = "\n".join(f"line {i}" for i in range(1, 6))
        f = tmp_path / "test.txt"
        f.write_text(content, encoding="utf-8")

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            patch = "SWAP 2.=3:\n+LINE TWO\n+LINE THREE\n"
            result = edit.invoke({"path": "test.txt", "patch": patch})
            assert "已编辑" in result
            new_content = f.read_text()
            assert "line 1" in new_content
            assert "LINE TWO" in new_content
            assert "LINE THREE" in new_content
            assert "line 2" not in new_content
            assert "line 4" in new_content
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_delete_lines(self, tmp_path):
        from faust_backend.tools.edit import edit
        import faust_backend.config_loader as conf

        content = "\n".join(f"line {i}" for i in range(1, 6))
        f = tmp_path / "test.txt"
        f.write_text(content, encoding="utf-8")

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            patch = "DEL 2.=3\n"
            result = edit.invoke({"path": "test.txt", "patch": patch})
            assert "已编辑" in result
            new = f.read_text().split("\n")
            assert new == ["line 1", "line 4", "line 5"]
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_ins_pre(self, tmp_path):
        from faust_backend.tools.edit import edit
        import faust_backend.config_loader as conf

        content = "line 1\nline 2"
        f = tmp_path / "test.txt"
        f.write_text(content, encoding="utf-8")

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            patch = "INS.PRE 2:\n+INSERTED\n"
            result = edit.invoke({"path": "test.txt", "patch": patch})
            assert "已编辑" in result
            new = f.read_text().split("\n")
            assert new == ["line 1", "INSERTED", "line 2"]
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_ins_post(self, tmp_path):
        from faust_backend.tools.edit import edit
        import faust_backend.config_loader as conf

        content = "line 1\nline 2"
        f = tmp_path / "test.txt"
        f.write_text(content, encoding="utf-8")

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            patch = "INS.POST 1:\n+INSERTED\n"
            result = edit.invoke({"path": "test.txt", "patch": patch})
            assert "已编辑" in result
            new = f.read_text().split("\n")
            assert new == ["line 1", "INSERTED", "line 2"]
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_missing_file(self):
        from faust_backend.tools.edit import edit
        import faust_backend.config_loader as conf

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = "/tmp"
        try:
            result = edit.invoke({"path": "nonexistent.txt", "patch": "SWAP 1.=1:\n+x\n"})
            assert "不存在" in result or "出错" in result or "No such file" in result
        finally:
            conf.PROJECT_ROOT = orig_root


# ============================================================
# Find tool
# ============================================================

class TestFindTool:
    def test_find_filesystem(self, tmp_path):
        from faust_backend.tools.find import find
        import faust_backend.config_loader as conf

        (tmp_path / "src/").mkdir()
        (tmp_path / "src/a.py").write_text("a")
        (tmp_path / "src/b.ts").write_text("b")
        (tmp_path / "tests/").mkdir()
        (tmp_path / "tests/test_a.py").write_text("test")

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            result = find.invoke({"patterns": ["src/**/*.py"]})
            assert "a.py" in result
            assert "test_a.py" not in result
            assert "b.ts" not in result
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_find_multiple_globs(self, tmp_path):
        from faust_backend.tools.find import find
        import faust_backend.config_loader as conf

        (tmp_path / "src/a.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src/a.py").write_text("a")
        (tmp_path / "tests/b.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests/b.py").write_text("b")

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            result = find.invoke({"patterns": ["src/**/*.py", "tests/**/*.py"]})
            assert "a.py" in result
            assert "b.py" in result
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_find_no_match(self, tmp_path):
        from faust_backend.tools.find import find
        import faust_backend.config_loader as conf

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            result = find.invoke({"patterns": ["nonexistent/**/*.xyz"]})
            assert "未匹配" in result
        finally:
            conf.PROJECT_ROOT = orig_root


# ============================================================
# Search tool (filesystem only, no memory dep)
# ============================================================

class TestSearchToolFilesystem:
    def test_search_regex(self, tmp_path):
        from faust_backend.tools.search import search
        import faust_backend.config_loader as conf

        (tmp_path / "src/").mkdir()
        (tmp_path / "src/a.py").write_text("def hello():\n    return 42\n", encoding="utf-8")
        (tmp_path / "src/b.py").write_text("class World:\n    pass\n", encoding="utf-8")

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            result = search.invoke({"pattern": "hello", "paths": ["src/"]})
            assert "hello" in result.lower()
        finally:
            conf.PROJECT_ROOT = orig_root

    def test_search_no_match(self, tmp_path):
        from faust_backend.tools.search import search
        import faust_backend.config_loader as conf

        (tmp_path / "src/a.py").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src/a.py").write_text("hello world")

        orig_root = conf.PROJECT_ROOT
        conf.PROJECT_ROOT = str(tmp_path)
        try:
            result = search.invoke({"pattern": "xyznonexistentpattern", "paths": ["src/"]})
            assert "未找到" in result
        finally:
            conf.PROJECT_ROOT = orig_root


# ============================================================
# Execute tool
# ============================================================

class TestExecuteTool:
    def test_execute_python(self):
        from faust_backend.tools.execute import execute
        result = execute.invoke({"language": "python", "code": "print('hello')"})
        assert "hello" in result

    def test_execute_python_multiline(self):
        from faust_backend.tools.execute import execute
        code = "x = 1 + 2\nprint(x)"
        result = execute.invoke({"language": "python", "code": code})
        assert "3" in result

    def test_execute_python_error(self):
        from faust_backend.tools.execute import execute
        result = execute.invoke({"language": "python", "code": "raise ValueError('test')"})
        assert "ValueError" in result or "exit code" in result

    def test_execute_shell(self):
        from faust_backend.tools.execute import execute
        result = execute.invoke({"language": "shell", "code": "echo hello world"})
        assert "hello world" in result

    def test_execute_timeout(self):
        from faust_backend.tools.execute import execute
        result = execute.invoke({"language": "python", "code": "import time; time.sleep(999)", "timeout": 1})
        assert "超时" in result

    def test_execute_unknown_language(self):
        from faust_backend.tools.execute import execute
        result = execute.invoke({"language": "ruby", "code": "puts 'hi'"})
        assert "不支持" in result


# ============================================================
# Multimodal / Image Support
# ============================================================

class TestToolValueToText:
    def test_dict_passes_through(self):
        from faust_backend.runtime.state import tool_value_to_text
        result = tool_value_to_text({"kind": "multimodal_tool_result", "text": "hi"})
        assert isinstance(result, dict)
        assert result["kind"] == "multimodal_tool_result"

    def test_string_unchanged(self):
        from faust_backend.runtime.state import tool_value_to_text
        result = tool_value_to_text("hello")
        assert result == "hello"


class TestArtifactImage:
    def test_put_multimodal_stores_correctly(self):
        from faust_backend.runtime.output_store import OutputStore, reset_output_store
        reset_output_store()
        store = OutputStore()
        payload = {
            "kind": "multimodal_tool_result",
            "text": "测试图片",
            "images": [{"url": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="}],
        }
        oid = store.put_multimodal(payload, tool_name="read")
        art = store.get(oid)
        assert art.content_type == "multimodal"
        assert art.mime_type == "image/png"
        assert art.content_base64 == "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        assert art.content == "测试图片"
        assert art.metadata.get("image_count") == 1

    def test_multimodal_summary(self):
        from faust_backend.runtime.output_store import OutputStore, reset_output_store
        reset_output_store()
        store = OutputStore()
        payload = {
            "kind": "multimodal_tool_result",
            "text": "一张截图",
            "images": [{"url": "data:image/jpeg;base64,abc123"}],
        }
        oid = store.put_multimodal(payload, tool_name="read")
        s = store.summary(oid)
        assert "一张截图" in s
        assert "1张图片" in s
        assert f"artifact://{oid}" in s

    def test_image_summary(self):
        from faust_backend.runtime.output_store import Artifact
        art = Artifact(
            output_id="img_1",
            content="test",
            tool_name="read",
            content_type="image",
            content_base64="abc",
            mime_type="image/png",
        )
        s = art.summary()
        assert "图片" in s
        assert "artifact://img_1" in s

    def test_multimodal_get_returns_json(self):
        import json
        from faust_backend.runtime.output_store import OutputStore, reset_output_store
        reset_output_store()
        store = OutputStore()
        payload = {
            "kind": "multimodal_tool_result",
            "text": "desc",
            "images": [{"url": "data:image/png;base64,hello123"}],
        }
        oid = store.put_multimodal(payload, tool_name="read")
        art = store.get(oid)
        result = art.get()
        data = json.loads(result)
        assert data["kind"] == "multimodal_tool_result"
        assert data["text"] == "desc"
        assert len(data["images"]) == 1
        assert "data:image/png;base64,hello123" in data["images"][0]["url"]


class TestMiddlewareMultimodal:
    def test_multimodal_json_passes_through(self):
        import json
        from faust_backend.runtime.output_store import reset_output_store
        from faust_backend.runtime.middleware import _store_and_summarize, get_output_store
        reset_output_store()
        store = get_output_store()
        payload = json.dumps({
            "kind": "multimodal_tool_result",
            "text": "图片描述",
            "images": [{"url": "data:image/png;base64,abc123"}],
        }, ensure_ascii=False)
        result = _store_and_summarize(store, "read", payload)
        data = json.loads(result)
        assert data["kind"] == "multimodal_tool_result"
        assert "图片描述" in data["text"]
        assert "artifact://" in data["text"]  # augmented with artifact ref
        assert len(data["images"]) == 1

    def test_dict_multimodal_passes_through(self):
        import json
        from faust_backend.runtime.output_store import reset_output_store
        from faust_backend.runtime.middleware import _store_and_summarize, get_output_store
        reset_output_store()
        store = get_output_store()
        payload = {
            "kind": "multimodal_tool_result",
            "text": "dict_test",
            "images": [{"url": "data:image/png;base64,xyz"}],
        }
        result = _store_and_summarize(store, "read", payload)
        data = json.loads(result)
        assert data["kind"] == "multimodal_tool_result"
        assert "dict_test" in data["text"]
        assert "artifact://" in data["text"]



class TestMiddlewareTruncation:
    """Verify wrap_tool_output actually truncates output for the paths LangGraph uses."""

    def setup_method(self):
        from faust_backend.runtime.output_store import reset_output_store
        reset_output_store()

    def _long_output(self) -> str:
        return "\n".join(f"line {i:04d}" for i in range(200))

    def test_sync_tool_run_truncates(self):
        """Sync @tool _run() → long output should be truncated with artifact ref."""
        from langchain.tools import tool
        from faust_backend.runtime.middleware import wrap_tool_output

        @tool
        def big_tool(x: str) -> str:
            """Returns lots of lines."""
            return self._long_output()

        wrapped = wrap_tool_output(big_tool)
        result = wrapped._run("ignored")

        assert "artifact://" in result
        assert len(result) < len(self._long_output())
        assert "[完整输出:" in result

    def test_sync_tool_invoke_truncates(self):
        """Sync @tool invoke() — the path LangGraph actually calls — must truncate."""
        from langchain.tools import tool
        from faust_backend.runtime.middleware import wrap_tool_output

        @tool
        def big_tool(x: str) -> str:
            """Returns lots of lines."""
            return self._long_output()

        wrapped = wrap_tool_output(big_tool)
        result = wrapped.invoke({"x": "ignored"})

        assert "artifact://" in result
        assert len(result) < len(self._long_output())
        assert "[完整输出:" in result

    def test_sync_tool_arun_also_truncates(self):
        """Sync @tool _arun() async path must also truncate."""
        import asyncio
        from langchain.tools import tool
        from faust_backend.runtime.middleware import wrap_tool_output

        @tool
        def big_tool(x: str) -> str:
            """Returns lots of lines."""
            return self._long_output()

        wrapped = wrap_tool_output(big_tool)
        result = asyncio.run(wrapped._arun("ignored"))

        assert "artifact://" in result
        assert len(result) < len(self._long_output())

    def test_short_output_passes_through(self):
        """Short single-line output (<=120 chars) should NOT create an artifact."""
        from langchain.tools import tool
        from faust_backend.runtime.middleware import wrap_tool_output

        @tool
        def small_tool(x: str) -> str:
            """Returns short."""
            return "OK"

        wrapped = wrap_tool_output(small_tool)
        result = wrapped.invoke({"x": "test"})

        assert result == "OK"
        assert "artifact://" not in result

    def test_multiline_short_output_still_truncates(self):
        """Multi-line but short-per-line — still needs artifact if >5 lines."""
        from langchain.tools import tool
        from faust_backend.runtime.middleware import wrap_tool_output

        @tool
        def ml_tool(x: str) -> str:
            """Returns 10 short lines."""
            return "\n".join(str(i) for i in range(10))

        wrapped = wrap_tool_output(ml_tool)
        result = wrapped.invoke({"x": "test"})

        assert "artifact://" in result

    def test_tool_exception_becomes_error_artifact(self):
        """When a tool raises, the exception is stored as an artifact."""
        from langchain.tools import tool
        from faust_backend.runtime.middleware import wrap_tool_output

        @tool
        def crash_tool(x: str) -> str:
            """Always crashes."""
            msg = "BOOM"
            raise RuntimeError(msg)

        wrapped = wrap_tool_output(crash_tool)
        result = wrapped._run("test")

        assert "工具执行出错" in result
        assert "artifact://" in result

    def test_async_tool_arun_truncates(self):
        """Async @tool — _arun path must truncate."""
        import asyncio
        from langchain.tools import tool
        from faust_backend.runtime.middleware import wrap_tool_output

        @tool
        async def async_big(x: str) -> str:
            """Async large output."""
            return self._long_output()

        wrapped = wrap_tool_output(async_big)
        result = asyncio.run(wrapped._arun("ignored"))

        assert "artifact://" in result
        assert len(result) < len(self._long_output())

    def test_both_run_and_arun_wrapped_independently(self):
        """After wrapping, _run and _arun must both exist and both truncate.

        This is the regression test for the bug where sync tools only had
        _arun wrapped, leaving _run (the path LangGraph uses) unwrapped.
        """
        import asyncio
        from langchain.tools import tool
        from faust_backend.runtime.middleware import wrap_tool_output

        @tool
        def dual_tool(x: str) -> str:
            """Dual."""
            return self._long_output()

        wrapped = wrap_tool_output(dual_tool)

        sync_result = wrapped._run("x")
        assert "artifact://" in sync_result

        async_result = asyncio.run(wrapped._arun("x"))
        assert "artifact://" in async_result

class TestReadImage:
    def test_read_image_returns_multimodal_json(self):
        import json, tempfile
        from pathlib import Path
        # Create a minimal 1x1 red PNG
        raw_png = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0bIDATx\x9cc\xf8O\x00\x00'
            b'\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(raw_png)
            tmp_path = f.name
        try:
            from faust_backend.tools.read import _read_image
            result = _read_image(Path(tmp_path))
            data = json.loads(result)
            assert data["kind"] == "multimodal_tool_result"
            assert "图片文件" in data["text"]
            assert len(data["images"]) == 1
            assert data["images"][0]["url"].startswith("data:image/png;base64,")
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    def test_read_image_force_plain_text(self):
        import json, tempfile
        from pathlib import Path
        raw_png = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0bIDATx\x9cc\xf8O\x00\x00'
            b'\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(raw_png)
            tmp_path = f.name
        try:
            from faust_backend.tools.read import _read_image
            result = _read_image(Path(tmp_path), force_plain_text=True)
            assert isinstance(result, str)
            assert "图片文件" in result
            assert "bytes" in result
            assert ".png" in result
            # Must NOT contain multimodal JSON markers
            assert "multimodal_tool_result" not in result
            assert "base64" not in result
            assert "data:image" not in result
        finally:
            Path(tmp_path).unlink(missing_ok=True)



class TestReadForcePlainText:
    def test_artifact_image_force_plain_text(self):
        from faust_backend.runtime.output_store import reset_output_store, get_output_store
        reset_output_store()
        store = get_output_store()
        payload = {
            "kind": "multimodal_tool_result",
            "text": "描述内容",
            "images": [{"url": "data:image/png;base64,abc123"}],
        }
        oid = store.put_multimodal(payload, tool_name="read")

        from faust_backend.tools.read import _read_artifact
        from faust_backend.runtime.uri import parse
        parsed = parse(f"artifact://{oid}")
        result = _read_artifact(parsed, force_plain_text=True)
        assert "描述内容" in result
        assert "base64" not in result
        assert "multimodal_tool_result" not in result
