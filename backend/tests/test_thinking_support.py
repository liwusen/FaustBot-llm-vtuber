"""
Unit tests for Plan 07 — LLM thinking/reasoning support.

Covers:
- thinking.py preset dictionary and get_thinking_params()
- ReasoningChatOpenAI subclass — reasoning_content preservation
- lifecycle.py _build_chat_model() thinking param merging + class selection
- lifecycle.py stream_chat_agent_events() reasoning_delta extraction

Run: python -m pytest backend/tests/test_thinking_support.py -v
"""

from __future__ import annotations

import os
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from faust_backend.thinking import (
    THINKING_PRESETS,
    ReasoningChatOpenAI,
    get_thinking_params,
)
from langchain_core.messages.ai import AIMessageChunk


# ============================================================
# ReasoningChatOpenAI — reasoning_content preservation
# ============================================================

class TestReasoningChatOpenAI:
    """Verify that the subclass preserves reasoning_content from the raw
    API delta into AIMessageChunk.additional_kwargs."""

    default_chunk_class = AIMessageChunk

    def test_reasoning_content_in_additional_kwargs(self):
        llm = ReasoningChatOpenAI(
            model="gpt-4o", api_key="sk-test", base_url="https://test.api/v1",
        )
        raw_chunk = {
            "choices": [{"delta": {"content": None, "reasoning_content": "I think step by step..."}}],
            "object": "chat.completion.chunk",
        }
        gc = llm._convert_chunk_to_generation_chunk(
            raw_chunk, self.default_chunk_class, None
        )
        assert gc is not None
        msg = gc.message
        assert isinstance(msg, AIMessageChunk)
        assert msg.additional_kwargs.get("reasoning_content") == "I think step by step..."

    def test_no_reasoning_content(self):
        llm = ReasoningChatOpenAI(
            model="gpt-4o", api_key="sk-test", base_url="https://test.api/v1",
        )
        raw_chunk = {"choices": [{"delta": {"content": "hello"}}], "object": "chat.completion.chunk"}
        gc = llm._convert_chunk_to_generation_chunk(raw_chunk, self.default_chunk_class, None)
        assert gc is not None
        msg = gc.message
        assert "reasoning_content" not in msg.additional_kwargs

    def test_choices_empty_does_not_crash(self):
        llm = ReasoningChatOpenAI(
            model="gpt-4o", api_key="sk-test", base_url="https://test.api/v1",
        )
        gc = llm._convert_chunk_to_generation_chunk({"choices": []}, self.default_chunk_class, None)
        assert gc is not None

    def test_delta_none_does_not_crash(self):
        llm = ReasoningChatOpenAI(
            model="gpt-4o", api_key="sk-test", base_url="https://test.api/v1",
        )
        gc = llm._convert_chunk_to_generation_chunk(
            {"choices": [{"delta": None}], "object": "chat.completion.chunk"},
            self.default_chunk_class, None,
        )
        assert gc is None


# ============================================================
# _build_chat_model() — thinking param merging + class selection
# ============================================================

