"""思考能力解析：/models 与 models.dev 数据 → 参数形状/档位。

全部使用虚构数据；不发网络请求。覆盖解析、优先级、档位映射与预算钳制。
"""

from __future__ import annotations

import pytest

from faust_backend.provider import ModelProvider
from faust_backend.thinking_caps import (
    MODE_BUDGET,
    MODE_EFFORT,
    MODE_TOGGLE,
    ThinkingCapability,
    ThinkingOption,
    budget_for_intensity,
    capability_from_catalog_model,
    capability_from_models_entry,
    capability_from_reasoning_options,
    describe_thinking,
    gemini_major,
    level_for_intensity,
    load_capability,
    resolve_thinking_capability,
)

BUDGETS = {"low": 1024, "medium": 4096, "high": 8192}


# ── 解析 ──


def test_catalog_effort_options():
    capability = capability_from_catalog_model(
        {
            "reasoning": True,
            "reasoning_options": [
                {"type": "effort", "values": ["low", "medium", "high"]}
            ],
        }
    )
    assert capability == {
        "reasoning": True,
        "options": [{"type": MODE_EFFORT, "values": ["low", "medium", "high"]}],
    }


def test_catalog_budget_options_keep_bounds():
    capability = capability_from_catalog_model(
        {
            "reasoning": True,
            "reasoning_options": [
                {"type": "budget_tokens", "min": 128, "max": 32768}
            ],
        }
    )
    assert capability is not None
    assert capability["options"] == [
        {"type": MODE_BUDGET, "min": 128, "max": 32768}
    ]


def test_catalog_toggle_and_unknown_types():
    capability = capability_from_catalog_model(
        {
            "reasoning": True,
            "reasoning_options": [
                {"type": "toggle"},
                {"type": "quantum_flux", "values": ["x"]},
                "not-a-dict",
            ],
        }
    )
    assert capability is not None
    assert capability["options"] == [{"type": MODE_TOGGLE}]


def test_catalog_reasoning_false_without_options():
    assert capability_from_catalog_model({"reasoning": False}) == {
        "reasoning": False,
        "options": [],
    }


def test_catalog_without_any_signal_returns_none():
    assert capability_from_catalog_model({"reasoning": None}) is None
    assert capability_from_catalog_model({}) is None
    assert capability_from_catalog_model(None) is None


def test_models_entry_deepseek_effort_shape():
    capability = capability_from_models_entry(
        {
            "id": "deepseek-flash",
            "effort": {"supported_levels": ["low", "high", "max"], "default_level": "high"},
        }
    )
    assert capability == {
        "reasoning": None,
        "options": [{"type": MODE_EFFORT, "values": ["low", "high", "max"]}],
    }


def test_models_entry_reasoning_options_take_priority_over_effort():
    capability = capability_from_models_entry(
        {
            "reasoning_options": [{"type": "budget_tokens", "max": 8192}],
            "effort": {"supported_levels": ["low"]},
        }
    )
    assert capability is not None
    assert capability["options"] == [{"type": MODE_BUDGET, "max": 8192}]


def test_models_entry_without_metadata_returns_none():
    assert capability_from_models_entry({"id": "m"}) is None
    assert capability_from_models_entry({"reasoning": True}) == {
        "reasoning": True,
        "options": [],
    }


def test_reasoning_options_ignore_blank_and_duplicate_levels():
    capability = capability_from_reasoning_options(
        [{"type": "effort", "values": [" High ", "high", "", None, "LOW"]}],
        reasoning=True,
    )
    assert capability is not None
    assert capability["options"][0]["values"] == ["high", "low"]


# ── 按格式选形状 ──


def test_gemini_4_prefers_effort_when_both_available():
    capability = load_capability(
        {
            "reasoning": True,
            "options": [
                {"type": MODE_EFFORT, "values": ["low", "medium", "high"]},
                {"type": MODE_BUDGET, "max": 32768},
            ],
        },
        source="models_dev",
    )
    option = capability.option_for("google_gemini", model="gemini-4-argon")
    assert option is not None
    assert option.mode == MODE_EFFORT


