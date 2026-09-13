"""read(with_metadata=True) 列举元数据测试。

覆盖：FS 目录（源码行数白名单 / 大文件 / 二进制 / 目录无元数据）、sourceCode://、
skill://（技能名 description + 目录内文件 stats）、memory://（日期/行数/前 3 tag）、
faustbot://（节点 description / 空描述 / set_description），
以及 with_metadata=False 时输出与既有行为一致。

Run: python -m pytest backend/tests/test_read_metadata.py -v
"""

from __future__ import annotations

import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _entry_meta(text: str, name: str) -> str | None:
    """返回列举里 name 条目紧跟的元数据行内容（去掉 `[ ]`）；无元数据返回 None。"""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == name:
            if idx + 1 < len(lines) and lines[idx + 1].startswith("    [") and lines[idx + 1].endswith("]"):
                return lines[idx + 1].strip()[1:-1]
            return None
    raise AssertionError(f"列举中找不到条目 {name!r}:\n{text}")


# ============================================================
# FS 目录列举
# ============================================================

class TestFsListingMetadata:
    @pytest.mark.asyncio
    async def test_source_file_gets_size_lines_date(self, tmp_path):
        from faust_backend.tools.read import read

        (tmp_path / "alpha.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        (tmp_path / "blob.bin").write_bytes(b"\x00\x01\x02")
        (tmp_path / "sub").mkdir()

        result = await read.ainvoke({"uri": str(tmp_path), "with_metadata": True})

        assert re.fullmatch(r"\d+B, 3 lines, \d{2}/\d{2}", _entry_meta(result, "alpha.py"))
        # 非源码扩展名：只有大小和日期，没有行数
        blob_meta = _entry_meta(result, "blob.bin")
        assert re.fullmatch(r"\d+B, \d{2}/\d{2}", blob_meta)
        # 目录条目不带元数据行
        assert _entry_meta(result, "sub/") is None

    @pytest.mark.asyncio
    async def test_without_metadata_output_is_unchanged(self, tmp_path):
        from faust_backend.tools.read import read

        (tmp_path / "alpha.py").write_text("a = 1\n", encoding="utf-8")
        (tmp_path / "sub").mkdir()

        result = await read.ainvoke({"uri": str(tmp_path)})

        assert result == "  sub/\n  alpha.py"

    @pytest.mark.asyncio
    async def test_oversized_source_omits_line_count(self, tmp_path):
        from faust_backend.tools.read import read

        (tmp_path / "big.py").write_text("x = 1\n" * 400_000, encoding="utf-8")

        result = await read.ainvoke({"uri": str(tmp_path), "with_metadata": True})

        meta = _entry_meta(result, "big.py")
        assert meta.endswith(f", {time.strftime('%m/%d')}")
        assert "MB" in meta
        assert "lines" not in meta

    @pytest.mark.asyncio
    async def test_undecodable_source_omits_line_count(self, tmp_path):
        from faust_backend.tools.read import read

        (tmp_path / "bad.py").write_bytes(b"\xff\xfe\x00broken")

        result = await read.ainvoke({"uri": str(tmp_path), "with_metadata": True})

        meta = _entry_meta(result, "bad.py")
        assert "lines" not in meta


# ============================================================
# sourceCode://
# ============================================================

class TestSourceCodeListingMetadata:
    @pytest.mark.asyncio
    async def test_tools_dir_lists_size_and_lines(self):
        from faust_backend.tools.read import read

        result = await read.ainvoke(
            {"uri": "sourceCode://backend/faust_backend/tools/", "with_metadata": True}
        )

        meta = _entry_meta(result, "sourceCode://backend/faust_backend/tools/read.py")
        assert re.fullmatch(r"\d+KB, \d+ lines, \d{2}/\d{2}", meta)

    @pytest.mark.asyncio
    async def test_repo_root_lists_file_metadata(self):
        from faust_backend.tools.read import read

        result = await read.ainvoke({"uri": "sourceCode://", "with_metadata": True})

        meta = _entry_meta(result, "sourceCode://README.md")
        assert re.fullmatch(r"\d+(\.\d)?(B|KB|MB), \d{2}/\d{2}", meta)


# ============================================================
# skill://
# ============================================================

class TestSkillListingMetadata:
    @pytest.fixture
    def agent_root(self, tmp_path, monkeypatch):
        from faust_backend.runtime import state

        root = tmp_path / "agents" / "demo_agent"
        skill = root / "skill.d" / "demo_skill"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("# demo\n", encoding="utf-8")
        (skill / "util.py").write_text("x = 1\ny = 2\n", encoding="utf-8")
        (skill / "_meta.json").write_text(
            '{"slug": "demo_skill", "version": "1.0.0", "description": "演示技能说明"}',
            encoding="utf-8",
        )
        monkeypatch.setattr(state, "AGENT_ROOT", str(root))
        return root

    @pytest.mark.asyncio
    async def test_skill_names_show_meta_description(self, agent_root):
        from faust_backend.tools.read import read

        result = await read.ainvoke({"uri": "skill://", "with_metadata": True})

        assert re.search(r"skill://demo_skill/\n    \[演示技能说明\]", result)

        plain = await read.ainvoke({"uri": "skill://"})
        assert "演示技能说明" not in plain

    @pytest.mark.asyncio
    async def test_skill_dir_files_show_stats(self, agent_root):
        from faust_backend.tools.read import read

        result = await read.ainvoke({"uri": "skill://demo_skill/", "with_metadata": True})

        assert re.fullmatch(
            r"\d+B, 2 lines, \d{2}/\d{2}", _entry_meta(result, "skill://demo_skill/util.py")
        )
        skill_md_meta = _entry_meta(result, "skill://demo_skill/SKILL.md")
        assert re.fullmatch(r"\d+B, \d{2}/\d{2}", skill_md_meta)


# ============================================================
# faustbot:// 节点 description
# ============================================================

class TestVfsDescription:
    @pytest.mark.asyncio
    async def test_listing_shows_description_and_blank_is_omitted(self):
        from faust_backend.tools.read import read
        from faust_backend.tools.vfs import get_faustbot_vfs

        vfs = await get_faustbot_vfs()
        await vfs.write("/meta-test-node", "content", description="测试节点说明")
        await vfs.write("/meta-blank-node", "content")
        await vfs.mkdir("/meta-test-dir", description="测试目录说明")
        try:
            result = await read.ainvoke({"uri": "faustbot://", "with_metadata": True})
            assert re.search(r"faustbot://meta-test-node\n    \[测试节点说明\]", result)
            assert re.search(r"faustbot://meta-test-dir/\n    \[测试目录说明\]", result)
            assert not re.search(r"faustbot://meta-blank-node\n    \[", result)

            plain = await read.ainvoke({"uri": "faustbot://"})
            assert "测试节点说明" not in plain
        finally:
            await vfs.delete("/meta-test-node")
            await vfs.delete("/meta-blank-node")
            await vfs.delete("/meta-test-dir")

    @pytest.mark.asyncio
    async def test_set_description_updates_and_errors_on_missing(self):
        from faust_backend.tools.read import read
        from faust_backend.tools.vfs import get_faustbot_vfs

        vfs = await get_faustbot_vfs()
        await vfs.write("/meta-set-node", "content")
        try:
            await vfs.set_description("/meta-set-node", "后设说明")
            result = await read.ainvoke({"uri": "faustbot://", "with_metadata": True})
            assert re.search(r"faustbot://meta-set-node\n    \[后设说明\]", result)

            with pytest.raises(FileNotFoundError):
                await vfs.set_description("/meta-missing-node", "x")
        finally:
            await vfs.delete("/meta-set-node")

    @pytest.mark.asyncio
    async def test_content_rewrite_keeps_description(self):
        from faust_backend.tools.vfs import get_faustbot_vfs

        vfs = await get_faustbot_vfs()
        await vfs.write("/meta-keep-node", "v1", description="保留说明")
        try:
            await vfs.write("/meta-keep-node", "v2")
            node = await vfs.get_node("/meta-keep-node")
            assert node is not None and node.description == "保留说明"

            await vfs.edit("/meta-keep-node", "v3")
            node = await vfs.get_node("/meta-keep-node")
            assert node is not None and node.description == "保留说明"
        finally:
            await vfs.delete("/meta-keep-node")


# ============================================================
# memory://
# ============================================================

class TestMemoryTreeMetadata:
    @pytest.mark.asyncio
    async def test_tree_shows_date_lines_and_first_three_tags(self, read_memory_store):
        from faust_backend.tools.read import read

        await read_memory_store.file_write(
            "/notes/work",
            "l1\nl2\nl3",
            description="工作笔记",
            tags=["工作", "计划", "重要", "第四个"],
            index=False,
        )

        result = await read.ainvoke({"uri": "memory://notes/", "with_metadata": True})

        assert re.search(r"work\n    \[\d{2}/\d{2}, 3 lines, #工作, #计划, #重要\]", result)
        assert "#第四个" not in result

    @pytest.mark.asyncio
    async def test_tree_without_metadata_has_no_bracket_lines(self, read_memory_store):
        from faust_backend.tools.read import read

        await read_memory_store.file_write(
            "/notes/work", "l1\nl2\nl3", description="工作笔记", tags=["工作"], index=False
        )

        result = await read.ainvoke({"uri": "memory://notes/"})

        assert "work" in result
        assert "[" not in result
