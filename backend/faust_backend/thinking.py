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

from faust_backend.thinking_caps import (
    GOOGLE_EFFORT_LEVELS,
    MODE_BUDGET,
    MODE_EFFORT,
    MODE_TOGGLE,
    ThinkingOption,
    budget_for_intensity,
    level_for_intensity,
)

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
    # MiMo (小米开放平台) thinking config via extra_body
    "mimo": {#extra_body,thinking_level
        "low":    {"extra_body": {"enable_thinking": True, "thinking_level": "low"}},
        "medium": {"extra_body": {"enable_thinking": True, "thinking_level": "medium"}},
        "high":   {"extra_body": {"enable_thinking": True, "thinking_level": "high"}},
    },
    # Zhipu AI GLM thinking config via extra_body
    "glm": {#extra_body,thinking_level
        "low":    {"extra_body": {"enable_thinking": True, "thinking_level": "low"}},
        "medium": {"extra_body": {"enable_thinking": True, "thinking_level": "medium"}},
        "high":   {"extra_body": {"enable_thinking": True, "thinking_level": "high"}},
    },
    # Minimax reasoning config
    "minimax": {#reasoning_effort
        "low":    {"reasoning_effort": "low"},
        "medium": {"reasoning_effort": "medium"},
        "high":   {"reasoning_effort": "high"},
    },

    "deepseek": {#extra_body,reasoning_effort,low,high,max,
        #reference: https://api-docs.deepseek.com/zh-cn/guides/thinking_mode
        "low":    {"extra_body": {"thinking": {"type": "enabled"}},"reasoning_effort": "low"},
        "medium": {"extra_body": {"thinking": {"type": "enabled"}},"reasoning_effort": "high"},
        "high":   {"extra_body": {"thinking": {"type": "enabled"}},"reasoning_effort": "max"},
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


# Anthropic thinking budget / Gemini thinking budget 的强度映射。
# 这四个格式的思考参数**结构完全不同**，不能把 ChatOpenAI 的
# `reasoning_effort` / `extra_body` 直接塞过去：
#
# - OpenAI Chat      : `reasoning_effort` / `extra_body.enable_thinking`（按预设）
# - OpenAI Responses : `reasoning={"effort": ...}`
# - Anthropic        : `thinking={"type": "enabled", "budget_tokens": N}`
# - Gemini           : `thinking_budget=N`（2.5）或 `reasoning_effort=<等级>`（3+）
#                      + `include_thoughts`
THINKING_BUDGETS = {"low": 1024, "medium": 4096, "high": 8192}

# Anthropic 的 thinking budget 下限（低于它上游直接 400）
ANTHROPIC_MIN_THINKING_BUDGET = 1024


def get_format_thinking_params(
    api_format: str,
    preset_name: str,
    intensity: str = "medium",
    *,
    option: ThinkingOption | None = None,
) -> dict:
    """按 API 格式翻译思考参数。

    `option` 来自 `thinking_caps` 的解析结果（手动覆盖 > `/models` >
    models.dev > 模型名启发式），决定「用等级还是预算」以及支持哪些档位；
    为 None 时保持历史行为（Gemini 恒用 `thinking_budget`）。

    `preset_name == "none"` 时恒为空（该 Provider 强制关闭思考）。
    `openai_chat` 沿用历史预设（qwen/deepseek/mimo/glm/minimax…），其方言由
    Provider 的 `thinking_type` 表达，不按目录数据改写；其余三种格式只使用
    强度 → 各自的官方参数结构，预设名对它们是 OpenAI 兼容网关的方言。
    """
    if str(preset_name or "") == "none":
        return {}
    if api_format == "openai_chat":
        return get_thinking_params(preset_name, intensity)

    effort = intensity if intensity in THINKING_BUDGETS else "medium"
    budget = THINKING_BUDGETS[effort]
    if api_format == "openai_responses":
        level = (
            level_for_intensity(effort, option.levels)
            if option is not None and option.mode == MODE_EFFORT
            else effort
        )
        return {"reasoning": {"effort": level}}
    if api_format == "anthropic_messages":
        if option is not None and option.mode == MODE_BUDGET:
            budget = budget_for_intensity(
                effort,
                option,
                THINKING_BUDGETS,
                floor=ANTHROPIC_MIN_THINKING_BUDGET,
            )
        return {"thinking": {"type": "enabled", "budget_tokens": budget}}
    if api_format == "google_gemini":
        # include_thoughts 让 Gemini 返回思考摘要，前端才能显示 reasoning_delta
        if option is None:
            return {"thinking_budget": budget, "include_thoughts": True}
        if option.mode == MODE_EFFORT:
            # Gemini 3/4 只接受 thinking level；ChatGoogleGenerativeAI 的
            # reasoning_effort 是 Literal，先过滤掉目录里的 xhigh/max 等方言。
            return {
                "reasoning_effort": level_for_intensity(
                    effort, option.levels, allowed=GOOGLE_EFFORT_LEVELS
                ),
                "include_thoughts": True,
            }
        if option.mode == MODE_BUDGET:
            return {
                "thinking_budget": budget_for_intensity(
                    effort, option, THINKING_BUDGETS
                ),
                "include_thoughts": True,
            }
        if option.mode == MODE_TOGGLE:
            return {"include_thoughts": True}
        return {}
    return {}


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
