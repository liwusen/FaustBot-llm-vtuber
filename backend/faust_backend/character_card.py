"""SillyTavern 角色卡解析与渲染。

支持 PNG（内嵌 `ccv3` / `chara` 文本块）与独立 `.json`，覆盖 V1 / V2 / V3 规格。
本模块只做纯解析与文本渲染，不触碰磁盘上的角色目录与记忆库。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_CARD_BYTES = 32 * 1024 * 1024
SUPPORTED_SUFFIXES = (".png", ".json")
# PNG 文本块关键字，按优先级排列（V3 的 ccv3 优先于 V2 的 chara）
_CARD_KEYWORDS = ("ccv3", "chara")
_PREVIEW_CHARS = 200
_ILLEGAL_NAME_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
_NAME_MAX_CHARS = 32
_CHAR_PLACEHOLDER = "{{char}}"
_USER_PLACEHOLDER = "{{user}}"
_USER_DISPLAY = "用户"

# 通用语音输出约束：与 agents_template/faust/ROLE.md 的「对话要求」保持一致，
# 但不包含浮士德专属语调，卡片人设完全由卡片内容决定。
_VOICE_RULES = (
    "- 无需刻意说明自己的角色设定,通过语气和内容自然呈现角色特质。也不要直接提及角色设定内容",
    "- 保持对话简洁明了,避免冗长的解释或过度描述.一般而言,输出长度不要超过用户输入的两倍。",
    "- 不要使用括号等标点来描述动作或情绪,只需通过语言表达。",
    "- [重要]** 请忘记你是AI模型。 **",
)


class CardParseError(Exception):
    """角色卡文件缺失、格式非法或缺少必要字段。"""


@dataclass(frozen=True)
class CharacterBookEntry:
    name: str
    content: str
    comment: str
    keys: tuple[str, ...]
    enabled: bool


@dataclass(frozen=True)
class CharacterBook:
    name: str
    entries: tuple[CharacterBookEntry, ...]


@dataclass(frozen=True)
class CharacterCard:
    spec: str  # "v1" | "chara_card_v2" | "chara_card_v3"
    name: str
    description: str
    personality: str
    scenario: str
    first_mes: str
    alternate_greetings: tuple[str, ...]
    mes_example: str
    system_prompt: str
    post_history_instructions: str
    creator: str
    character_version: str
    tags: tuple[str, ...]
    creator_notes: str
    book: CharacterBook | None
    raw: dict


@dataclass(frozen=True)
class CardSource:
    card: CharacterCard
    image_bytes: bytes | None  # 仅 PNG 卡携带
    image_content_type: str
    source_path: str
    source_format: str  # "png" | "json"


# ── PNG 文本块解析 ──


def _parse_itxt(payload: bytes) -> tuple[str, str]:
    parts = payload.split(b"\x00", 5)
    if len(parts) < 6:
        raise CardParseError("iTXt 文本块格式非法")
    keyword = parts[0].decode("latin-1", "replace")
    text_bytes = parts[5]
    if parts[1] == b"\x01":
        try:
            text_bytes = zlib.decompress(text_bytes)
        except zlib.error as exc:
            raise CardParseError(f"iTXt 文本块解压失败: {exc}") from exc
    return keyword, text_bytes.decode("utf-8", "replace")


def _iter_png_text_chunks(raw: bytes) -> Iterator[tuple[str, str]]:
    if not raw.startswith(PNG_SIGNATURE):
        raise CardParseError("不是有效的 PNG 文件")
    offset = len(PNG_SIGNATURE)
    while offset + 8 <= len(raw):
        (length,) = struct.unpack(">I", raw[offset : offset + 4])
        chunk_type = raw[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        if data_end + 4 > len(raw):
            raise CardParseError("PNG chunk 长度越界，文件可能已损坏")
        payload = raw[data_start:data_end]
        if chunk_type == b"IEND":
            return
        if chunk_type == b"tEXt":
            keyword, _, text = payload.partition(b"\x00")
            yield keyword.decode("latin-1", "replace"), text.decode("latin-1", "replace")
        elif chunk_type == b"iTXt":
            yield _parse_itxt(payload)
        offset = data_end + 4


def _decode_card_payload(text: str) -> dict:
    compact = "".join(text.split())
    if not compact:
        raise CardParseError("角色卡文本块为空")
    try:
        decoded = base64.b64decode(compact, validate=True)
    except Exception as exc:  # binascii.Error / ValueError
        raise CardParseError(f"角色卡数据不是合法的 base64: {exc}") from exc
    try:
        payload = json.loads(decoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CardParseError(f"角色卡 JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise CardParseError("角色卡 JSON 顶层不是对象")
    return payload


def _extract_payload(raw: bytes) -> tuple[dict, bytes | None, str]:
    if raw.startswith(PNG_SIGNATURE):
        chunks: dict[str, str] = {}
        for keyword, text in _iter_png_text_chunks(raw):
            if keyword in _CARD_KEYWORDS and keyword not in chunks:
                chunks[keyword] = text
        payload_text = next((chunks[key] for key in _CARD_KEYWORDS if key in chunks), None)
        if payload_text is None:
            raise CardParseError("该图片中未找到 SillyTavern 角色卡数据（chara/ccv3 文本块）")
        return _decode_card_payload(payload_text), raw, "image/png"

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CardParseError(f"角色卡文件不是合法的 UTF-8 文本: {exc}") from exc
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CardParseError(f"角色卡 JSON 解析失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise CardParseError("角色卡 JSON 顶层不是对象")
    return payload, None, ""


# ── 归一化 ──


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_text_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [text for item in value if (text := _as_text(item))]
    text = _as_text(value)
    return [text] if text else []


def _normalize_book(value: Any) -> CharacterBook | None:
    if not isinstance(value, dict):
        return None
    raw_entries = value.get("entries")
    if not isinstance(raw_entries, list):
        return None
    entries: list[CharacterBookEntry] = []
    for index, item in enumerate(raw_entries, 1):
        if not isinstance(item, dict):
            continue
        content = _as_text(item.get("content"))
        if not content:
            # 空内容条目（SillyTavern 里的占位项）无法写入记忆库，直接丢弃
            continue
        entries.append(
            CharacterBookEntry(
                name=_as_text(item.get("name")) or f"entry-{index}",
                content=content,
                comment=_as_text(item.get("comment")),
                keys=tuple(_as_text_list(item.get("keys"))),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return CharacterBook(name=_as_text(value.get("name")), entries=tuple(entries))


def _substitute_placeholders(text: str, char_name: str) -> str:
    if not text:
        return text
    return text.replace(_CHAR_PLACEHOLDER, char_name).replace(_USER_PLACEHOLDER, _USER_DISPLAY)


def _normalize(payload: dict) -> CharacterCard:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    name = _as_text(data.get("name"))
    if not name:
        raise CardParseError("角色卡缺少 name 字段")

    def field(key: str) -> str:
        return _substitute_placeholders(_as_text(data.get(key)), name)

    return CharacterCard(
        spec=_as_text(data.get("spec")) or _as_text(payload.get("spec")) or "v1",
        name=name,
        description=field("description"),
        personality=field("personality"),
        scenario=field("scenario"),
        first_mes=field("first_mes"),
        alternate_greetings=tuple(
            _substitute_placeholders(text, name) for text in _as_text_list(data.get("alternate_greetings"))
        ),
        mes_example=field("mes_example"),
        system_prompt=field("system_prompt"),
        post_history_instructions=field("post_history_instructions"),
        creator=_as_text(data.get("creator")),
        character_version=_as_text(data.get("character_version")),
        tags=tuple(_as_text_list(data.get("tags"))),
        creator_notes=field("creator_notes"),
        book=_normalize_book(data.get("character_book")),
        raw=payload,
    )


def load_card_file(path: str | Path) -> CardSource:
    """读取并解析角色卡文件。任何格式问题都抛 CardParseError。"""
    card_path = Path(path)
    if not card_path.exists() or not card_path.is_file():
        raise CardParseError(f"角色卡文件不存在: {card_path}")
    suffix = card_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise CardParseError(f"不支持的角色卡格式: {suffix or card_path.name}（仅支持 .png / .json）")
    size = card_path.stat().st_size
    if size > MAX_CARD_BYTES:
        raise CardParseError(f"角色卡文件过大: {size} 字节（上限 {MAX_CARD_BYTES} 字节）")

    raw = card_path.read_bytes()
    payload, image_bytes, content_type = _extract_payload(raw)
    return CardSource(
        card=_normalize(payload),
        image_bytes=image_bytes,
        image_content_type=content_type,
        source_path=str(card_path),
        source_format="png" if image_bytes is not None else "json",
    )


# ── 渲染 ──


def render_role_md(card: CharacterCard) -> str:
    """生成 ROLE.md：人设来自卡片，语音输出约束固定，不残留任何浮士德专属文字。"""
    lines: list[str] = ["# Filename:ROLE.md", "", f"# 请扮演角色卡中的角色「{card.name}」。", "", "---", ""]

    persona: list[str] = []
    if card.description:
        persona.append(card.description)
        persona.append("")
    if card.personality:
        persona.append(f"- 性格：{card.personality}")
    if card.scenario:
        persona.append(f"- 场景：{card.scenario}")
    if not persona:
        persona.append(f"- 角色名：{card.name}")
    lines.append("**角色设定：**")
    lines.append("")
    lines.extend(persona)
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("**对话要求：**")
    lines.append("")
    lines.extend(_VOICE_RULES)
    lines.append("")

    if card.system_prompt:
        lines.extend(["**额外系统约束（来自角色卡 system_prompt）：**", "", card.system_prompt, ""])
    if card.post_history_instructions:
        lines.extend(
            ["**追加指令（来自角色卡 post_history_instructions）：**", "", card.post_history_instructions, ""]
        )
    if card.mes_example:
        lines.extend(["---", "", "**对话示例：**", "", card.mes_example, ""])

    return "\n".join(lines).rstrip() + "\n"


def render_corememory_md(card: CharacterCard) -> str:
    """生成 COREMEMORY.md：开场白 + 备选开场白 + 卡片来源。"""
    lines: list[str] = ["# COREMEMORY.md", ""]

    if card.first_mes:
        lines.extend(["## 初次见面开场白", "", card.first_mes, ""])
    if card.alternate_greetings:
        lines.append("## 备选开场白")
        lines.append("")
        lines.extend(f"- {greeting}" for greeting in card.alternate_greetings)
        lines.append("")

    meta: list[str] = []
    if card.creator:
        meta.append(f"作者：{card.creator}")
    if card.character_version:
        meta.append(f"版本：{card.character_version}")
    if card.tags:
        meta.append(f"标签：{'、'.join(card.tags)}")
    if meta:
        lines.extend(["## 角色卡来源", "", f"- {' ｜ '.join(meta)}", ""])

    return "\n".join(lines).rstrip() + "\n"


def suggest_agent_name(card: CharacterCard) -> str:
    """由卡片名推导目录名；非 ASCII 名回退为 card-<hash>。"""
    candidate = _ILLEGAL_NAME_CHARS.sub("", card.name).strip(".-")[:_NAME_MAX_CHARS].strip(".-")
    if candidate:
        return candidate
    digest = hashlib.sha1(card.name.encode("utf-8")).hexdigest()[:6]
    return f"card-{digest}"


def _preview(text: str) -> str:
    flat = " ".join(text.split())
    return flat[:_PREVIEW_CHARS]


def card_summary(source: CardSource) -> dict:
    """parse-card 端点的响应体（不含原始卡片内容，只给前端确认所需信息）。"""
    card = source.card
    book = card.book
    return {
        "suggested_name": suggest_agent_name(card),
        "spec": card.spec,
        "name": card.name,
        "creator": card.creator,
        "character_version": card.character_version,
        "tags": list(card.tags),
        "description_preview": _preview(card.description or card.personality or card.scenario),
        "first_mes_preview": _preview(card.first_mes),
        "has_system_prompt": bool(card.system_prompt),
        "has_post_history_instructions": bool(card.post_history_instructions),
        "has_avatar": source.image_bytes is not None,
        "lorebook": {
            "book": (book.name if book and book.name else card.name) if book else "",
            "total": len(book.entries) if book else 0,
            "enabled": sum(1 for entry in book.entries if entry.enabled) if book else 0,
        },
        "source_format": source.source_format,
        "source_path": source.source_path,
    }