def test_gemini_2_5_uses_budget_when_both_available():
    capability = load_capability(
        {
            "reasoning": True,
            "options": [
                {"type": MODE_EFFORT, "values": ["low", "medium", "high"]},
                {"type": MODE_BUDGET, "min": 128, "max": 32768},
            ],
        },
        source="models_dev",
    )
    option = capability.option_for("google_gemini", model="gemini-2.5-pro")
    assert option is not None
    assert option.mode == MODE_BUDGET


def test_gemini_toggle_only():
    capability = load_capability(
        {"reasoning": True, "options": [{"type": MODE_TOGGLE}]}, source="models_dev"
    )
    option = capability.option_for("google_gemini", model="gemini-4-argon")
    assert option is not None
    assert option.mode == MODE_TOGGLE


def test_google_without_data_falls_back_by_model_generation():
    empty = ThinkingCapability(source="heuristic")
    assert empty.option_for("google_gemini", model="gemini-4-argon").mode == MODE_EFFORT
    assert empty.option_for("google_gemini", model="gemini-3-pro").mode == MODE_EFFORT
    assert empty.option_for("google_gemini", model="gemini-2.5-pro").mode == MODE_BUDGET
    # 无版本号 → 保守沿用预算（历史行为）
    assert empty.option_for("google_gemini", model="gemini-model").mode == MODE_BUDGET


def test_non_google_formats_ignore_budget_options():
    capability = load_capability(
        {"reasoning": True, "options": [{"type": MODE_BUDGET, "max": 8192}]},
        source="models_dev",
    )
    assert capability.option_for("openai_responses", model="m") is None
    assert capability.option_for("openai_chat", model="m") is None


def test_responses_uses_effort_option():
    capability = load_capability(
        {"reasoning": True, "options": [{"type": MODE_EFFORT, "values": ["high"]}]},
        source="models_api",
    )
    option = capability.option_for("openai_responses", model="m")
    assert option is not None and option.mode == MODE_EFFORT


def test_reasoning_false_never_returns_option():
    capability = load_capability(
        {"reasoning": False, "options": [{"type": MODE_EFFORT}]}, source="models_api"
    )
    assert capability.option_for("google_gemini", model="gemini-4-argon") is None
    assert capability.option_for("openai_responses", model="m") is None


# ── 优先级 ──


def _provider(**overrides) -> ModelProvider:
    defaults = dict(name="GW", base_url="https://gw.example.com/v1", key="sk-test")
    defaults.update(overrides)
    return ModelProvider(**defaults)


def test_manual_mode_wins_over_all_sources():
    provider = _provider(
        model_thinking_modes={"m": "budget"},
        model_thinking_caps={
            "m": {"reasoning": True, "options": [{"type": MODE_EFFORT}]}
        },
        model_catalog_routes={
            "m": {
                "thinking": {
                    "reasoning": True,
                    "options": [{"type": MODE_TOGGLE}],
                }
            }
        },
    )
    capability = resolve_thinking_capability(provider, "m")
    assert capability.source == "manual"
    assert capability.options[0].mode == MODE_BUDGET


def test_manual_none_disables_thinking():
    provider = _provider(model_thinking_modes={"m": "none"})
    capability = resolve_thinking_capability(provider, "m")
    assert capability.reasoning is False
    assert capability.option_for("google_gemini", model="gemini-4-argon") is None


def test_invalid_manual_mode_falls_back_to_caps():
    provider = _provider(
        model_thinking_modes={"m": "wat"},
        model_thinking_caps={
            "m": {"reasoning": True, "options": [{"type": MODE_BUDGET}]}
        },
    )
    capability = resolve_thinking_capability(provider, "m")
    assert capability.source == "models_api"
    assert capability.options[0].mode == MODE_BUDGET


