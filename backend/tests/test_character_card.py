"""SillyTavern 角色卡加载器测试：解析、渲染、导入编排、模板同步守卫。"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import faust_backend.admin_runtime as admin_runtime
import faust_backend.character_card as character_card
from faust_backend.memory.store import GraphStore

TEMPLATE_DIR = Path(admin_runtime.conf.PROJECT_ROOT) / "agents_template" / "faust"


# ── 构造测试卡片 ──


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", crc)


def _png_with_text(keyword: str, text: str, *, itxt: bool = False) -> bytes:
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    if itxt:
        payload = (
            keyword.encode("utf-8")
            + b"\x00\x00\x00\x00\x00"
            + text.encode("utf-8")
        )
        text_chunk = _png_chunk(b"iTXt", payload)
    else:
        text_chunk = _png_chunk(b"tEXt", keyword.encode("latin-1") + b"\x00" + text.encode("latin-1"))
    return character_card.PNG_SIGNATURE + ihdr + text_chunk + _png_chunk(b"IEND", b"")


def _encode_payload(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")


def _v2_card(**overrides) -> dict:
    data = {
        "name": "Alice",
        "description": "来自{{char}}世界的炼金术士，服务于{{user}}。",
        "personality": "冷静、好奇",
        "scenario": "在浮空城的工作室里",
        "first_mes": "你好，我是爱丽丝。",
        "alternate_greetings": ["早安。"],
        "mes_example": "<START>\n{{user}}: 你好\n{{char}}: 你好呀。",
        "system_prompt": "始终以第二人称称呼用户。",
        "post_history_instructions": "不要提及测试环境。",
        "creator": "tester",
        "character_version": "1.2",
        "tags": ["fantasy", "alchemy"],
        "character_book": {
            "name": "Lore",
            "entries": [
                {
                    "name": "浮空城",
                    "keys": ["浮空城", "city"],
                    "content": "浮空城建于云端。",
                    "comment": "地理设定",
                    "enabled": True,
                },
                {"name": "禁用条目", "keys": [], "content": "不应导入。", "enabled": False},
                {"name": "空条目", "keys": [], "content": "   ", "enabled": True},
            ],
        },
        "spec": "chara_card_v2",
    }
    data.update(overrides)
    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": data}


def _write_card(tmp_path: Path, name: str, payload: dict) -> Path:
    path = tmp_path / name
    path.write_bytes(_png_with_text("chara", _encode_payload(payload)))
    return path


def _isolate(monkeypatch, tmp_path: Path) -> None:
    """把 agent 目录与记忆库指向 tmp_path，并关闭向量嵌入（不联网）。"""
    monkeypatch.setattr(admin_runtime.conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(admin_runtime.conf, "AGENT_NAME", "faust")
    monkeypatch.setattr(admin_runtime.conf, "BM25_ONLY", True)
    monkeypatch.setattr(admin_runtime, "BACKEND_ROOT", tmp_path)
    monkeypatch.setattr(admin_runtime, "AGENTS_ROOT", tmp_path / "agents")


# ── 解析 ──


def test_parse_png_tex_chunk(tmp_path):
    path = _write_card(tmp_path, "alice.png", _v2_card())
    source = character_card.load_card_file(path)

    assert source.source_format == "png"
    assert source.image_content_type == "image/png"
    assert source.image_bytes == path.read_bytes()
    assert source.card.spec == "chara_card_v2"
    assert source.card.name == "Alice"
    assert source.card.personality == "冷静、好奇"
    assert source.card.first_mes == "你好，我是爱丽丝。"
    assert source.card.alternate_greetings == ("早安。",)
    assert source.card.tags == ("fantasy", "alchemy")
    assert source.card.book is not None
    # 空内容条目在解析阶段被丢弃
    assert [entry.name for entry in source.card.book.entries] == ["浮空城", "禁用条目"]


def test_parse_png_itxt_prefers_ccv3(tmp_path):
    v2 = _encode_payload(_v2_card())
    v3_card = {"spec": "chara_card_v3", "data": {"name": "V3角色", "description": "三版卡片"}}
    png = character_card.PNG_SIGNATURE + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    png += _png_chunk(b"tEXt", b"chara\x00" + v2.encode("latin-1"))
    png += _png_chunk(b"iTXt", b"ccv3\x00\x00\x00\x00\x00" + _encode_payload(v3_card).encode("utf-8"))
    png += _png_chunk(b"IEND", b"")
    path = tmp_path / "v3.png"
    path.write_bytes(png)

    card = character_card.load_card_file(path).card
    assert card.spec == "chara_card_v3"
    assert card.name == "V3角色"


def test_parse_json_v1_and_v2(tmp_path):
    v1_path = tmp_path / "v1.json"
    v1_path.write_text(json.dumps({"name": "Flat", "description": "扁平卡"}), encoding="utf-8")
    v1 = character_card.load_card_file(v1_path)
    assert v1.source_format == "json"
    assert v1.image_bytes is None
    assert v1.card.spec == "v1"
    assert v1.card.description == "扁平卡"

    v2_path = tmp_path / "v2.json"
    v2_path.write_text(json.dumps(_v2_card(), ensure_ascii=False), encoding="utf-8")
    v2 = character_card.load_card_file(v2_path)
    assert v2.card.name == "Alice"
    assert v2.card.book is not None and v2.card.book.name == "Lore"


def test_parse_errors(tmp_path):
    plain_png = tmp_path / "plain.png"
    plain_png.write_bytes(character_card.PNG_SIGNATURE + _png_chunk(b"IEND", b""))
    with pytest.raises(character_card.CardParseError, match="未找到 SillyTavern 角色卡数据"):
        character_card.load_card_file(plain_png)

    nameless = tmp_path / "nameless.json"
    nameless.write_text(json.dumps({"description": "无名字"}), encoding="utf-8")
    with pytest.raises(character_card.CardParseError, match="缺少 name 字段"):
        character_card.load_card_file(nameless)

    wrong_ext = tmp_path / "card.txt"
    wrong_ext.write_text("{}", encoding="utf-8")
    with pytest.raises(character_card.CardParseError, match="不支持的角色卡格式"):
        character_card.load_card_file(wrong_ext)

    missing = tmp_path / "nope.png"
    with pytest.raises(character_card.CardParseError, match="不存在"):
        character_card.load_card_file(missing)


def test_placeholder_substitution(tmp_path):
    card = character_card.load_card_file(_write_card(tmp_path, "alice.png", _v2_card())).card
    assert "来自Alice世界的炼金术士，服务于用户。" == card.description
    assert "{{user}}" not in card.mes_example and "Alice: 你好呀。" in card.mes_example


# ── 渲染 ──


def test_render_role_md(tmp_path):
    card = character_card.load_card_file(_write_card(tmp_path, "alice.png", _v2_card())).card
    text = character_card.render_role_md(card)

    assert "请扮演角色卡中的角色「Alice」" in text
    assert "来自Alice世界的炼金术士" in text
    assert "- 性格：冷静、好奇" in text
    assert "- 场景：在浮空城的工作室里" in text
    # 通用语音输出约束
    assert "不要使用括号等标点来描述动作或情绪" in text
    assert "输出长度不要超过用户输入的两倍" in text
    # 卡片附加约束
    assert "始终以第二人称称呼用户。" in text
    assert "不要提及测试环境。" in text
    assert "你好呀。" in text
    # 不残留浮士德人设
    assert "浮士德" not in text
    assert "FAUST" not in text.upper()


def test_render_role_md_omits_empty_sections(tmp_path):
    path = tmp_path / "bare.json"
    path.write_text(json.dumps({"name": "Bare"}), encoding="utf-8")
    text = character_card.render_role_md(character_card.load_card_file(path).card)

    assert "请扮演角色卡中的角色「Bare」" in text
    assert "- 角色名：Bare" in text
    assert "额外系统约束" not in text
    assert "追加指令" not in text
    assert "对话示例" not in text


def test_render_corememory_md(tmp_path):
    card = character_card.load_card_file(_write_card(tmp_path, "alice.png", _v2_card())).card
    text = character_card.render_corememory_md(card)

    assert "## 初次见面开场白" in text
    assert "你好，我是爱丽丝。" in text
    assert "## 备选开场白" in text
    assert "- 早安。" in text
    assert "作者：tester" in text


def test_suggest_agent_name(tmp_path):
    ascii_card = tmp_path / "ascii.json"
    ascii_card.write_text(json.dumps({"name": "Alice Smith"}), encoding="utf-8")
    assert character_card.suggest_agent_name(character_card.load_card_file(ascii_card).card) == "AliceSmith"

    cjk_card = tmp_path / "cjk.json"
    cjk_card.write_text(json.dumps({"name": "爱丽丝"}), encoding="utf-8")
    suggested = character_card.suggest_agent_name(character_card.load_card_file(cjk_card).card)
    assert suggested.startswith("card-")
    assert character_card._ILLEGAL_NAME_CHARS.search(suggested) is None


# ── 导入编排 ──


def test_import_character_card_writes_persona_lorebook_and_avatar(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    card_path = _write_card(tmp_path, "alice.png", _v2_card())

    report = asyncio.run(admin_runtime.import_character_card(str(card_path), agent_name="alice"))

    agent_dir = tmp_path / "agents" / "alice"
    # AGENT.md 永远是 faust 模板：模型始终收到 FaustBot 核心规则
    assert (agent_dir / "AGENT.md").read_text(encoding="utf-8") == (TEMPLATE_DIR / "AGENT.md").read_text(
        encoding="utf-8"
    )
    assert (agent_dir / "TASK.md").read_text(encoding="utf-8") == (TEMPLATE_DIR / "TASK.md").read_text(
        encoding="utf-8"
    )
    # 人设来自卡片，卡片 system_prompt 只进 ROLE.md
    role = (agent_dir / "ROLE.md").read_text(encoding="utf-8")
    assert "请扮演角色卡中的角色「Alice」" in role
    assert "始终以第二人称称呼用户。" in role
    assert "浮士德" not in role
    core = (agent_dir / "COREMEMORY.md").read_text(encoding="utf-8")
    assert "你好，我是爱丽丝。" in core

    archived = json.loads((agent_dir / "card.json").read_text(encoding="utf-8"))
    assert archived["card"]["data"]["name"] == "Alice"
    assert archived["source_format"] == "png"

    assert report["files"]["AGENT.md"] == "template"
    assert report["files"]["ROLE.md"] == "card"
    assert report["lorebook"] == {
        "book": "Lore",
        "total": 2,
        "enabled": 1,
        "imported": 1,
        "skipped_disabled": 1,
        "error_count": 0,
        "errors": [],
    }
    assert report["avatar"] == {"path": "/images/alice.png"}

    store = GraphStore("alice")
    try:
        node = asyncio.run(store.file_read("/角色卡/Lore/浮空城.md"))
        assert node["content"] == "浮空城建于云端。"
        assert "触发词: 浮空城, city" in node["description"]
        with pytest.raises(FileNotFoundError):
            asyncio.run(store.file_read("/角色卡/Lore/禁用条目.md"))
        attachment = asyncio.run(store.attachment_read("/images/alice.png"))
        assert attachment["content_type"] == "image/png"
        assert base64.b64decode(attachment["content_base64"]) == card_path.read_bytes()
    finally:
        store.close()


def test_import_character_card_rejects_existing_agent(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    card_path = _write_card(tmp_path, "alice.png", _v2_card())
    asyncio.run(admin_runtime.import_character_card(str(card_path), agent_name="alice"))

    with pytest.raises(FileExistsError, match="agent 已存在"):
        asyncio.run(admin_runtime.import_character_card(str(card_path), agent_name="alice"))


def test_import_character_card_surfaces_lorebook_failures(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    card_path = _write_card(tmp_path, "alice.png", _v2_card())

    async def _failing_write(self, path, content, **kwargs):
        raise RuntimeError("嵌入服务不可用")

    monkeypatch.setattr(GraphStore, "file_write", _failing_write)
    report = asyncio.run(admin_runtime.import_character_card(str(card_path), agent_name="alice"))

    assert report["lorebook"]["imported"] == 0
    assert report["lorebook"]["error_count"] == 1
    assert "浮空城" in report["lorebook"]["errors"][0]
    assert "嵌入服务不可用" in report["lorebook"]["errors"][0]
    # 世界书失败不阻断角色创建
    assert (tmp_path / "agents" / "alice" / "ROLE.md").exists()


def test_import_character_card_rolls_back_on_fatal_error(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    card_path = _write_card(tmp_path, "alice.png", _v2_card())

    async def _failing_attachment(self, path, image_base64, **kwargs):
        raise RuntimeError("磁盘写入失败")

    monkeypatch.setattr(GraphStore, "attachment_write", _failing_attachment)
    with pytest.raises(RuntimeError, match="磁盘写入失败"):
        asyncio.run(admin_runtime.import_character_card(str(card_path), agent_name="alice"))

    assert not (tmp_path / "agents" / "alice").exists()


# ── 模板同步守卫 ──


def test_sync_template_files_skips_non_faust(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    agent_dir = tmp_path / "agents" / "alice"
    agent_dir.mkdir(parents=True)
    (agent_dir / "ROLE.md").write_text("卡片人设", encoding="utf-8")

    result = admin_runtime.sync_template_files("alice")

    assert result == {name: False for name in admin_runtime.AGENT_SYNC_FILES}
    assert (agent_dir / "ROLE.md").read_text(encoding="utf-8") == "卡片人设"


def test_sync_template_files_updates_faust(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    faust_dir = tmp_path / "agents" / "faust"
    faust_dir.mkdir(parents=True)
    (faust_dir / "AGENT.md").write_text("旧内容", encoding="utf-8")

    result = admin_runtime.sync_template_files("faust")

    assert result["AGENT.md"] is True
    assert (faust_dir / "AGENT.md").read_text(encoding="utf-8") == (TEMPLATE_DIR / "AGENT.md").read_text(
        encoding="utf-8"
    )


def test_sanitize_agent_name_rejects_dot_segments():
    for bad in (".", "..", "a/b", "名字"):
        with pytest.raises(ValueError):
            admin_runtime._sanitize_agent_name(bad)
