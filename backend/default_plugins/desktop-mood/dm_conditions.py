"""规则条件：通用原语 + 兼容旧的专用类型。

原语（field 支持一层点号路径，如 `battery.percent`、`app_session.seconds`）：
    field_over / field_under / field_eq / field_contains / field_in / field_changed

通用修饰（引擎层处理，见 impl.py）：
    probability         0~1，命中后再过一次概率
    for_seconds         条件需连续满足 N 秒才触发
    when_not_disturbed  true 时被免打扰闸门挡住就不触发（speech/nimble 默认受闸门约束）

旧专用类型（保持可用）：idle_over / return_active / cpu_over / memory_over /
battery_under / hour_range / window_contains / smtc_playing。
"""

from __future__ import annotations

from typing import Any

LEGACY_CONDITION_TYPES = (
    'idle_over', 'return_active', 'cpu_over', 'memory_over',
    'battery_under', 'hour_range', 'window_contains', 'smtc_playing',
)
PRIMITIVE_CONDITION_TYPES = (
    'field_over', 'field_under', 'field_eq', 'field_contains', 'field_in', 'field_changed',
)
CONDITION_TYPES = LEGACY_CONDITION_TYPES + PRIMITIVE_CONDITION_TYPES


def get_path(context: dict[str, Any], path: str) -> tuple[bool, Any]:
    """按点号路径取值；返回 (是否存在, 值)。"""
    node: Any = context
    for part in str(path or '').split('.'):
        if not isinstance(node, dict) or part not in node:
            return False, None
        node = node[part]
    return True, node


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _compare(condition: dict[str, Any], context: dict[str, Any], op: str) -> bool:
    found, value = get_path(context, str(condition.get('field') or ''))
    if not found:
        return False
    if op == 'eq':
        expected = condition.get('value')
        if isinstance(expected, bool) or isinstance(value, bool):
            return bool(value) == bool(expected)
        if isinstance(expected, (int, float)) and not isinstance(expected, bool):
            number = _number(value)
            return number is not None and abs(number - float(expected)) < 1e-9
        return str(value) == str(expected)
    left, right = _number(value), _number(condition.get('value'))
    if left is None or right is None:
        return False
    return left >= right if op == 'over' else left <= right


def evaluate(condition: dict[str, Any], context: dict[str, Any], edges: dict[str, Any] | None = None) -> bool:
    """单个条件是否命中。字段缺失一律 False（感知被关掉时规则自然不触发）。"""
    condition = condition or {}
    ctype = str(condition.get('type') or '')
    edges = edges or {}
    if ctype == 'idle_over':
        return _compare({'field': 'idle_seconds', 'value': condition.get('seconds')}, context, 'over')
    if ctype == 'return_active':
        return edges.get('idle_prev') == 'idle' and edges.get('idle_next') == 'active'
    if ctype == 'cpu_over':
        return _compare({'field': 'cpu', 'value': condition.get('value')}, context, 'over')
    if ctype == 'memory_over':
        return _compare({'field': 'memory', 'value': condition.get('value')}, context, 'over')
    if ctype == 'battery_under':
        charging_found, charging = get_path(context, 'battery.charging')
        return (not (charging_found and bool(charging))
                and _compare({'field': 'battery.percent', 'value': condition.get('value')}, context, 'under'))
    if ctype == 'hour_range':
        found, hour = get_path(context, 'hour')
        if not found:
            return False
        return int(condition.get('start') or 0) <= int(hour or 0) <= int(condition.get('end') or 23)
    if ctype == 'window_contains':
        needle = str(condition.get('value') or '').lower()
        return bool(needle) and needle in str(context.get('window_title') or '').lower()
    if ctype == 'smtc_playing':
        return bool(edges.get('smtc_playing'))

    if ctype == 'field_over':
        return _compare(condition, context, 'over')
    if ctype == 'field_under':
        return _compare(condition, context, 'under')
    if ctype == 'field_eq':
        return _compare(condition, context, 'eq')
    if ctype == 'field_contains':
        found, value = get_path(context, str(condition.get('field') or ''))
        if not found:
            return False
        needle = str(condition.get('value') or '').lower()
        if not needle:
            return False
        if isinstance(value, (list, tuple, set)):
            return any(needle in str(item).lower() for item in value)
        if isinstance(value, dict):
            return any(needle in str(item).lower() for item in value.values())
        return needle in str(value).lower()
    if ctype == 'field_in':
        found, value = get_path(context, str(condition.get('field') or ''))
        if not found:
            return False
        options = condition.get('values')
        if not isinstance(options, (list, tuple)):
            return False
        if isinstance(value, (list, tuple, set)):
            return any(str(item) in {str(opt) for opt in options} for item in value)
        return str(value) in {str(opt) for opt in options}
    if ctype == 'field_changed':
        path = str(condition.get('field') or '')
        was_found, previous = get_path(context.get('_previous') or {}, path)
        now_found, current = get_path(context, path)
        if not was_found or not now_found:
            return False
        return previous != current
    return False


