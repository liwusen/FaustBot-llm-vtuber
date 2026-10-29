"""思考（reasoning）能力解析：从 ``/models`` 与 models.dev 目录推断参数形状。

同一句「开思考」在不同协议里的写法完全不同：Google 可能收 ``thinking_budget``
（Gemini 2.5）也可能只收 ``thinking_level``（Gemini 3/4，传错直接 400），
OpenAI Responses 收 ``reasoning.effort``，Anthropic 只认
``thinking.budget_tokens``。因此构建模型前必须先回答「这个模型支持哪种形状、
支持哪些档位」。

数据来源的优先级（高 → 低）：

1. ``provider.model_thinking_modes[model]``：用户显式覆盖
   （``effort`` / ``budget`` / ``toggle`` / ``none``）；
2. ``provider.model_thinking_caps[model]``：Provider ``/models`` 自报
   （``reasoning_options`` 或 DeepSeek 风格 ``effort.supported_levels``，
   以及 ``reasoning: false``）；
3. ``provider.model_catalog_routes[model]["thinking"]``：models.dev 目录；
4. 模型名启发式：**只用于 Google 格式**（Gemini ≥3 → 等级，2.x/无版本 →
   预算）；其余格式返回「未知」，继续由 Provider 的 ``thinking_type``
   预设决定方言。

能力统一用可持久化的 dict 表示（两处存储同构）：

    {
        "reasoning": True | False | None,
        "options": [
            {"type": "effort", "values": ["low", "medium", "high"]},
            {"type": "budget_tokens", "min": 128, "max": 32768},
            {"type": "toggle"},
        ],
    }

未知/损坏的字段一律丢弃；解析不出任何信息时返回空能力（等价于「没有数据」），
由调用方回退，绝不因此阻塞模型构建。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence

from faust_backend.core.log import get_logger

log = get_logger("faust.thinking_caps")

# 能力选项类型（与 models.dev 的 reasoning_options.type 同词）
MODE_EFFORT = "effort"
MODE_BUDGET = "budget_tokens"
MODE_TOGGLE = "toggle"

OPTION_MODES = (MODE_EFFORT, MODE_BUDGET, MODE_TOGGLE)

# 用户手动覆盖的取值（budget 是用户友好的短名，落库时映射到 MODE_BUDGET）
MANUAL_MODES = ("effort", "budget", "toggle", "none")

# 思考强度从弱到强。xhigh/max/none 只出现在部分 Provider 的目录数据里，
# 不在本项目的三档强度内，但需要参与「最近支持档位」的排序。
LEVEL_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_LEVEL_RANK = {level: index for index, level in enumerate(LEVEL_ORDER)}

# ChatGoogleGenerativeAI 的 reasoning_effort 是 Literal，只接受这四个值；
# 目录里可能出现 xhigh/max（其他厂商方言），必须先过滤再映射。
GOOGLE_EFFORT_LEVELS = ("minimal", "low", "medium", "high")

_GEMINI_VERSION = re.compile(r"gemini[-_ ]?(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ThinkingOption:
    """一种被上游接受的思考参数形状。"""

    mode: str
    levels: tuple[str, ...] = ()
    min_budget: int | None = None
    max_budget: int | None = None


@dataclass(frozen=True)
class ThinkingCapability:
    """某个模型可用的思考形状集合，以及「是否支持推理」的明确结论。"""

    options: tuple[ThinkingOption, ...] = ()
    reasoning: bool | None = None
    #: manual | models_api | models_dev | heuristic（空串 = 未解析）
    source: str = ""

    def option_for(self, api_format: str, *, model: str = "") -> ThinkingOption | None:
        """按 API 格式选出要使用的形状；None 表示「不改变现有行为」。

        - Google：优先等级（Gemini 3+ 或只提供等级时），否则预算，再次 toggle；
          完全没有数据时按模型名启发式给出默认形状。
        - OpenAI Responses：只有 ``effort`` 能改变参数取值。
        - Anthropic：只有 ``budget_tokens`` 值得采用（用于钳制预算）。
        - OpenAI Chat：返回 None，继续由 Provider 的 ``thinking_type`` 预设决定。
        """
        if self.reasoning is False:
            return None

        by_mode = {option.mode: option for option in self.options}
        if api_format == "google_gemini":
            effort = by_mode.get(MODE_EFFORT)
            budget = by_mode.get(MODE_BUDGET)
            major = gemini_major(model)
            if effort is not None and (budget is None or (major or 0) >= 3):
                return effort
            if budget is not None:
                return budget
            if MODE_TOGGLE in by_mode:
                return by_mode[MODE_TOGGLE]
            if not self.options:
                # 没有任何目录/API 数据时的保守回退：Gemini 3/4 用等级，
                # 2.x 与无版本号的模型保持历史行为（预算）。
                return ThinkingOption(
                    MODE_EFFORT if (major or 0) >= 3 else MODE_BUDGET
                )
            return None
        if api_format == "openai_responses":
            return by_mode.get(MODE_EFFORT)
        if api_format == "anthropic_messages":
            return by_mode.get(MODE_BUDGET)
        return None


# ── 解析 ──


def _as_int(value: Any) -> int | None:
    """宽容地取整数；bool 是 int 的子类，必须显式排除。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _dedupe(values: Sequence[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_option(raw: Any) -> dict | None:
    """把一条 ``reasoning_options`` 规范成可持久化的 dict；非法项返回 None。"""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").strip().lower()
    if kind == MODE_EFFORT:
        values = raw.get("values")
        levels = _dedupe(values) if isinstance(values, (list, tuple)) else []
        # 没有 values 时仍保留「该模型用等级」这一形状信息。
        return {"type": MODE_EFFORT, "values": levels}
    if kind in (MODE_BUDGET, "budget"):
        result: dict[str, Any] = {"type": MODE_BUDGET}
        minimum = _as_int(raw.get("min"))
        maximum = _as_int(raw.get("max"))
        if minimum is not None:
            result["min"] = minimum
        if maximum is not None:
            result["max"] = maximum
        return result
    if kind == MODE_TOGGLE:
        return {"type": MODE_TOGGLE}
    return None


def normalize_options(raw: Any) -> list[dict]:
    """规范化 ``reasoning_options`` 列表（models.dev 与 ``/models`` 同构）。"""
    if not isinstance(raw, (list, tuple)):
        return []
    options: list[dict] = []
    for item in raw:
        normalized = _normalize_option(item)
        if normalized is not None:
            options.append(normalized)
    return options


def capability_from_reasoning_options(
    raw: Any, *, reasoning: Any = None
) -> dict | None:
    """由 ``reasoning_options`` 生成可持久化能力；无信息时返回 None。"""
    options = normalize_options(raw)
    flag = reasoning if isinstance(reasoning, bool) else None
    if not options and flag is None:
        return None
    return {"reasoning": flag, "options": options}


def capability_from_models_entry(item: Any) -> dict | None:
    """解析 Provider ``/models`` 的一条模型记录。

    兼容两种自报方式：models.dev 风格的 ``reasoning_options``，以及 DeepSeek
    实测的 ``effort: {"supported_levels": [...]}``；``reasoning: false`` 单独
    记录（没有 options 也要保留，用于关闭思考参数）。
    """
    if not isinstance(item, dict):
        return None
    options = normalize_options(item.get("reasoning_options"))
    if not options:
        effort = item.get("effort")
        if isinstance(effort, dict):
            options = normalize_options(
                [{"type": MODE_EFFORT, "values": effort.get("supported_levels")}]
            )
    return capability_from_reasoning_options(options, reasoning=item.get("reasoning"))


def capability_from_catalog_model(model: Any) -> dict | None:
    """解析 models.dev 目录里的一条模型记录。"""
    if not isinstance(model, dict):
        return None
    return capability_from_reasoning_options(
        model.get("reasoning_options"), reasoning=model.get("reasoning")
    )


def load_capability(raw: Any, *, source: str = "") -> ThinkingCapability:
    """把持久化的能力 dict 还原成对象；损坏的数据降级为空能力。"""
    options: list[ThinkingOption] = []
    reasoning: bool | None = None
    if isinstance(raw, dict):
        flag = raw.get("reasoning")
        reasoning = flag if isinstance(flag, bool) else None
        for item in normalize_options(raw.get("options")):
            options.append(
                ThinkingOption(
                    mode=str(item.get("type") or ""),
                    levels=tuple(item.get("values") or ()),
                    min_budget=item.get("min"),
                    max_budget=item.get("max"),
                )
            )
    return ThinkingCapability(options=tuple(options), reasoning=reasoning, source=source)


# ── 解析优先级 ──


def resolve_thinking_capability(provider: Any, model: str) -> ThinkingCapability:
    """按「手动 > /models > models.dev > 启发式」解析一个模型的能力。"""
    manual = (getattr(provider, "model_thinking_modes", None) or {}).get(model)
    if isinstance(manual, str) and manual.strip():
        mode = manual.strip().lower()
        if mode in MANUAL_MODES:
            if mode == "none":
                return ThinkingCapability(reasoning=False, source="manual")
            kind = MODE_BUDGET if mode == "budget" else mode
            return ThinkingCapability(
                options=(ThinkingOption(kind),), reasoning=True, source="manual"
            )
        log.warning(
            "模型 '%s' 的手动思考格式 '%s' 非法（可用: %s），改按自动推断",
            model,
            manual,
            ", ".join(MANUAL_MODES),
        )

    caps = getattr(provider, "model_thinking_caps", None) or {}
    raw = caps.get(model)
    if isinstance(raw, dict):
        return load_capability(raw, source="models_api")

    route = (getattr(provider, "model_catalog_routes", None) or {}).get(model)
    if isinstance(route, dict):
        raw = route.get("thinking")
        if isinstance(raw, dict):
            return load_capability(raw, source="models_dev")
        # 旧版本配置只有 reasoning 布尔值：保持「明确不支持则不发送」。
        if route.get("reasoning") is False:
            return ThinkingCapability(reasoning=False, source="models_dev")

    return ThinkingCapability(source="heuristic")


# ── 强度映射 ──


def gemini_major(model: str) -> int | None:
    """从模型名取 Gemini 主版本号（``gemini-4-argon`` → 4）；取不到返回 None。"""
    match = _GEMINI_VERSION.search(str(model or ""))
    return int(match.group(1)) if match else None


def level_for_intensity(
    intensity: str,
    levels: Sequence[str],
    *,
    allowed: Sequence[str] | None = None,
) -> str:
    """把本项目的三档强度映射到模型支持的等级。

    精确命中直接使用；否则取「不小于请求等级的最小支持值」；都更弱时取最强
    支持值。这样 ``[low, high, max]`` 的 medium → high，与既有 deepseek
    预设一致，也避免向只支持 high 的模型发 low。
    """
    requested = str(intensity or "medium").strip().lower()
    candidates = [str(level or "").strip().lower() for level in (levels or ())]
    candidates = [level for level in candidates if level]
    if allowed:
        allowed_set = {str(level).strip().lower() for level in allowed}
        candidates = [level for level in candidates if level in allowed_set]
    if not candidates:
        return requested
    if requested in candidates:
        return requested
    requested_rank = _LEVEL_RANK.get(requested, _LEVEL_RANK["medium"])
    ranked = sorted((_LEVEL_RANK.get(level, requested_rank), level) for level in candidates)
    stronger = [item for item in ranked if item[0] >= requested_rank]
    return stronger[0][1] if stronger else ranked[-1][1]


def budget_for_intensity(
    intensity: str,
    option: ThinkingOption | None,
    budgets: dict[str, int],
    *,
    floor: int | None = None,
) -> int:
    """把强度换算成预算 token，并按模型支持的 min/max 钳制。"""
    level = str(intensity or "medium").strip().lower()
    value = int(budgets.get(level, budgets.get("medium", 4096)))
    if option is not None:
        if option.min_budget is not None:
            value = max(value, option.min_budget)
        if option.max_budget is not None:
            value = min(value, option.max_budget)
    if floor is not None:
        value = max(value, floor)
    return value


def describe_thinking(
    capability: ThinkingCapability, api_format: str, *, model: str = ""
) -> dict | None:
    """给管理端/前端展示的解析结果（不含强度，只说明形状与来源）。"""
    if capability.reasoning is False:
        return {"mode": "none", "source": capability.source, "levels": []}
    option = capability.option_for(api_format, model=model)
    if option is None:
        return None
    return {
        "mode": option.mode,
        "source": capability.source,
        "levels": list(option.levels),
    }
