"""Thinking/reasoning preset definitions for multiple AI providers.

Each preset maps intensity levels (low/medium/high) to kwargs suitable
for passing to LangChain ChatOpenAI.  Only the main agent uses these;
Araya and security checker are unaffected.
"""

from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI
from langchain_core.messages.ai import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

THINKING_PRESETS = {
    "none": {},
    "openai": {#reasoning_effort only, no extra_body
        "low":    {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "medium"},
        "high":   {"reasoning_effort": "high"},
    },
    # Qwen (Alibaba Cloud Bailian) passes thinking config
    # through extra_body per provider docs.
    "qwen": {#extra_body,thinking_level
        "low":    {"extra_body": {"enable_thinking": True, "thinking_level": "low"}},
        "medium": {"extra_body": {"enable_thinking": True, "thinking_level": "medium"}},
        "high":   {"extra_body": {"enable_thinking": True, "thinking_level": "high"}},
    },
    # DeepSeek enables thinking via extra_body too.
    "deepseek": {#extra_body,reasoning_effort
        "low":    {"extra_body": {"thinking": {"type": "enabled"}},"reasoning_effort": "high"},
        "medium": {"extra_body": {"thinking": {"type": "enabled"}},"reasoning_effort": "high"},
        "high":   {"extra_body": {"thinking": {"type": "enabled"}},"reasoning_effort": "high"},
    },
}


def get_thinking_params(preset_name: str, intensity: str = "medium") -> dict:
    """Return merged kwargs dict for ChatOpenAI.

    Keys may include ``reasoning_effort``, ``extra_body``, etc.
    When the preset is unrecognised, ``"none"`` (or empty dict) is
    used as a safe fallback.
    """
    preset = THINKING_PRESETS.get(preset_name, THINKING_PRESETS.get("none", {}))
    if isinstance(preset, dict) and "low" in preset:
        # Intensity-based preset (openai / qwen / deepseek).
        return dict(preset.get(intensity, preset.get("medium", {})))
    return dict(preset)


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass that preserves ``reasoning_content`` from the
    raw streaming response in ``additional_kwargs``.

    ``langchain-openai`` 1.x deliberately discards non-standard response
    fields such as ``reasoning_content`` (used by OpenAI o1/o3, DeepSeek
    R1, etc.).  This wrapper intercepts ``_convert_chunk_to_generation_chunk``
    and copies ``reasoning_content`` (and fallback keys ``reasoning``,
    ``think``) into the ``AIMessageChunk.additional_kwargs`` so that
    downstream code (e.g. ``stream_chat_agent_events``) can yield
    ``reasoning_delta`` events.
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if generation_chunk is None:
            return None
        # Extract reasoning_content from the raw API delta
        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
        if choices:
            delta = choices[0].get("delta", {})
            if isinstance(delta, dict):
                reasoning = (
                    delta.get("reasoning_content")
                    or delta.get("reasoning")
                    or delta.get("think")
                )
                if reasoning:
                    msg = generation_chunk.message
                    if isinstance(msg, AIMessageChunk):
                        msg.additional_kwargs["reasoning_content"] = reasoning
        return generation_chunk