def validate(condition: Any) -> str | None:
    """结构校验，返回错误说明或 None。"""
    if not isinstance(condition, dict):
        return 'condition 必须是 JSON 对象'
    ctype = condition.get('type')
    if ctype not in CONDITION_TYPES:
        return f'condition.type 非法: {ctype!r}（支持: {", ".join(CONDITION_TYPES)}）'
    if ctype in PRIMITIVE_CONDITION_TYPES:
        field = condition.get('field')
        if not isinstance(field, str) or not field.strip():
            return f'{ctype} 需要非空字符串字段 field'
        if ctype in ('field_over', 'field_under'):
            if _number(condition.get('value')) is None:
                return f'{ctype} 需要数值字段 value'
        elif ctype in ('field_eq', 'field_contains'):
            if condition.get('value') in (None, ''):
                return f'{ctype} 需要字段 value'
        elif ctype == 'field_in':
            if not isinstance(condition.get('values'), (list, tuple)) or not condition['values']:
                return 'field_in 需要非空数组字段 values'
    if 'for_seconds' in condition and _number(condition.get('for_seconds')) is None:
        return 'for_seconds 必须是数字（秒）'
    if 'probability' in condition:
        probability = _number(condition.get('probability'))
        if probability is None or not 0 <= probability <= 1:
            return 'probability 必须在 0~1 之间'
    return None


def describe(condition: dict[str, Any]) -> str:
    """给面板/指南用的一句话说明。"""
    condition = condition or {}
    ctype = str(condition.get('type') or '')
    field = str(condition.get('field') or '')
    if ctype == 'field_over':
        text = f'{field} ≥ {condition.get("value")}'
    elif ctype == 'field_under':
        text = f'{field} ≤ {condition.get("value")}'
    elif ctype == 'field_eq':
        text = f'{field} = {condition.get("value")}'
    elif ctype == 'field_contains':
        text = f'{field} 含 "{condition.get("value")}"'
    elif ctype == 'field_in':
        text = f'{field} ∈ {condition.get("values")}'
    elif ctype == 'field_changed':
        text = f'{field} 变化'
    elif ctype == 'idle_over':
        text = f'空闲 ≥ {condition.get("seconds")} 秒'
    elif ctype == 'return_active':
        text = '从空闲回到活跃'
    elif ctype == 'cpu_over':
        text = f'CPU ≥ {condition.get("value")}%'
    elif ctype == 'memory_over':
        text = f'内存 ≥ {condition.get("value")}%'
    elif ctype == 'battery_under':
        text = f'未充电且电量 ≤ {condition.get("value")}%'
    elif ctype == 'hour_range':
        text = f'{condition.get("start")}~{condition.get("end")} 点'
    elif ctype == 'window_contains':
        text = f'窗口标题含 "{condition.get("value")}"'
    elif ctype == 'smtc_playing':
        text = '刚开始播放'
    else:
        text = ctype or '未知条件'
    if condition.get('for_seconds'):
        text += f'（持续 {condition["for_seconds"]} 秒）'
    return text