class TestBuildChatModel:
    """Verify that _build_chat_model() chooses the right class and passes
    correct kwargs, driven by the ModelProviders instance (provider::model spec).

    We patch build_ReasoningChatOpenAI_from_spec to capture usage counts
    and kwargs.
    """

    def _call_and_capture(self, **overrides: Any) -> dict:
        """Build a fake ModelProviders and call _build_chat_model."""
        import asyncio
        from unittest.mock import patch

        from faust_backend.provider import ModelProviders, new_provider
        from faust_backend.runtime.lifecycle import _build_chat_model

        providers = ModelProviders()
        new_provider(providers, "test", "https://test.api/v1", "test-key")
        providers.main_model = "test::gpt-4o"

        captured: dict[str, Any] = {}

        async def fake_build(provs, spec="", intensity=None):
            captured["spec"] = spec
            captured["intensity"] = intensity
            return {"spec": spec, "intensity": intensity}

        with patch(
            "faust_backend.provider.build_ReasoningChatOpenAI_from_spec",
            side_effect=fake_build,
        ) as mock_build, patch(
            "faust_backend.runtime.state.get_model_providers",
            return_value=providers,
        ):
            result = asyncio.run(_build_chat_model(model_name="test::gpt-4o"))
            return {"mock_build": mock_build, "captured": captured, "result": result}

    def test_thinking_enabled_by_default_provider(self):
        info = self._call_and_capture()
        assert info["captured"]["intensity"] == "medium"  # thinking_type != none

    def test_thinking_disabled_when_type_none(self):
        from faust_backend.provider import ModelProviders, new_provider
        from faust_backend.runtime.lifecycle import _build_chat_model
        import asyncio
        from unittest.mock import patch

        providers = ModelProviders()
        new_provider(providers, "test", "https://test.api/v1", "test-key")
        providers.providers[0].thinking_type = "none"
        providers.main_model = "test::gpt-4o"

        captured: dict[str, Any] = {}

        async def fake_build(provs, spec="", intensity=None):
            captured["intensity"] = intensity
            return object()

        with patch(
            "faust_backend.provider.build_ReasoningChatOpenAI_from_spec",
            side_effect=fake_build,
        ), patch(
            "faust_backend.runtime.state.get_model_providers",
            return_value=providers,
        ):
            asyncio.run(_build_chat_model(model_name="test::gpt-4o"))
        assert captured["intensity"] is None  # thinking 关闭

    def test_spec_passed_through(self):
        info = self._call_and_capture()
        assert info["captured"]["spec"] == "test::gpt-4o"

    def test_missing_main_model_raises(self):
        from faust_backend.provider import ModelProviders
        from faust_backend.runtime.lifecycle import _build_chat_model
        import asyncio
        from unittest.mock import patch

        providers = ModelProviders()

        with patch(
            "faust_backend.runtime.state.get_model_providers",
            return_value=providers,
        ):
            try:
                asyncio.run(_build_chat_model(model_name=None))
                assert False, "should raise"
            except RuntimeError as e:
                assert "main_model" in str(e)


# ============================================================
# stream_chat_agent_events() — reasoning_delta extraction
# ============================================================

