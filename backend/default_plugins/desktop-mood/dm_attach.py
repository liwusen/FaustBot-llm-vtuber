"""即时附加（attach）：把"变化过的桌面信息"压进 ≤100 字，随用户消息一起交给模型。

设计约束（用户要求，勿改）：

1. **不对用户输入做任何分析**：本模块只吃桌面状态、时间线事件与规则暂存文本，
   永远不读、不匹配用户说的话（关键词/意图/情感一律不做）。
2. **追加内容总长硬上限 100 字**（含 ``[桌面] `` 前缀与 `` · `` 分隔符）。
3. **不重复**：只放相对上次附加发生变化的内容；最近 N 条已发过的行不再重复发出。

优先级：规则暂存的文本 > 时间线事件 > 字段变化（按源的 attach_weight 排序）。
规则 attach 命中后同样走这套算法补齐，不另开一套。
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, replace
from typing import Any, Iterable, Sequence

PREFIX = '[桌面] '
SEPARATOR = ' · '
BUDGET_DEFAULT = 100
MAX_LINES = 6
RING_SIZE = 20
EVENT_WEIGHT = 75
EVENT_LIMIT = 5
RULE_PRIORITY_DEFAULT = 50
MIN_TRUNCATED_CHARS = 8

KIND_RANK = {'rule': 0, 'event': 1, 'field': 2}


@dataclass(frozen=True)
class Candidate:
    """一条候选附加信息。key 用于变更追踪，text 是实际会出现的单行文本。"""

    key: str
    text: str
    weight: int
    at: int
    kind: str


def normalize(text: str) -> str:
    return ' '.join(str(text or '').split())


def fingerprint(text: str) -> str:
    """行级指纹：用于"最近发过的不再重复"的去重环。"""
    return hashlib.sha1(normalize(text).encode('utf-8')).hexdigest()[:12]


def prune_queue(queue: Sequence[Any], now: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按 TTL 切分附加队列：返回 (保留, 过期丢弃)。丢弃的条目由调用方记日志。"""
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for entry in queue:
        if not isinstance(entry, dict):
            continue
        expires_at = int(entry.get('expires_at') or 0)
        if expires_at and expires_at <= now:
            dropped.append(entry)
        else:
            kept.append(entry)
    return kept, dropped


def rule_candidates(queue: Sequence[Any], ring: Iterable[str]) -> list[Candidate]:
    """规则 attach 暂存的文本（作者显式指定，优先级最高）。"""
    sent = set(ring)
    out: list[Candidate] = []
    for entry in queue:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get('text') or '').strip()
        if not text or fingerprint(text) in sent:
            continue
        out.append(Candidate(key=f'rule:{fingerprint(text)}', text=text,
                             weight=int(entry.get('priority') or RULE_PRIORITY_DEFAULT),
                             at=int(entry.get('at') or 0), kind='rule'))
    return out


def event_candidates(events: Sequence[Any], since_at: int, ring: Iterable[str],
                     limit: int = EVENT_LIMIT) -> list[Candidate]:
    """时间线事件：只取上次附加之后发生的，新的在前。"""
    sent = set(ring)
    out: list[Candidate] = []
    for event in reversed(list(events)[-60:]):
        if not isinstance(event, dict):
            continue
        at = int(event.get('at') or 0)
        if at <= since_at:
            continue
        body = str(event.get('text') or '').strip()
        if not body:
            continue
        line = f'{time.strftime("%H:%M", time.localtime(at))} {body}'
        if fingerprint(line) in sent:
            continue
        out.append(Candidate(key=f'event:{at}', text=line, weight=EVENT_WEIGHT, at=at, kind='event'))
        if len(out) >= max(1, int(limit)):
            break
    return out


def _has_content(value: Any) -> bool:
    """空值不算"变化到有内容"：null / 空串 / 空列表 / 空字典一律不进附加（0 与 False 是有意义的）。"""
    if value is None or value == '':
        return False
    if isinstance(value, (list, dict, tuple)) and len(value) == 0:
        return False
    return True


def field_candidates(registry: Any, sources: Iterable[dict[str, Any]], observed: dict[str, Any],
                     last_pushed: dict[str, str], ring: Iterable[str]) -> list[Candidate]:
    """字段变化：只取相对上次附加变化过的字段，权重取自源元数据的 attach_weight（0 = 不参与）。

    参与字段默认是该源声明的全部字段，噪声大的源用 attach_fields 收窄。
    """
    sent = set(ring)
    out: list[Candidate] = []
    for source in sources:
        weight = registry.attach_weight(source)
        if weight <= 0:
            continue
        for field in registry.attach_fields_of(source):
            if field not in observed or not _has_content(observed[field]):
                continue
            rendered = registry.format_field(field, observed)
            if not rendered:
                continue
            key = f'field:{field}'
            line = f'{registry.labels.get(field, field)}: {rendered}'
            if last_pushed.get(key) == line or fingerprint(line) in sent:
                continue
            out.append(Candidate(key=key, text=line, weight=weight, at=0, kind='field'))
    return out


def compose(candidates: Iterable[Candidate], budget: int = BUDGET_DEFAULT, *,
            prefix: str = PREFIX, separator: str = SEPARATOR,
            max_lines: int = MAX_LINES) -> tuple[str, list[Candidate]]:
    """按预算拼出附加块；返回 (块文本, 被采用的候选)。

    预算包含前缀与分隔符本身；单条候选超预算时截断（至少留 8 字正文），仍然放不下就放弃。
    """
    if budget <= len(prefix):
        return '', []
    ordered = sorted(candidates, key=lambda item: (
        KIND_RANK.get(item.kind, 9), -item.weight, -item.at, len(item.text), item.key))
    chosen: list[Candidate] = []
    seen: set[str] = set()
    used = len(prefix)
    for candidate in ordered:
        if len(chosen) >= max(1, int(max_lines)):
            break
        if candidate.text in seen:
            continue
        cost = len(candidate.text) + (len(separator) if chosen else 0)
        if used + cost <= budget:
            chosen.append(candidate)
            seen.add(candidate.text)
            used += cost
            continue
        if chosen:
            continue  # 后面更短的候选也许还塞得下
        room = budget - len(prefix) - 1
        if room >= MIN_TRUNCATED_CHARS:
            chosen.append(replace(candidate, text=candidate.text[:room] + '…'))
            seen.add(candidate.text)
        break
    if not chosen:
        return '', []
    return prefix + separator.join(item.text for item in chosen), chosen


def update_ring(ring: Sequence[Any], lines: Iterable[str], now: int, size: int = RING_SIZE) -> list[dict[str, Any]]:
    """把本轮真正发出去的行写进去重环（跨重启持久化，避免重启后又推一遍同样的天气）。"""
    merged = [entry for entry in ring if isinstance(entry, dict)]
    for line in lines:
        merged.append({'hash': fingerprint(line), 'at': int(now)})
    return merged[-max(1, int(size)):]
