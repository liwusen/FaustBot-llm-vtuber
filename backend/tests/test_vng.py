from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import faust_backend.agent.vng as vng
import faust_backend.api.vng as vng_api
from faust_backend.agent.runtime import state as runtime_state
from faust_backend.agent.runtime.turn import session_stats


def _notice(mode: str) -> HumanMessage:
    return HumanMessage(
        content=vng.VNG_NOTICE_ON if mode == "on" else vng.VNG_NOTICE_OFF,
        additional_kwargs={"lc_source": vng.MODE_SOURCE, "vng_mode": mode},
    )


class _FakeAgent:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, dict, str | None]] = []

    async def aupdate_state(self, config, update, as_node=None):
        self.calls.append((config, update, as_node))


class _FakeProviders:
    main_model = "test::model"


# ── normalize_choices ──


def test_normalize_choices_valid_and_action():
    items = vng.normalize_choices(
        {"options": ["你刚才那句话是什么意思？", "（沉默）", "算了，换个话题吧。"]}
    )
    assert [item["text"] for item in items] == [
        "你刚才那句话是什么意思？",
        "（沉默）",
        "算了，换个话题吧。",
    ]
    assert [item["is_action"] for item in items] == [False, True, False]


def test_normalize_choices_strips_quotes_and_dedupes():
    items = vng.normalize_choices(
        {"options": ["“你好”", "你好", "  第二句  ", "第三句", "第四句"]}
    )
    assert [item["text"] for item in items] == ["你好", "第二句", "第三句"]


def test_normalize_choices_truncates_and_requires_two():
    long_text = "啊" * 80
    items = vng.normalize_choices({"options": [long_text, "短句"]})
    assert len(items) == 2
    assert items[0]["text"].endswith("…")
    assert len(items[0]["text"]) == vng.MAX_CHOICE_CHARS

    assert vng.normalize_choices({"options": ["只有一条"]}) == []
    assert vng.normalize_choices({"options": []}) == []
    assert vng.normalize_choices({}) == []
    assert vng.normalize_choices(None) == []  # type: ignore[arg-type]


# ── 模式通知 ──


def test_is_and_latest_vng_notice():
    on = _notice("on")
    off = _notice("off")
    plain = HumanMessage(content="你好")
    assert vng.is_vng_notice(on) is True
    assert vng.is_vng_notice(plain) is False
    assert vng.latest_vng_notice([plain, on, AIMessage(content="好")]) == "on"
    assert vng.latest_vng_notice([on, off]) == "off"
    assert vng.latest_vng_notice([]) is None
    assert vng.latest_vng_notice_message([on, off]) is off


def test_append_vng_notice_writes_tail_message():
    agent = _FakeAgent()
    assert asyncio.run(vng.append_vng_notice(agent, True)) is True
    config, update, as_node = agent.calls[0]
    message = update["messages"][0]
    assert isinstance(message, HumanMessage)
    assert message.additional_kwargs["vng_mode"] == "on"
    assert "VNG" in message.content
    assert as_node == "model"
    assert config["configurable"]["thread_id"] == runtime_state.THREAD_ID


@pytest.mark.parametrize(
    ("enabled", "messages", "expect_write", "expect_mode"),
    [
        (True, [HumanMessage(content="hi")], True, "on"),
        (True, [_notice("on")], False, None),
        (True, [_notice("off")], True, "on"),
        (False, [_notice("on")], True, "off"),
        (False, [_notice("off")], False, None),
        (False, [HumanMessage(content="hi")], False, None),
    ],
)
def test_ensure_vng_notice_reconciles(monkeypatch, enabled, messages, expect_write, expect_mode):
    agent = _FakeAgent()

    async def _fake_messages():
        return list(messages)

    monkeypatch.setattr(vng, "get_vng_enabled", lambda: enabled)
    monkeypatch.setattr(session_stats, "get_checkpoint_messages", _fake_messages)

    assert asyncio.run(vng.ensure_vng_notice(agent)) is expect_write
    if not expect_write:
        assert agent.calls == []
        return
    message = agent.calls[0][1]["messages"][0]
    assert message.additional_kwargs["vng_mode"] == expect_mode


# ── 上下文与提示词 ──