class TestStreamReasoningDelta:
    """Verify that reasoning content is properly extracted from AIMessageChunk."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        import faust_backend.config_loader as conf
        for key in ("CHAT_API_KEY", "CHAT_API_BASE", "THINKING_ENABLED",
                     "THINKING_PRESET", "THINKING_INTENSITY"):
            if not hasattr(conf, key):
                setattr(conf, key, "" if "KEY" in key or "BASE" in key or "PRESET" in key or "INTENSITY" in key else False)
        yield

    def _make_mock_chunk(self, content: str = "", additional_kwargs: dict | None = None) -> MagicMock:
        chunk = MagicMock()
        chunk.type = "ai"
        chunk.content = content
        chunk.additional_kwargs = additional_kwargs or {}
        return chunk

    def _mock_agent_stream(self, events: list[dict]) -> AsyncMock:
        agent = AsyncMock()
        async def _stream(*args, **kwargs):
            for evt in events:
                yield evt
        agent.astream_events = _stream
        return agent

    @pytest.mark.asyncio
    async def test_reasoning_content_yields_reasoning_delta(self):
        from faust_backend.runtime.lifecycle import stream_chat_agent_events
        chunk = self._make_mock_chunk(content="Hello", additional_kwargs={"reasoning_content": "I think..."})
        agent = self._mock_agent_stream([
            {"event": "on_chat_model_stream", "data": {"chunk": chunk}, "name": "", "run_id": ""}
        ])
        results = []
        async for evt in stream_chat_agent_events(agent, {"messages": []}):
            results.append(evt)
        reasoning_events = [e for e in results if e["type"] == "reasoning_delta"]
        delta_events = [e for e in results if e["type"] == "delta"]
        assert len(reasoning_events) == 1
        assert reasoning_events[0]["content"] == "I think..."
        assert len(delta_events) == 1
        assert delta_events[0]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_no_reasoning_only_delta(self):
        from faust_backend.runtime.lifecycle import stream_chat_agent_events
        chunk = self._make_mock_chunk(content="Only text")
        agent = self._mock_agent_stream([
            {"event": "on_chat_model_stream", "data": {"chunk": chunk}, "name": "", "run_id": ""}
        ])
        results = []
        async for evt in stream_chat_agent_events(agent, {"messages": []}):
            results.append(evt)
        reasoning_events = [e for e in results if e["type"] == "reasoning_delta"]
        delta_events = [e for e in results if e["type"] == "delta"]
        assert len(reasoning_events) == 0
        assert len(delta_events) == 1
        assert delta_events[0]["content"] == "Only text"

    @pytest.mark.asyncio
    async def test_reasoning_fallback_keys(self):
        from faust_backend.runtime.lifecycle import stream_chat_agent_events
        chunk1 = self._make_mock_chunk(content="A", additional_kwargs={"reasoning": "think..."})
        chunk2 = self._make_mock_chunk(content="B", additional_kwargs={"think": "ponder..."})
        agent = self._mock_agent_stream([
            {"event": "on_chat_model_stream", "data": {"chunk": chunk1}, "name": "", "run_id": ""},
            {"event": "on_chat_model_stream", "data": {"chunk": chunk2}, "name": "", "run_id": ""},
        ])
        results = []
        async for evt in stream_chat_agent_events(agent, {"messages": []}):
            results.append(evt)
        reasoning_events = [e for e in results if e["type"] == "reasoning_delta"]
        assert len(reasoning_events) == 2
        assert reasoning_events[0]["content"] == "think..."
        assert reasoning_events[1]["content"] == "ponder..."

    @pytest.mark.asyncio
    async def test_empty_content_skipped(self):
        from faust_backend.runtime.lifecycle import stream_chat_agent_events
        chunk = self._make_mock_chunk(content=None)
        agent = self._mock_agent_stream([
            {"event": "on_chat_model_stream", "data": {"chunk": chunk}, "name": "", "run_id": ""}
        ])
        results = []
        async for evt in stream_chat_agent_events(agent, {"messages": []}):
            results.append(evt)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_non_ai_chunk_skipped(self):
        from faust_backend.runtime.lifecycle import stream_chat_agent_events
        chunk = MagicMock()
        chunk.type = "human"
        chunk.content = "skip me"
        agent = self._mock_agent_stream([
            {"event": "on_chat_model_stream", "data": {"chunk": chunk}, "name": "", "run_id": ""}
        ])
        results = []
        async for evt in stream_chat_agent_events(agent, {"messages": []}):
            results.append(evt)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_non_dict_events_skipped(self):
        from faust_backend.runtime.lifecycle import stream_chat_agent_events
        agent = self._mock_agent_stream(["string_event", 42])
        results = []
        async for evt in stream_chat_agent_events(agent, {"messages": []}):
            results.append(evt)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_additional_kwargs_none_does_not_crash(self):
        from faust_backend.runtime.lifecycle import stream_chat_agent_events
        chunk = MagicMock()
        chunk.type = "ai"
        chunk.content = "safe"
        chunk.additional_kwargs = None
        agent = self._mock_agent_stream([
            {"event": "on_chat_model_stream", "data": {"chunk": chunk}, "name": "", "run_id": ""}
        ])
        results = []
        async for evt in stream_chat_agent_events(agent, {"messages": []}):
            results.append(evt)
        delta_events = [e for e in results if e["type"] == "delta"]
        assert len(delta_events) == 1
        assert delta_events[0]["content"] == "safe"