def test_models_api_wins_over_catalog():
    provider = _provider(
        model_thinking_caps={
            "m": {"reasoning": True, "options": [{"type": MODE_BUDGET, "max": 8192}]}
        },
        model_catalog_routes={
            "m": {
                "thinking": {
                    "reasoning": True,
                    "options": [{"type": MODE_EFFORT}],
                }
            }
        },
    )
    capability = resolve_thinking_capability(provider, "m")
    assert capability.source == "models_api"
    assert capability.options[0].mode == MODE_BUDGET


def test_catalog_used_when_no_api_data():
    provider = _provider(
        model_catalog_routes={
            "m": {
                "thinking": {
                    "reasoning": True,
                    "options": [{"type": MODE_EFFORT, "values": ["low", "high"]}],
                }
            }
        }
    )
    capability = resolve_thinking_capability(provider, "m")
    assert capability.source == "models_dev"
    assert capability.options[0].levels == ("low", "high")


def test_legacy_catalog_reasoning_false_still_disables():
    provider = _provider(model_catalog_routes={"m": {"reasoning": False}})
    capability = resolve_thinking_capability(provider, "m")
    assert capability.reasoning is False
    assert capability.source == "models_dev"


def test_no_data_yields_heuristic_source():
    capability = resolve_thinking_capability(_provider(), "m")
    assert capability.source == "heuristic"
    assert capability.options == ()


# ── 档位与预算映射 ──


def test_gemini_major_parsing():
    assert gemini_major("gemini-4-argon") == 4
    assert gemini_major("google/gemini-2.5-pro") == 2
    assert gemini_major("gemini-3-flash") == 3
    assert gemini_major("claude-haiku-5-5") is None


@pytest.mark.parametrize(
    ("levels", "intensity", "expected"),
    [
        (["low", "high", "max"], "medium", "high"),
        (["high", "max"], "low", "high"),
        (["max"], "medium", "max"),
        (["low", "medium", "high"], "medium", "medium"),
        (["low", "medium", "high"], "high", "high"),
        ([], "medium", "medium"),
    ],
)
def test_level_mapping_picks_nearest_supported(levels, intensity, expected):
    assert level_for_intensity(intensity, levels) == expected


def test_google_level_mapping_filters_unsupported_literals():
    # xhigh/max 不是 ChatGoogleGenerativeAI 的合法 Literal → 丢弃后再映射
    assert (
        level_for_intensity("medium", ["high", "max"], allowed=("minimal", "low", "medium", "high"))
        == "high"
    )
    # 过滤后没有候选 → 保持请求档位，交给 SDK 的默认校验
    assert (
        level_for_intensity("medium", ["max"], allowed=("minimal", "low", "medium", "high"))
        == "medium"
    )


def test_budget_clamped_to_model_bounds():
    option = ThinkingOption(MODE_BUDGET, min_budget=8192, max_budget=32768)
    assert budget_for_intensity("low", option, BUDGETS) == 8192
    option_cap = ThinkingOption(MODE_BUDGET, max_budget=1024)
    assert budget_for_intensity("high", option_cap, BUDGETS) == 1024


def test_budget_floor_applied_last():
    option = ThinkingOption(MODE_BUDGET, max_budget=512)
    assert budget_for_intensity("medium", option, BUDGETS, floor=1024) == 1024


# ── 展示 ──


def test_describe_thinking_reports_mode_and_source():
    capability = load_capability(
        {"reasoning": True, "options": [{"type": MODE_EFFORT, "values": ["low", "high"]}]},
        source="models_dev",
    )
    described = describe_thinking(capability, "google_gemini", model="gemini-4-argon")
    assert described == {
        "mode": MODE_EFFORT,
        "source": "models_dev",
        "levels": ["low", "high"],
    }


def test_describe_thinking_disabled():
    capability = load_capability({"reasoning": False}, source="manual")
    assert describe_thinking(capability, "google_gemini", model="gemini-4-argon") == {
        "mode": "none",
        "source": "manual",
        "levels": [],
    }


def test_describe_thinking_returns_none_without_shape():
    capability = ThinkingCapability(source="heuristic")
    assert describe_thinking(capability, "openai_chat", model="m") is None