def test_render_transcript_filters_notice_and_requires_ai_last():
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="你好"),
        AIMessage(content="你好呀"),
        _notice("on"),
        HumanMessage(content="（沉默）"),
        AIMessage(content="……"),
    ]
    text, reason = vng.render_transcript(messages)
    assert reason is None
    assert "玩家: 你好" in text
    assert "VNG" not in text
    assert text.splitlines()[-1].endswith("……")

    assert vng.render_transcript([]) == ("", "no_messages")
    assert vng.render_transcript([HumanMessage(content="你好")]) == (
        "",
        "last_message_not_assistant",
    )
    assert vng.render_transcript([HumanMessage(content="你好"), AIMessage(content="  ")]) == (
        "",
        "last_message_not_assistant",
    )


def test_render_transcript_honours_char_budget():
    messages = [HumanMessage(content="玩家" * 400) for _ in range(20)]
    messages.append(AIMessage(content="回复" * 400))
    text, reason = vng.render_transcript(messages)
    assert reason is None
    assert len(text) <= vng.TRANSCRIPT_MAX_CHARS


def test_build_choice_messages_contains_persona_and_transcript():
    messages = vng.build_choice_messages("角色设定", "玩家: 你好\nFaust: 你好呀")
    assert isinstance(messages[0], SystemMessage)
    assert "JSON" in messages[0].content
    assert isinstance(messages[1], HumanMessage)
    assert "角色设定" in messages[1].content
    assert "玩家: 你好" in messages[1].content


# ── 生成 ──


def _patch_generation(monkeypatch, *, result=None, delay=0.0, providers=None):
    from faust_backend.llm import formats as llm_formats
    from faust_backend.llm import provider as llm_provider

    monkeypatch.setattr(runtime_state, "get_model_providers", lambda: providers or _FakeProviders())
    monkeypatch.setattr(vng, "_read_role", lambda: "角色设定")

    async def _fake_messages():
        return [HumanMessage(content="你好"), AIMessage(content="你好呀")]

    async def _fake_build(*args, **kwargs):
        return object()

    async def _fake_ainvoke(*args, **kwargs):
        if delay:
            await asyncio.sleep(delay)
        return result

    async def _fake_close(model):
        return None

    monkeypatch.setattr(session_stats, "get_checkpoint_messages", _fake_messages)
    monkeypatch.setattr(llm_provider, "build_main_chat_model", _fake_build)
    monkeypatch.setattr(llm_provider, "get_model_api_format", lambda *a, **k: "openai_chat")
    monkeypatch.setattr(llm_formats, "ainvoke_json", _fake_ainvoke)
    monkeypatch.setattr(llm_formats, "close_chat_model", _fake_close)


def test_generate_choices_ok(monkeypatch):
    _patch_generation(
        monkeypatch,
        result={"options": ["第一句", "第二句", "第三句"]},
    )
    result = asyncio.run(vng.generate_choices())
    assert result["status"] == "ok"
    assert len(result["items"]) == 3
    assert result["model"] == "test::model"


def test_generate_choices_empty_output(monkeypatch):
    _patch_generation(monkeypatch, result={"options": []})
    result = asyncio.run(vng.generate_choices())
    assert result == {"status": "empty", "reason": "model_output_invalid"}


def test_generate_choices_timeout(monkeypatch):
    monkeypatch.setattr(vng, "TIMEOUT_SECONDS", 0.01)
    _patch_generation(
        monkeypatch,
        result={"options": ["第一句", "第二句", "第三句"]},
        delay=0.1,
    )
    result = asyncio.run(vng.generate_choices())
    assert result["status"] == "error"
    assert "超时" in result["error"]


# ── HTTP 适配 ──


def test_vng_endpoint_disabled(monkeypatch):
    monkeypatch.setattr(vng_api, "get_vng_enabled", lambda: False)
    assert asyncio.run(vng_api.vng_choices()) == {"status": "disabled"}


def test_vng_endpoint_live_blocked(monkeypatch):
    monkeypatch.setattr(vng_api, "get_vng_enabled", lambda: True)
    monkeypatch.setattr(vng_api, "is_live_mode", lambda: True)
    assert asyncio.run(vng_api.vng_choices()) == {
        "status": "blocked",
        "reason": "live_mode",
    }


def test_vng_endpoint_runtime_not_ready(monkeypatch):
    monkeypatch.setattr(vng_api, "get_vng_enabled", lambda: True)
    monkeypatch.setattr(vng_api, "is_live_mode", lambda: False)
    monkeypatch.setattr(vng_api.state, "RUNTIME_READY", False)
    monkeypatch.setattr(vng_api.state, "agent", None)
    result = asyncio.run(vng_api.vng_choices())
    assert result["status"] == "error"


def test_vng_endpoint_ok(monkeypatch):
    monkeypatch.setattr(vng_api, "get_vng_enabled", lambda: True)
    monkeypatch.setattr(vng_api, "is_live_mode", lambda: False)
    monkeypatch.setattr(vng_api.state, "RUNTIME_READY", True)
    monkeypatch.setattr(vng_api.state, "agent", object())

    async def _fake_generate():
        return {"status": "ok", "items": [{"text": "第一句", "is_action": False}]}

    monkeypatch.setattr(vng_api.vng, "generate_choices", _fake_generate)
    result = asyncio.run(vng_api.vng_choices())
    assert result["status"] == "ok"


# ── /vng 命令 ──


@pytest.mark.parametrize(
    ("enabled", "current", "live", "expect_set", "expect_append", "expected_text"),
    [
        (True, False, False, True, True, "已进入 VNG（视觉小说）模式。"),
        (True, True, False, False, False, "已在 VNG 模式（/vng off 退出）。"),
        (False, True, False, True, True, "已退出 VNG 模式。"),
        (False, False, False, False, False, "当前不在 VNG 模式。"),
        (True, False, True, False, False, "直播模式下无法进入 VNG 模式。"),
    ],
)
def test_set_vng_mode(monkeypatch, enabled, current, live, expect_set, expect_append, expected_text):
    from faust_backend.api.chat import chat as chat_api
    from faust_backend.core import ui_settings
    from faust_backend.live import mode as live_mode

    written: list[bool] = []
    appended: list[bool] = []

    monkeypatch.setattr(ui_settings, "get_vng_enabled", lambda: current)
    monkeypatch.setattr(ui_settings, "set_vng_enabled", lambda value: written.append(value))
    monkeypatch.setattr(live_mode, "is_live_mode", lambda: live)
    monkeypatch.setattr(chat_api.state, "agent", None)

    async def _fake_append(agent, value):
        appended.append(value)
        return True

    monkeypatch.setattr(vng, "append_vng_notice", _fake_append)

    reply = asyncio.run(chat_api._set_vng_mode(enabled))
    assert reply == expected_text
    assert written == ([enabled] if expect_set else [])
    assert appended == ([enabled] if expect_append else [])


def test_handle_slash_command_routes_vng(monkeypatch):
    from faust_backend.api.chat import chat as chat_api

    captured = {}
    called: list[bool] = []

    async def _fake_dispatch(websocket, factory):
        captured["factory"] = factory
        return True, None

    async def _fake_set(enabled, websocket=None):
        called.append(enabled)
        return "ok"

    monkeypatch.setattr(chat_api, "_dispatch_runtime_command", _fake_dispatch)
    monkeypatch.setattr(chat_api, "_set_vng_mode", _fake_set)

    handled, reply = asyncio.run(chat_api._handle_slash_command("/vng off"))
    assert handled is True and reply is None
    asyncio.run(captured["factory"]())
    assert called == [False]

    handled, reply = asyncio.run(chat_api._handle_slash_command("/vng bad"))
    assert handled is True
    assert "用法" in reply


# ── 压缩保留 ──


def test_compact_replacement_preserves_vng_notice():
    from faust_backend.agent.runtime.compact import CompactMiddleware

    notice = _notice("on")
    messages = [
        SystemMessage(content="system"),
        HumanMessage(content="你好"),
        AIMessage(content="你好呀"),
        notice,
    ]
    middleware = object.__new__(CompactMiddleware)
    replacement = middleware._build_replacement(messages, "summary", preserved=[])
    assert isinstance(replacement[0], SystemMessage)
    assert vng.is_vng_notice(replacement[1])
    assert len(replacement) == 2

    # 已被保留轮次包含时不能再插一份
    deduped = middleware._build_replacement(messages, "summary", preserved=[notice])
    assert all(not vng.is_vng_notice(message) for message in deduped)
    assert len(deduped) == 1
